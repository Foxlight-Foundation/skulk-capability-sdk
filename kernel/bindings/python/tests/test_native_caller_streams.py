"""Real installed caller ABI: binary media, half-close, cleanup and failures."""

import asyncio
import json
import tempfile
from pathlib import Path

import _skulk_capability_kernel as native  # pyright: ignore[reportMissingModuleSource]
import pytest
from test_native_execution_transport import read_body, startup


def packet(direction: str, sequence: int, kind: str) -> str:
    """Build synthetic call-bound projections; the compiled codec admits them."""
    return json.dumps(
        dict(call_id="call", direction=direction, sequence=sequence, kind=kind)
    )


@pytest.mark.parametrize(
    "mode", ["server_streaming", "client_streaming", "bidirectional"]
)
async def test_caller_media_half_close_and_exact_end(mode: str) -> None:
    """The real native socket preserves media and waits for a separate end."""
    done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    release_end = asyncio.Event()

    async def peer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            hello = json.loads(await read_body(reader))
            assert hello["call_id"] == "call" and hello["protocol"] == 6
            assert hello["token"] == "a" * 64
            if mode != "server_streaming":
                for expected in ["started", "chunk", "completed"]:
                    header = json.loads(await read_body(reader))
                    assert header["frame"]["kind"] == expected
                    media = await reader.readexactly(header["media_size"])
                    assert media == (b"\x00\xff" if expected == "chunk" else b"")
            sequence = 1
            if mode != "client_streaming":
                value = json.loads(packet("provider_to_caller", sequence, "chunk"))
                value.update(
                    payload={"fraction": 1.25},
                    media={"kind": "inline", "media_type": "audio/pcm"},
                )
                writer.write(
                    native.encode_transport_v2_packet(json.dumps(value), b"\xff\x00")
                )
                sequence += 1
            writer.write(
                native.encode_transport_v2_packet(
                    packet("provider_to_caller", sequence, "completed"), b""
                )
            )
            await writer.drain()
            await release_end.wait()
            writer.write(
                native.encode_transport_v2_packet(
                    '{"kind":"end","call_id":"call"}', b""
                )
            )
            await writer.drain()
            assert await reader.read() == b""
            done.set_result(None)
        except BaseException as error:
            if not done.done():
                done.set_exception(error)
        finally:
            writer.close()
            await writer.wait_closed()

    with tempfile.TemporaryDirectory(prefix="caller-", dir="/tmp") as directory:
        socket = str(Path(directory) / "sock")
        async with await asyncio.start_unix_server(peer, socket):
            session = await native.open_transport_v2_client_stream(
                startup(socket), "call", mode
            )
            try:
                if mode != "server_streaming":
                    await session.send_input(
                        packet("caller_to_provider", 0, "started"), b""
                    )
                    value = json.loads(packet("caller_to_provider", 1, "chunk"))
                    value["media"] = {"kind": "inline", "media_type": "audio/pcm"}
                    await session.send_input(json.dumps(value), b"\x00\xff")
                    await session.send_input(
                        packet("caller_to_provider", 2, "completed"), b""
                    )
                if mode != "client_streaming":
                    output = await session.next_output()
                    assert output is not None and output[1] == b"\xff\x00"
                    assert '"fraction":1.25' in output[0]
                terminal = await session.next_output()
                assert (
                    terminal is not None
                    and json.loads(terminal[0])["kind"] == "completed"
                )
                pending = asyncio.ensure_future(session.next_output())
                await asyncio.sleep(0.02)
                assert not pending.done(), "terminal must not fabricate an end"
                release_end.set()
                assert await pending is None
                await session.finish()
                await asyncio.wait_for(done, 2)
            finally:
                release_end.set()
                await session.close()


@pytest.mark.parametrize(
    "mode", ["server_streaming", "client_streaming", "bidirectional"]
)
async def test_caller_cancellation_closes_without_reopening(mode: str) -> None:
    """Cancelling the actual native receive fences reuse and closes its peer."""
    done: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def peer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await read_body(reader)
            assert await reader.read() == b""
            done.set_result(None)
        except BaseException as error:
            if not done.done():
                done.set_exception(error)
        finally:
            writer.close()
            await writer.wait_closed()

    with tempfile.TemporaryDirectory(prefix="caller-", dir="/tmp") as directory:
        socket = str(Path(directory) / "sock")
        async with await asyncio.start_unix_server(peer, socket):
            session = await native.open_transport_v2_client_stream(
                startup(socket), "call", mode
            )
            pending = asyncio.ensure_future(session.next_output())
            await asyncio.sleep(0.02)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            await asyncio.gather(session.close(), session.close())
            with pytest.raises(ValueError):
                await session.next_output()
            await asyncio.wait_for(done, 2)
