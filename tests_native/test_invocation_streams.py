"""Invocation clients against real compiled control/media sockets, not host grants.

Synthetic peers exercise author ownership. Production caller authentication,
matching owner admission and signed installed composition are separate gates.
"""

import asyncio
import json
import time
from typing import Literal

import _skulk_capability_kernel as native  # pyright: ignore[reportMissingModuleSource]
import pytest
from pydantic import JsonValue, TypeAdapter
from test_application import Peer, document, header, running, startup_value
from test_delegation import target

from skulk_capability_sdk.application import (
    CallStream,
    CapabilityApp,
    CapabilityCallError,
    CapabilityEndpoint,
    Invocation,
    StreamMode,
)

_OBJECT = TypeAdapter(dict[str, JsonValue])
_OBJECTS = TypeAdapter(list[dict[str, JsonValue]])


def destination(mode: StreamMode = "server_streaming") -> CapabilityEndpoint:
    """Build an independently admitted exact destination, without an authority claim."""
    startup = startup_value("/tmp/unused", "/tmp/unused-media", mode)
    manifest = _OBJECT.validate_python(startup["manifest"])
    contracts = _OBJECT.validate_python(manifest["contracts"])
    descriptor = _OBJECTS.validate_python(contracts["descriptors"])[0]
    return CapabilityEndpoint.from_descriptor("stream-receiver", descriptor)


async def start(
    peer: Peer,
    request: dict[str, JsonValue],
    mode: StreamMode,
    *,
    seconds: float | None = None,
    deadline: int | None = None,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Supply one synthetic start and independently validate its actual hello."""
    await peer.send(
        "delegate_started",
        parent_call_id=request["parent_call_id"],
        request_id=_OBJECT.validate_python(request["request"])["request_id"],
        media_call_id="host-media",
        mode=mode,
        remaining_seconds=seconds,
        deadline_unix_ms=deadline,
    )
    reader, writer = await asyncio.wait_for(peer.streams.get(), 2)
    record = native.ManagedV2Contract.from_json(
        "Startup", document(peer.startup).decode()
    )
    record.admit_hello((await header(reader)).decode(), "host-media")
    return reader, writer


async def input_packet(
    reader: asyncio.StreamReader,
) -> tuple[dict[str, JsonValue], bytes]:
    """Read bounded actual transport bytes through the released compiled codec."""
    raw = await header(reader)
    media = await reader.readexactly(native.transport_v2_media_size(raw))
    projection, media = native.decode_transport_v2_packet(
        len(raw).to_bytes(4, "big") + raw + media
    )
    return _OBJECT.validate_json(projection), media


async def output(
    writer: asyncio.StreamWriter,
    kind: Literal["chunk", "completed", "failed", "cancelled"],
    sequence: int,
    *,
    media: bytes = b"",
) -> None:
    """Send an independently framed provider output and separate optional media."""
    value: dict[str, JsonValue] = {
        "call_id": "host-media",
        "direction": "provider_to_caller",
        "sequence": sequence,
        "kind": kind,
        "payload": {"output": kind},
    }
    if media:
        value["media"] = {"kind": "inline", "media_type": "audio/pcm"}
    if kind in ("failed", "cancelled"):
        value["error"] = {"code": "provider_error", "message": ""}
    writer.write(native.encode_transport_v2_packet(document(value).decode(), media))
    await writer.drain()


async def end(writer: asyncio.StreamWriter) -> None:
    """Send a separate admitted end; a terminal alone is never completion."""
    writer.write(
        native.encode_transport_v2_packet('{"call_id":"host-media","kind":"end"}', b"")
    )
    await writer.drain()


async def finished(
    peer: Peer, request: dict[str, JsonValue], error: str | None = None
) -> None:
    """Finalize only the actual parent's exact admitted child request."""
    await peer.send(
        "delegate_finished",
        parent_call_id=request["parent_call_id"],
        request_id=_OBJECT.validate_python(request["request"])["request_id"],
        error=error,
    )


@pytest.mark.parametrize(
    "mode", ["server_streaming", "client_streaming", "bidirectional"]
)
async def test_public_stream_half_close_binary_and_joint_finalization(
    mode: StreamMode,
) -> None:
    """Terminal exposure waits for end/control while the sole reader answers health."""
    selected = destination(mode)
    terminal_seen = asyncio.Event()
    saved: list[CallStream] = []

    async def caller(call: Invocation) -> dict[str, JsonValue]:
        async with call.stream(selected, b'{ "input": "exact child" }') as stream:
            saved.append(stream)
            assert stream.mode == mode
            with pytest.raises(ValueError, match="pending"):
                await call.call(selected, {"input": "racing child"})
            if mode != "server_streaming":
                with pytest.raises(ValueError, match="input schema"):
                    await stream.send({"wrong": "no input key"})
                await stream.send(
                    {"input": "valid"},
                    media_metadata={"kind": "inline", "media_type": "audio/pcm"},
                    media=b"\x00\xff",
                )
                await stream.complete_input()
            frames: list[str] = []
            async for frame in stream:
                assert "exact child" not in repr(frame)
                frames.append(frame.kind)
                if frame.kind == "chunk":
                    assert frame.media == b"\xff\x00"
                    owned = frame.projection
                    owned["payload"] = {"output": "changed"}
                    assert frame.payload == {"output": "chunk"}
                else:
                    terminal_seen.set()
            assert stream.finished
        with pytest.raises(ValueError, match="retired"):
            await stream.next_output()
        return {"output": ",".join(frames)}

    app = CapabilityApp()
    app.unary("example", caller)
    async with running(app) as peer:
        await peer.invoke(allowed_targets=(target(selected),))
        request, body = await peer.receive()
        assert request["kind"] == "delegate" and body == b'{ "input": "exact child" }'
        reader, writer = await start(peer, request, mode)
        if mode != "server_streaming":
            for sequence, kind in enumerate(("started", "chunk", "completed")):
                frame, media = await input_packet(reader)
                assert frame["kind"] == kind and frame["sequence"] == sequence
                assert frame["call_id"] == "host-media"
                assert media == (b"\x00\xff" if kind == "chunk" else b"")
        sequence = 1
        if mode != "client_streaming":
            await output(writer, "chunk", sequence, media=b"\xff\x00")
            sequence += 1
        await output(writer, "completed", sequence)
        await peer.health()
        assert not terminal_seen.is_set()
        await end(writer)
        await peer.health()
        assert not terminal_seen.is_set()
        await finished(peer, request)
        result, body = await asyncio.wait_for(peer.receive(), 2)
        assert result["kind"] == "result" and result["error"] is None
        assert json.loads(body) == {
            "output": "completed" if mode == "client_streaming" else "chunk,completed"
        }
        assert terminal_seen.is_set() and saved[0].finished


async def test_refusal_before_stream_start_preserves_next_admitted_sequence() -> None:
    """A known pre-start refusal creates no media and preserves its active parent."""
    selected = destination()

    async def caller(call: Invocation) -> dict[str, JsonValue]:
        with pytest.raises(CapabilityCallError) as refused:
            async with call.stream(selected, {"input": "first"}):
                raise AssertionError("refused stream must not open")
        assert refused.value.code == "denied"
        async with call.stream(selected, {"input": "second"}) as stream:
            async for _frame in stream:
                pass
        return {"output": "second completed"}

    app = CapabilityApp()
    app.unary("example", caller)
    async with running(app) as peer:
        await peer.invoke(allowed_targets=(target(selected),))
        first, _ = await peer.receive()
        await peer.send(
            "delegate_result",
            parent_call_id=first["parent_call_id"],
            request_id=_OBJECT.validate_python(first["request"])["request_id"],
            error="denied",
        )
        request, _ = await peer.receive()
        assert _OBJECT.validate_python(request["request"])["request_id"] == f"{2:064x}"
        assert peer.streams.empty()
        _reader, writer = await start(peer, request, "server_streaming")
        await output(writer, "completed", 1)
        await end(writer)
        await finished(peer, request)
        result, _ = await peer.receive()
        assert result["error"] is None


@pytest.mark.parametrize("started", [False, True])
async def test_child_deadline_expires_and_late_control_keeps_other_roots_safe(
    started: bool,
) -> None:
    """A finite child bound retires that parent and retains its late correlation."""
    selected = destination()
    deadline = time.time_ns() // 1_000_000 + 500

    async def caller(call: Invocation) -> dict[str, JsonValue]:
        if call.call_id != "call-1":
            return {"output": "other root"}
        async with call.stream(
            selected, {"input": "request"}, deadline_unix_ms=deadline
        ) as stream:
            await stream.next_output()
        return {"output": "must not succeed"}

    app = CapabilityApp()
    app.unary("example", caller)
    async with running(app, concurrency=2) as peer:
        await peer.invoke(allowed_targets=(target(selected),))
        request, _ = await peer.receive()
        assert (
            _OBJECT.validate_python(request["request"])["deadline_unix_ms"] == deadline
        )
        reader: asyncio.StreamReader | None = None
        if started:
            reader, _writer = await start(
                peer,
                request,
                "server_streaming",
                seconds=0.1,
                deadline=time.time_ns() // 1_000_000 + 100,
            )
        result, body = await asyncio.wait_for(peer.receive(), 2)
        assert result["error"] is not None and not body
        if reader is not None:
            assert await asyncio.wait_for(reader.read(), 2) == b""
        for index in range(3):
            await peer.invoke(f"unrelated-{index}")
            result, _ = await peer.receive()
            assert result["error"] is None
        if started:
            await finished(peer, request, "cancelled")
        else:
            await peer.send(
                "delegate_result",
                parent_call_id=request["parent_call_id"],
                request_id=_OBJECT.validate_python(request["request"])["request_id"],
                error="cancelled",
            )
        await peer.health()


async def test_repeated_cancel_does_not_interrupt_stream_parent_resources() -> None:
    """Stream teardown and handler resources stay owned during repeated cancel."""
    selected = destination()
    cleaning, release = asyncio.Event(), asyncio.Event()

    async def caller(call: Invocation) -> dict[str, JsonValue]:
        try:
            async with call.stream(selected, {"input": "request"}) as stream:
                await stream.next_output()
        finally:
            cleaning.set()
            await release.wait()
        return {"output": "must not succeed"}

    app = CapabilityApp()
    app.unary("example", caller)
    async with running(app) as peer:
        await peer.invoke(allowed_targets=(target(selected),))
        request, _ = await peer.receive()
        reader, _writer = await start(peer, request, "server_streaming")
        await peer.send("cancel", call_id="call-1")
        await asyncio.wait_for(cleaning.wait(), 2)
        assert await asyncio.wait_for(reader.read(), 2) == b""
        await peer.send("cancel", call_id="call-1")
        await peer.health()
        release.set()
        result, body = await peer.receive()
        assert result["error"] == "cancelled" and not body
        await finished(peer, request, "cancelled")
        await peer.health()


async def test_uncapped_stream_stays_healthy_past_thirty_seconds() -> None:
    """No aggregate timer is introduced by a null caller stream lifetime."""
    selected = destination()

    async def caller(call: Invocation) -> dict[str, JsonValue]:
        async with call.stream(selected, {"input": "request"}) as stream:
            async for _frame in stream:
                pass
        return {"output": "complete"}

    app = CapabilityApp()
    app.unary("example", caller)
    async with running(app) as peer:
        await peer.invoke(allowed_targets=(target(selected),))
        request, _ = await peer.receive()
        _reader, writer = await start(peer, request, "server_streaming")
        for _ in range(32):
            await asyncio.sleep(1)
            await peer.health()
        await output(writer, "completed", 1)
        await end(writer)
        await finished(peer, request)
        result, _ = await peer.receive()
        assert result["error"] is None


@pytest.mark.parametrize(
    "fault",
    [
        "wrong-mode",
        "finish-first",
        "early-success",
        "wider-lifetime",
        "duplicate-start",
    ],
)
async def test_control_phase_refusals_cannot_create_success(fault: str) -> None:
    """Malformed negotiation fences the actual compiled application connection."""
    selected = destination()

    async def caller(call: Invocation) -> dict[str, JsonValue]:
        async with call.stream(selected, {"input": "request"}) as stream:
            async for _frame in stream:
                pass
        return {"output": "must not succeed"}

    app = CapabilityApp()
    app.unary("example", caller)
    with pytest.raises(ValueError):
        async with running(app) as peer:
            context = await peer.invoke(
                allowed_targets=(target(selected),), seconds=5.0
            )
            request, _ = await peer.receive()
            fields: dict[str, JsonValue] = {
                "parent_call_id": request["parent_call_id"],
                "request_id": _OBJECT.validate_python(request["request"])["request_id"],
            }
            if fault == "finish-first":
                await peer.send("delegate_finished", b"", **fields, error=None)
            elif fault == "early-success":
                await peer.send("delegate_result", b"{}", **fields, error=None)
            else:
                fields.update(
                    media_call_id="host-media",
                    mode="bidirectional"
                    if fault == "wrong-mode"
                    else "server_streaming",
                    remaining_seconds=None if fault == "wider-lifetime" else 4.0,
                    deadline_unix_ms=None
                    if fault == "wider-lifetime"
                    else context["deadline_unix_ms"],
                )
                await peer.send("delegate_started", b"", **fields)
                if fault == "duplicate-start":
                    await peer.send("delegate_started", b"", **fields)
            await asyncio.wait_for(asyncio.shield(peer.app_task), 2)


async def test_early_stream_exit_retires_parent_and_keeps_late_finish_correlated() -> (
    None
):
    """A closed child cannot be replayed; late final cleanup does not kill health."""
    selected = destination()

    async def caller(call: Invocation) -> dict[str, JsonValue]:
        async with call.stream(selected, {"input": "request"}) as stream:
            chunk = await stream.next_output()
            assert chunk is not None and chunk.kind == "chunk"
        return {"output": "must not succeed"}

    app = CapabilityApp()
    app.unary("example", caller)
    async with running(app, concurrency=2) as peer:
        await peer.invoke(allowed_targets=(target(selected),))
        request, _ = await peer.receive()
        reader, writer = await start(peer, request, "server_streaming")
        await output(writer, "chunk", 1)
        result, _ = await asyncio.wait_for(peer.receive(), 2)
        assert result["kind"] == "result" and result["error"] == "cancelled"
        assert await reader.read() == b""
        await peer.health()
        await finished(peer, request, "cancelled")
        await peer.health()


@pytest.mark.parametrize("phase", ["before-start", "media", "after-control"])
async def test_parent_cancellation_drains_media_without_losing_late_control(
    phase: str,
) -> None:
    """Cancellation wakes owned reads, closes media, and retains late correlation."""
    selected = destination()
    cleaned = asyncio.Event()

    async def caller(call: Invocation) -> dict[str, JsonValue]:
        try:
            async with call.stream(selected, {"input": "request"}) as stream:
                await stream.next_output()
        finally:
            cleaned.set()
        return {"output": "must not succeed"}

    app = CapabilityApp()
    app.unary("example", caller)
    async with running(app, concurrency=2) as peer:
        await peer.invoke(allowed_targets=(target(selected),))
        request, _ = await peer.receive()
        reader: asyncio.StreamReader | None = None
        if phase != "before-start":
            reader, _writer = await start(peer, request, "server_streaming")
        if phase == "after-control":
            await finished(peer, request)
        await peer.send("cancel", call_id="call-1")
        result, body = await asyncio.wait_for(peer.receive(), 2)
        assert result["error"] == "cancelled" and not body
        assert cleaned.is_set()
        if reader is not None:
            assert await asyncio.wait_for(reader.read(), 2) == b""
        if phase == "before-start":
            await peer.send(
                "delegate_result",
                parent_call_id=request["parent_call_id"],
                request_id=_OBJECT.validate_python(request["request"])["request_id"],
                error="cancelled",
            )
        elif phase != "after-control":
            await finished(peer, request, "cancelled")
        await peer.health()


@pytest.mark.parametrize("corrupt", ["schema", "sequence", "end", "control"])
async def test_buffered_terminal_cannot_hide_invalid_output_or_cleanup(
    corrupt: str,
) -> None:
    """A malformed data/final-control combination never produces parent success."""
    selected = destination()
    successful = asyncio.Event()

    async def caller(call: Invocation) -> dict[str, JsonValue]:
        async with call.stream(selected, {"input": "request"}) as stream:
            async for _frame in stream:
                pass
        successful.set()
        return {"output": "must not succeed"}

    app = CapabilityApp()
    app.unary("example", caller)
    async with running(app) as peer:
        await peer.invoke(allowed_targets=(target(selected),))
        request, _ = await peer.receive()
        _reader, writer = await start(peer, request, "server_streaming")
        if corrupt == "schema":
            writer.write(
                native.encode_transport_v2_packet(
                    document(
                        {
                            "call_id": "host-media",
                            "direction": "provider_to_caller",
                            "sequence": 1,
                            "kind": "completed",
                            "payload": {"wrong": "missing output"},
                        }
                    ).decode(),
                    b"",
                )
            )
            await writer.drain()
        else:
            await output(writer, "completed", 2 if corrupt == "sequence" else 1)
            if corrupt == "end":
                writer.close()
                await writer.wait_closed()
            elif corrupt == "control":
                await end(writer)
                await finished(peer, request, "outcome_unknown")
        result, body = await asyncio.wait_for(peer.receive(), 2)
        assert result["error"] is not None and not body and not successful.is_set()


async def test_local_bootstrap_schema_refusal_has_no_wire_effect_or_sequence() -> None:
    """Correcting a local payload still sends the first actual delegation sequence."""
    value = destination().descriptor()
    value["input_schema"] = {"type": "object", "required": ["required"]}
    selected = CapabilityEndpoint.from_descriptor("stream-receiver", value)

    async def caller(call: Invocation) -> dict[str, JsonValue]:
        with pytest.raises(ValueError, match="input schema"):
            async with call.stream(selected, {"wrong": "local refusal"}):
                raise AssertionError("invalid bootstrap unexpectedly entered")
        async with call.stream(selected, {"required": "exact"}) as stream:
            async for _frame in stream:
                pass
        return {"output": "ok"}

    app = CapabilityApp()
    app.unary("example", caller)
    async with running(app) as peer:
        await peer.invoke(allowed_targets=(target(selected),))
        request, body = await peer.receive()
        assert request["kind"] == "delegate" and json.loads(body) == {
            "required": "exact"
        }
        assert _OBJECT.validate_python(request["request"])["request_id"] == f"{1:064x}"
        _reader, writer = await start(peer, request, "server_streaming")
        await output(writer, "completed", 1)
        await end(writer)
        await finished(peer, request)
        result, _ = await peer.receive()
        assert result["error"] is None
