"""Native job facade through actual compiled application calls and scheduling."""

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from pydantic import JsonValue, TypeAdapter
from test_application import Peer, running

from skulk_capability_sdk.application import ApplicationStartup, CapabilityApp
from skulk_capability_sdk.contracts import OperationBounds
from skulk_capability_sdk.jobs import DurableJobs, JobDefinition
from skulk_capability_sdk.operations import OperationPage, OperationPlan, OperationState


class Backend:
    """Synthetic backend with separately observable acceptance and termination."""

    def __init__(self) -> None:
        self.submissions: list[str] = []
        self.started = asyncio.Event()
        self.complete = False
        self.cancelled = False

    async def submit(self, operation_id: str, payload: dict[str, JsonValue]) -> str:
        self.submissions.append(operation_id)
        self.started.set()
        return operation_id

    async def observe(
        self, reference: str
    ) -> tuple[
        OperationState,
        float | None,
        str | None,
        dict[str, JsonValue] | None,
        str | None,
    ]:
        if self.cancelled:
            return "cancelled", None, None, None, None
        if self.complete:
            return "succeeded", 1.0, "done", {"value": 2}, None
        return "running", 0.0, "waiting", None, None

    async def cancel(self, reference: str) -> None:
        self.cancelled = True


def definition() -> JobDefinition:
    """Use explicit native-safe input/result shapes, not a legacy Descriptor."""
    return JobDefinition.create(
        id="fixture.jobs",
        version="1.0.0",
        title="Synthetic jobs",
        description="Account-free inference job fixture",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "number"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        result_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )


async def plan(value: dict[str, JsonValue]) -> OperationPlan:
    """Return a fixed synthetic estimate without creating a backend job."""
    return OperationPlan(
        plan_digest="a" * 64, summary="Synthetic", expires_at=2**31, input=value
    )


def application(
    directory: Path,
    backend: Backend,
    spec: JobDefinition,
    observed: list[ApplicationStartup] | None = None,
) -> tuple[CapabilityApp, DurableJobs]:
    jobs = DurableJobs(spec, backend, plan=plan, poll_seconds=0.01, adapter_seconds=0.2)

    @asynccontextmanager
    async def setup(startup: ApplicationStartup) -> AsyncGenerator[None]:
        if observed is not None:
            observed.append(startup)
        async with jobs.serve(startup, directory=directory):
            yield

    app = CapabilityApp(setup=setup)
    jobs.register(app)
    return app, jobs


async def request(
    peer: Peer, role: str, value: dict[str, JsonValue], *, caller: str = "client:first"
) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    """Send an independently admitted original caller and exact native target."""
    await peer.invoke(
        payload=json.dumps(value).encode(),
        capability_id=f"fixture.jobs.{role}",
        caller_id=caller,
    )
    frame, body = await asyncio.wait_for(peer.receive(), 3)
    assert frame["kind"] == "result"
    return frame, TypeAdapter(dict[str, JsonValue]).validate_json(body) if body else {}


async def state(
    peer: Peer, operation_id: str, expected: str, *, caller: str = "client:first"
) -> dict[str, JsonValue]:
    async with asyncio.timeout(3):
        while True:
            frame, value = await request(
                peer,
                "read",
                {"op": "status", "operation_id": operation_id},
                caller=caller,
            )
            assert frame["error"] is None
            if value["state"] == expected:
                return value
            await asyncio.sleep(0.01)


async def test_native_jobs_idempotency_original_caller_partition_and_health(
    tmp_path: Path,
) -> None:
    backend, spec = Backend(), definition()
    app, _ = application(tmp_path / "jobs", backend, spec)
    async with running(
        app,
        descriptors=spec.descriptors(),
        operations=OperationBounds().model_dump(mode="json"),
    ) as peer:
        first: dict[str, JsonValue] = {
            "op": "start",
            "operation_id": "first-only",
            "input": {"value": 1},
        }
        frame, _ = await request(peer, "work", first)
        assert frame["error"] is None
        await asyncio.wait_for(backend.started.wait(), 1)
        frame, _ = await request(peer, "work", {**first, "input": {"value": 1.0}})
        assert frame["error"] is None and len(backend.submissions) == 1
        frame, _ = await request(peer, "work", {**first, "input": {"value": 3}})
        assert frame["error"] is not None and len(backend.submissions) == 1
        frame, _ = await request(peer, "read", first)
        assert frame["error"] == "invalid_payload"
        frame, _ = await request(
            peer,
            "read",
            {"op": "status", "operation_id": "first-only"},
            caller="client:second",
        )
        assert frame["error"] is not None
        frame, page = await request(
            peer, "read", {"op": "list"}, caller="client:second"
        )
        assert (
            frame["error"] is None and page["operations"] == [] and page["active"] == 0
        )
        frame, _ = await request(
            peer,
            "work",
            {"op": "cancel", "operation_id": "first-only"},
            caller="client:second",
        )
        assert frame["error"] is not None and not backend.cancelled
        await peer.health()
        frame, _ = await request(peer, "work", first, caller="client:second")
        assert frame["error"] is None
        backend.complete = True
        value = await state(peer, "first-only", "succeeded")
        assert value["backend_reference"] is None and value["result"] == {"value": 2}
        await state(peer, "first-only", "succeeded", caller="client:second")
        assert (
            len(backend.submissions) == 2
            and backend.submissions[0] != backend.submissions[1]
        )
        for caller in ("client:first", "client:second"):
            frame, page = await request(peer, "read", {"op": "list"}, caller=caller)
            assert frame["error"] is None
            observed = OperationPage.model_validate_json(json.dumps(page))
            assert len(observed.operations) == 1
            assert observed.operations[0].operation_id == "first-only"
            assert observed.operations[0].input is None


async def test_native_jobs_restart_reconciles_original_reference_without_replay(
    tmp_path: Path,
) -> None:
    backend, spec = Backend(), definition()
    folder = tmp_path / "jobs"
    app, _ = application(folder, backend, spec)
    async with running(
        app,
        descriptors=spec.descriptors(),
        operations=OperationBounds().model_dump(mode="json"),
    ) as peer:
        frame, _ = await request(
            peer, "work", {"op": "start", "operation_id": "kept", "input": {"value": 1}}
        )
        assert frame["error"] is None
        await state(peer, "kept", "running")
    assert len(backend.submissions) == 1
    backend.complete = True
    app, _ = application(folder, backend, spec)
    async with running(
        app,
        descriptors=spec.descriptors(),
        operations=OperationBounds().model_dump(mode="json"),
    ) as peer:
        value = await state(peer, "kept", "succeeded")
        assert value["submission"] == "accepted" and len(backend.submissions) == 1
        frame, _ = await request(
            peer, "work", {"op": "start", "operation_id": "kept", "input": {"value": 1}}
        )
        assert frame["error"] is None and len(backend.submissions) == 1


async def test_native_uncertain_submission_remains_outstanding_after_restart(
    tmp_path: Path,
) -> None:
    """An ambiguous backend create never becomes a retryable new submission."""

    class UncertainBackend(Backend):
        async def submit(self, operation_id: str, payload: dict[str, JsonValue]) -> str:
            self.submissions.append(operation_id)
            raise RuntimeError("private provider token and response")

    backend, spec = UncertainBackend(), definition()
    directory = tmp_path / "jobs"
    app, _ = application(directory, backend, spec)
    async with running(
        app,
        descriptors=spec.descriptors(),
        operations=OperationBounds().model_dump(mode="json"),
    ) as peer:
        await request(
            peer,
            "work",
            {"op": "start", "operation_id": "uncertain", "input": {"value": 1}},
        )
        value = await state(peer, "uncertain", "interrupted")
        assert value[
            "submission"
        ] == "uncertain" and "private provider" not in json.dumps(value)
    app, _ = application(directory, backend, spec)
    async with running(
        app,
        descriptors=spec.descriptors(),
        operations=OperationBounds().model_dump(mode="json"),
    ) as peer:
        frame, value = await request(
            peer,
            "work",
            {"op": "start", "operation_id": "uncertain", "input": {"value": 1}},
        )
        assert frame["error"] is None and value["state"] == "interrupted"
        await asyncio.sleep(0.05)
        assert len(backend.submissions) == 1
        _, page = await request(peer, "read", {"op": "list"})
        assert page["outstanding"] == 1 and page["active"] == 0
        await peer.health()


async def test_cancel_acceptance_requires_backend_termination_observation(
    tmp_path: Path,
) -> None:
    """A successful cancel request is not a terminal job result."""

    class CancellingBackend(Backend):
        def __init__(self) -> None:
            super().__init__()
            self.requested = asyncio.Event()

        async def cancel(self, reference: str) -> None:
            self.requested.set()

    backend, spec = CancellingBackend(), definition()
    app, _ = application(tmp_path / "jobs", backend, spec)
    async with running(
        app,
        descriptors=spec.descriptors(),
        operations=OperationBounds().model_dump(mode="json"),
    ) as peer:
        await request(
            peer,
            "work",
            {"op": "start", "operation_id": "cancel", "input": {"value": 1}},
        )
        await state(peer, "cancel", "running")
        frame, value = await request(
            peer, "work", {"op": "cancel", "operation_id": "cancel"}
        )
        assert frame["error"] is None and value["state"] == "running"
        await asyncio.wait_for(backend.requested.wait(), 1)
        await state(peer, "cancel", "running")
        backend.cancelled = True
        await state(peer, "cancel", "cancelled")
        await peer.health()


async def test_invalid_native_result_is_failed_before_persistence(
    tmp_path: Path,
) -> None:
    """Portable results are checked before persistence in the shared journal."""

    class InvalidBackend(Backend):
        async def observe(
            self, reference: str
        ) -> tuple[
            OperationState,
            float | None,
            str | None,
            dict[str, JsonValue] | None,
            str | None,
        ]:
            return "succeeded", 1.0, None, {"value": float("nan")}, None

    backend, spec = InvalidBackend(), definition()
    app, _ = application(tmp_path / "jobs", backend, spec)
    async with running(
        app,
        descriptors=spec.descriptors(),
        operations=OperationBounds().model_dump(mode="json"),
    ) as peer:
        await request(
            peer,
            "work",
            {"op": "start", "operation_id": "invalid", "input": {"value": 1}},
        )
        value = await state(peer, "invalid", "failed")
        assert (
            value["result"] is None
            and value["error"] == "job result does not satisfy its native schema"
        )
        await peer.health()


async def test_pruned_job_identity_cannot_start_again(tmp_path: Path) -> None:
    """Display retention never revives the caller's durable idempotency key."""
    backend, spec = Backend(), definition()
    backend.complete = True
    app, _ = application(tmp_path / "jobs", backend, spec)
    bounds = OperationBounds(retain=1)
    async with running(
        app, descriptors=spec.descriptors(), operations=bounds.model_dump(mode="json")
    ) as peer:
        for operation_id in ("old", "new"):
            await request(
                peer,
                "work",
                {"op": "start", "operation_id": operation_id, "input": {"value": 1}},
            )
            await state(peer, operation_id, "succeeded")
        frame, _ = await request(
            peer, "work", {"op": "start", "operation_id": "old", "input": {"value": 1}}
        )
        assert frame["error"] is not None and len(backend.submissions) == 2
        _, page = await request(peer, "read", {"op": "list"})
        visible = OperationPage.model_validate_json(json.dumps(page))
        assert [s.operation_id for s in visible.operations] == ["new"]


async def test_repeated_shutdown_cancellation_retains_job_custody(
    tmp_path: Path,
) -> None:
    """A replacement cannot reopen state while an old adapter is still cleaning up."""

    class SlowBackend(Backend):
        def __init__(self) -> None:
            super().__init__()
            self.cleaning = asyncio.Event()
            self.release = asyncio.Event()
            self.cleaned = asyncio.Event()

        async def submit(self, operation_id: str, payload: dict[str, JsonValue]) -> str:
            self.submissions.append(operation_id)
            self.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.cleaning.set()
                await self.release.wait()
                self.cleaned.set()
            return "unused"

    backend, spec = SlowBackend(), definition()
    folder = tmp_path / "jobs"
    jobs = DurableJobs(spec, backend, plan=plan, poll_seconds=0.01, adapter_seconds=5)
    observed: list[ApplicationStartup] = []

    @asynccontextmanager
    async def setup(startup: ApplicationStartup) -> AsyncGenerator[None]:
        observed.append(startup)
        async with jobs.serve(startup, directory=folder):
            yield

    app = CapabilityApp(setup=setup)
    jobs.register(app)
    async with running(
        app,
        descriptors=spec.descriptors(),
        operations=OperationBounds().model_dump(mode="json"),
    ) as peer:
        await request(
            peer,
            "work",
            {"op": "start", "operation_id": "cleanup", "input": {"value": 1}},
        )
        await asyncio.wait_for(backend.started.wait(), 1)
        peer.app_task.cancel()
        await asyncio.wait_for(backend.cleaning.wait(), 1)
        peer.app_task.cancel()
        peer.app_task.cancel()
        replacement = DurableJobs(spec, Backend(), plan=plan)
        try:
            with pytest.raises(BlockingIOError):
                async with replacement.serve(observed[0], directory=folder):
                    raise AssertionError("reopened state before cleanup")
            assert not peer.app_task.done() and not backend.cleaned.is_set()
        finally:
            backend.release.set()
        with pytest.raises(asyncio.CancelledError):
            await peer.app_task
        assert backend.cleaned.is_set()
    # The lock is released only after actual owned adapter cleanup.
    async with replacement.serve(observed[0], directory=folder):
        pass


async def test_actual_job_process_crash_never_resubmits_an_uncertain_create(
    tmp_path: Path,
) -> None:
    """SIGKILL after a synthetic backend effect retains the journal's replay fence."""
    import os
    import sys

    class ProcessApplication:
        def __init__(self) -> None:
            self.process: asyncio.subprocess.Process | None = None
            self.crash_expected = False

        async def serve(self, configuration_descriptor: int) -> None:
            """Acquire, supervise and reap only this test's separate SDK process."""

            async def spawn() -> None:
                self.process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-I",
                    str(Path(__file__).parent / "fixtures/job_application.py"),
                    str(configuration_descriptor),
                    pass_fds=(configuration_descriptor,),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )

            acquisition = asyncio.create_task(spawn())
            try:
                await asyncio.shield(acquisition)
                assert self.process is not None
                code = await self.process.wait()
                assert code == 0 or self.crash_expected
            finally:
                await acquisition
                os.close(configuration_descriptor)
                if self.process is not None and self.process.returncode is None:
                    self.process.kill()
                    await self.process.wait()

    spec = definition()
    folder = tmp_path / "jobs"
    app = ProcessApplication()
    async with running(
        app,
        descriptors=spec.descriptors(),
        operations=OperationBounds().model_dump(mode="json"),
        configuration={"directory": str(folder)},
    ) as peer:
        frame, _ = await request(
            peer,
            "work",
            {"op": "start", "operation_id": "crashed", "input": {"value": 1}},
        )
        assert frame["error"] is None
        assert app.process is not None and app.process.stdout is not None
        assert await asyncio.wait_for(app.process.stdout.readline(), 3) == (
            b"synthetic effect accepted\n"
        )
        assert len((folder / "backend-effects").read_bytes().splitlines()) == 1
        assert app.process is not None
        app.crash_expected = True
        app.process.kill()
        await app.process.wait()
        await peer.app_task
    replacement = ProcessApplication()
    async with running(
        replacement,
        descriptors=spec.descriptors(),
        operations=OperationBounds().model_dump(mode="json"),
        configuration={"directory": str(folder)},
    ) as peer:
        value = await state(peer, "crashed", "interrupted")
        assert value["submission"] == "uncertain"
        frame, _ = await request(
            peer,
            "work",
            {"op": "start", "operation_id": "crashed", "input": {"value": 1.0}},
        )
        assert frame["error"] is None
        await asyncio.sleep(0.05)
        assert len((folder / "backend-effects").read_bytes().splitlines()) == 1
        await peer.health()


async def test_changed_operation_bounds_cannot_reopen_accepted_state(
    tmp_path: Path,
) -> None:
    """An update cannot silently increase the lifetime of already accepted work."""
    backend, spec = Backend(), definition()
    folder = tmp_path / "jobs"
    original, _ = application(folder, backend, spec)
    async with running(
        original,
        descriptors=spec.descriptors(),
        operations=OperationBounds().model_dump(mode="json"),
    ) as peer:
        await request(
            peer,
            "work",
            {"op": "start", "operation_id": "bounded", "input": {"value": 1}},
        )
        await asyncio.wait_for(backend.started.wait(), 1)
    before = (folder / "operations.sqlite").read_bytes()
    replacement = DurableJobs(spec, backend, plan=plan)

    @asynccontextmanager
    async def changed(startup: ApplicationStartup) -> AsyncGenerator[None]:
        with pytest.raises(ValueError, match="belongs to another"):
            async with replacement.serve(startup, directory=folder):
                raise AssertionError("changed job limits were silently adopted")
        yield

    app = CapabilityApp(setup=changed)
    replacement.register(app)
    async with running(
        app,
        descriptors=spec.descriptors(),
        operations=OperationBounds(max_runtime_seconds=7200).model_dump(mode="json"),
    ) as peer:
        await peer.health()
    assert (folder / "operations.sqlite").read_bytes() == before
    assert len(backend.submissions) == 1


@pytest.mark.parametrize("damage", ["missing", "empty", "corrupt", "binding"])
async def test_initialized_job_state_is_never_recreated(
    tmp_path: Path, damage: str
) -> None:
    """Missing and damaged state cannot reset durable reservations or node custody."""
    import sqlite3

    backend, spec = Backend(), definition()
    folder = tmp_path / "jobs"
    observed: list[ApplicationStartup] = []
    app, _ = application(folder, backend, spec, observed)
    async with running(
        app,
        descriptors=spec.descriptors(),
        operations=OperationBounds().model_dump(mode="json"),
    ):
        pass
    path = folder / "operations.sqlite"
    if damage == "missing":
        path.unlink()
    elif damage in {"empty", "corrupt"}:
        path.write_bytes(b"" if damage == "empty" else b"not a database")
    else:
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE native_job_binding SET binding_digest='different'"
            )
    before = path.read_bytes() if path.exists() else None
    replacement = DurableJobs(spec, backend, plan=plan)
    with pytest.raises((OSError, ValueError, sqlite3.Error)):
        async with replacement.serve(observed[0], directory=folder):
            raise AssertionError("damaged job state was recreated")
    assert (path.read_bytes() if path.exists() else None) == before
    assert backend.submissions == []


async def test_nonportable_adapter_metadata_never_enters_native_status(
    tmp_path: Path,
) -> None:
    """Unsafe Unicode in callback metadata cannot poison the persisted status schema."""

    class UnsafeMetadataBackend(Backend):
        def __init__(self) -> None:
            super().__init__()
            self.observed = asyncio.Event()

        async def observe(
            self, reference: str
        ) -> tuple[
            OperationState,
            float | None,
            str | None,
            dict[str, JsonValue] | None,
            str | None,
        ]:
            self.observed.set()
            return "succeeded", 1.0, "\ud800", {"value": 2}, None

    backend, spec = UnsafeMetadataBackend(), definition()
    app, _ = application(tmp_path / "jobs", backend, spec)
    async with running(
        app,
        descriptors=spec.descriptors(),
        operations=OperationBounds().model_dump(mode="json"),
    ) as peer:
        await request(
            peer,
            "work",
            {"op": "start", "operation_id": "metadata", "input": {"value": 1}},
        )
        await asyncio.wait_for(backend.observed.wait(), 1)
        await asyncio.sleep(0.03)
        frame, value = await request(
            peer, "read", {"op": "status", "operation_id": "metadata"}
        )
        assert frame["error"] is None and value["state"] != "succeeded"
        assert value["result"] is None and "\\ud800" not in json.dumps(value)
        await peer.health()
