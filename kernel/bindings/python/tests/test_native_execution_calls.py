"""Real compiled call framing and protected native socket lifecycle checks."""

import asyncio
import hashlib
import json
import tempfile
from pathlib import Path
from typing import cast

import _skulk_capability_kernel as native  # pyright: ignore[reportMissingModuleSource]
import pytest

from tools.check_calls_profile import Native, fixture, python_case


def invoke() -> tuple[dict[str, object], bytes]:
    """Use independently authored synthetic wire metadata, never host authority."""
    case = next(
        c for c in fixture(True).cases if c.identity == "invoke-unary-budget-None"
    )
    return json.loads(case.source), bytes.fromhex(case.payload_hex)


def startup(socket: str) -> native.ManagedV2Contract:
    """Return a real native-admitted account-free startup for a synthetic peer."""
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


async def body(reader: asyncio.StreamReader) -> bytes:
    """An independent peer reads only the existing protected hello prefix."""
    length = int.from_bytes(await reader.readexactly(4), "big")
    assert 2 <= length <= 65536
    return await reader.readexactly(length)


def test_complete_independent_calls_vectors() -> None:
    """Compare every complete expected byte vector/refusal to the installed ABI."""
    inputs = fixture(True)
    assert native.calls_v2_profile_digest() == inputs.profile_digest
    for case in inputs.cases:
        assert python_case(cast(Native, native), case) == case.expected(), case.identity


def test_request_hash_binds_exact_raw_json_bytes() -> None:
    """Equivalent JSON with different bytes must not inherit the old binding."""
    metadata, payload = invoke()
    packet = native.encode_call_v2_frame(json.dumps(metadata), payload)
    _, actual = native.decode_call_v2_frame(packet)
    assert actual == payload
    with pytest.raises(ValueError):
        native.encode_call_v2_frame(
            json.dumps(metadata), json.dumps(json.loads(payload)).encode()
        )


def test_exact_raw_payload_resource_bound() -> None:
    """The binary body boundary is independent of control metadata size."""
    metadata, _ = invoke()
    payload = b'{"x":"' + b"x" * (16777216 - 8) + b'"}'
    metadata["payload_size"] = len(payload)
    context = cast(dict[str, object], metadata["context"])
    context["request_sha256"] = hashlib.sha256(payload).hexdigest()
    packet = native.encode_call_v2_frame(json.dumps(metadata), payload)
    assert native.decode_call_v2_frame(packet)[1] == payload
    with pytest.raises(ValueError):
        native.encode_call_v2_frame(json.dumps(metadata), payload + b" ")


async def test_native_call_channel_preserves_body_and_startup_binding() -> None:
    """Real native socket I/O exchanges exact raw payloads after compiled hello."""
    received: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
    release = asyncio.Event()
    golden = next(
        c for c in fixture(True).cases if c.identity == "invoke-unary-budget-None"
    )
    writers: list[asyncio.StreamWriter] = []
    with tempfile.TemporaryDirectory(prefix="calls-py-", dir="/tmp") as directory:
        record = startup(str(Path(directory) / "sock"))

        async def peer(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            writers.append(writer)
            try:
                record.admit_hello((await body(reader)).decode())
                writer.write(bytes.fromhex(golden.canonical or ""))
                await writer.drain()
                size = int.from_bytes(await reader.readexactly(4), "big")
                assert 2 <= size <= 262144
                metadata = json.loads(await reader.readexactly(size))
                assert metadata["kind"] == "result" and metadata["call_id"] == "call-1"
                received.set_result(await reader.readexactly(metadata["payload_size"]))
                await release.wait()
            except BaseException as error:
                if not received.done():
                    received.set_exception(error)
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_unix_server(peer, str(Path(directory) / "sock"))
        channel = await native.open_call_v2_control(record, 3)
        try:
            metadata, payload = await channel.receive()
            assert (
                json.loads(metadata)["context"]["request_sha256"]
                == hashlib.sha256(payload).hexdigest()
            )
            assert payload == bytes.fromhex(golden.payload_hex)
            response = b'{ "ok" : true }'
            await channel.send(
                json.dumps(
                    {
                        "contract_profile": "skulk.calls/2",
                        "profile_digest": native.calls_v2_profile_digest(),
                        "protocol": 2,
                        "kind": "result",
                        "call_id": "call-1",
                        "error": None,
                        "payload_size": len(response),
                    }
                ),
                response,
            )
            assert await asyncio.wait_for(received, 2) == response
        finally:
            release.set()
            await channel.close()
            for writer in writers:
                writer.close()
                await writer.wait_closed()
            server.close()
            await server.wait_closed()


async def test_interrupted_call_read_poisoning_prevents_later_reuse() -> None:
    """Cancellation after a partial prefix cannot resume as another call."""
    connected: asyncio.Future[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = (
        asyncio.get_running_loop().create_future()
    )

    async def peer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await body(reader)
        connected.set_result((reader, writer))

    with tempfile.TemporaryDirectory(prefix="calls-cancel-", dir="/tmp") as directory:
        path = str(Path(directory) / "sock")
        server = await asyncio.start_unix_server(peer, path)
        channel = await native.open_call_v2_control(startup(path), 3)
        _, writer = await connected
        try:
            writer.write(b"\x00\x00")
            await writer.drain()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(channel.receive(), 0.03)
            await asyncio.sleep(0.02)
            item = next(c for c in fixture(True).cases if c.identity == "shutdown")
            with pytest.raises(ValueError, match="closed"):
                await channel.send(item.source, b"")
        finally:
            await channel.close()
            writer.close()
            await writer.wait_closed()
            server.close()
            await server.wait_closed()


def test_header_hint_is_bounded_and_does_not_admit_request_bytes() -> None:
    """A host can size its read natively but must still admit the actual body."""
    metadata, payload = invoke()
    source = json.dumps(metadata)
    assert native.call_v2_payload_size(source) == len(payload)
    changed = payload.replace(b"hello", b"other")
    assert len(changed) == len(payload)
    with pytest.raises(ValueError):
        native.encode_call_v2_frame(source, changed)
    metadata["payload_size"] = 16777216
    assert native.call_v2_payload_size(json.dumps(metadata)) == 16777216
    metadata["payload_size"] = 16777217
    with pytest.raises(ValueError):
        native.call_v2_payload_size(json.dumps(metadata))
    metadata, _ = invoke()
    cast(dict[str, object], metadata["context"])["caller_id"] = ""
    with pytest.raises(ValueError):
        native.call_v2_payload_size(json.dumps(metadata))
