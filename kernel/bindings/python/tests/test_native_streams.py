"""Legacy Python interoperability and native async ownership on real local sockets."""

import asyncio
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Literal

# Native-only implementation; the checked .pyi supplies all Python types.
import _skulk_capability_kernel as native  # pyright: ignore[reportMissingModuleSource]
import pytest

from skulk_capability_sdk.streams import (
    InlineMedia,
    StreamEnd,
    StreamFrame,
    StreamHello,
    read_packet,
    write_packet,
)
from skulk_capability_sdk.wire import read_message

FIXTURE = (
    Path(__file__).resolve().parents[4]
    / "tests/fixtures/author-baseline/sdk-0.5.0-streams.json"
)


def projection(frame: StreamFrame) -> str:
    """The reference omits inline bytes from its finite JSON header."""
    return json.dumps(frame.model_dump(mode="python", exclude={"media": {"data"}}))


def hello() -> str:
    return json.dumps(
        {
            "kind": "stream_hello",
            "protocol": 4,
            "call_id": "call",
            "token": "0" * 64,
            "capability_node_id": "node",
            "manifest_sha256": "a" * 64,
        }
    )


async def test_frozen_and_native_packets_cross_the_reference_reader() -> None:
    reference = json.loads(FIXTURE.read_text())
    for item in reference["packets"]:
        wire = bytes.fromhex(item["wire_hex"])
        assert hashlib.sha256(wire).hexdigest() == item["wire_sha256"]
        metadata, media = native.decode_stream_packet(wire)
        assert json.loads(metadata) == item["projection"]
        assert media == bytes.fromhex(item["media_hex"])
        encoded = native.encode_stream_packet(metadata, media)
        reader = asyncio.StreamReader()
        reader.feed_data(encoded)
        decoded = await read_packet(reader)
        assert (
            decoded.model_dump(mode="python", exclude={"media": {"data"}})
            == item["projection"]
        )
    maximum = InlineMedia(data=bytes(range(256)) * 4096, media_type="audio/pcm")
    frame = StreamFrame(
        call_id="call",
        direction="provider_to_caller",
        sequence=1,
        kind="chunk",
        media=maximum,
    )
    decoded_projection, decoded_media = native.decode_stream_packet(
        native.encode_stream_packet(projection(frame), maximum.data)
    )
    assert json.loads(decoded_projection)["media"]["media_type"] == "audio/pcm"
    assert decoded_media == maximum.data
    with pytest.raises(ValueError, match="media exceeds"):
        native.encode_stream_packet(projection(frame), maximum.data + b"x")


@pytest.mark.parametrize(
    "mode", ["server_streaming", "client_streaming", "bidirectional"]
)
async def test_native_lifecycle_half_close_health_and_cleanup_ack(mode: str) -> None:
    received: asyncio.Future[list[StreamFrame | StreamEnd]] = (
        asyncio.get_running_loop().create_future()
    )
    cleanup_started = asyncio.Event()
    cleanup_done = asyncio.Event()

    async def owner(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            assert isinstance(await read_message(reader), StreamHello)
            if mode != "server_streaming":
                inputs: tuple[tuple[int, Literal["started", "completed"]], ...] = (
                    (0, "started"),
                    (1, "completed"),
                )
                for sequence, kind in inputs:
                    await write_packet(
                        writer,
                        StreamFrame(
                            call_id="call",
                            direction="caller_to_provider",
                            sequence=sequence,
                            kind=kind,
                        ),
                    )
            terminal = await read_packet(reader)
            assert cleanup_done.is_set()
            end = await read_packet(reader)
            received.set_result([terminal, end])
        except BaseException as error:
            if not received.done():
                received.set_exception(error)
        finally:
            writer.close()
            await writer.wait_closed()

    with tempfile.TemporaryDirectory(prefix="native-stream-", dir="/tmp") as directory:
        socket = str(Path(directory) / "sock")
        server = await asyncio.start_unix_server(owner, socket)
        session = await native.open_stream(socket, hello(), "call", mode, 2.0)
        try:
            if mode != "server_streaming":
                first = await session.next_input()
                assert first is not None and json.loads(first[0])["kind"] == "started"
                second = await session.next_input()
                assert (
                    second is not None and json.loads(second[0])["kind"] == "completed"
                )
                assert await session.next_input() is None
            await session.send_output(
                projection(
                    StreamFrame(
                        call_id="call",
                        direction="provider_to_caller",
                        sequence=1,
                        kind="completed",
                    )
                ),
                b"",
            )

            # Native holds the terminal while language iterator cleanup is pending.
            async def cleanup() -> None:
                cleanup_started.set()
                await asyncio.sleep(0.04)
                cleanup_done.set()

            task = asyncio.create_task(cleanup())
            await cleanup_started.wait()
            health_ticks = 0
            while not cleanup_done.is_set():
                health_ticks += 1
                await asyncio.sleep(0.002)
                assert not received.done()
            await task
            assert health_ticks > 1
            await session.finish()
            frames = await asyncio.wait_for(received, 1)
            assert isinstance(frames[0], StreamFrame) and frames[0].kind == "completed"
            assert isinstance(frames[1], StreamEnd)
        finally:
            await session.close()
            server.close()
            await server.wait_closed()


async def test_native_deadline_closes_idle_input_and_transport() -> None:
    ended: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()

    async def owner(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await read_message(reader)
            ended.set_result(await reader.read())
        finally:
            writer.close()
            await writer.wait_closed()

    with tempfile.TemporaryDirectory(prefix="native-stream-", dir="/tmp") as directory:
        socket = str(Path(directory) / "sock")
        server = await asyncio.start_unix_server(owner, socket)
        session = await native.open_stream(
            socket, hello(), "call", "bidirectional", 0.05
        )
        try:
            with pytest.raises(ValueError, match="deadline"):
                await session.next_input()
            assert await asyncio.wait_for(ended, 1) == b""
        finally:
            await session.close()
            server.close()
            await server.wait_closed()


async def test_native_preserves_reference_defaults_and_literal_normalization() -> None:
    for synthetic in [False, 0, 0.0]:
        frame = {
            "call_id": "call",
            "direction": "caller_to_provider",
            "sequence": 0,
            "kind": "started",
            "synthetic": synthetic,
        }
        encoded = native.encode_stream_packet(json.dumps(frame), b"")
        decoded, _ = native.decode_stream_packet(encoded)
        assert json.loads(decoded) == StreamFrame.model_validate_json(
            json.dumps(frame)
        ).model_dump(mode="python")
    raw = b'{"protocol":4.0,"frame":{"call_id":"call"},"media_size":-0}'
    decoded, _ = native.decode_stream_packet(len(raw).to_bytes(4, "big") + raw)
    assert json.loads(decoded) == {"kind": "end", "call_id": "call"}
