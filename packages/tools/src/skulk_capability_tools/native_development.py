"""Local native author sessions over the public compiled control/media ABI.

The session owns source processes and synthetic local calls. It is not a Fabric
issuer, managed installer, provider account service or production owner.
"""

import asyncio
import base64
import hashlib
import json
import os
import secrets
import shutil
import signal
import sys
import tempfile
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue, TypeAdapter

from skulk_capability_sdk.contracts import Contract, Denied
from skulk_capability_sdk.runtime import finish_owned
from skulk_capability_sdk.storage import HostLock, read_private, secure_directory

from ._native_build_worker import BuiltSource
from ._node_source_worker import Selection
from .inspection import read_public
from .native_author import (
    OBJECT,
    OBJECTS,
    TEXT,
    Kernel,
    Mode,
    Observer,
    Record,
    document,
    kernel,
)

MAX_LOCAL_BYTES = 4 * 1024 * 1024
MAX_MEDIA_BYTES = 1024 * 1024
MAX_GENERATION_SECONDS = 30.0
_MODE: TypeAdapter[Mode] = TypeAdapter(Mode)


class Chunk(Contract):
    """One bounded source-test input; binary media is independent of payload JSON."""

    payload: dict[str, JsonValue]
    media_base64: str = Field(default="", max_length=1398104)
    media_type: str = Field(default="application/octet-stream", max_length=256)

    def media(self) -> bytes:
        """Refuse malformed/oversized attachments before any source work begins."""
        value = base64.b64decode(self.media_base64, validate=True)
        if len(value) > MAX_MEDIA_BYTES:
            raise Denied("Local input media exceeds its frame bound")
        return value


class Request(Contract):
    """Private author IPC request, never a caller-supplied execution context."""

    kind: Literal["invoke", "debug"]
    capability: str | None = Field(default=None, max_length=193)
    payload: str = Field(default='{"text":"Hello"}', max_length=1048576)
    chunks: tuple[Chunk, ...] = Field(default=(), max_length=64)
    seconds: float | None = Field(default=None, gt=0, le=3600)


async def _header(reader: asyncio.StreamReader, maximum: int) -> tuple[bytes, bytes]:
    first = await reader.readexactly(1)
    async with asyncio.timeout(5):
        prefix = first + await reader.readexactly(3)
        size = int.from_bytes(prefix, "big")
        if not 2 <= size <= maximum:
            raise Denied("Native local frame exceeds its bound")
        return prefix, await reader.readexactly(size)


async def _local_read(reader: asyncio.StreamReader) -> bytes:
    return (await _header(reader, MAX_LOCAL_BYTES))[1]


async def _local_write(writer: asyncio.StreamWriter, value: object) -> None:
    raw = document(value)
    if len(raw) > MAX_LOCAL_BYTES:
        raise Denied("Local reply exceeds its frame bound")
    writer.write(len(raw).to_bytes(4, "big") + raw)
    async with asyncio.timeout(5):
        await writer.drain()


async def _terminate(process: asyncio.subprocess.Process) -> None:
    for kind, seconds in ((signal.SIGTERM, 2), (signal.SIGKILL, 2)):
        try:
            os.killpg(process.pid, kind)
        except ProcessLookupError:
            break
        try:
            async with asyncio.timeout(seconds):
                await process.wait()
            break
        except TimeoutError:
            continue
    await process.wait()
    # Reap owned descendants even if a source parent exited before them. This
    # is a private source process group, never a discovered production process.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def _spawn(
    *arguments: str, descriptors: tuple[int, ...] = (), cwd: Path
) -> asyncio.subprocess.Process:
    acquisition = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *arguments,
            pass_fds=descriptors,
            start_new_session=True,
            cwd=cwd,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
        )
    )
    try:
        return await asyncio.shield(acquisition)
    except asyncio.CancelledError:

        async def retire() -> None:
            process = await acquisition
            await _terminate(process)

        await finish_owned(asyncio.create_task(retire()))
        raise


async def _build(project: Path, output: Path | None, cwd: Path) -> BuiltSource | str:
    arguments = [
        sys.executable,
        "-I",
        "-B",
        "-m",
        "skulk_capability_tools._native_build_worker",
        "--project",
        str(project),
    ]
    # Fingerprinting also runs off the event loop in its own bounded process.
    # A source validator must never run inside the author's control reader.
    if output is None:
        result = cwd / ("fingerprint-" + secrets.token_hex(8) + ".json")
        arguments += ["--fingerprint-output", str(result)]
    else:
        result = output / "author-build.json"
        arguments += ["--output", str(output)]
    process = await _spawn(*arguments, cwd=cwd)
    try:
        async with asyncio.timeout(MAX_GENERATION_SECONDS):
            code = await process.wait()
        if code != 0:
            raise Denied("Native source build refused; see the author's stderr")
        raw = read_private(result, 3 * 1024 * 1024)
        if output is None:
            return TEXT.validate_python(OBJECT.validate_json(raw)["fingerprint"])
        return BuiltSource.model_validate_json(raw)
    finally:
        await finish_owned(asyncio.create_task(_terminate(process)))
        if output is None:
            result.unlink(missing_ok=True)


@dataclass(slots=True)
class _Call:
    result: asyncio.Future[tuple[dict[str, JsonValue], bytes]]
    stream: asyncio.Future[tuple[asyncio.StreamReader, asyncio.StreamWriter]]


class NativeSourceChild:
    """Own one local generation, native channels and original process group."""

    def __init__(
        self,
        project: Path,
        directory: Path,
        generation: int,
        failure_signal: asyncio.Event | None = None,
        configuration: Path | None = None,
        credential_selection: Path | None = None,
    ) -> None:
        self.project, self.directory, self.generation = project, directory, generation
        self.native: Kernel = kernel()
        self.built: BuiltSource | None = None
        self.startup: Record | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.reader_task: asyncio.Task[None] | None = None
        self.servers: list[asyncio.Server] = []
        self.connections: list[asyncio.StreamWriter] = []
        self.pending: dict[str, _Call] = {}
        self.health_requests: dict[str, asyncio.Future[dict[str, JsonValue]]] = {}
        self.failed = asyncio.Event()
        self.failure_signal = failure_signal
        self.configuration = configuration
        self.credential_selection = credential_selection
        self.settings_digest = ""
        self.closing = False
        self.write_lock = asyncio.Lock()
        self.owned_directory: tuple[int, int] | None = None

    def _fail(self) -> None:
        self.failed.set()
        if self.failure_signal is not None:
            self.failure_signal.set()

    async def start(self) -> None:
        """Build outside the reader, launch once, and authenticate native hello."""
        secure_directory(self.directory.parent)
        self.directory.mkdir(mode=0o700)
        identity = self.directory.stat()
        self.owned_directory = identity.st_dev, identity.st_ino
        settings = (
            read_public(self.configuration, 65536)
            if self.configuration is not None
            else None
        )
        self.settings_digest = hashlib.sha256(settings or b"").hexdigest()
        if self.credential_selection is not None:
            # Validate selection custody without retrieving any credential.
            read_private(self.credential_selection, 65536)
        built = await _build(self.project, self.directory / "build", self.directory)
        if isinstance(built, str):
            raise Denied("Source generation metadata is unavailable")
        self.built = built
        connected: asyncio.Future[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = (
            asyncio.get_running_loop().create_future()
        )

        def control(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            if connected.done():
                writer.close()
            else:
                self.connections.append(writer)
                connected.set_result((reader, writer))

        async def media(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            self.connections.append(writer)
            try:
                async with asyncio.timeout(5):
                    _, hello = await _header(reader, 262144)
                call_id = TEXT.validate_python(OBJECT.validate_json(hello)["call_id"])
                pending = self.pending.get(call_id)
                if self.startup is None or pending is None or pending.stream.done():
                    raise Denied("Unsolicited local native media channel")
                self.startup.admit_hello(hello.decode(), call_id)
                pending.stream.set_result((reader, writer))
            except (
                Denied,
                ValueError,
                OSError,
                TimeoutError,
                asyncio.IncompleteReadError,
            ):
                writer.close()
                if writer in self.connections:
                    self.connections.remove(writer)
                if not self.closing:
                    self._fail()

        control_path, media_path = self.directory / "control", self.directory / "media"
        for path, callback in ((control_path, control), (media_path, media)):
            self.servers.append(await asyncio.start_unix_server(callback, str(path)))
            path.chmod(0o600)
        manifest = OBJECT.validate_json(built.manifest.canonical_json)
        startup: dict[str, JsonValue] = {
            "protocol": 6,
            "contract_profile": "skulk.managed/2",
            "profile_digest": self.native.managed_v2_profile_digest(),
            "node_id": "local-author",
            "host_id": "local-author",
            "transport_id": "local-author",
            "manifest": manifest,
            "manifest_digest": built.manifest.digest,
            "executable": str(self.directory / "build/bundle.pyz"),
            "socket": str(control_path),
            "stream_socket": str(media_path),
            "token": secrets.token_hex(32),
            "serve_host": None,
            "configuration": OBJECT.validate_json(settings)
            if settings is not None
            else {}
            if built.manifest.configuration_schema is not None
            else None,
            "credential_config": str(self.credential_selection)
            if self.credential_selection is not None
            else None,
        }
        self.startup = self.native.ManagedV2Contract.from_json(
            "Startup", document(startup).decode()
        )
        with tempfile.TemporaryFile() as inherited, tempfile.TemporaryFile() as source:
            inherited.write(self.startup.canonical())
            inherited.seek(0)
            descriptors = (inherited.fileno(),)
            if built.application is None:
                command = [
                    sys.executable,
                    "-I",
                    "-B",
                    str(self.directory / "build/bundle.pyz"),
                ]
            else:
                source.write(
                    Selection(
                        application=built.application,
                        directory=str(self.directory / "build/application"),
                    )
                    .model_dump_json()
                    .encode()
                )
                source.seek(0)
                command = [
                    sys.executable,
                    "-I",
                    "-B",
                    "-m",
                    "skulk_capability_tools._node_source_worker",
                    "--source-fd",
                    str(source.fileno()),
                ]
                descriptors += (source.fileno(),)
            self.process = await _spawn(
                *command,
                "--config-fd",
                str(inherited.fileno()),
                descriptors=descriptors,
                cwd=self.directory.parent,
            )

        async def hello() -> asyncio.StreamReader:
            reader, self.writer = await connected
            _, raw = await _header(reader, 262144)
            if self.startup is None:
                raise Denied("Source startup is unavailable")
            self.startup.admit_hello(raw.decode())
            return reader

        handshake = asyncio.create_task(hello())
        exited = asyncio.create_task(self.process.wait())
        try:
            async with asyncio.timeout(30):
                done, _ = await asyncio.wait(
                    (handshake, exited), return_when=asyncio.FIRST_COMPLETED
                )
                if exited in done and not handshake.done():
                    raise Denied("Native source exited before its authenticated hello")
                reader = await handshake
        finally:
            for task in (handshake, exited):
                if not task.done():
                    task.cancel()
            await asyncio.gather(handshake, exited, return_exceptions=True)
        self.reader_task = asyncio.create_task(self._read(reader))
        await self.health()

    async def _read(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                prefix, raw = await _header(reader, 262144)
                size = self.native.call_v2_payload_size(raw.decode())
                async with asyncio.timeout(5):
                    body = await reader.readexactly(size)
                admitted, body = self.native.decode_call_v2_frame(prefix + raw + body)
                frame = OBJECT.validate_json(admitted)
                if frame["kind"] == "health":
                    pending = self.health_requests.pop(
                        TEXT.validate_python(frame["nonce"]), None
                    )
                    if pending is None or pending.done():
                        raise Denied("Uncorrelated local health reply")
                    pending.set_result(frame)
                elif frame["kind"] == "result":
                    call = self.pending.get(TEXT.validate_python(frame["call_id"]))
                    if call is None or call.result.done():
                        raise Denied("Uncorrelated native result")
                    call.result.set_result((frame, body))
                else:
                    raise Denied(
                        "Local source sessions do not grant delegated Fabric execution"
                    )
        except (Denied, ValueError, OSError, TimeoutError, asyncio.IncompleteReadError):
            if not self.closing:
                self._fail()
        finally:
            for future in [
                *(one.result for one in self.pending.values()),
                *(one.stream for one in self.pending.values()),
                *self.health_requests.values(),
            ]:
                if not future.done():
                    future.cancel()

    async def send(self, kind: str, body: bytes = b"", **fields: JsonValue) -> None:
        """Serialize an actual native frame; source clients never supply its context."""
        await self._send_packet(self._packet(kind, body, **fields))

    def _packet(self, kind: str, body: bytes = b"", **fields: JsonValue) -> bytes:
        return self.native.encode_call_v2_frame(
            document(
                {
                    "protocol": 2,
                    "contract_profile": "skulk.calls/2",
                    "profile_digest": self.native.calls_v2_profile_digest(),
                    "kind": kind,
                    "payload_size": len(body),
                    **fields,
                }
            ).decode(),
            body,
        )

    async def _send_packet(self, packet: bytes) -> None:
        if self.writer is None or self.failed.is_set():
            raise Denied("Native source channel is unavailable")
        async with self.write_lock:
            self.writer.write(packet)
            async with asyncio.timeout(5):
                await self.writer.drain()

    async def health(self) -> dict[str, JsonValue]:
        """Challenge cached readiness independently of an active local invocation."""
        nonce = secrets.token_hex(16)
        future: asyncio.Future[dict[str, JsonValue]] = (
            asyncio.get_running_loop().create_future()
        )
        self.health_requests[nonce] = future
        try:
            await self.send("health", nonce=nonce, ready=False, surfaces=[])
            async with asyncio.timeout(2):
                value = await asyncio.shield(future)
            if value["ready"] is not True:
                raise Denied("Native source application is not ready")
            return value
        finally:
            self.health_requests.pop(nonce, None)

    def descriptor(self, capability: str | None) -> dict[str, JsonValue]:
        """Select exactly one declared version; never classify or route by payload."""
        if self.built is None:
            raise Denied("Native source has not started")
        manifest = OBJECT.validate_json(self.built.manifest.canonical_json)
        descriptors = OBJECTS.validate_python(
            OBJECT.validate_python(manifest["contracts"])["descriptors"]
        )
        matches = [
            one
            for one in descriptors
            if (
                one["io_mode"] == "unary"
                if capability is None
                else capability
                in {str(one["id"]), str(one["id"]) + "@" + str(one["version"])}
            )
        ]
        if len(matches) != 1:
            raise Denied("Choose exactly one native --capability ID@VERSION")
        return matches[0]

    async def invoke(self, request: Request) -> AsyncIterator[dict[str, JsonValue]]:
        """Dispatch a local call and drain cancellation before reuse."""
        descriptor = self.descriptor(request.capability)
        mode = _MODE.validate_python(descriptor["io_mode"])
        body = request.payload.encode()
        if not self.native.PortableSchema(
            document(descriptor["input_schema"]).decode()
        ).matches(request.payload):
            raise Denied("Local payload violates the compiled input schema")
        if request.chunks and mode not in {"client_streaming", "bidirectional"}:
            raise Denied("Selected mode does not accept input chunks")
        for chunk in request.chunks:
            chunk.media()
            if not self.native.PortableSchema(
                document(descriptor["input_chunk_schema"]).decode()
            ).matches(document(chunk.payload).decode()):
                raise Denied("Local chunk violates the compiled input schema")
        call_id, scope = secrets.token_hex(16), secrets.token_hex(32)
        target: dict[str, JsonValue] = {
            "node_id": "local-author",
            "capability_id": descriptor["id"],
            "version": descriptor["version"],
            "behavior": descriptor["behavior"],
            "processing_domain": descriptor["processing_domain"],
            "descriptor_revision": self.native.portable_v2_contract_digest(
                "Descriptor", document(descriptor).decode()
            ),
        }
        if self.built is None:
            raise Denied("Native source has not started")
        bounds = OBJECT.validate_python(
            OBJECT.validate_json(self.built.manifest.canonical_json)["bounds"]
        )
        maximum = TypeAdapter(float).validate_python(
            bounds["call_seconds" if mode == "unary" else "stream_seconds"]
        )
        seconds = min(request.seconds or maximum, maximum)
        # Frame admission also precedes dispatch: an invalid media declaration
        # cannot start backend work and then fail only in the input sender.
        packets = self._input_packets(call_id, mode, request)
        context: dict[str, JsonValue] = {
            "protocol": 3,
            "contract_profile": "skulk.composition/3",
            "profile_digest": self.native.composition_v3_profile_digest(),
            "kind": "context",
            "caller_id": "local-author",
            "caller_kind": "client",
            "issuer_node_id": "local-author",
            "policy_revision": "0" * 64,
            "root_scope_id": scope,
            "scope_id": scope,
            "parent_scope_id": None,
            "executor": target,
            "allowed_targets": [target],
            "allowed_processing_domains": [descriptor["processing_domain"]],
            "ancestry": [],
            "request_sha256": hashlib.sha256(body).hexdigest(),
            "deadline_unix_ms": time.time_ns() // 1000000 + int(seconds * 1000) + 1000,
        }
        loop = asyncio.get_running_loop()
        packet = self._packet(
            "invoke",
            body,
            call_id=call_id,
            mode=mode,
            context=context,
            remaining_seconds=seconds,
        )
        pending = _Call(loop.create_future(), loop.create_future())
        self.pending[call_id] = pending
        completed = False
        try:
            await self._send_packet(packet)
            async with asyncio.timeout(seconds + 1):
                if mode != "unary":
                    async for frame in self._stream(call_id, mode, packets, pending):
                        yield frame
                frame, payload = await asyncio.shield(pending.result)
            completed = True
            yield {
                "kind": "result",
                "scope": "local-development",
                "call_id": call_id,
                "error": frame["error"],
                "payload": OBJECT.validate_json(payload) if payload else None,
            }
        finally:
            if not completed:

                async def drain() -> None:
                    try:
                        await self.send("cancel", call_id=call_id)
                        async with asyncio.timeout(5):
                            await asyncio.shield(pending.result)
                    except (ValueError, OSError, TimeoutError, asyncio.CancelledError):
                        self._fail()
                        if self.process is not None:
                            await _terminate(self.process)

                await finish_owned(asyncio.create_task(drain()))
            self.pending.pop(call_id, None)
            if pending.stream.done() and not pending.stream.cancelled():
                _, stream_writer = pending.stream.result()
                stream_writer.close()
                await stream_writer.wait_closed()
                if stream_writer in self.connections:
                    self.connections.remove(stream_writer)
            for future in (pending.result, pending.stream):
                if not future.done():
                    future.cancel()

    def _input_packets(self, call_id: str, mode: Mode, request: Request) -> list[bytes]:
        if mode not in {"client_streaming", "bidirectional"}:
            return []
        packets: list[bytes] = []
        for sequence, item in enumerate((None, *request.chunks, None)):
            kind = (
                "started"
                if sequence == 0
                else "completed"
                if sequence == len(request.chunks) + 1
                else "chunk"
            )
            projection: dict[str, JsonValue] = {
                "call_id": call_id,
                "direction": "caller_to_provider",
                "sequence": sequence,
                "kind": kind,
            }
            media = b""
            if item is not None:
                media = item.media()
                projection["payload"] = item.payload
                if media:
                    projection["media"] = {
                        "kind": "inline",
                        "media_type": item.media_type,
                    }
            packets.append(
                self.native.encode_transport_v2_packet(
                    document(projection).decode(), media
                )
            )
        return packets

    async def _stream(
        self, call_id: str, mode: Mode, packets: list[bytes], pending: _Call
    ) -> AsyncIterator[dict[str, JsonValue]]:
        reader, writer = await asyncio.shield(pending.stream)
        observer: Observer = self.native.TransportV2Observer(call_id, mode)

        async def input_frames() -> None:
            for packet in packets:
                observer.accept_input(packet)
                writer.write(packet)
                async with asyncio.timeout(5):
                    await writer.drain()

        sender = asyncio.create_task(input_frames())
        try:
            while not observer.finished:
                receiving = asyncio.create_task(_header(reader, 262144))
                try:
                    if not sender.done():
                        await asyncio.wait(
                            (sender, receiving), return_when=asyncio.FIRST_COMPLETED
                        )
                    if sender.done():
                        await sender
                    prefix, raw = await receiving
                finally:
                    if not receiving.done():
                        receiving.cancel()
                    await asyncio.gather(receiving, return_exceptions=True)
                size = self.native.transport_v2_media_size(raw)
                async with asyncio.timeout(5):
                    media = await reader.readexactly(size)
                projection, media = observer.accept_output(prefix + raw + media)
                frame = OBJECT.validate_json(projection)
                yield {
                    "kind": "frame",
                    "scope": "local-development",
                    "frame": frame,
                    "media_base64": base64.b64encode(media).decode(),
                }
            await sender
        finally:
            if not sender.done():
                sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
            if writer in self.connections:
                self.connections.remove(writer)

    async def close(self) -> None:
        """Drain normal shutdown and reap the group on bounded failure."""
        self.closing = True
        if self.process is not None:
            try:
                if (
                    self.process.returncode is None
                    and self.writer is not None
                    and not self.failed.is_set()
                ):
                    await self.send("shutdown")
                    async with asyncio.timeout(5):
                        await self.process.wait()
            except (Denied, ValueError, OSError, TimeoutError):
                pass
            await _terminate(self.process)
        if self.reader_task is not None:
            self.reader_task.cancel()
            await asyncio.gather(self.reader_task, return_exceptions=True)
        for connection in self.connections:
            connection.close()
        for server in self.servers:
            server.close()
            server.close_clients()
            await server.wait_closed()
        if self.owned_directory is not None:
            identity = self.directory.lstat()
            if (identity.st_dev, identity.st_ino) != self.owned_directory:
                raise Denied("Owned source generation was replaced")
            # Only immutable generation artifacts are disposable. The stable
            # session working directory and application state are preserved.
            shutil.rmtree(self.directory)
            self.owned_directory = None


@asynccontextmanager
async def source_child(
    project: Path, state: Path, generation: int
) -> AsyncGenerator[NativeSourceChild]:
    """Keep cleanup ownership through repeated cancellation and startup failure."""
    child = NativeSourceChild(project, state / ("g" + str(generation)), generation)
    try:
        await child.start()
        yield child
    finally:
        await finish_owned(asyncio.create_task(child.close()))


async def serve(
    project: Path,
    state: Path,
    *,
    watch: bool = False,
    configuration: Path | None = None,
    credential_selection: Path | None = None,
) -> None:
    """Serve private source IPC and restart edited generations only between calls."""
    if sys.platform not in {"darwin", "linux"}:
        raise Denied(
            "Native author sessions require a qualified macOS or Linux runtime"
        )
    project, state = project.resolve(strict=True), state.absolute()
    configuration = configuration.absolute() if configuration is not None else None
    credential_selection = (
        credential_selection.absolute() if credential_selection is not None else None
    )
    secure_directory(state)
    lock = HostLock(state)
    path = state / "session.sock"
    gate = asyncio.Lock()
    stopped = asyncio.Event()
    current = NativeSourceChild(
        project,
        state / ("g-" + secrets.token_hex(8)),
        1,
        stopped,
        configuration,
        credential_selection,
    )
    handlers: set[asyncio.Task[None]] = set()
    server: asyncio.Server | None = None

    async def accept(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        nonlocal current
        task = asyncio.current_task()
        if task is not None:
            handlers.add(task)
        if len(handlers) > 8:
            writer.close()
            if task is not None:
                handlers.discard(task)
            return
        try:
            async with asyncio.timeout(5):
                request = Request.model_validate_json(await _local_read(reader))
            if request.kind == "debug":
                health = await current.health()
                if current.built is None:
                    raise Denied("Source metadata is unavailable")
                await _local_write(
                    writer,
                    {
                        "kind": "debug",
                        "scope": "local-development",
                        "ready": health["ready"],
                        "generation": current.generation,
                        "pid": current.process.pid if current.process else None,
                        "language": current.built.language,
                        "manifest_digest": current.built.manifest.digest,
                    },
                )
                return

            async def forward() -> None:
                nonlocal current
                async with gate:
                    if watch:
                        stamp = await _build(project, None, state)
                        settings_digest = hashlib.sha256(
                            read_public(configuration, 65536)
                            if configuration is not None
                            else b""
                        ).hexdigest()
                        if (
                            current.built is None
                            or stamp != current.built.fingerprint
                            or settings_digest != current.settings_digest
                        ):
                            await finish_owned(asyncio.create_task(current.close()))
                            current = NativeSourceChild(
                                project,
                                state / ("g-" + secrets.token_hex(8)),
                                current.generation + 1,
                                stopped,
                                configuration,
                                credential_selection,
                            )
                            try:
                                await current.start()
                            except BaseException:
                                # A partial replacement cannot serve another call.
                                stopped.set()
                                raise

                    async for reply in current.invoke(request):
                        await _local_write(writer, reply)

            # Observe disconnect while queued or rebuilding as well as in the
            # handler: a departed client must never start deferred source work.
            invocation = asyncio.create_task(forward())
            disconnected = asyncio.create_task(reader.read(1))
            try:
                done, _ = await asyncio.wait(
                    (invocation, disconnected), return_when=asyncio.FIRST_COMPLETED
                )
                if disconnected in done and not invocation.done():
                    invocation.cancel()
                await invocation
            finally:
                if not invocation.done():
                    invocation.cancel()
                disconnected.cancel()

                async def settle() -> None:
                    await asyncio.gather(
                        invocation, disconnected, return_exceptions=True
                    )

                await finish_owned(asyncio.create_task(settle()))
        except (
            Denied,
            ValueError,
            OSError,
            TimeoutError,
            asyncio.IncompleteReadError,
        ) as error:
            try:
                await _local_write(
                    writer,
                    {
                        "kind": "error",
                        "scope": "local-development",
                        "error": str(error)[:1024],
                    },
                )
            except (ValueError, OSError, TimeoutError):
                pass
            if current.failed.is_set():
                stopped.set()
        finally:
            writer.close()
            await writer.wait_closed()
            if task is not None:
                handlers.discard(task)

    try:
        await current.start()
        path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(accept, str(path))
        path.chmod(0o600)
        print(
            json.dumps({"ready": True, "scope": "local-development", "native": True}),
            flush=True,
        )
        await stopped.wait()
    finally:
        if server is not None:
            server.close()
            server.close_clients()
            await server.wait_closed()
        for handler in handlers.copy():
            handler.cancel()
        await asyncio.gather(*handlers, return_exceptions=True)
        await finish_owned(asyncio.create_task(current.close()))
        path.unlink(missing_ok=True)
        lock.close()


async def call(state: Path, request: Request) -> AsyncIterator[dict[str, JsonValue]]:
    """Stream one local request without reconnection, retry or production identity."""
    reader, writer = await asyncio.open_unix_connection(
        str(state.absolute() / "session.sock")
    )
    try:
        await _local_write(writer, request.model_dump(mode="json"))
        # Replacement startup has a separate budget from admitted handler work.
        async with asyncio.timeout((request.seconds or 300) + 70):
            while True:
                reply = OBJECT.validate_json(await _local_read(reader))
                yield reply
                if reply["kind"] in {"result", "debug", "error"}:
                    return
    finally:
        writer.close()
        await writer.wait_closed()
