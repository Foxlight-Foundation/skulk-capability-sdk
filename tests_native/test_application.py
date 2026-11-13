"""Public app handlers exercised against the actual released native wire ABI.

The peer is independent account-free test infrastructure, not a replacement
kernel or an assertion of production host authority. Missing native packages
are an explicit collection failure in this separately invoked suite.
"""

import asyncio
import hashlib
import json
import os
import tempfile
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import _skulk_capability_kernel as native  # pyright: ignore[reportMissingModuleSource]
import pytest
from pydantic import JsonValue, TypeAdapter

from skulk_capability_sdk.application import (
    ApplicationStartup,
    CapabilityApp,
    InputFrame,
    Invocation,
    OutputFrame,
)

_OBJECT: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])
_OBJECTS: TypeAdapter[list[dict[str, JsonValue]]] = TypeAdapter(
    list[dict[str, JsonValue]]
)
_ROOT = Path(__file__).resolve().parents[1]
type Mode = Literal["unary", "server_streaming", "client_streaming", "bidirectional"]


def document(value: object) -> bytes:
    """Encode synthetic JSON while preserving numeric and Unicode kinds."""
    return json.dumps(value, allow_nan=False).encode()


def startup_value(socket: str, stream_socket: str, mode: Mode) -> dict[str, JsonValue]:
    """Adapt a frozen native startup through its actual published validators."""
    cases = _OBJECTS.validate_python(
        _OBJECT.validate_json(
            (_ROOT / "kernel/contracts/managed-v2-cases.json").read_bytes()
        )["cases"]
    )
    value = _OBJECT.validate_json(
        str(
            next(
                c["canonical"]
                for c in cases
                if c["name"] == "Startup" and c.get("canonical")
            )
        )
    )
    value.update(socket=socket, stream_socket=stream_socket, token="a" * 64)
    manifest = _OBJECT.validate_python(value["manifest"])
    contracts = _OBJECT.validate_python(manifest["contracts"])
    descriptor = _OBJECTS.validate_python(contracts["descriptors"])[0]
    descriptor.update(behavior="inference", processing_domain="external", io_mode=mode)
    if mode in {"client_streaming", "bidirectional"}:
        descriptor["input_chunk_schema"] = {"type": "object", "required": ["input"]}
    if mode in {"server_streaming", "bidirectional"}:
        descriptor["output_chunk_schema"] = {"type": "object", "required": ["output"]}
    if mode != "unary":
        descriptor["output_schema"] = {"type": "object", "required": ["output"]}
    contracts["descriptors"] = [descriptor]
    manifest.update(effect="inference", contracts=contracts)
    value["manifest"] = manifest
    value["manifest_digest"] = native.ManagedV2Contract.from_json(
        "Manifest", document(manifest).decode()
    ).digest
    return value


async def header(reader: asyncio.StreamReader) -> bytes:
    """Read an independent bounded length-prefixed native header."""
    size = int.from_bytes(await reader.readexactly(4), "big")
    assert 2 <= size <= 262144
    return await reader.readexactly(size)


@dataclass
class Peer:
    """Own one synthetic host connection and the actual public app task."""

    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    app_task: asyncio.Task[None]
    startup: dict[str, JsonValue]
    streams: asyncio.Queue[tuple[asyncio.StreamReader, asyncio.StreamWriter]]

    async def send(self, kind: str, payload: bytes = b"", **fields: JsonValue) -> None:
        """Write only frames accepted by the real compiled calls encoder."""
        self.writer.write(
            native.encode_call_v2_frame(
                document(
                    {
                        "protocol": 2,
                        "contract_profile": "skulk.calls/2",
                        "profile_digest": native.calls_v2_profile_digest(),
                        "kind": kind,
                        "payload_size": len(payload),
                        **fields,
                    }
                ).decode(),
                payload,
            )
        )
        await self.writer.drain()

    async def receive(self) -> tuple[dict[str, JsonValue], bytes]:
        """Read and independently re-admit the complete actual response frame."""
        raw = await header(self.reader)
        value = _OBJECT.validate_json(raw)
        body = await self.reader.readexactly(native.call_v2_payload_size(raw.decode()))
        admitted, payload = native.decode_call_v2_frame(
            len(raw).to_bytes(4, "big") + raw + body
        )
        assert _OBJECT.validate_json(admitted) == value
        return value, payload

    async def invoke(
        self,
        call_id: str = "call-1",
        *,
        payload: bytes = b'{ "input" : "hello" }',
        seconds: float | None = None,
        wrong_revision: bool = False,
    ) -> None:
        """Bind an original caller to exact target and raw request bytes."""
        manifest = _OBJECT.validate_python(self.startup["manifest"])
        contracts = _OBJECT.validate_python(manifest["contracts"])
        descriptor = _OBJECTS.validate_python(contracts["descriptors"])[0]
        target: dict[str, JsonValue] = {
            "node_id": self.startup["node_id"],
            "capability_id": descriptor["id"],
            "version": descriptor["version"],
            "behavior": descriptor["behavior"],
            "processing_domain": descriptor["processing_domain"],
            "descriptor_revision": "b" * 64
            if wrong_revision
            else native.portable_v2_contract_digest(
                "Descriptor", document(descriptor).decode()
            ),
        }
        context: dict[str, JsonValue] = {
            "protocol": 3,
            "contract_profile": "skulk.composition/3",
            "profile_digest": native.composition_v3_profile_digest(),
            "kind": "context",
            "caller_id": "client:synthetic-private",
            "caller_kind": "client",
            "issuer_node_id": "core-profile:synthetic",
            "policy_revision": "a" * 64,
            "root_scope_id": "1" * 64,
            "scope_id": "1" * 64,
            "parent_scope_id": None,
            "executor": target,
            "allowed_targets": [target],
            "allowed_processing_domains": ["external"],
            "ancestry": [],
            "request_sha256": hashlib.sha256(payload).hexdigest(),
            "deadline_unix_ms": None
            if seconds is None
            else time.time_ns() // 1_000_000 + int(seconds * 1000) + 1000,
        }
        await self.send(
            "invoke",
            payload,
            call_id=call_id,
            mode=descriptor["io_mode"],
            context=context,
            remaining_seconds=seconds,
        )

    async def health(self) -> None:
        """Prove the sole reader handles health independently of model silence."""
        await self.send("health", nonce="health-1", ready=False, surfaces=[])
        value, body = await asyncio.wait_for(self.receive(), 2)
        assert (
            value["kind"] == "health"
            and value["nonce"] == "health-1"
            and value["ready"] is True
            and body == b""
        )


@asynccontextmanager
async def running(app: CapabilityApp, mode: Mode = "unary") -> AsyncGenerator[Peer]:
    """Use real compiled startup/control/stream channels and drain all sockets."""
    writers: list[asyncio.StreamWriter] = []
    connected: asyncio.Queue[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = (
        asyncio.Queue()
    )
    streams: asyncio.Queue[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = (
        asyncio.Queue()
    )
    with tempfile.TemporaryDirectory(prefix="native-app-", dir="/tmp") as directory:
        control_path, stream_path = (
            str(Path(directory) / "control"),
            str(Path(directory) / "stream"),
        )
        value = startup_value(control_path, stream_path, mode)
        record = native.ManagedV2Contract.from_json("Startup", document(value).decode())

        def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writers.append(writer)
            connected.put_nowait((reader, writer))

        def accept_stream(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            writers.append(writer)
            streams.put_nowait((reader, writer))

        server = await asyncio.start_unix_server(accept, control_path)
        stream_server = await asyncio.start_unix_server(accept_stream, stream_path)
        with tempfile.TemporaryFile() as startup:
            startup.write(record.canonical())
            startup.seek(0)
            task = asyncio.create_task(app.serve(os.dup(startup.fileno())))
        try:
            reader, writer = await asyncio.wait_for(connected.get(), 3)
            record.admit_hello((await header(reader)).decode())
            yield Peer(reader, writer, task, value, streams)
            if not task.done():
                peer = Peer(reader, writer, task, value, streams)
                await peer.send("shutdown")
            if not task.cancelled():
                await asyncio.wait_for(asyncio.shield(task), 3)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            for writer in writers:
                writer.close()
                await writer.wait_closed()
            server.close()
            stream_server.close()
            await server.wait_closed()
            await stream_server.wait_closed()


async def test_exact_payload_original_caller_and_setup_projection() -> None:
    """The public handler receives the original caller and exact UTF-8 body."""
    observed: list[ApplicationStartup] = []
    closed = asyncio.Event()

    @asynccontextmanager
    async def setup(value: ApplicationStartup) -> AsyncGenerator[None]:
        observed.append(value)
        try:
            yield
        finally:
            closed.set()

    async def handler(call: Invocation) -> dict[str, JsonValue]:
        assert call.raw_payload == b'{ "input" : "hello" }'
        assert call.context.export()["caller_id"] == "client:synthetic-private"
        modified = call.context.export()
        modified["caller_id"] = "changed"
        assert call.context.export()["caller_id"] == "client:synthetic-private"
        assert "synthetic-private" not in repr(call)
        return {"ok": True}

    app = CapabilityApp(setup=setup)
    app.unary("example", handler)
    async with running(app) as peer:
        assert observed[0].node_id == "node" and observed[0].host_id == "host"
        assert observed[0].configuration is None and observed[0].serve_host is None
        assert "aaaa" not in repr(observed[0])
        for call_id in ["call-1", "call-2"]:
            await peer.invoke(call_id)
            value, body = await peer.receive()
            assert value["error"] is None and json.loads(body) == {"ok": True}
            await peer.health()
    assert closed.is_set()


async def test_wrong_binding_is_refused_before_handler() -> None:
    """Valid native metadata with a different signed target cannot run behavior."""
    entered = False

    async def handler(_call: Invocation) -> dict[str, JsonValue]:
        nonlocal entered
        entered = True
        return {}

    app = CapabilityApp()
    app.unary("example", handler)
    async with running(app) as peer:
        await peer.invoke(wrong_revision=True)
        value, body = await peer.receive()
        assert value["error"] == "invalid_payload" and body == b"" and not entered


@pytest.mark.parametrize("seconds", [None, 0.1])
async def test_repeated_cancel_waits_for_cleanup_and_allows_next_request(
    seconds: float | None,
) -> None:
    """Cancellation cannot interrupt cleanup, block health, or poison the next call."""
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def handler(call: Invocation) -> dict[str, JsonValue]:
        if call.call_id == "call-2":
            return {"next": True}
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup.set()
            await release.wait()
        return {}

    app = CapabilityApp()
    app.unary("example", handler)
    async with running(app) as peer:
        await peer.invoke(seconds=seconds)
        await entered.wait()
        await peer.send("cancel", call_id="call-1")
        await cleanup.wait()
        await peer.send("cancel", call_id="call-1")
        if seconds is not None:
            # The original generation timer must be disarmed before cleanup;
            # its expiry cannot cancel teardown again or publish a result.
            await asyncio.sleep(0.15)
        await peer.health()
        release.set()
        value, body = await peer.receive()
        assert value["error"] == "cancelled" and body == b""
        await peer.invoke("call-2")
        value, body = await peer.receive()
        assert value["error"] is None and json.loads(body) == {"next": True}


async def test_repeated_serve_cancellation_drains_application_setup() -> None:
    """Repeated cancellation retains ownership through application cleanup."""
    cleaning, release = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def setup(_value: ApplicationStartup) -> AsyncGenerator[None]:
        try:
            yield
        finally:
            cleaning.set()
            await release.wait()

    async def handler(_call: Invocation) -> dict[str, JsonValue]:
        return {}

    app = CapabilityApp(setup=setup)
    app.unary("example", handler)
    async with running(app) as peer:
        peer.app_task.cancel()
        await cleaning.wait()
        peer.app_task.cancel()
        await asyncio.sleep(0)
        assert not peer.app_task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await peer.app_task


@pytest.mark.parametrize(
    "mode", ["server_streaming", "client_streaming", "bidirectional"]
)
async def test_stream_media_halfclose_and_terminal_after_cleanup(mode: Mode) -> None:
    """The public iterator keeps separate media and delays terminal/end until closed."""
    cleaning, release = asyncio.Event(), asyncio.Event()

    async def handler(
        _call: Invocation, inputs: AsyncIterator[InputFrame]
    ) -> AsyncGenerator[OutputFrame]:
        try:
            if mode != "server_streaming":
                observed = [frame async for frame in inputs]
                assert [f.projection["kind"] for f in observed] == [
                    "started",
                    "chunk",
                    "completed",
                ]
                assert observed[1].media == b"\x00\xff\x01"
            if mode != "client_streaming":
                yield OutputFrame("chunk", {"output": "hello"})
            yield OutputFrame("completed", {"output": "hello"})
        finally:
            cleaning.set()
            await release.wait()

    app = CapabilityApp()
    app.stream("example", handler)
    async with running(app, mode) as peer:
        await peer.invoke()
        reader, writer = await asyncio.wait_for(peer.streams.get(), 3)
        hello = _OBJECT.validate_json(await header(reader))
        assert (
            hello["protocol"] == 6
            and hello["call_id"] == "call-1"
            and hello["token"] == "a" * 64
        )
        if mode != "server_streaming":
            for sequence, kind in enumerate(["started", "chunk", "completed"]):
                frame: dict[str, JsonValue] = {
                    "call_id": "call-1",
                    "direction": "caller_to_provider",
                    "sequence": sequence,
                    "kind": kind,
                }
                media = b""
                if kind == "chunk":
                    media = b"\x00\xff\x01"
                    frame.update(
                        payload={"input": "audio"},
                        media={
                            "kind": "inline",
                            "media_type": "audio/pcm",
                        },
                    )
                writer.write(
                    native.encode_transport_v2_packet(document(frame).decode(), media)
                )
            await writer.drain()
        if mode != "client_streaming":
            chunk = _OBJECT.validate_json(await header(reader))
            assert _OBJECT.validate_python(chunk["frame"])["kind"] == "chunk"
        await cleaning.wait()
        await peer.health()
        release.set()
        terminal = _OBJECT.validate_json(await header(reader))
        end = _OBJECT.validate_json(await header(reader))
        assert (
            _OBJECT.validate_python(terminal["frame"])["kind"] == "completed"
            and _OBJECT.validate_python(end["frame"])["kind"] == "end"
        )
        value, body = await peer.receive()
        assert value["error"] is None and json.loads(body) == {}


async def test_timeout_waits_for_handler_cleanup() -> None:
    """An explicit finite generation budget terminates with a sanitized timeout."""
    closed = asyncio.Event()

    async def handler(_call: Invocation) -> dict[str, JsonValue]:
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()
        return {}

    app = CapabilityApp()
    app.unary("example", handler)
    async with running(app) as peer:
        await peer.invoke(seconds=0.03)
        value, body = await peer.receive()
        assert value["error"] == "timeout" and not body and closed.is_set()


async def test_backend_errors_are_sanitized() -> None:
    """Backend text never reaches the control response or retained diagnostics."""

    async def handler(_call: Invocation) -> dict[str, JsonValue]:
        raise RuntimeError("synthetic-do-not-echo-account-or-content")

    app = CapabilityApp()
    app.unary("example", handler)
    async with running(app) as peer:
        await peer.invoke()
        value, body = await peer.receive()
        assert value["error"] == "provider_error" and not body
        assert "synthetic-do-not-echo" not in document(value).decode()


async def test_uncapped_generation_remains_healthy_past_thirty_seconds() -> None:
    """Explicit null survives the former thirty-second generation convention."""
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(_call: Invocation) -> dict[str, JsonValue]:
        entered.set()
        await release.wait()
        return {"ok": True}

    app = CapabilityApp()
    app.unary("example", handler)
    async with running(app) as peer:
        await peer.invoke()
        await entered.wait()
        for _ in range(31):
            # The synthetic owner honors the signed one-second health cadence;
            # model output remains completely silent for the entire interval.
            await asyncio.sleep(1)
            await peer.health()
        release.set()
        value, body = await peer.receive()
        assert value["error"] is None and json.loads(body) == {"ok": True}
