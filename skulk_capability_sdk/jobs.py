"""Native durable inference jobs over the shared journal and scheduler.

The host authorizes read/inference descriptors. This facade partitions job
observations by the original admitted caller; exported contexts grant no rights.
Governed proposal/write execution remains outside this execution cohort.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import sqlite3
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, cast, final
from urllib.parse import quote

from pydantic import JsonValue, TypeAdapter

from .application import (
    ApplicationStartup,
    CapabilityApp,
    Invocation,
)
from .contracts import OperationBounds
from .database import Database
from .operations import (
    MAX_OFFSET,
    PAGE_SIZE,
    JobAdapter,
    OperationCall,
    OperationLog,
    OperationPage,
    OperationPlan,
    OperationStatus,
    SubmissionRefused,
    operation_schemas,
)
from .runtime import finish_owned, native_module
from .storage import HostLock, atomic_private, read_private, secure_directory

_OBJECT = TypeAdapter(dict[str, JsonValue])
_OBJECTS = TypeAdapter(list[dict[str, JsonValue]])
_TEXT = TypeAdapter(str)
_INTEGER = TypeAdapter(int)
_PRINCIPAL_FIELDS = ("caller_id", "caller_kind", "issuer_node_id")


class _Journal(Protocol):
    def close(self) -> None: ...


class _Schema(Protocol):
    def matches(self, source: str) -> bool: ...


class _SchemaFactory(Protocol):
    def __call__(self, source: str) -> _Schema: ...


class _JournalFactory(Protocol):
    def __call__(self, path: str, bounds_json: str, now: int) -> _Journal: ...


class _Runner(Protocol):
    def start(self) -> Awaitable[None]: ...
    def stop(self) -> Awaitable[None]: ...
    def reserve(self, operation_id: str | None, payload: str) -> Awaitable[str]: ...
    def status(self, operation_id: str) -> Awaitable[str | None]: ...
    def cancel(self, operation_id: str) -> Awaitable[str]: ...
    def log(self, operation_id: str, offset: int) -> Awaitable[str]: ...
    def next_request(self) -> Awaitable[str | None]: ...
    def respond(self, id: int, response: str) -> bool: ...


class _Kernel(Protocol):
    @property
    def NativeJournal(self) -> _JournalFactory: ...
    @property
    def PortableSchema(self) -> _SchemaFactory: ...
    def portable_canonical_json(self, source: str) -> bytes: ...
    def portable_v2_contract_json(self, name: str, source: str) -> bytes: ...
    def portable_v2_contract_digest(self, name: str, source: str) -> str: ...
    def portable_v2_profile_digest(self) -> str: ...
    def operation_runner(
        self,
        journal: _Journal,
        logs: str,
        scope: str,
        poll_seconds: float,
        adapter_seconds: float,
        result_schema: str,
    ) -> _Runner: ...


def _kernel() -> _Kernel:
    return cast(_Kernel, native_module())


def _json(value: object) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_kernel().portable_canonical_json(_json(value))).hexdigest()


@final
@dataclass(frozen=True, slots=True, repr=False)
class JobDefinition:
    """Two native descriptors: job observation and durable inference submission."""

    _canonical: bytes = field(repr=False)

    @classmethod
    def create(
        cls,
        *,
        id: str,
        version: str,
        title: str,
        description: str,
        input_schema: dict[str, JsonValue],
        result_schema: dict[str, JsonValue],
        processing_domain: Literal["local", "private_site", "external"] = "local",
    ) -> JobDefinition:
        """Build compiled portable/2 declarations, without admitting any caller.

        The read endpoint accepts status/list/log only. The inference endpoint
        accepts start/cancel/plan. Adapters must perform inference, not governed
        provisioning or unrelated write effects. Include both descriptors and
        explicit operation bounds in the signed managed/2 manifest.
        """
        kernel = _kernel()
        call_schema, output_schema = operation_schemas(
            input_schema, result_schema, maximum_offset=MAX_OFFSET - 1
        )
        variants = _OBJECTS.validate_python(call_schema["oneOf"], strict=True)
        descriptors: list[dict[str, JsonValue]] = []
        for suffix, behavior, verbs in (
            ("read", "read", {"status", "list", "log"}),
            ("work", "inference", {"start", "cancel", "plan"}),
        ):
            selected: list[JsonValue] = []
            for variant in variants:
                properties = _OBJECT.validate_python(variant["properties"], strict=True)
                operation = _OBJECT.validate_python(properties["op"], strict=True)
                if operation["const"] in verbs:
                    selected.append(variant)
            schema = dict(call_schema)
            schema["oneOf"] = selected
            raw = {
                "contract_profile": "skulk.portable/2",
                "profile_digest": kernel.portable_v2_profile_digest(),
                "id": f"{id}.{suffix}",
                "version": version,
                "title": title,
                "description": description,
                "behavior": behavior,
                "processing_domain": "local" if suffix == "read" else processing_domain,
                "io_mode": "unary",
                "input_schema": schema,
                "output_schema": output_schema,
                "annotations": {"skulk.operation": "1", "skulk.jobs": "1"},
            }
            descriptors.append(
                _OBJECT.validate_json(
                    kernel.portable_v2_contract_json("Descriptor", _json(raw))
                )
            )
        return cls(
            kernel.portable_canonical_json(
                _json(
                    {
                        "descriptors": descriptors,
                        "result_schema": result_schema,
                    }
                )
            )
        )

    def descriptors(self) -> list[dict[str, JsonValue]]:
        """Return independent native read/work declarations for signing."""
        return _OBJECTS.validate_python(
            _OBJECT.validate_json(self._canonical)["descriptors"], strict=True
        )

    def result_schema(self) -> dict[str, JsonValue]:
        """Return a copy of the compiled portable result-schema declaration."""
        return _OBJECT.validate_python(
            _OBJECT.validate_json(self._canonical)["result_schema"], strict=True
        )


_ORIGINS = """
CREATE TABLE IF NOT EXISTS native_job_binding (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    binding_digest TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS native_job_origins (
    internal_id TEXT PRIMARY KEY,
    principal TEXT NOT NULL,
    original_id TEXT NOT NULL,
    UNIQUE(principal,original_id)
);
"""


@dataclass(slots=True, repr=False)
class _State:
    journal: _Journal
    runner: _Runner
    database: Database
    bounds: OperationBounds
    scope: str
    node_id: str
    callbacks: dict[int, asyncio.Task[None]] = field(default_factory=dict)
    pump: asyncio.Task[None] | None = None
    closing: bool = False
    failed: bool = False

    def key(self, principal: str, operation_id: str) -> str:
        return _digest(
            {"scope": self.scope, "principal": principal, "operation_id": operation_id}
        )

    def origin(self, principal: str, operation_id: str) -> str:
        key = self.key(principal, operation_id)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT principal,original_id FROM native_job_origins "
                "WHERE internal_id=?",
                (key,),
            ).fetchone()
            if row is None:
                count = connection.execute(
                    "SELECT count(*) FROM native_job_origins"
                ).fetchone()[0]
                if count >= self.bounds.reservations:
                    raise ValueError("job identity capacity reached")
                connection.execute(
                    "INSERT INTO native_job_origins VALUES (?,?,?)",
                    (key, principal, operation_id),
                )
            elif row != (principal, operation_id):
                raise ValueError("job identity differs")
        return key

    def page(
        self, principal: str, offset: int
    ) -> tuple[list[tuple[str, str]], int, int]:
        with self.database.transaction() as connection:
            rows: list[tuple[str, str]] = connection.execute(
                "SELECT o.internal_id,o.original_id FROM native_job_origins o "
                "JOIN operations j ON j.operation_id=o.internal_id "
                "WHERE o.principal=? AND j.scope=? "
                "ORDER BY j.sequence DESC LIMIT ? OFFSET ?",
                (principal, self.scope, PAGE_SIZE + 1, offset),
            ).fetchall()
            counts: tuple[int, int] = connection.execute(
                "SELECT coalesce(sum(j.state IN ('queued','running')),0), "
                "coalesce(sum(j.state='interrupted'),0) FROM native_job_origins o "
                "JOIN operations j ON j.operation_id=o.internal_id "
                "WHERE o.principal=? AND j.scope=?",
                (principal, self.scope),
            ).fetchone()
        return rows, counts[0], counts[1]


def _open(
    directory: Path, binding: dict[str, JsonValue], bounds: OperationBounds
) -> tuple[_Journal, Database]:
    marker, path = directory / "native-jobs.json", directory / "operations.sqlite"
    canonical = _kernel().portable_canonical_json(_json(binding))
    if marker.exists():
        if read_private(marker) != canonical:
            raise ValueError("native job state belongs to another definition or node")
        read_private(path, 128 * 1024**2)
        # NativeJournal initializes its legacy-compatible tables. Prove the
        # initialized native facade still has its schema before allowing that.
        with contextlib.closing(
            sqlite3.connect(
                "file:" + quote(str(path), safe="/") + "?mode=ro", uri=True, timeout=2
            )
        ) as connection:
            for query in (
                "SELECT key,input_digest,reserved_at FROM reservations LIMIT 0",
                "SELECT key,operation_id,scope,state,submission,sequence "
                "FROM operations LIMIT 0",
                "SELECT internal_id,principal,original_id "
                "FROM native_job_origins LIMIT 0",
            ):
                connection.execute(query)
            if connection.execute(
                "SELECT binding_digest FROM native_job_binding WHERE singleton=1"
            ).fetchone() != (_digest(binding),):
                raise ValueError("native job journal binding differs")
    elif path.exists() or (directory / "logs").exists():
        raise ValueError("unbound native job storage requires explicit recovery")
    journal = _kernel().NativeJournal(
        str(path), bounds.model_dump_json(), int(time.time())
    )
    try:
        database = Database(path, _ORIGINS)
        if not marker.exists():
            with database.transaction() as connection:
                connection.execute(
                    "INSERT INTO native_job_binding VALUES (1,?)",
                    (_digest(binding),),
                )
            atomic_private(marker, canonical)
        return journal, database
    except BaseException:
        journal.close()
        raise


@final
class DurableJobs:
    """Register a native inference job service with private original-caller state.

    Use serve() in the application's setup context. Native scheduling and replay
    fences are shared with other language bindings. Cancellation of a start RPC
    does not retract a durable acceptance: inspect the same operation ID instead
    of creating another. Accepted work has its own declared runtime bound; it
    holds no live caller context and cannot use one to delegate after the RPC.
    """

    def __init__(
        self,
        definition: JobDefinition,
        adapter: JobAdapter,
        *,
        plan: Callable[[dict[str, JsonValue]], Awaitable[OperationPlan]],
        poll_seconds: float = 1.0,
        adapter_seconds: float = 30.0,
    ) -> None:
        """Bind provider-owned adapter behavior and explicit native timing policy."""
        self.definition, self.adapter, self.plan = definition, adapter, plan
        self.poll_seconds, self.adapter_seconds = poll_seconds, adapter_seconds
        self._state: _State | None = None
        self._opening = False
        self._result_validator = _kernel().PortableSchema(
            _json(definition.result_schema())
        )

    def register(self, app: CapabilityApp) -> None:
        """Register exact read/work handlers before application startup."""
        read, work = self.definition.descriptors()
        app.unary(
            _TEXT.validate_python(read["id"], strict=True),
            self._read,
            version=_TEXT.validate_python(read["version"], strict=True),
        )
        app.unary(
            _TEXT.validate_python(work["id"], strict=True),
            self._work,
            version=_TEXT.validate_python(work["version"], strict=True),
        )

    @asynccontextmanager
    async def serve(
        self, startup: ApplicationStartup, *, directory: Path
    ) -> AsyncGenerator[None]:
        """Own private durable state and all adapter cleanup until context exit.

        The caller chooses stable application storage, e.g. beneath the normal
        host-selected process working directory. No request can select a path.
        The directory is bound to the installed node and exact descriptors;
        changed or missing initialized state requires explicit recovery.
        """
        if self._opening or self._state is not None:
            raise ValueError("job service is already open")
        self._opening = True
        lock: HostLock | None = None
        state: _State | None = None
        journal: _Journal | None = None
        database: Database | None = None
        try:
            descriptors = self.definition.descriptors()
            for descriptor in descriptors:
                endpoint = startup.endpoint(
                    _TEXT.validate_python(descriptor["id"], strict=True),
                    _TEXT.validate_python(descriptor["version"], strict=True),
                )
                if (
                    endpoint.canonical_descriptor()
                    != _kernel().portable_v2_contract_json(
                        "Descriptor", _json(descriptor)
                    )
                ):
                    raise ValueError("native job descriptors differ from startup")
            bounds = startup.operation_bounds
            if bounds is None:
                raise ValueError("native job bounds are not declared")
            directory = directory.absolute()
            secure_directory(directory)
            custody = directory / "custody"
            secure_directory(custody)
            lock = HostLock(custody)
            scope = _digest(
                {
                    "node_id": startup.node_id,
                    "descriptors": descriptors,
                    "format": "skulk.jobs/1",
                }
            )

            async def open_state() -> None:
                nonlocal journal, database
                journal, database = await asyncio.to_thread(
                    _open,
                    directory,
                    {
                        "format": "skulk.jobs/1",
                        "node_id": startup.node_id,
                        "scope": scope,
                        "bounds": bounds.model_dump(mode="json"),
                    },
                    bounds,
                )

            await finish_owned(asyncio.create_task(open_state()))
            assert journal is not None and database is not None
            runner = _kernel().operation_runner(
                journal,
                str(directory / "logs"),
                scope,
                self.poll_seconds,
                self.adapter_seconds,
                _json(self.definition.result_schema()),
            )
            state = _State(journal, runner, database, bounds, scope, startup.node_id)
            self._state = state

            async def start() -> None:
                await runner.start()

            await finish_owned(asyncio.create_task(start()))
            state.pump = asyncio.create_task(self._pump(state))
            yield
        finally:

            async def close() -> None:
                try:
                    if state is not None:
                        state.closing = True
                        await state.runner.stop()
                        if state.pump is not None:
                            await asyncio.gather(state.pump, return_exceptions=True)
                        tasks = tuple(state.callbacks.values())
                        for task in tasks:
                            if not task.done() and task.cancelling() == 0:
                                task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                finally:
                    if journal is not None:
                        journal.close()
                    if lock is not None:
                        lock.close()
                    self._state = None
                    self._opening = False

            await finish_owned(asyncio.create_task(close()))

    def _current(self) -> _State:
        state = self._state
        if state is None or state.closing or state.failed:
            raise ValueError("native job service is unavailable")
        return state

    def _admit(
        self, invocation: Invocation, role: Literal["read", "work"]
    ) -> tuple[_State, str, OperationCall]:
        state = self._current()
        context = invocation.context.export()
        target = _OBJECT.validate_python(context["executor"], strict=True)
        descriptor = self.definition.descriptors()[0 if role == "read" else 1]
        if (
            target["node_id"] != state.node_id
            or invocation.capability_id != descriptor["id"]
            or invocation.version != descriptor["version"]
            or target["behavior"] != descriptor["behavior"]
            or target["processing_domain"] != descriptor["processing_domain"]
            or target["descriptor_revision"]
            != _kernel().portable_v2_contract_digest("Descriptor", _json(descriptor))
        ):
            raise PermissionError("native job endpoint differs")
        principal = _digest({field: context[field] for field in _PRINCIPAL_FIELDS})
        call = OperationCall.model_validate_json(
            _kernel().portable_canonical_json(invocation.raw_payload.decode())
        )
        if (call.op in ("status", "list", "log")) != (role == "read"):
            raise PermissionError("native job verb requires its exact descriptor")
        return state, principal, call

    @staticmethod
    def _status(raw: str, operation_id: str) -> dict[str, JsonValue]:
        status = OperationStatus.model_validate_json(raw)
        return status.model_copy(
            update={"operation_id": operation_id, "backend_reference": None}
        ).model_dump(mode="json")

    async def _read(self, invocation: Invocation) -> dict[str, JsonValue]:
        state, principal, call = self._admit(invocation, "read")
        if call.op == "list":
            offset = call.offset or 0
            rows, active, outstanding = await finish_owned(
                asyncio.create_task(asyncio.to_thread(state.page, principal, offset))
            )
            statuses: list[OperationStatus] = []
            for key, original_id in rows[:PAGE_SIZE]:
                raw = await state.runner.status(key)
                if raw is not None:
                    value = OperationStatus.model_validate_json(raw)
                    statuses.append(
                        value.model_copy(
                            update={
                                "operation_id": original_id,
                                "backend_reference": None,
                                "input": None,
                                "result": None,
                            }
                        )
                    )
            return OperationPage(
                operations=tuple(statuses),
                active=active,
                outstanding=outstanding,
                next_offset=offset + PAGE_SIZE if len(rows) > PAGE_SIZE else None,
            ).model_dump(mode="json")
        assert call.operation_id is not None
        key = state.key(principal, call.operation_id)
        raw = await state.runner.status(key)
        if raw is None:
            raise ValueError("unknown operation")
        if call.op == "status":
            return self._status(raw, call.operation_id)
        log = OperationLog.model_validate_json(
            await state.runner.log(key, call.offset or 0)
        )
        return log.model_copy(update={"operation_id": call.operation_id}).model_dump(
            mode="json"
        )

    async def _work(self, invocation: Invocation) -> dict[str, JsonValue]:
        state, principal, call = self._admit(invocation, "work")
        if call.op == "plan":
            assert call.input is not None
            async with asyncio.timeout(self.adapter_seconds):
                result = await self.plan(call.input)
            return result.model_dump(mode="json")
        if call.op == "cancel":
            assert call.operation_id is not None
            key = state.key(principal, call.operation_id)
            if await state.runner.status(key) is None:
                raise ValueError("unknown operation")
            return self._status(await state.runner.cancel(key), call.operation_id)
        assert call.op == "start" and call.input is not None
        operation_id = call.operation_id or _digest(call.input)[:32]
        key = await finish_owned(
            asyncio.create_task(
                asyncio.to_thread(state.origin, principal, operation_id)
            )
        )
        if self._current() is not state:
            raise ValueError("native job service changed")
        raw = await state.runner.reserve(
            key, _kernel().portable_canonical_json(_json(call.input)).decode()
        )
        return self._status(raw, operation_id)

    async def _pump(self, state: _State) -> None:
        try:
            while (raw := await state.runner.next_request()) is not None:
                request = _OBJECT.validate_json(raw)
                identifier = _INTEGER.validate_python(request["id"], strict=True)
                if request["kind"] == "abort":
                    task = state.callbacks.get(identifier)
                    if task is not None:
                        if not task.done() and task.cancelling() == 0:
                            task.cancel()

                        async def drain(owned: asyncio.Task[None]) -> None:
                            await asyncio.gather(owned, return_exceptions=True)

                        await finish_owned(asyncio.create_task(drain(task)))
                    continue
                # The shared worker issues one callback at a time. Never overlap
                # an abandoned adapter effect with a replacement callback.
                state.callbacks = {
                    key: task
                    for key, task in state.callbacks.items()
                    if not task.done()
                }
                if state.callbacks:
                    raise ValueError("native job callbacks overlap")
                state.callbacks[identifier] = asyncio.create_task(
                    self._callback(state, identifier, request)
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            state.failed = True
            await state.runner.stop()

    async def _callback(
        self, state: _State, identifier: int, request: dict[str, JsonValue]
    ) -> None:
        response: dict[str, JsonValue] = {"kind": "failed"}
        try:
            if request["kind"] == "submit":
                reference = await self.adapter.submit(
                    _TEXT.validate_python(request["operation_id"], strict=True),
                    _OBJECT.validate_python(request["input"], strict=True),
                )
                response = {"kind": "accepted", "reference": reference}
            elif request["kind"] == "observe":
                status, progress, stage, result, error = await self.adapter.observe(
                    _TEXT.validate_python(request["reference"], strict=True)
                )
                if status == "succeeded":
                    try:
                        valid = result is not None and self._result_validator.matches(
                            _json(result)
                        )
                    except (TypeError, ValueError):
                        valid = False
                    if not valid:
                        status, result, error = (
                            "failed",
                            None,
                            "job result does not satisfy its native schema",
                        )
                response = {
                    "kind": "observed",
                    "state": status,
                    "progress": progress,
                    "stage": stage,
                    "result": result,
                    "error": error,
                }
            elif request["kind"] == "cancel":
                await self.adapter.cancel(
                    _TEXT.validate_python(request["reference"], strict=True)
                )
                response = {"kind": "cancel_accepted"}
            else:
                raise ValueError("unknown native callback")
        except SubmissionRefused:
            response = {"kind": "refused", "reason": "backend refused submission"}
        except asyncio.CancelledError:
            raise
        except Exception:
            response = {"kind": "failed"}
        try:
            # Late responses cannot restore native callback authority. Backend
            # diagnostics never appear in a generic failure response.
            state.runner.respond(
                identifier,
                _kernel().portable_canonical_json(_json(response)).decode(),
            )
        except (TypeError, ValueError):
            state.runner.respond(identifier, '{"kind":"failed"}')
