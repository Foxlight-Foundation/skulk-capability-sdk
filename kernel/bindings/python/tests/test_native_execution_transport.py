"""Installed protocol-six wire ABI and real native channel lifecycle."""

import asyncio
import json
import tempfile
from pathlib import Path
from typing import cast

import _skulk_capability_kernel as native  # pyright: ignore[reportMissingModuleSource]
import pytest

from tools.check_transport_profile import Native, fixture, python_case


def startup(socket: str) -> native.ManagedV2Contract:
    """Use an independently captured synthetic managed startup with no account."""
    root = Path(__file__).resolve().parents[4]
    cases = json.loads((root / "kernel/contracts/managed-v2-cases.json").read_text())
    value = json.loads(
        next(
            c["canonical"]
            for c in cases["cases"]
            if c["name"] == "Startup" and c.get("canonical")
        )
    )
    value.update(socket=socket, stream_socket=socket, token="a" * 64)
    return native.ManagedV2Contract.from_json("Startup", json.dumps(value))


async def read_body(reader: asyncio.StreamReader) -> bytes:
    """Independent test peer parses the exact bounded network length prefix."""
    size = int.from_bytes(await reader.readexactly(4), "big")
    assert 2 <= size <= 65536
    return await reader.readexactly(size)


def control(kind: str) -> str:
    """Create a closed synthetic control record for native admission."""
    return json.dumps(
        {
            "protocol": 6,
            "contract_profile": "skulk.transport/2",
            "profile_digest": native.transport_v2_profile_digest(),
            "kind": kind,
        }
    )


def test_complete_independent_wire_vectors() -> None:
    """Every full byte vector and refusal matches the installed compiled ABI."""
    inputs = fixture(True)
    assert native.transport_v2_profile_digest() == inputs.profile_digest
    for case in inputs.cases:
        assert python_case(cast(Native, native), case) == case.expected(), case.identity
        if case.valid and case.wire_hex is not None:
            wire = bytes.fromhex(case.wire_hex)
            if case.action == "control":
                assert json.loads(
                    native.decode_transport_v2_control(wire)
                ) == json.loads(case.source)
            else:
                _, media = native.decode_transport_v2_packet(wire)
                assert media.hex() == case.media_hex
            for malformed in [wire[:-1], wire + b"x"]:
                with pytest.raises(ValueError):
                    if case.action == "control":
                        native.decode_transport_v2_control(malformed)
                    else:
                        native.decode_transport_v2_packet(malformed)
    with pytest.raises(ValueError):
        native.encode_transport_v2_packet('{"kind":"end","call_id":"\ud800"}', b"")


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
                hello["protocol"] == 6
                and hello["contract_profile"] == "skulk.handshake/2"
            )
            assert hello["call_id"] == "call" and hello["token"] == "a" * 64
            if mode != "server_streaming":
                for sequence, kind in [(0, "started"), (1, "completed")]:
                    writer.write(
                        native.encode_transport_v2_packet(
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
                    header["contract_profile"] == "skulk.transport/2"
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

    with tempfile.TemporaryDirectory(prefix="wire6-", dir="/tmp") as directory:
        socket = str(Path(directory) / "sock")
        server = await asyncio.start_unix_server(peer, socket)
        session = await native.open_transport_v2_stream(startup(socket), "call", mode)
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

    with tempfile.TemporaryDirectory(prefix="control6-", dir="/tmp") as directory:
        socket = str(Path(directory) / "sock")
        server = await asyncio.start_unix_server(peer, socket)
        channel = await native.open_transport_v2_control(startup(socket), 2.0)
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


def test_execution_deadline_and_identity_goldens() -> None:
    """The real ABI preserves original callers and every nullable expiry case."""
    from tools.check_inference_profiles import Native as CompositionNative
    from tools.check_inference_profiles import fixture as composition_fixture
    from tools.check_inference_profiles import python_case as composition_case

    inputs = composition_fixture(True)
    assert native.composition_v3_profile_digest() == inputs.profile_digest
    for case in inputs.cases:
        assert (
            composition_case(cast(CompositionNative, native), case) == case.expected()
        ), case.identity
    source = next(c.source for c in inputs.cases if c.name == "Context")
    record = native.CompositionV3Contract.from_json("Context", source)
    assert repr(record) == "CompositionV3Contract(Context)"
    assert "synthetic-do-not-echo-secret" not in repr(record)
    with pytest.raises(ValueError):
        native.CompositionV2Contract.from_json("Context", source)


async def test_explicit_null_budget_refuses_boolean_and_invalid_numbers() -> None:
    """An invalid generation budget is refused before any socket operation."""
    record = startup("/tmp/synthetic-no-listener-execution.sock")
    for invalid in [True, -1, 0, float("nan"), float("inf"), 9007199254741]:
        with pytest.raises(ValueError):
            await native.open_transport_v2_stream(
                record, "call", "bidirectional", invalid
            )
