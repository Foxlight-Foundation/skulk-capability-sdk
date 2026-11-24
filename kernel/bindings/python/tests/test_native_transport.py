"""Installed protocol-five wire ABI and real native channel lifecycle."""

import asyncio
import json
import tempfile
from pathlib import Path
from typing import cast

import _skulk_capability_kernel as native  # pyright: ignore[reportMissingModuleSource]
import pytest

from tools.check_transport_profile import Native, fixture, python_case


def startup(socket: str) -> native.ManagedContract:
    """Use an independently captured synthetic managed startup with no account."""
    root = Path(__file__).resolve().parents[4]
    cases = json.loads((root / "kernel/contracts/managed-v1-cases.json").read_text())
    value = json.loads(
        next(
            c["canonical"]
            for c in cases["cases"]
            if c["name"] == "Startup" and c.get("canonical")
        )
    )
    value.update(socket=socket, stream_socket=socket, token="a" * 64)
    return native.ManagedContract.from_json("Startup", json.dumps(value))


async def read_body(reader: asyncio.StreamReader) -> bytes:
    """Independent test peer parses the exact bounded network length prefix."""
    size = int.from_bytes(await reader.readexactly(4), "big")
    assert 2 <= size <= 65536
    return await reader.readexactly(size)


def control(kind: str) -> str:
    """Create a closed synthetic control record for native admission."""
    return json.dumps(
        {
            "protocol": 5,
            "contract_profile": "skulk.transport/1",
            "profile_digest": native.transport_profile_digest(),
            "kind": kind,
        }
    )


def test_complete_independent_wire_vectors() -> None:
    """Every full byte vector and refusal matches the installed compiled ABI."""
    inputs = fixture()
    assert native.transport_profile_digest() == inputs.profile_digest
    for case in inputs.cases:
        assert python_case(cast(Native, native), case) == case.expected(), case.identity
        if case.valid and case.wire_hex is not None:
            wire = bytes.fromhex(case.wire_hex)
            if case.action == "control":
                assert json.loads(native.decode_portable_control(wire)) == json.loads(
                    case.source
                )
            else:
                _, media = native.decode_portable_packet(wire)
                assert media.hex() == case.media_hex
            for malformed in [wire[:-1], wire + b"x"]:
                with pytest.raises(ValueError):
                    if case.action == "control":
                        native.decode_portable_control(malformed)
                    else:
                        native.decode_portable_packet(malformed)
    with pytest.raises(ValueError):
        native.encode_portable_packet('{"kind":"end","call_id":"\ud800"}', b"")


@pytest.mark.parametrize(
    "mode", ["server_streaming", "client_streaming", "bidirectional"]
)
async def test_portable_stream_uses_startup_binding_and_holds_terminal_for_cleanup(
    mode: str,
) -> None:
    """Actual native I/O preserves direction half-close and one terminal/end pair."""
    done: asyncio.Future[list[str]] = asyncio.get_running_loop().create_future()
    cleaned = asyncio.Event()

    async def peer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            hello = json.loads(await read_body(reader))
            assert (
                hello["protocol"] == 5
                and hello["contract_profile"] == "skulk.handshake/1"
            )
            assert hello["call_id"] == "call" and hello["token"] == "a" * 64
            if mode != "server_streaming":
                for sequence, kind in [(0, "started"), (1, "completed")]:
                    writer.write(
                        native.encode_portable_packet(
                            json.dumps(
                                {
                                    "call_id": "call",
                                    "direction": "caller_to_provider",
                                    "sequence": sequence,
                                    "kind": kind,
                                }
                            ),
                            b"",
                        )
                    )
                await writer.drain()
            observed: list[str] = []
            for _ in range(2):
                header = json.loads(await read_body(reader))
                assert (
                    header["contract_profile"] == "skulk.transport/1"
                    and header["media_size"] == 0
                )
                assert cleaned.is_set()
                observed.append(header["frame"]["kind"])
            done.set_result(observed)
        except BaseException as error:
            if not done.done():
                done.set_exception(error)
        finally:
            writer.close()
            await writer.wait_closed()

    with tempfile.TemporaryDirectory(prefix="wire5-", dir="/tmp") as directory:
        socket = str(Path(directory) / "sock")
        server = await asyncio.start_unix_server(peer, socket)
        session = await native.open_portable_stream(startup(socket), "call", mode, 2.0)
        try:
            if mode != "server_streaming":
                assert await session.next_input() is not None
                assert await session.next_input() is not None
                assert await session.next_input() is None
            await session.send_output(
                json.dumps(
                    {
                        "call_id": "call",
                        "direction": "provider_to_caller",
                        "sequence": 1,
                        "kind": "completed",
                    }
                ),
                b"",
            )
            await asyncio.sleep(0.02)
            assert not done.done()
            cleaned.set()
            await session.finish()
            assert await asyncio.wait_for(done, 1.0) == ["completed", "end"]
        finally:
            await session.close()
            server.close()
            await server.wait_closed()


async def test_native_control_owns_deadline_and_poisoned_partial_read() -> None:
    """Cancellation cannot leave a partial control record reusable for later work."""
    connected: asyncio.Future[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = (
        asyncio.get_running_loop().create_future()
    )

    async def peer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await read_body(reader)
        connected.set_result((reader, writer))

    with tempfile.TemporaryDirectory(prefix="control5-", dir="/tmp") as directory:
        socket = str(Path(directory) / "sock")
        server = await asyncio.start_unix_server(peer, socket)
        channel = await native.open_portable_control(startup(socket), 2.0)
        reader, writer = await connected
        try:
            await channel.send(control("shutdown"))
            assert json.loads(await read_body(reader))["kind"] == "shutdown"
            writer.write(b"\x00\x00")
            await writer.drain()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(channel.receive(), 0.03)
            await asyncio.sleep(0.02)
            with pytest.raises(ValueError, match="closed"):
                await channel.send(control("shutdown"))
            assert await asyncio.wait_for(reader.read(1), 1.0) == b""
        finally:
            await channel.close()
            writer.close()
            await writer.wait_closed()
            server.close()
            await server.wait_closed()


async def test_sixteen_megabyte_request_body_crosses_bounded_binary_packets() -> None:
    """The gateway-sized synthetic body is exact across real native stream I/O.

    This proves transport custody, not JSON inference or account/provider behavior.
    """
    import hashlib

    done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    chunk = bytes(range(256)) * 4096

    async def peer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            assert json.loads(await read_body(reader))["call_id"] == "call"
            for sequence in range(18):
                kind = (
                    "started"
                    if sequence == 0
                    else "completed"
                    if sequence == 17
                    else "chunk"
                )
                metadata: dict[str, object] = {
                    "call_id": "call",
                    "direction": "caller_to_provider",
                    "sequence": sequence,
                    "kind": kind,
                }
                if kind == "chunk":
                    metadata["media"] = {
                        "kind": "inline",
                        "media_type": "application/octet-stream",
                    }
                writer.write(
                    native.encode_portable_packet(
                        json.dumps(metadata), chunk if kind == "chunk" else b""
                    )
                )
                await writer.drain()
            assert json.loads(await read_body(reader))["frame"]["kind"] == "completed"
            assert json.loads(await read_body(reader))["frame"]["kind"] == "end"
            done.set_result(None)
        except BaseException as error:
            if not done.done():
                done.set_exception(error)
        finally:
            writer.close()
            await writer.wait_closed()

    with tempfile.TemporaryDirectory(prefix="bulk-wire5-", dir="/tmp") as directory:
        socket = str(Path(directory) / "sock")
        server = await asyncio.start_unix_server(peer, socket)
        session = await native.open_portable_stream(
            startup(socket), "call", "client_streaming", 5.0
        )
        try:
            digest = hashlib.sha256()
            size = 0
            while (packet := await session.next_input()) is not None:
                digest.update(packet[1])
                size += len(packet[1])
            assert size == 16 * 1024 * 1024
            assert (
                digest.hexdigest()
                == "341aacac661ccb210720bedaa9ead5d668fe5ea41a73532fc147c71e34040df1"
            )
            await session.send_output(
                json.dumps(
                    {
                        "call_id": "call",
                        "direction": "provider_to_caller",
                        "sequence": 1,
                        "kind": "completed",
                    }
                ),
                b"",
            )
            await session.finish()
            await asyncio.wait_for(done, 1.0)
        finally:
            await session.close()
            server.close()
            await server.wait_closed()
