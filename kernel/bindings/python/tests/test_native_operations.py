"""Installed native scheduling preserves backend ownership through Python adapters."""

import asyncio
import fcntl
import gc
import json
from pathlib import Path

# The implementation is a native extension; declarations are checked separately.
import _skulk_capability_kernel as native  # pyright: ignore[reportMissingModuleSource]
import pytest

from skulk_capability_sdk.operations import OperationStatus


async def request(runner: native.NativeOperationRunner, kind: str) -> dict[str, object]:
    """Consume cancellation advisories and return one current native callback."""
    while True:
        raw = await asyncio.wait_for(runner.next_request(), 1)
        assert raw is not None
        value: dict[str, object] = json.loads(raw)
        if value["kind"] == "abort":
            continue
        assert value["kind"] == kind
        return value


async def status(
    runner: native.NativeOperationRunner, operation_id: str
) -> OperationStatus:
    raw = await runner.status(operation_id)
    assert raw is not None
    return OperationStatus.model_validate_json(raw)


async def wait_state(
    runner: native.NativeOperationRunner, operation_id: str, state: str
) -> OperationStatus:
    """Wait for a durable observation without triggering backend work."""
    async with asyncio.timeout(1):
        while True:
            value = await status(runner, operation_id)
            if value.state == state:
                return value
            await asyncio.sleep(0.001)


async def test_python_adapter_bridge_drives_owned_jobs_and_schema_results(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    journal = native.NativeJournal(str(tmp_path / "operations.sqlite"), "{}", 100)
    runner = native.operation_runner(
        journal,
        str(tmp_path / "logs"),
        "scope",
        0.005,
        0.2,
        '{"type":"object","required":["value"]}',
    )
    try:
        await runner.reserve("op", '{"number":1.0}')
        await runner.start()
        submit = await request(runner, "submit")
        assert (await status(runner, "op")).submission == "uncertain"
        assert submit["input"] == {"number": 1.0}
        assert isinstance(submit["id"], int)
        assert runner.respond(
            submit["id"], '{"kind":"accepted","reference":"original"}'
        )
        observe = await request(runner, "observe")
        assert observe["reference"] == "original" and isinstance(observe["id"], int)
        assert runner.respond(
            observe["id"],
            '{"kind":"observed","state":"succeeded","result":{"value":2}}',
        )
        value = await wait_state(runner, "op", "succeeded")
        assert value.backend_reference == "original" and value.result == {"value": 2}
        assert not runner.respond(observe["id"], '{"kind":"observed","state":"failed"}')
    finally:
        await runner.stop()
        journal.close()


async def test_python_cancel_is_confirmed_by_observation_and_health_remains_live(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    journal = native.NativeJournal(str(tmp_path / "operations.sqlite"), "{}", 100)
    runner = native.operation_runner(
        journal, str(tmp_path / "logs"), "scope", 0.005, 0.2, "{}"
    )
    try:
        await runner.reserve("op", "{}")
        await runner.start()
        submit = await request(runner, "submit")
        assert isinstance(submit["id"], int)
        runner.respond(submit["id"], '{"kind":"accepted","reference":"owned"}')
        observe = await request(runner, "observe")
        assert isinstance(observe["id"], int)
        await runner.cancel("op")
        runner.respond(observe["id"], '{"kind":"observed","state":"running"}')
        cancel = await request(runner, "cancel")
        assert isinstance(cancel["id"], int)
        runner.respond(cancel["id"], '{"kind":"cancel_accepted"}')
        observe = await request(runner, "observe")
        assert isinstance(observe["id"], int)
        ticks = 0
        for _ in range(4):
            ticks += 1
            await asyncio.sleep(0.002)
            assert (await status(runner, "op")).state == "running"
        assert ticks == 4
        runner.respond(observe["id"], '{"kind":"observed","state":"cancelled"}')
        assert (await wait_state(runner, "op", "cancelled")).result is None
    finally:
        await runner.stop()
        journal.close()


async def test_stop_and_timeout_refuse_late_reply_without_resubmitting(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    journal = native.NativeJournal(str(tmp_path / "operations.sqlite"), "{}", 100)
    runner = native.operation_runner(
        journal, str(tmp_path / "logs"), "scope", 0.005, 0.02, "{}"
    )
    try:
        await runner.reserve("op", "{}")
        await runner.start()
        submit = await request(runner, "submit")
        assert isinstance(submit["id"], int)
        value = await wait_state(runner, "op", "interrupted")
        assert value.submission == "uncertain" and value.backend_reference is None
        assert not runner.respond(
            submit["id"], '{"kind":"accepted","reference":"late"}'
        )
        await runner.stop()
        await runner.start()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(request(runner, "submit"), 0.03)
    finally:
        await runner.stop()
        journal.close()


async def test_scope_ownership_refuses_duplicate_workers_and_live_journal_close(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    journal = native.NativeJournal(str(tmp_path / "operations.sqlite"), "{}", 100)
    first = native.operation_runner(
        journal, str(tmp_path / "logs"), "scope", 0.005, 0.2, "{}"
    )
    second = native.operation_runner(
        journal, str(tmp_path / "logs"), "scope", 0.005, 0.2, "{}"
    )
    try:
        await first.reserve("op", "{}")
        await first.start()
        with pytest.raises(ValueError, match="storage"):
            await second.start()
        with pytest.raises(ValueError, match="busy"):
            journal.close()
        with pytest.raises(ValueError, match="busy"):
            journal.release_ownership()
        submit = await request(first, "submit")
        assert submit["operation_id"] == "op"
    finally:
        await first.stop()
        await second.stop()
        journal.close()


async def test_facade_drop_during_pending_start_cannot_create_orphan_worker(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    path = tmp_path / "operations.sqlite"
    journal = native.NativeJournal(str(path), "{}", 100)
    runner = native.operation_runner(
        journal, str(tmp_path / "logs"), "scope", 0.005, 2.0, "{}"
    )
    await runner.reserve("queued", "{}")
    with (tmp_path / "supervisor.lock").open("r+b") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        pending = runner.start()
        await asyncio.sleep(0.08)
        del runner
        gc.collect()
        fcntl.flock(held, fcntl.LOCK_UN)
        with pytest.raises(ValueError, match="stopped"):
            await asyncio.wait_for(pending, 3)
    # No worker may claim the row or retain the journal lease after the facade
    # is gone. The independently opened handle proves lease release as well.
    record = json.loads(
        journal.transact(
            '{"method":"status","scope":"scope","operation_id":"queued","now":101}'
        )
    )
    assert record["state"] == "queued"
    assert record["submission"] == "pending"
    reopened = native.NativeJournal(str(path), "{}", 101)
    reopened.close()
    journal.close()
