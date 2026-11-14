"""Explicit native application handlers over the published portable execution ABI.

The host authenticates callers and owns execution authority. This module only
dispatches startup-bound calls received on the compiled protected channel. It
does not implement a host, resolve destinations or read provider credentials.
"""

import asyncio
import hashlib
import importlib
import importlib.metadata
import json
import os
import stat
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import (
    AbstractAsyncContextManager,
    AsyncExitStack,
    aclosing,
    asynccontextmanager,
)
from dataclasses import dataclass, field
from typing import Literal, Protocol, cast, final

from pydantic import JsonValue, TypeAdapter

_OBJECT = TypeAdapter(dict[str, JsonValue])
_TEXT = TypeAdapter(str)
_OPTIONAL_TEXT: TypeAdapter[str | None] = TypeAdapter(str | None)
_CONFIGURATION: TypeAdapter[dict[str, JsonValue] | None] = TypeAdapter(
    dict[str, JsonValue] | None
)
_VALUES: TypeAdapter[list[JsonValue]] = TypeAdapter(list[JsonValue])
_OBJECTS = TypeAdapter(list[dict[str, JsonValue]])
_SECONDS: TypeAdapter[float | None] = TypeAdapter(float | None)
_DEADLINE: TypeAdapter[int | None] = TypeAdapter(int | None)
_NUMBER = TypeAdapter(float)
_INTEGER = TypeAdapter(int)
type StreamMode = Literal["server_streaming", "client_streaming", "bidirectional"]


class _Record(Protocol):
    def canonical(self) -> bytes: ...


class _Factory(Protocol):
    def from_json(self, name: str, source: str) -> _Record: ...


class _Schema(Protocol):
    def matches(self, source: str) -> bool: ...


class _SchemaFactory(Protocol):
    def __call__(self, source: str) -> _Schema: ...


class _Control(Protocol):
    def receive(self) -> Awaitable[tuple[bytes, bytes]]: ...
    def send(self, source: str, payload: bytes) -> Awaitable[None]: ...
    def close(self) -> Awaitable[None]: ...


class _Stream(Protocol):
    def next_input(self) -> Awaitable[tuple[str, bytes] | None]: ...
    def send_output(self, projection: str, media: bytes) -> Awaitable[None]: ...
    def finish(self) -> Awaitable[None]: ...
    def close(self) -> Awaitable[None]: ...


class _Native(Protocol):
    @property
    def ManagedV2Contract(self) -> _Factory: ...
    @property
    def CompositionV3Contract(self) -> _Factory: ...
    @property
    def PortableSchema(self) -> _SchemaFactory: ...
    def portable_v2_contract_digest(self, name: str, source: str) -> str: ...
    def calls_v2_profile_digest(self) -> str: ...
    def encode_call_v2_frame(self, source: str, payload: bytes) -> bytes: ...
    def open_call_v2_control(
        self, startup: _Record, seconds: float
    ) -> Awaitable[_Control]: ...
    def open_transport_v2_stream(
        self, startup: _Record, call_id: str, mode: str, seconds: float | None
    ) -> Awaitable[_Stream]: ...


def _native() -> _Native:
    if importlib.metadata.version("skulk-capability-kernel") not in {
        "0.7.0rc3",
        "0.7.0rc4",
    }:
        raise ValueError("native application requires the explicit execution cohort")
    return cast(
        _Native, cast(object, importlib.import_module("_skulk_capability_kernel"))
    )


@dataclass(slots=True)
class _InvocationJob:
    """Keep result publication separate from cancellable generation and cleanup."""

    task: asyncio.Task[None] | None = None
    work: asyncio.Task[None] | None = None
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True
        if (
            self.work is not None
            and not self.work.done()
            and self.work.cancelling() == 0
        ):
            self.work.cancel()


async def _finish_owned[Value](task: asyncio.Task[Value]) -> Value:
    """Keep ownership through repeated caller cancellation until cleanup finishes."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


async def _during[Value](
    work: Callable[[], Awaitable[Value]], seconds: float | None
) -> Value:
    """Disarm the caller's timer before cancelling and draining owned behavior."""

    async def run() -> Value:
        return await work()

    task = asyncio.create_task(run())
    try:
        async with asyncio.timeout(seconds):
            # Timeout/host cancellation first unwinds this waiter's timer. Only
            # then may cleanup cancel the separately owned handler, once.
            return await asyncio.shield(task)
    finally:
        if not task.done() and task.cancelling() == 0:
            task.cancel()

        async def drain() -> None:
            await asyncio.gather(task, return_exceptions=True)

        await _finish_owned(asyncio.create_task(drain()))


@asynccontextmanager
async def _owned_context[Value](
    context: AbstractAsyncContextManager[Value],
) -> AsyncGenerator[Value]:
    value = await context.__aenter__()
    try:
        yield value
    except BaseException as error:

        async def exit_error(exception: BaseException) -> bool | None:
            return await context.__aexit__(
                type(exception), exception, exception.__traceback__
            )

        if not await _finish_owned(asyncio.create_task(exit_error(error))):
            raise
    else:

        async def exit_normal() -> None:
            await context.__aexit__(None, None, None)

        await _finish_owned(asyncio.create_task(exit_normal()))


@final
@dataclass(frozen=True, slots=True, repr=False)
class ApplicationStartup:
    """Read-only application settings projected from native-admitted startup.

    Protected handshake material is not part of this public projection.
    Configuration, serving address and stable app identities are supplied by the
    host; they are not inferred from an account or from incoming requests.
    """

    _canonical: bytes = field(repr=False)

    @property
    def node_id(self) -> str:
        """Return this actual installed application's persistent identity."""
        return _TEXT.validate_python(
            _OBJECT.validate_json(self._canonical)["node_id"], strict=True
        )

    @property
    def host_id(self) -> str:
        """Return its actual owner identity, independently of Core transport."""
        return _TEXT.validate_python(
            _OBJECT.validate_json(self._canonical)["host_id"], strict=True
        )

    @property
    def serve_host(self) -> str | None:
        """Return the optional host-selected address; absence means loopback only."""
        return _OPTIONAL_TEXT.validate_python(
            _OBJECT.validate_json(self._canonical)["serve_host"], strict=True
        )

    @property
    def configuration(self) -> dict[str, JsonValue] | None:
        """Return an owned copy of already admitted application settings."""
        return _CONFIGURATION.validate_python(
            _OBJECT.validate_json(self._canonical)["configuration"], strict=True
        )


type ApplicationSetup = Callable[
    [ApplicationStartup], AbstractAsyncContextManager[None]
]


@asynccontextmanager
async def _no_setup(_startup: ApplicationStartup) -> AsyncGenerator[None]:
    yield


@final
@dataclass(frozen=True, slots=True, repr=False)
class CallContext:
    """An immutable copy of host-admitted metadata, not a bearer or a grant.

    Obtain this only from an invocation. Exporting or parsing the metadata cannot
    activate another call. The host separately rechecks current authority.
    """

    _canonical: bytes = field(repr=False)

    def export(self) -> dict[str, JsonValue]:
        """Return an owned projection; modifying it cannot change the admitted call."""
        return _OBJECT.validate_json(self._canonical)


@final
@dataclass(frozen=True, slots=True, repr=False)
class Invocation:
    """One startup-bound request with exact bytes and original caller context."""

    call_id: str
    capability_id: str
    payload: dict[str, JsonValue] = field(repr=False)
    raw_payload: bytes = field(repr=False)
    context: CallContext = field(repr=False)
    _resources: AsyncExitStack = field(default_factory=AsyncExitStack, repr=False)

    @property
    def version(self) -> str:
        """Return the exact signed capability version admitted for this invocation."""
        target = _OBJECT.validate_python(self.context.export()["executor"], strict=True)
        return _TEXT.validate_python(target["version"], strict=True)

    async def enter_resource[Resource](
        self, context: AbstractAsyncContextManager[Resource]
    ) -> Resource:
        """Enter a handler resource whose SDK-owned exit resists cancellation.

        Register during the active handler, and drain application-owned tasks
        before returning. Registered exits run before result/terminal completion.
        This does not protect arbitrary cleanup hidden inside a backend function.
        """
        return await self._resources.enter_async_context(context)


@final
@dataclass(frozen=True, slots=True, repr=False)
class InputFrame:
    """One native-admitted input projection and separate bounded binary media.

    Blob declarations are metadata only. This SDK never resolves a path, URL or
    blob reference supplied by a caller.
    """

    projection: dict[str, JsonValue] = field(repr=False)
    media: bytes = field(default=b"", repr=False)


@final
@dataclass(frozen=True, slots=True, repr=False)
class OutputFrame:
    """One proposed stream output; the compiled channel admits its full shape.

    Supply exactly one terminal after any chunks. Terminal delivery waits for
    iterator cleanup and the native end acknowledgment. Media metadata is the
    portable inline/blob projection, with inline bytes supplied separately.
    """

    kind: Literal["chunk", "completed", "failed", "cancelled"]
    payload: dict[str, JsonValue] | None = field(default=None, repr=False)
    media_metadata: dict[str, JsonValue] | None = field(default=None, repr=False)
    media: bytes = field(default=b"", repr=False)
    error: str | None = None


type UnaryHandler = Callable[[Invocation], Awaitable[dict[str, JsonValue]]]
type StreamHandler = Callable[
    [Invocation, AsyncIterator[InputFrame]], AsyncGenerator[OutputFrame]
]
type SurfaceReports = Callable[[], list[dict[str, JsonValue]]]
type _HandlerKey = tuple[str, str | None]


def _bound_handlers[Handler](
    registered: dict[_HandlerKey, Handler],
    descriptors: dict[tuple[str, str], dict[str, JsonValue]],
) -> dict[_HandlerKey, Handler]:
    bound: dict[_HandlerKey, Handler] = {}
    for (identifier, version), handler in registered.items():
        matches = [
            key
            for key in descriptors
            if key[0] == identifier and (version is None or key[1] == version)
        ]
        if len(matches) != 1 or matches[0] in bound:
            raise ValueError("application handler version is ambiguous or undeclared")
        bound[matches[0]] = handler
    return bound


@final
class CapabilityApp:
    """Run explicit native unary/stream handlers with one protected control reader.

    Register handlers before serving. Health uses cached synchronous callbacks,
    independent of active inference. An application supplies behavior; the host
    retains admission, policy, process ownership and destination selection.
    """

    def __init__(
        self,
        *,
        setup: ApplicationSetup = _no_setup,
        ready: Callable[[], bool] = lambda: True,
        surfaces: SurfaceReports = lambda: [],
    ) -> None:
        """Configure cheap cached health observations; neither callback performs I/O."""
        self._unary: dict[_HandlerKey, UnaryHandler] = {}
        self._streams: dict[_HandlerKey, StreamHandler] = {}
        self._setup, self._ready, self._surfaces = setup, ready, surfaces
        self._serving = False

    def unary(
        self, capability_id: str, handler: UnaryHandler, *, version: str | None = None
    ) -> None:
        """Register unary behavior; specify versions for multi-version IDs."""
        self._register(capability_id, version)
        self._unary[capability_id, version] = handler

    def stream(
        self, capability_id: str, handler: StreamHandler, *, version: str | None = None
    ) -> None:
        """Register stream behavior with optional exact version selection."""
        self._register(capability_id, version)
        self._streams[capability_id, version] = handler

    def _register(self, capability_id: str, version: str | None) -> None:
        key = capability_id, version
        if self._serving or key in self._unary or key in self._streams:
            raise ValueError("application handler is already bound")

    async def serve(self, configuration_descriptor: int) -> None:
        """Consume the private startup descriptor and own all handlers until shutdown.

        The signed manifest must name exactly the registered handlers. Only the
        explicit native managed/calls/transport cohort is accepted. Cancellation,
        malformed channels and shutdown drain handlers before closing control.
        No uncertain request is replayed or reconnected.
        """
        if self._serving:
            raise ValueError("application can serve only once")
        self._serving = True
        with os.fdopen(configuration_descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or metadata.st_mode & 0o077
            ):
                raise ValueError(
                    "application startup must be a private regular descriptor"
                )
            raw = source.read(262145)
        native = _native()
        startup = native.ManagedV2Contract.from_json("Startup", raw.decode())
        value = _OBJECT.validate_json(startup.canonical())
        manifest = _OBJECT.validate_python(value["manifest"], strict=True)
        contracts = _OBJECT.validate_python(manifest["contracts"], strict=True)
        descriptors = {
            (
                _TEXT.validate_python(d["id"], strict=True),
                _TEXT.validate_python(d["version"], strict=True),
            ): d
            for d in _OBJECTS.validate_python(contracts["descriptors"], strict=True)
        }
        self._unary = _bound_handlers(self._unary, descriptors)
        self._streams = _bound_handlers(self._streams, descriptors)
        if set(descriptors) != self._unary.keys() | self._streams.keys():
            raise ValueError("registered handlers differ from the signed manifest")
        for identifier, descriptor in descriptors.items():
            if (descriptor["io_mode"] == "unary") != (identifier in self._unary):
                raise ValueError(
                    "registered handler mode differs from the signed manifest"
                )
        public_startup = ApplicationStartup(
            json.dumps(
                {
                    name: value[name]
                    for name in ("node_id", "host_id", "serve_host", "configuration")
                },
                allow_nan=False,
            ).encode()
        )

        async def run() -> None:
            async with _owned_context(self._setup(public_startup)):
                await self._serve_control(native, startup, value, descriptors)

        task = asyncio.create_task(run())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.done() and task.cancelling() == 0:
                task.cancel()
            try:
                await _finish_owned(task)
            finally:
                raise asyncio.CancelledError from None

    async def _serve_control(
        self,
        native: _Native,
        startup: _Record,
        value: dict[str, JsonValue],
        descriptors: dict[tuple[str, str], dict[str, JsonValue]],
    ) -> None:
        manifest = _OBJECT.validate_python(value["manifest"], strict=True)
        bounds = _OBJECT.validate_python(manifest["bounds"], strict=True)
        # This renews with host health, independently of model output. Respect
        # the signed cadence rather than imposing a shorter fixed idle budget.
        control_seconds = max(
            _NUMBER.validate_python(bounds["startup_seconds"], strict=True),
            2 * _NUMBER.validate_python(bounds["health_seconds"], strict=True),
        )
        control = await native.open_call_v2_control(startup, control_seconds)
        jobs: dict[str, _InvocationJob] = {}
        capacity = _INTEGER.validate_python(bounds["concurrency"], strict=True)

        def frame(kind: str, **fields: JsonValue) -> str:
            return json.dumps(
                {
                    "protocol": 2,
                    "contract_profile": "skulk.calls/2",
                    "profile_digest": native.calls_v2_profile_digest(),
                    "kind": kind,
                    "payload_size": 0,
                    **fields,
                },
                allow_nan=False,
            )

        async def execute(
            job: _InvocationJob, request: dict[str, JsonValue], body: bytes
        ) -> None:
            call_id = _TEXT.validate_python(request["call_id"], strict=True)
            result: dict[str, JsonValue] = {}
            encoded = b""
            error: str | None = None
            try:
                if job.cancelled:
                    raise asyncio.CancelledError
                context = native.CompositionV3Contract.from_json(
                    "Context", json.dumps(request["context"])
                )
                fields = _OBJECT.validate_json(context.canonical())
                target = _OBJECT.validate_python(fields["executor"], strict=True)
                identifier = _TEXT.validate_python(target["capability_id"], strict=True)
                version = _TEXT.validate_python(target["version"], strict=True)
                descriptor = descriptors[identifier, version]
                mode = _TEXT.validate_python(descriptor["io_mode"], strict=True)
                if (
                    target["node_id"] != value["node_id"]
                    or target["behavior"] != descriptor["behavior"]
                    or target["processing_domain"] != descriptor["processing_domain"]
                    or target["version"] != descriptor["version"]
                    or target["descriptor_revision"]
                    != native.portable_v2_contract_digest(
                        "Descriptor", json.dumps(descriptor)
                    )
                    or request["mode"] != mode
                    or fields["request_sha256"] != hashlib.sha256(body).hexdigest()
                ):
                    raise ValueError("application request binding differs")
                if not native.PortableSchema(
                    json.dumps(descriptor["input_schema"])
                ).matches(body.decode()):
                    raise ValueError("application input schema refused")
                resources = AsyncExitStack()
                invocation = Invocation(
                    call_id,
                    identifier,
                    _OBJECT.validate_json(body),
                    body,
                    CallContext(context.canonical()),
                    resources,
                )
                seconds = _SECONDS.validate_python(
                    request["remaining_seconds"], strict=True
                )
                deadline = _DEADLINE.validate_python(
                    fields["deadline_unix_ms"], strict=True
                )
                if (seconds is None) != (deadline is None):
                    raise ValueError("application lifetime binding differs")
                if deadline is not None:
                    assert seconds is not None
                    seconds = min(
                        seconds, (deadline - time.time_ns() // 1_000_000) / 1000
                    )
                    if seconds <= 0:
                        raise TimeoutError

                async def handle() -> None:
                    nonlocal result
                    if mode == "unary":
                        async with _owned_context(resources):
                            result = _OBJECT.validate_python(
                                await self._unary[identifier, version](invocation),
                                strict=True,
                            )
                            schema = descriptor["output_schema"]
                            if schema is not None and not native.PortableSchema(
                                json.dumps(schema)
                            ).matches(json.dumps(result, allow_nan=False)):
                                raise ValueError("application result schema refused")
                    else:
                        await self._execute_stream(
                            native,
                            startup,
                            invocation,
                            resources,
                            descriptor,
                            mode,
                            seconds,
                        )

                job.work = asyncio.create_task(_during(handle, seconds))
                await asyncio.shield(job.work)
                encoded = json.dumps(result, allow_nan=False).encode()
                native.encode_call_v2_frame(
                    frame(
                        "result", call_id=call_id, error=None, payload_size=len(encoded)
                    ),
                    encoded,
                )
            except asyncio.CancelledError:
                error = "cancelled"
            except TimeoutError:
                error = "timeout"
            except (KeyError, ValueError):
                error = "invalid_payload"
            except Exception:
                # Backend exceptions are meaningful application failures. Their
                # text may contain content or credentials and never becomes IPC.
                error = "provider_error"
            finally:
                if error is not None:
                    encoded = b""
                await control.send(
                    frame(
                        "result",
                        call_id=call_id,
                        error=error,
                        payload_size=len(encoded),
                    ),
                    encoded,
                )

        try:
            while True:
                metadata, body = await control.receive()
                request = _OBJECT.validate_json(metadata)
                kind = request["kind"]
                if kind == "shutdown":
                    return
                if kind == "health":
                    await control.send(
                        frame(
                            "health",
                            nonce=request["nonce"],
                            ready=self._ready(),
                            surfaces=_VALUES.validate_python(
                                self._surfaces(), strict=True
                            ),
                        ),
                        b"",
                    )
                elif kind == "invoke":
                    for identifier, job in list(jobs.items()):
                        if job.task is not None and job.task.done():
                            job.task.result()
                            del jobs[identifier]
                    if len(jobs) >= capacity:
                        raise ValueError("application workload capacity exceeded")
                    call_id = _TEXT.validate_python(request["call_id"], strict=True)
                    if call_id in jobs:
                        raise ValueError("application call identity already active")
                    job = _InvocationJob()
                    jobs[call_id] = job
                    job.task = asyncio.create_task(execute(job, request, body))
                elif kind == "cancel":
                    call_id = _TEXT.validate_python(request["call_id"], strict=True)
                    # Cancel behavior once. A late cancel cannot interrupt a
                    # result frame whose cleanup and publication we already own.
                    # A settled call may already have been collected while its
                    # result crossed the socket. Cancellation is idempotent;
                    # an absent ID creates no work and grants no authority.
                    if call_id in jobs:
                        jobs[call_id].cancel()
                else:
                    raise ValueError("application control direction refused")
        finally:

            async def close() -> None:
                try:
                    for job in jobs.values():
                        job.cancel()
                    await asyncio.gather(
                        *(job.task for job in jobs.values() if job.task is not None),
                        return_exceptions=True,
                    )
                finally:
                    await control.close()

            await _finish_owned(asyncio.create_task(close()))

    async def _execute_stream(
        self,
        native: _Native,
        startup: _Record,
        invocation: Invocation,
        resources: AsyncExitStack,
        descriptor: dict[str, JsonValue],
        mode: str,
        seconds: float | None,
    ) -> None:
        stream = await native.open_transport_v2_stream(
            startup, invocation.call_id, mode, seconds
        )

        async def inputs() -> AsyncGenerator[InputFrame]:
            while (packet := await stream.next_input()) is not None:
                projection, media = packet
                value = _OBJECT.validate_json(projection)
                schema = descriptor["input_chunk_schema"]
                if (
                    value["kind"] == "chunk"
                    and value["payload"] is not None
                    and schema is not None
                    and not native.PortableSchema(json.dumps(schema)).matches(
                        json.dumps(value["payload"])
                    )
                ):
                    raise ValueError("application input chunk schema refused")
                yield InputFrame(value, media)

        try:
            async with _owned_context(resources):
                async with _owned_context(aclosing(inputs())) as ingress:
                    async with _owned_context(
                        aclosing(
                            self._streams[invocation.capability_id, invocation.version](
                                invocation, ingress
                            )
                        )
                    ) as outputs:
                        sequence = 1
                        async for output in outputs:
                            schema = descriptor["output_chunk_schema"]
                            if (
                                output.kind == "chunk"
                                and output.payload is not None
                                and schema is not None
                                and not native.PortableSchema(
                                    json.dumps(schema)
                                ).matches(json.dumps(output.payload))
                            ):
                                raise ValueError(
                                    "application output chunk schema refused"
                                )
                            result_schema = descriptor["output_schema"]
                            if (
                                output.kind == "completed"
                                and result_schema is not None
                                and not native.PortableSchema(
                                    json.dumps(result_schema)
                                ).matches(json.dumps(output.payload, allow_nan=False))
                            ):
                                raise ValueError("application terminal schema refused")
                            projection: dict[str, JsonValue] = {
                                "call_id": invocation.call_id,
                                "direction": "provider_to_caller",
                                "sequence": sequence,
                                "kind": output.kind,
                                "payload": output.payload,
                                "media": output.media_metadata,
                                "error": None
                                if output.error is None
                                # Native terminals require a message. Keep it
                                # empty so backend exception text cannot leak.
                                else {"code": output.error, "message": ""},
                            }
                            await stream.send_output(
                                json.dumps(projection, allow_nan=False), output.media
                            )
                            sequence += 1
                            if output.kind != "chunk":
                                # A terminal transfers the iterator into owned close;
                                # never resume natural teardown as cancellable work.
                                break
            # The compiled stream retains its terminal until the iterator has
            # closed, then sends exactly one end. Control result follows cleanup.
            await stream.finish()
        finally:

            async def close() -> None:
                await stream.close()

            await _finish_owned(asyncio.create_task(close()))
