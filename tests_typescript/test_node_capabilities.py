"""Generic Node clients over real issued framing and original parent scope."""

import asyncio
import importlib
import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue, TypeAdapter

from skulk_capability_sdk.application import CapabilityEndpoint
from tests_native.test_application import document, header, running
from tests_native.test_capabilities import DiscoveryNative
from tests_native.test_delegation import endpoint, reply, target
from tests_typescript.test_node_application import NodeApplication

_OBJECT = TypeAdapter(dict[str, JsonValue])


@pytest.mark.parametrize("behavior", ["inference", "proposal", "execute"])
async def test_node_read_never_dispatches_a_different_effect(
    tmp_path: Path,
    behavior: str,
) -> None:
    """A real Node application returns refusal without a metadata/child request."""
    descriptor = endpoint().descriptor()
    descriptor["behavior"] = behavior
    app = NodeApplication(
        tmp_path,
        "const endpoint = new CapabilityEndpoint('receiver', "
        + json.dumps(descriptor)
        + ");\n"
        + """const app = new CapabilityApp();
app.unary('example', async call => {
  try { await new CapabilityClient(call).read(endpoint, {input:'private-marker'}); }
  catch (error) {
    if (error instanceof CapabilityInterfaceError) return {code:error.code};
    throw error;
  }
  throw Error('non-read dispatched');
});""",
    )
    async with running(app) as peer:
        await peer.invoke()
        result, raw = await peer.receive()
        assert result["kind"] == "result" and result["error"] is None
        assert json.loads(raw) == {"code": "not_read"}


async def test_node_generic_client_does_not_rebind_a_completed_parent(
    tmp_path: Path,
) -> None:
    """A fresh root cannot revive an earlier facade's retired invocation."""
    descriptor = endpoint().descriptor()
    app = NodeApplication(
        tmp_path,
        "const endpoint = new CapabilityEndpoint('receiver', "
        + json.dumps(descriptor)
        + ");\n"
        + """let retained;
const app = new CapabilityApp();
app.unary('example', async call => {
  if (retained === undefined) {
    retained = new CapabilityClient(call); return {done:true};
  }
  try { await retained.propose(endpoint, {input:'after-completion'}); }
  catch { return {retired:true}; }
  throw Error('retired parent reused');
});""",
    )
    async with running(app) as peer:
        await peer.invoke("c" * 64)
        result, raw = await peer.receive()
        assert result["error"] is None and json.loads(raw) == {"done": True}
        await peer.invoke("d" * 64)
        result, raw = await peer.receive()
        assert result["error"] is None and json.loads(raw) == {"retired": True}


async def test_node_configured_read_has_no_discovery_dependency(tmp_path: Path) -> None:
    """Native read admission works without constructing a metadata socket."""
    descriptor = endpoint().descriptor()
    descriptor["behavior"] = "read"
    destination = CapabilityEndpoint.from_descriptor("receiver", descriptor)
    app = NodeApplication(
        tmp_path,
        "const endpoint = new CapabilityEndpoint('receiver', "
        + json.dumps(descriptor)
        + ");\n"
        + """const app = new CapabilityApp();
app.unary('example', async call =>
  (await new CapabilityClient(call).read(endpoint, {input:'direct'})).payload);""",
    )
    async with running(app) as peer:
        context = await peer.invoke(allowed_targets=(target(destination),))
        request, raw = await peer.receive()
        assert request["kind"] == "delegate" and json.loads(raw) == {"input": "direct"}
        delegated = _OBJECT.validate_python(request["request"], strict=True)
        assert delegated["parent_scope_id"] == context["scope_id"]
        assert int(str(delegated["request_id"]), 16) == 1
        await reply(peer, request)
        result, raw = await peer.receive()
        assert result["error"] is None and json.loads(raw) == {"output": "child"}


async def test_node_generic_interfaces_preserve_caller_and_effect_sequence(
    tmp_path: Path,
) -> None:
    """Native metadata reads and pure intent cannot become a child execution."""
    native = cast(DiscoveryNative, importlib.import_module("_skulk_capability_kernel"))
    descriptor = endpoint().descriptor()
    descriptor["behavior"] = "read"
    destination = CapabilityEndpoint.from_descriptor("receiver", descriptor)
    app = NodeApplication(
        tmp_path,
        "const endpoint = new CapabilityEndpoint('receiver', "
        + json.dumps(descriptor)
        + ");\n"
        + """const app = new CapabilityApp();
app.unary('example', async call => {
  if (call.context.export().caller_id !== 'client:synthetic-private')
    throw new Error('caller changed');
  const client = new CapabilityClient(call);
  if ((await client.describe(endpoint)).behavior !== 'read')
    throw new Error('effect changed');
  const intent = await client.propose(endpoint, {input:'intent-only'});
  if (intent.payload.input !== 'intent-only') throw new Error('intent changed');
  const raw = new TextEncoder().encode('{ "input": "actual-read" }');
  return (await client.read(endpoint, raw)).payload;
});""",
    )
    queries: list[dict[str, JsonValue]] = []
    owned: set[asyncio.Task[None]] = set()
    async with running(app) as peer:
        startup = document(peer.startup).decode()
        offer: dict[str, JsonValue] = {
            "plugin_id": "managed.generic",
            "runtime_digest": "a" * 64,
            "incarnation": "b" * 32,
            "target": target(destination),
            "descriptor": destination.descriptor(),
            "stream_seconds": 30.0,
            "cleanup_seconds": 3.0,
            "startup_seconds": 3.0,
            "inference_seconds": None,
        }

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
                queries.append(query)
                writer.write(
                    native.discovery_reply(
                        startup, metadata.decode(), document([offer]), None
                    )
                )
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
                "c" * 64, allowed_targets=(target(destination),)
            )
            request, body = await peer.receive()
            assert (
                request["kind"] == "delegate" and body == b'{ "input": "actual-read" }'
            )
            delegation = _OBJECT.validate_python(request["request"], strict=True)
            assert int(str(delegation["request_id"]), 16) == 1
            assert delegation["parent_scope_id"] == context["scope_id"]
            assert delegation["parent_request_sha256"] == context["request_sha256"]
            assert delegation["destination"] == target(destination)
            assert [int(str(q["request_id"]), 16) for q in queries] == [1, 2]
            for query in queries:
                assert set(query) == {
                    "parent_call_id",
                    "parent_scope_id",
                    "parent_request_sha256",
                    "request_id",
                }
                assert query["parent_scope_id"] == context["scope_id"]
            await reply(peer, request)
            result, raw = await peer.receive()
            assert result["kind"] == "result" and result["error"] is None
            assert json.loads(raw) == {"output": "child"}
        finally:
            server.close()
            server.close_clients()
            await server.wait_closed()
            await asyncio.gather(*owned)
