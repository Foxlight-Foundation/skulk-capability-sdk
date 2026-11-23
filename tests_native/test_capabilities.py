"""Native generic interfaces: strict effects, inert intent and original callers."""

import asyncio
import hashlib
import importlib
import json
from pathlib import Path
from typing import Protocol, cast

import pytest
from pydantic import JsonValue, TypeAdapter
from test_application import document, header, running
from test_delegation import endpoint, reply, target

from skulk_capability_sdk.application import (
    CapabilityApp,
    CapabilityEndpoint,
    Invocation,
)
from skulk_capability_sdk.capabilities import (
    CapabilityClient,
    CapabilityDescription,
    CapabilityInterfaceError,
    CapabilityProposal,
)
from tools.check_discovery_profile import Native

_OBJECT = TypeAdapter(dict[str, JsonValue])
_VECTORS = TypeAdapter(list[dict[str, JsonValue]]).validate_json(
    Path(__file__).with_name("capability-interface-vectors.json").read_bytes()
)


@pytest.mark.parametrize("vector", _VECTORS, ids=[str(v["behavior"]) for v in _VECTORS])
def test_fixed_cross_language_intent_bytes(vector: dict[str, JsonValue]) -> None:
    """Both clients match independently recorded native bytes, including Unicode."""
    destination = CapabilityEndpoint.from_descriptor(
        "receiver", _OBJECT.validate_python(vector["descriptor"], strict=True)
    )
    proposal = CapabilityProposal.prepare(
        destination, _OBJECT.validate_python(vector["payload"], strict=True)
    )
    assert proposal.canonical().decode() == vector["canonical"]
    assert proposal.request_bytes.decode() == vector["request"]
    assert proposal.digest == vector["digest"]


class DiscoveryNative(Native, Protocol):
    """Real issued codec, never a host authority or replacement caller."""

    def discovery_socket(self, startup: str) -> str: ...


@pytest.mark.parametrize("behavior", ["read", "inference", "proposal", "execute"])
def test_inert_intents_preserve_exact_effect_and_native_input(behavior: str) -> None:
    """Proposal preparation cannot relabel an effect or retain mutable arguments."""
    declaration = endpoint().descriptor()
    declaration["behavior"] = behavior
    destination = CapabilityEndpoint.from_descriptor("receiver", declaration)
    payload: dict[str, JsonValue] = {"input": "private-proposal-marker", "value": 1.0}
    proposal = CapabilityProposal.prepare(destination, payload)
    payload["input"] = "mutated"
    copy = proposal.payload
    copy["input"] = "changed-again"
    assert proposal.payload["input"] == "private-proposal-marker"
    assert proposal.request_bytes == b'{"input":"private-proposal-marker","value":1}'
    intent = _OBJECT.validate_json(proposal.canonical())
    assert intent["format"] == 1
    assert intent["kind"] == "capability_request_proposal"
    assert set(intent) == {
        "format",
        "kind",
        "destination",
        "descriptor",
        "payload",
        "request_sha256",
    }
    assert intent["descriptor"] == destination.descriptor()
    assert intent["destination"] == {
        "node_id": "receiver",
        "capability_id": destination.capability_id,
        "version": destination.version,
        "descriptor_revision": destination.descriptor_revision,
    }
    assert (
        intent["request_sha256"] == hashlib.sha256(proposal.request_bytes).hexdigest()
    )
    assert proposal.digest == hashlib.sha256(proposal.canonical()).hexdigest()
    assert "private-proposal-marker" not in repr(proposal)
    assert not hasattr(proposal, "execute") and not hasattr(proposal, "approve")
    description = CapabilityDescription.from_endpoint(destination)
    assert description.behavior == behavior
    changed = description.descriptor()
    changed["behavior"] = "read"
    assert description.descriptor()["behavior"] == behavior
    assert "private-proposal-marker" not in repr(description)


@pytest.mark.parametrize("node_id", ["", "node\n", "bad node", "a" * 129])
def test_invalid_destination_identifiers_refuse_without_private_text(
    node_id: str,
) -> None:
    """Language projections must agree on complete ASCII identifier admission."""
    destination = CapabilityEndpoint.from_descriptor(node_id, endpoint().descriptor())
    with pytest.raises(CapabilityInterfaceError) as failed:
        CapabilityProposal.prepare(destination, {"input": "private-proposal-marker"})
    assert failed.value.code == "invalid_descriptor"
    assert "private-proposal-marker" not in str(failed.value)


@pytest.mark.parametrize(
    "payload",
    [
        {"input": 1},
        {"input": "private-proposal-marker", "bad": float("nan")},
        {"input": "private-proposal-marker", "bad": 2**53},
        {"input": "x" * 66000},
    ],
)
def test_native_schema_and_portability_refuse_invalid_proposals(
    payload: dict[str, JsonValue],
) -> None:
    """No language validator or malformed-input exception text replaces native rules."""
    descriptor = endpoint().descriptor()
    descriptor["input_schema"] = {
        "type": "object",
        "required": ["input"],
        "properties": {"input": {"type": "string"}},
    }
    destination = CapabilityEndpoint.from_descriptor("receiver", descriptor)
    with pytest.raises(CapabilityInterfaceError) as failed:
        CapabilityProposal.prepare(destination, payload)
    assert failed.value.code == "invalid_payload"
    assert "private-proposal-marker" not in str(failed.value)


@pytest.mark.parametrize("behavior", ["inference", "proposal", "execute"])
async def test_read_never_dispatches_other_effects(behavior: str) -> None:
    """The real application returns refusal without a metadata or child request."""
    declaration = endpoint().descriptor()
    declaration["behavior"] = behavior
    destination = CapabilityEndpoint.from_descriptor("receiver", declaration)
    app = CapabilityApp()

    async def handle(call: Invocation) -> dict[str, JsonValue]:
        try:
            await CapabilityClient(call).read(destination, {"input": "private-marker"})
        except CapabilityInterfaceError as error:
            return {"code": error.code}
        raise AssertionError("non-read handler was dispatched")

    app.unary("example", handle)
    async with running(app) as peer:
        await peer.invoke()
        result, body = await peer.receive()
        assert result["kind"] == "result" and result["error"] is None
        assert json.loads(body) == {"code": "not_read"}


async def test_generic_client_cannot_reuse_a_completed_parent() -> None:
    """Retaining the facade cannot replace a retired caller with a new root."""
    retained: list[CapabilityClient] = []
    app = CapabilityApp()

    async def handle(call: Invocation) -> dict[str, JsonValue]:
        retained.append(CapabilityClient(call))
        return {"done": True}

    app.unary("example", handle)
    async with running(app) as peer:
        await peer.invoke("c" * 64)
        result, _ = await peer.receive()
        assert result["error"] is None
        with pytest.raises(ValueError):
            await retained[0].describe(endpoint())
        with pytest.raises(ValueError):
            await retained[0].propose(endpoint(), {"input": "after-completion"})


async def test_configured_read_needs_no_optional_discovery_channel() -> None:
    """Older compatible call hosts can still admit a configured original-caller read."""
    descriptor = endpoint().descriptor()
    descriptor["behavior"] = "read"
    destination = CapabilityEndpoint.from_descriptor("receiver", descriptor)
    app = CapabilityApp()

    async def handle(call: Invocation) -> dict[str, JsonValue]:
        return (
            await CapabilityClient(call).read(destination, {"input": "direct"})
        ).payload

    app.unary("example", handle)
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


@pytest.mark.parametrize("mode", ["client_streaming", "bidirectional"])
def test_proposal_cannot_drop_required_input_media(mode: str) -> None:
    """A JSON intent must not silently claim to represent an input stream."""
    descriptor = endpoint().descriptor()
    descriptor.update(io_mode=mode, input_chunk_schema={"type": "object"})
    if mode == "bidirectional":
        descriptor["output_chunk_schema"] = {"type": "object"}
    destination = CapabilityEndpoint.from_descriptor("receiver", descriptor)
    with pytest.raises(CapabilityInterfaceError) as failed:
        CapabilityProposal.prepare(destination, {"input": "without-media"})
    assert failed.value.code == "unsupported_mode"


@pytest.mark.requires_discovery_v1
async def test_real_generic_client_keeps_parent_and_child_effect_sequence() -> None:
    """Two scoped observations and an inert proposal precede one actual read."""
    native = cast(DiscoveryNative, importlib.import_module("_skulk_capability_kernel"))
    declaration = endpoint().descriptor()
    declaration["behavior"] = "read"
    destination = CapabilityEndpoint.from_descriptor("receiver", declaration)
    app = CapabilityApp()
    queries: list[dict[str, JsonValue]] = []
    owned: set[asyncio.Task[None]] = set()
    failures: list[Exception] = []

    async def handle(call: Invocation) -> dict[str, JsonValue]:
        assert call.context.export()["caller_id"] == "client:synthetic-private"
        try:
            client = CapabilityClient(call)
            described = await client.describe(destination)
            assert described.behavior == "read"
            proposal = await client.propose(destination, {"input": "intent-only"})
            assert proposal.payload == {"input": "intent-only"}
            return (
                await client.read(destination, b'{ "input": "actual-read" }')
            ).payload
        except Exception as error:
            failures.append(error)
            raise

    app.unary("example", handle)
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
            ), failures
            delegation = _OBJECT.validate_python(request["request"], strict=True)
            assert int(str(delegation["request_id"]), 16) == 1
            assert delegation["parent_scope_id"] == context["scope_id"]
            assert delegation["parent_request_sha256"] == context["request_sha256"]
            assert delegation["destination"] == target(destination)
            assert len(queries) == 2
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
            result, result_body = await peer.receive()
            assert result["kind"] == "result" and result["error"] is None
            assert json.loads(result_body) == {"output": "child"}
        finally:
            server.close()
            server.close_clients()
            await server.wait_closed()
            await asyncio.gather(*owned)
