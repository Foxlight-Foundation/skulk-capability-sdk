"""Public Node invocation discovery over real sockets and the issued Python ABI."""

import asyncio
import importlib
import importlib.metadata
import json
from pathlib import Path
from typing import Protocol, cast

import pytest
from pydantic import JsonValue, TypeAdapter

from tests_native.test_application import document, header, running
from tests_typescript.test_node_application import NodeApplication
from tests_typescript.test_node_calls import endpoint, reply
from tools.check_discovery_profile import Native

_OBJECT = TypeAdapter(dict[str, JsonValue])


class DiscoveryNative(Native, Protocol):
    """Independent issued frame/proof codec, without production caller authority."""

    def discovery_socket(self, startup: str) -> str: ...


@pytest.mark.parametrize("fault", ["none", "denied", "corrupt", "trailing"])
async def test_node_discovery_retains_parent_and_its_separate_query_sequence(
    tmp_path: Path, fault: str
) -> None:
    """A synthetic host verifies native proofs, distinct child IDs and retirement.

    The host supplies no production authorization; Core separately qualifies
    original caller permissions. This tests the independently installed Node
    client against real compiled Python framing, never a JavaScript codec mock.
    """
    assert importlib.metadata.version("skulk-capability-kernel") == "0.7.0rc7"
    native = cast(DiscoveryNative, importlib.import_module("_skulk_capability_kernel"))
    source, target = endpoint()
    app = NodeApplication(
        tmp_path,
        source
        + """const app = new CapabilityApp();
app.unary('example', async call => {
  if (call.context.export().caller_id !== 'client:original-discovery')
    throw Error('original caller changed');
  let code = 'complete';
  try {
    if ((await call.discover()).offers.length !== 0) throw Error('directory mismatch');
    if ((await call.discover()).offers.length !== 0) throw Error('directory mismatch');
  } catch (error) {
    if (!(error instanceof DiscoveryError)) throw error;
    code = error.code;
  }
  const child = code === 'complete'
    ? (await call.call(endpoint, {input:'after discovery'})).payload.output : null;
  return {code, child};
});""",
    )
    observed: list[dict[str, JsonValue]] = []
    owned: set[asyncio.Task[None]] = set()
    async with running(app) as peer:
        startup = document(peer.startup).decode()

        async def serve(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                metadata = await header(reader)
                assert await reader.read(1) == b""
                query = _OBJECT.validate_json(
                    native.admit_discovery_request(
                        startup, len(metadata).to_bytes(4, "big") + metadata
                    )
                )
                assert set(query) == {
                    "parent_call_id",
                    "parent_scope_id",
                    "parent_request_sha256",
                    "request_id",
                }
                assert query["parent_call_id"] == "c" * 64
                observed.append(query)
                response = native.discovery_reply(
                    startup,
                    metadata.decode(),
                    b"" if fault == "denied" else b"[]",
                    "denied" if fault == "denied" else None,
                )
                if fault == "corrupt":
                    response = response[:-1] + b"}"
                if fault == "trailing":
                    response += b"x"
                writer.write(response)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            owned.add(asyncio.create_task(serve(reader, writer)))

        server = await asyncio.start_unix_server(
            accept, native.discovery_socket(startup)
        )
        try:
            context = await peer.invoke(
                "c" * 64,
                caller_id="client:original-discovery",
                allowed_targets=(target,),
            )
            async with asyncio.timeout(5):
                frame, body = await peer.receive()
                if fault == "none":
                    assert frame["kind"] == "delegate"
                    assert _OBJECT.validate_python(frame["request"])["request_id"] == (
                        f"{1:064x}"
                    )
                    assert json.loads(body) == {"input": "after discovery"}
                    await reply(peer, frame)
                    frame, body = await peer.receive()
                if fault in ("corrupt", "trailing"):
                    assert frame["error"] is not None
                else:
                    assert frame["error"] is None
                    assert json.loads(body) == {
                        "code": "complete" if fault == "none" else "denied",
                        "child": "child" if fault == "none" else None,
                    }
            for query in observed:
                assert query["parent_scope_id"] == context["scope_id"]
                assert query["parent_request_sha256"] == context["request_sha256"]
            assert [int(str(q["request_id"]), 16) for q in observed] == (
                [1, 2] if fault == "none" else [1]
            )
        finally:
            server.close()
            server.close_clients()
            await server.wait_closed()
            await asyncio.gather(*owned)


async def test_node_cancel_drains_a_silent_discovery_before_parent_completion(
    tmp_path: Path,
) -> None:
    """Cancellation finishes without a metadata reply and admits the next root."""
    native = cast(DiscoveryNative, importlib.import_module("_skulk_capability_kernel"))
    app = NodeApplication(
        tmp_path,
        """const app = new CapabilityApp();
app.unary('example', async call => {
  if (call.callId === 'next') return {next:true};
  await call.discover();
  throw Error('silent discovery unexpectedly completed');
});""",
    )
    entered, release = asyncio.Event(), asyncio.Event()
    owned: set[asyncio.Task[None]] = set()
    async with running(app) as peer:
        startup = document(peer.startup).decode()

        async def serve(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                metadata = await header(reader)
                assert await reader.read(1) == b""
                native.admit_discovery_request(
                    startup, len(metadata).to_bytes(4, "big") + metadata
                )
                entered.set()
                await release.wait()
            finally:
                writer.close()
                await writer.wait_closed()

        def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            owned.add(asyncio.create_task(serve(reader, writer)))

        server = await asyncio.start_unix_server(
            accept, native.discovery_socket(startup)
        )
        try:
            await peer.invoke("c" * 64)
            async with asyncio.timeout(5):
                await entered.wait()
                await peer.send("cancel", call_id="c" * 64)
                await peer.send("cancel", call_id="c" * 64)
                frame, body = await peer.receive()
                assert frame["error"] == "cancelled" and body == b""
                assert not release.is_set()
                await peer.health()
                await peer.invoke("next")
                frame, body = await peer.receive()
                assert frame["error"] is None and json.loads(body) == {"next": True}
        finally:
            release.set()
            server.close()
            server.close_clients()
            await server.wait_closed()
            await asyncio.gather(*owned)
