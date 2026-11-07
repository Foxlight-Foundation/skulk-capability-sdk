"""Stream bounds and lifecycle refusals are part of the standalone SDK contract."""

import asyncio
import json
import tempfile
from collections.abc import AsyncGenerator, AsyncIterator, Iterable
from contextlib import suppress
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from skulk_capability_sdk.contracts import Descriptor, Manifest, Startup, StreamInvoke
from skulk_capability_sdk.streams import (
    MAX_MEDIA_BYTES,
    InlineMedia,
    Sequence,
    StreamEnd,
    StreamError,
    StreamFrame,
    StreamHello,
    execute_stream,
    read_packet,
    write_packet,
)
from skulk_capability_sdk.wire import ProtocolError, read_message


@pytest.mark.parametrize("mode", ["client_streaming", "bidirectional"])
@pytest.mark.parametrize("fault", ["cancelled", "sequence", "half_close_data"])
async def test_child_helper_arbitrates_input_failure_before_completion(
    mode: Literal["client_streaming", "bidirectional"],
    fault: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Co-ready malformed/cancelled ingress cannot publish a successful terminal."""
    wait = asyncio.wait

    async def co_ready(
        futures: Iterable[asyncio.Task[object]],
        *,
        return_when: Literal["FIRST_COMPLETED", "FIRST_EXCEPTION", "ALL_COMPLETED"],
    ) -> tuple[set[asyncio.Task[object]], set[asyncio.Task[object]]]:
        tasks = tuple(futures)
        if any(
            getattr(task.get_coro(), "__qualname__", "")
            == "execute_stream.<locals>.output"
            for task in tasks
        ):
            return_when = "ALL_COMPLETED"
        return await wait(tasks, return_when=return_when)

    monkeypatch.setattr(asyncio, "wait", co_ready)
    descriptor = Descriptor(
        id="echo",
        version="1.0.0",
        title="Echo",
        description="Echo",
        input_schema={},
        input_chunk_schema={},
        io_mode=mode,
        output_chunk_schema={} if mode == "bidirectional" else None,
    )
    manifest = Manifest(
        bundle_id="echo",
        bundle_version="1.0.0",
        executable="/opt/echo.pyz",
        executable_sha256="0" * 64,
        arguments=(),
        descriptors=(descriptor,),
    )
    call = StreamInvoke(
        call_id="call",
        capability_id=descriptor.id,
        version=descriptor.version,
        descriptor_revision=descriptor.revision,
        payload={},
        remaining_seconds=5.0,
    )
    received: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()

    async def owner(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            assert isinstance(await read_message(reader), StreamHello)
            await write_packet(
                writer,
                StreamFrame(
                    call_id=call.call_id,
                    direction="caller_to_provider",
                    sequence=0,
                    kind="started",
                ),
            )
            await write_packet(
                writer,
                StreamFrame(
                    call_id=call.call_id,
                    direction="caller_to_provider",
                    sequence=99 if fault == "sequence" else 1,
                    kind="cancelled"
                    if fault == "cancelled"
                    else "chunk"
                    if fault == "sequence"
                    else "completed",
                    payload={"text": "hello"} if fault != "cancelled" else None,
                    error=StreamError(code="cancelled", message="cancelled")
                    if fault == "cancelled"
                    else None,
                ),
            )
            received.set_result(await reader.read())
        finally:
            writer.close()
            await writer.wait_closed()

    async def handler(
        invocation: StreamInvoke, inputs: AsyncIterator[StreamFrame]
    ) -> AsyncGenerator[StreamFrame]:
        await anext(inputs)
        yield StreamFrame(
            call_id=invocation.call_id,
            direction="provider_to_caller",
            sequence=1,
            kind="completed",
        )

    # macOS Unix addresses are short; a nested pytest temporary path exceeds
    # that bound, so this owner socket gets its own private temporary directory.
    with tempfile.TemporaryDirectory(prefix="sdk-stream-", dir="/tmp") as directory:
        socket = str(Path(directory) / "socket")
        server = await asyncio.start_unix_server(owner, socket)
        try:
            startup = Startup(
                socket=socket,
                stream_socket=socket,
                token="0" * 64,
                node_id="node",
                host_id="host",
                transport_id="transport",
                manifest=manifest,
            )
            with pytest.raises(ProtocolError):
                await execute_stream(startup, call, handler)
            assert await asyncio.wait_for(received, 5) == b""
        finally:
            server.close()
            await server.wait_closed()


async def test_early_refusal_keeps_output_clean_during_late_upload() -> None:
    """Unconsumed caller bytes cannot reset the terminal and cleanup acknowledgment."""
    descriptor = Descriptor(
        id="refuse",
        version="1.0.0",
        title="Refuse",
        description="Refuse before consuming request media",
        input_schema={},
        input_chunk_schema={},
        output_chunk_schema={},
        io_mode="bidirectional",
    )
    manifest = Manifest(
        bundle_id="refuse",
        bundle_version="1.0.0",
        executable="/opt/refuse.pyz",
        executable_sha256="0" * 64,
        arguments=(),
        descriptors=(descriptor,),
    )
    call = StreamInvoke(
        call_id="call",
        capability_id=descriptor.id,
        version=descriptor.version,
        descriptor_revision=descriptor.revision,
        payload={},
        remaining_seconds=5.0,
    )
    received: asyncio.Future[list[str]] = asyncio.get_running_loop().create_future()

    async def owner(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            assert isinstance(await read_message(reader), StreamHello)
            await write_packet(
                writer,
                StreamFrame(
                    call_id=call.call_id,
                    direction="caller_to_provider",
                    sequence=0,
                    kind="started",
                ),
            )
            first = await read_packet(reader)
            assert isinstance(first, StreamFrame) and first.kind == "chunk"
            # Schema/admission work can delay input writes until after the
            # child's refusal has already been buffered on the other direction.
            for sequence in range(1, 4):
                await write_packet(
                    writer,
                    StreamFrame(
                        call_id=call.call_id,
                        direction="caller_to_provider",
                        sequence=sequence,
                        kind="chunk",
                        media=InlineMedia(
                            media_type="application/octet-stream",
                            data=b"x" * MAX_MEDIA_BYTES,
                        ),
                    ),
                )
            terminal, end = await read_packet(reader), await read_packet(reader)
            assert isinstance(terminal, StreamFrame) and terminal.kind == "completed"
            assert isinstance(end, StreamEnd)
            assert await reader.read() == b""
            received.set_result([first.kind, terminal.kind, "end"])
        except Exception as error:
            received.set_exception(error)
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()

    async def handler(
        invocation: StreamInvoke, inputs: AsyncIterator[StreamFrame]
    ) -> AsyncGenerator[StreamFrame]:
        # Refusal must not read or deliver caller media to application code.
        _ = inputs
        yield StreamFrame(
            call_id=invocation.call_id,
            direction="provider_to_caller",
            sequence=1,
            kind="chunk",
            payload={"refused": True},
        )
        yield StreamFrame(
            call_id=invocation.call_id,
            direction="provider_to_caller",
            sequence=2,
            kind="completed",
        )

    with tempfile.TemporaryDirectory(prefix="sdk-refusal-", dir="/tmp") as directory:
        socket = str(Path(directory) / "socket")
        server = await asyncio.start_unix_server(owner, socket)
        try:
            startup = Startup(
                socket=socket,
                stream_socket=socket,
                token="0" * 64,
                node_id="node",
                host_id="host",
                transport_id="transport",
                manifest=manifest,
            )
            await execute_stream(startup, call, handler)
            assert await asyncio.wait_for(received, 5) == ["chunk", "completed", "end"]
        finally:
            server.close()
            await server.wait_closed()


@pytest.mark.parametrize(
    "mode", ["unary", "server_streaming", "client_streaming", "bidirectional"]
)
def test_descriptor_requires_the_exact_chunk_schemas(mode: str) -> None:
    """An advertised streaming mode always has its corresponding schemas."""
    projection: dict[str, object] = {
        "id": "echo",
        "version": "1.0.0",
        "title": "Echo",
        "description": "Echo",
        "input_schema": {},
        "output_schema": {},
        "io_mode": mode,
        "input_chunk_schema": {}
        if mode in ("client_streaming", "bidirectional")
        else None,
        "output_chunk_schema": {}
        if mode in ("server_streaming", "bidirectional")
        else None,
    }
    descriptor = Descriptor.model_validate(projection)
    assert descriptor.io_mode == mode
    for field in ("input_chunk_schema", "output_chunk_schema"):
        malformed: dict[str, object] = {
            **projection,
            field: None if projection[field] is not None else {},
        }
        with pytest.raises(ValidationError):
            Descriptor.model_validate(malformed)
    if mode != "unary":
        with pytest.raises(ValidationError, match="requires protocol 4"):
            Manifest(
                protocol=3,
                bundle_id="echo",
                bundle_version="1.0.0",
                executable="/opt/echo.pyz",
                executable_sha256="0" * 64,
                arguments=(),
                descriptors=(descriptor,),
            )


async def test_raw_media_round_trip_at_the_actual_bound() -> None:
    """Arbitrary non-UTF8 bytes fit without base64 or a control-frame size mismatch."""
    received: asyncio.Future[StreamFrame] = asyncio.get_running_loop().create_future()
    frame = StreamFrame(
        call_id="call",
        direction="provider_to_caller",
        sequence=1,
        kind="chunk",
        media=InlineMedia(
            data=bytes(range(256)) * (MAX_MEDIA_BYTES // 256), media_type="audio/pcm"
        ),
    )

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            result = await read_packet(reader)
            assert isinstance(result, StreamFrame)
            received.set_result(result)
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        _, writer = await asyncio.open_connection(
            "127.0.0.1", server.sockets[0].getsockname()[1]
        )
        await write_packet(writer, frame)
        assert await asyncio.wait_for(received, 5) == frame
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize(
    "fault", ["header", "media", "duplicate", "nonfinite", "attachment", "protocol"]
)
async def test_malformed_lengths_are_refused_without_reading_media(fault: str) -> None:
    """Length and schema errors do not wait for or allocate attacker-sized media."""
    reader = asyncio.StreamReader()
    if fault == "header":
        reader.feed_data((2**32 - 1).to_bytes(4, "big"))
    else:
        header = {
            "protocol": 3 if fault == "protocol" else 4,
            "frame": {"kind": "end", "call_id": "call"},
            "media_size": MAX_MEDIA_BYTES + 1
            if fault == "media"
            else 1
            if fault == "attachment"
            else 0,
        }
        raw = json.dumps(header).encode()
        if fault == "duplicate":
            raw = raw.replace(b'"media_size": 0', b'"media_size": 0, "media_size": 0')
        if fault == "nonfinite":
            raw = raw.replace(b'"media_size": 0', b'"media_size": NaN')
        reader.feed_data(len(raw).to_bytes(4, "big") + raw)
    with pytest.raises((ProtocolError, ValidationError)):
        await asyncio.wait_for(read_packet(reader), 0.1)


@pytest.mark.parametrize(
    "change",
    [{"call_id": "other"}, {"sequence": 2}, {"direction": "caller_to_provider"}],
)
def test_identity_and_sequence_are_fenced(change: dict[str, object]) -> None:
    """A different caller, direction or missing frame cannot reuse this stream."""
    frame = StreamFrame(
        call_id="call", direction="provider_to_caller", sequence=1, kind="completed"
    )
    validator = Sequence("call", "provider_to_caller", next_sequence=1)
    with pytest.raises(ProtocolError):
        validator.accept(frame.model_copy(update=change))
    validator.accept(frame)
    with pytest.raises(ProtocolError):
        validator.accept(frame)


def test_stream_admission_cannot_downgrade_protocol() -> None:
    with pytest.raises(ValidationError):
        StreamInvoke(
            protocol=3,
            call_id="call",
            capability_id="echo",
            version="1.0.0",
            descriptor_revision="0" * 16,
            remaining_seconds=1.0,
            payload={},
        )
