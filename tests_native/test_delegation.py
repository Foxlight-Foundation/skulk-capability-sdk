"""Public application clients over the real released compiled call ABI.

Synthetic host peers qualify SDK ownership and wire behavior. They do not
establish production caller authentication or signed installed composition.
"""

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

import _skulk_capability_kernel as native  # pyright: ignore[reportMissingModuleSource]
import pytest
from pydantic import JsonValue, TypeAdapter
from test_application import Peer, document, header, running, startup_value

from skulk_capability_sdk.application import (
    CapabilityApp,
    CapabilityCallError,
    CapabilityEndpoint,
    InputFrame,
    Invocation,
    OutputFrame,
)

_OBJECT = TypeAdapter(dict[str, JsonValue])
_OBJECTS = TypeAdapter(list[dict[str, JsonValue]])


def endpoint(peer: Peer | None = None) -> CapabilityEndpoint:
    """Project a synthetic destination through the actual compiled descriptor."""
    startup = (
        peer.startup
        if peer is not None
        else startup_value("/tmp/unused-control", "/tmp/unused-stream", "unary")
    )
    manifest = _OBJECT.validate_python(startup["manifest"])
    contracts = _OBJECT.validate_python(manifest["contracts"])
    descriptor = _OBJECTS.validate_python(contracts["descriptors"])[0]
    return CapabilityEndpoint.from_descriptor("receiver", descriptor)


def target(destination: CapabilityEndpoint) -> dict[str, JsonValue]:
    """Independently derive the exact signed-target projection for host policy."""
    descriptor = destination.descriptor()
    return {
        "node_id": destination.node_id,
        "capability_id": descriptor["id"],
        "version": descriptor["version"],
        "behavior": descriptor["behavior"],
        "processing_domain": descriptor["processing_domain"],
        "descriptor_revision": native.portable_v2_contract_digest(
            "Descriptor", document(descriptor).decode()
        ),
    }


async def reply(
    peer: Peer,
    request: dict[str, JsonValue],
    body: bytes = b'{ "output": "child" }',
    *,
    error: str | None = None,
) -> None:
    """Return one native-admitted exact-parent/exact-sequence response."""
    delegation = _OBJECT.validate_python(request["request"])
    await peer.send(
        "delegate_result",
        body if error is None else b"",
        parent_call_id=request["parent_call_id"],
        request_id=delegation["request_id"],
        error=error,
    )


@pytest.mark.parametrize("seconds", [None, 10.0])
async def test_two_public_apps_compose_with_original_context_and_exact_bytes(
    seconds: float | None,
) -> None:
    """The shared admission preserves caller, raw body, ancestry and cleanup order."""
    observed: list[str] = []
    raw = b'{ "input" : "child request", "number": 1.0 }'

    @asynccontextmanager
    async def resource() -> AsyncGenerator[None]:
        try:
            yield
        finally:
            observed.append("child-cleaned")

    async def receiver(call: Invocation) -> dict[str, JsonValue]:
        await call.enter_resource(resource())
        context = call.context.export()
        assert context["caller_id"] == "client:synthetic-private"
        assert context["caller_kind"] == "client"
        assert context["issuer_node_id"] == "core-profile:synthetic"
        assert context["scope_id"] == "2" * 64
        assert context["root_scope_id"] == context["parent_scope_id"] == "1" * 64
        assert (
            len(TypeAdapter(list[JsonValue]).validate_python(context["ancestry"])) == 1
        )
        assert call.raw_payload == raw
        return {"output": "composed"}

    receiver_app = CapabilityApp()
    receiver_app.unary("example", receiver)
    async with running(receiver_app, node_id="receiver") as receiver_peer:
        destination = endpoint(receiver_peer)
        changed = destination.descriptor()
        changed["version"] = "8.0.0"
        assert destination.descriptor()["version"] != "8.0.0"

        async def caller(call: Invocation) -> dict[str, JsonValue]:
            result = await call.call(destination, raw)
            assert result.raw_payload == b'{"output": "composed"}'
            changed = result.payload
            changed["output"] = "changed"
            assert result.payload == {"output": "composed"}
            assert "composed" not in repr(result)
            observed.append("parent-resumed")
            return result.payload

        app = CapabilityApp()
        app.unary("example", caller)
        async with running(app, node_id="caller") as peer:
            context = await peer.invoke(
                payload=b'{"input":"parent", "caller_id":"forged"}',
                allowed_targets=(target(destination),),
                seconds=seconds,
            )
            request, body = await peer.receive()
            assert request["kind"] == "delegate" and body == raw
            fields = _OBJECT.validate_python(request["request"])
            assert fields["parent_scope_id"] == context["scope_id"]
            assert fields["parent_request_sha256"] == context["request_sha256"]
            assert fields["request_sha256"] == hashlib.sha256(raw).hexdigest()
            assert fields["destination"] == target(destination)
            parent = native.CompositionV3Contract.from_json(
                "Context", document(context).decode()
            )
            policy = {
                name: context[name]
                for name in [
                    "protocol",
                    "contract_profile",
                    "profile_digest",
                    "caller_id",
                    "caller_kind",
                    "issuer_node_id",
                    "policy_revision",
                    "allowed_targets",
                    "allowed_processing_domains",
                    "deadline_unix_ms",
                ]
            }
            policy["kind"] = "policy"
            child = parent.admit(
                native.CompositionV3Contract.from_json(
                    "DelegateRequest", document(fields).decode()
                ),
                native.CompositionV3Contract.from_json(
                    "Policy", document(policy).decode()
                ),
                document(context["executor"]).decode(),
                body,
                time.time_ns() // 1_000_000,
                "2" * 64,
            )
            child_context = _OBJECT.validate_json(child.canonical())
            assert child_context["deadline_unix_ms"] == context["deadline_unix_ms"]
            await receiver_peer.send(
                "invoke",
                body,
                call_id="child-1",
                mode="unary",
                context=child_context,
                remaining_seconds=seconds,
            )
            response, result = await receiver_peer.receive()
            assert response["error"] is None and observed == ["child-cleaned"]
            await reply(peer, request, result)
            response, result = await peer.receive()
            assert response["error"] is None and json.loads(result) == {
                "output": "composed"
            }
            assert observed == ["child-cleaned", "parent-resumed"]
            await peer.health()


async def test_denied_call_consumes_sequence_and_explicit_sibling_call_works() -> None:
    """A known refusal permits an explicit next call without any automatic retry."""
    destination = endpoint()
    deadline = time.time_ns() // 1_000_000 + 20000

    async def handler(call: Invocation) -> dict[str, JsonValue]:
        with pytest.raises(CapabilityCallError) as raised:
            await call.call(destination, {"input": "first"}, deadline_unix_ms=deadline)
        assert (
            raised.value.code == "denied"
            and str(raised.value) == "capability call failed: denied"
        )
        return (await call.call(destination, {"input": "second"})).payload

    app = CapabilityApp()
    app.unary("example", handler)
    async with running(app) as peer:
        context = await peer.invoke(seconds=10)
        request, body = await peer.receive()
        fields = _OBJECT.validate_python(request["request"])
        assert (
            fields["request_id"] == f"{1:064x}"
            and fields["deadline_unix_ms"] == deadline
        )
        assert json.loads(body) == {"input": "first"}
        await reply(peer, request, error="denied")
        request, body = await peer.receive()
        fields = _OBJECT.validate_python(request["request"])
        assert (
            fields["request_id"] == f"{2:064x}"
            and fields["deadline_unix_ms"] == context["deadline_unix_ms"]
        )
        assert json.loads(body) == {"input": "second"}
        await reply(peer, request)
        response, _body = await peer.receive()
        assert response["error"] is None


@pytest.mark.parametrize(
    "error",
    [
        "denied",
        "timeout",
        "outcome_unknown",
        "provider_error",
        "cancelled",
        "invalid_payload",
    ],
)
async def test_closed_host_errors_propagate_without_replay(error: str) -> None:
    """Each error completes once, preserving uncertainty and sanitized metadata."""

    async def handler(call: Invocation) -> dict[str, JsonValue]:
        return (await call.call(endpoint(), {"input": "private-content"})).payload

    app = CapabilityApp()
    app.unary("example", handler)
    async with running(app) as peer:
        await peer.invoke()
        request, _body = await peer.receive()
        await reply(peer, request, error=error)
        response, body = await peer.receive()
        assert response["kind"] == "result" and response["error"] == error and not body
        assert "private-content" not in document(response).decode()
        await peer.health()


async def test_local_refusals_do_not_send_or_consume_sequence() -> None:
    """Bad payloads, descriptor modes and target IDs refuse before channel effects."""
    strict = endpoint().descriptor()
    strict["input_schema"] = {"type": "object", "required": ["input"]}
    destination = CapabilityEndpoint.from_descriptor("receiver", strict)
    descriptor = destination.descriptor()
    descriptor["io_mode"] = "server_streaming"
    descriptor["output_chunk_schema"] = {"type": "object"}
    stream_destination = CapabilityEndpoint.from_descriptor("receiver", descriptor)

    async def handler(call: Invocation) -> dict[str, JsonValue]:
        for payload in [b"[]", b"{}", b"\xff"]:
            with pytest.raises(ValueError):
                await call.call(destination, payload)
        with pytest.raises(ValueError, match="unary destination"):
            await call.call(stream_destination, {"input": "good"})
        with pytest.raises(ValueError):
            await call.call(
                CapabilityEndpoint.from_descriptor(
                    "bad target", destination.descriptor()
                ),
                {"input": "good"},
            )
        return (await call.call(destination, {"input": "good"})).payload

    app = CapabilityApp()
    app.unary("example", handler)
    async with running(app) as peer:
        await peer.invoke()
        request, _body = await peer.receive()
        assert _OBJECT.validate_python(request["request"])["request_id"] == f"{1:064x}"
        await reply(peer, request)
        response, _body = await peer.receive()
        assert response["error"] is None


async def test_one_pending_call_per_parent_without_queueing() -> None:
    """A concurrent sibling refuses locally while health remains independently live."""
    first_sent, checked = asyncio.Event(), asyncio.Event()

    async def handler(call: Invocation) -> dict[str, JsonValue]:
        first = asyncio.create_task(call.call(endpoint(), {"input": "first"}))
        try:
            await first_sent.wait()
            with pytest.raises(ValueError, match="already pending"):
                await call.call(endpoint(), {"input": "second"})
            checked.set()
            return (await first).payload
        finally:
            if not first.done():
                first.cancel()
            await asyncio.gather(first, return_exceptions=True)

    app = CapabilityApp()
    app.unary("example", handler)
    async with running(app) as peer:
        await peer.invoke()
        request, _body = await peer.receive()
        first_sent.set()
        await asyncio.wait_for(checked.wait(), 2)
        await peer.health()
        await reply(peer, request)
        response, _body = await peer.receive()
        assert response["error"] is None


@pytest.mark.parametrize("fault", ["parent", "sequence", "duplicate"])
async def test_wrong_or_duplicate_response_retires_channel(fault: str) -> None:
    """Correlation faults cannot resume another handler or replay its effects."""
    held = asyncio.Event()

    async def handler(call: Invocation) -> dict[str, JsonValue]:
        await call.call(endpoint(), {"input": "child"})
        await held.wait()
        return {}

    app = CapabilityApp()
    app.unary("example", handler)
    with pytest.raises(
        ValueError, match="delegated (parent|result correlation) refused"
    ):
        async with running(app) as peer:
            await peer.invoke()
            request, _body = await peer.receive()
            if fault == "parent":
                request["parent_call_id"] = "other-parent"
            elif fault == "sequence":
                fields = _OBJECT.validate_python(request["request"])
                fields["request_id"] = f"{2:064x}"
                request["request"] = fields
            await reply(peer, request)
            if fault == "duplicate":
                await reply(peer, request)
            await asyncio.wait_for(asyncio.shield(peer.app_task), 3)


async def test_cancel_pending_child_drains_cleanup_and_accepts_late_reply() -> None:
    """Host cancellation keeps health and another root alive while cleanup drains."""
    cleaning, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def resource() -> AsyncGenerator[None]:
        try:
            yield
        finally:
            cleaning.set()
            await release.wait()
            cleaned.set()

    async def handler(call: Invocation) -> dict[str, JsonValue]:
        if call.call_id == "other-root":
            return {"ok": True}
        await call.enter_resource(resource())
        return (await call.call(endpoint(), {"input": "child"})).payload

    app = CapabilityApp()
    app.unary("example", handler)
    async with running(app, concurrency=2) as peer:
        await peer.invoke()
        request, _body = await peer.receive()
        await peer.send("cancel", call_id="call-1")
        await asyncio.wait_for(cleaning.wait(), 2)
        await peer.send("cancel", call_id="call-1")
        await peer.health()
        await peer.invoke("other-root")
        response, body = await peer.receive()
        assert response["call_id"] == "other-root" and response["error"] is None
        assert json.loads(body) == {"ok": True} and not cleaned.is_set()
        await reply(peer, request, error="cancelled")
        release.set()
        response, body = await peer.receive()
        assert response["call_id"] == "call-1" and response["error"] == "cancelled"
        assert not body and cleaned.is_set()
        await peer.health()


async def test_swallowed_local_child_cancellation_cannot_publish_success() -> None:
    """Abandoning an uncertain child permanently retires that invocation client."""
    sent = asyncio.Event()

    async def handler(call: Invocation) -> dict[str, JsonValue]:
        child = asyncio.create_task(call.call(endpoint(), {"input": "child"}))
        try:
            await sent.wait()
            child.cancel()
            await asyncio.gather(child, return_exceptions=True)
            with pytest.raises(ValueError, match="closed"):
                await call.call(endpoint(), {"input": "replay"})
            return {"incorrect": "success"}
        finally:
            if not child.done():
                child.cancel()
            await asyncio.gather(child, return_exceptions=True)

    app = CapabilityApp()
    app.unary("example", handler)
    async with running(app) as peer:
        await peer.invoke()
        request, _body = await peer.receive()
        sent.set()
        response, body = await peer.receive()
        assert (
            response["kind"] == "result"
            and response["error"] == "cancelled"
            and not body
        )
        await reply(peer, request, error="cancelled")
        await peer.health()


async def test_invocation_client_is_retired_after_return() -> None:
    """A retained context/client cannot create a later root or a new effect."""
    retained: list[Invocation] = []

    async def handler(call: Invocation) -> dict[str, JsonValue]:
        retained.append(call)
        return {}

    app = CapabilityApp()
    app.unary("example", handler)
    async with running(app) as peer:
        await peer.invoke()
        response, _body = await peer.receive()
        assert response["error"] is None
        with pytest.raises(ValueError, match="closed"):
            await retained[0].call(endpoint(), {"input": "after-return"})
        await peer.health()


async def test_return_with_unjoined_child_cannot_publish_success() -> None:
    """Application return cannot abandon an SDK call and claim successful cleanup."""
    sent = asyncio.Event()
    children: list[asyncio.Task[object]] = []

    async def handler(call: Invocation) -> dict[str, JsonValue]:
        child = asyncio.create_task(call.call(endpoint(), {"input": "child"}))
        children.append(child)
        await sent.wait()
        return {"incorrect": "success"}

    app = CapabilityApp()
    app.unary("example", handler)
    async with running(app) as peer:
        await peer.invoke()
        request, _body = await peer.receive()
        sent.set()
        response, body = await peer.receive()
        assert response["error"] == "cancelled" and not body
        results = await asyncio.wait_for(
            asyncio.gather(*children, return_exceptions=True), 2
        )
        assert all(isinstance(result, asyncio.CancelledError) for result in results)
        await reply(peer, request, error="cancelled")
        await peer.health()


async def test_cancelled_cleanup_cannot_create_a_new_delegation() -> None:
    """Teardown has no late client authority after host cancellation."""
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def handler(call: Invocation) -> dict[str, JsonValue]:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            with pytest.raises(ValueError, match="closed"):
                await call.call(endpoint(), {"input": "late-effect"})
            cleaned.set()
        return {}

    app = CapabilityApp()
    app.unary("example", handler)
    async with running(app) as peer:
        await peer.invoke()
        await asyncio.wait_for(entered.wait(), 2)
        await peer.send("cancel", call_id="call-1")
        response, body = await peer.receive()
        assert response["error"] == "cancelled" and not body and cleaned.is_set()
        await peer.health()


async def test_result_schema_is_checked_before_return_to_handler() -> None:
    """Successful wire framing cannot bypass the selected output contract."""
    descriptor = endpoint().descriptor()
    descriptor["output_schema"] = {"type": "object", "required": ["output"]}
    destination = CapabilityEndpoint.from_descriptor("receiver", descriptor)

    async def handler(call: Invocation) -> dict[str, JsonValue]:
        return (await call.call(destination, {"input": "child"})).payload

    app = CapabilityApp()
    app.unary("example", handler)
    async with running(app) as peer:
        await peer.invoke()
        request, _body = await peer.receive()
        await reply(peer, request, b'{"wrong":true}')
        response, body = await peer.receive()
        assert response["error"] == "invalid_payload" and not body
        await peer.health()


async def test_parent_timeout_preserves_timeout_and_drains_child_cleanup() -> None:
    """Retiring an abandoned child cannot turn the parent's expiry into a cancel."""
    cleaned = asyncio.Event()

    @asynccontextmanager
    async def resource() -> AsyncGenerator[None]:
        try:
            yield
        finally:
            cleaned.set()

    async def handler(call: Invocation) -> dict[str, JsonValue]:
        await call.enter_resource(resource())
        return (await call.call(endpoint(), {"input": "child"})).payload

    app = CapabilityApp()
    app.unary("example", handler)
    async with running(app) as peer:
        await peer.invoke(seconds=0.1)
        request, _body = await peer.receive()
        assert request["kind"] == "delegate"
        response, body = await peer.receive()
        assert response["error"] == "timeout" and not body and cleaned.is_set()
        await reply(peer, request, error="timeout")
        await peer.health()


async def test_handled_unknown_outcome_cannot_authorize_another_child() -> None:
    """An author may report uncertainty but cannot reuse that parent for effects."""

    async def handler(call: Invocation) -> dict[str, JsonValue]:
        with pytest.raises(CapabilityCallError) as raised:
            await call.call(endpoint(), {"input": "child"})
        assert raised.value.code == "outcome_unknown"
        with pytest.raises(ValueError, match="closed"):
            await call.call(endpoint(), {"input": "replay"})
        return {"status": "uncertain"}

    app = CapabilityApp()
    app.unary("example", handler)
    async with running(app) as peer:
        await peer.invoke()
        request, _body = await peer.receive()
        await reply(peer, request, error="outcome_unknown")
        response, body = await peer.receive()
        assert response["error"] is None and json.loads(body) == {"status": "uncertain"}
        await peer.health()


async def test_streaming_parent_can_await_a_unary_child() -> None:
    """The client shares call scope with stream handlers and their media cleanup."""

    async def handler(
        call: Invocation, _inputs: AsyncIterator[InputFrame]
    ) -> AsyncGenerator[OutputFrame]:
        result = await call.call(endpoint(), {"input": "child"})
        yield OutputFrame("completed", result.payload)

    app = CapabilityApp()
    app.stream("example", handler)
    async with running(app, "server_streaming") as peer:
        await peer.invoke()
        reader, _writer = await asyncio.wait_for(peer.streams.get(), 3)
        await header(reader)
        request, _body = await peer.receive()
        await reply(peer, request)
        for kind in ["completed", "end"]:
            packet = _OBJECT.validate_json(await header(reader))
            assert _OBJECT.validate_python(packet["frame"])["kind"] == kind
        response, _body = await peer.receive()
        assert response["error"] is None


async def test_channel_loss_drains_pending_call_and_application_resources() -> None:
    """Disconnect never reconnects or replays an uncertain child."""
    cleaned = asyncio.Event()

    @asynccontextmanager
    async def resource() -> AsyncGenerator[None]:
        try:
            yield
        finally:
            cleaned.set()

    async def handler(call: Invocation) -> dict[str, JsonValue]:
        await call.enter_resource(resource())
        return (await call.call(endpoint(), {"input": "child"})).payload

    app = CapabilityApp()
    app.unary("example", handler)
    with pytest.raises(ValueError):
        async with running(app) as peer:
            await peer.invoke()
            request, _body = await peer.receive()
            assert request["kind"] == "delegate"
            peer.writer.close()
            await peer.writer.wait_closed()
            await asyncio.wait_for(asyncio.shield(peer.app_task), 3)
    assert cleaned.is_set()


async def test_uncapped_child_wait_remains_healthy_past_thirty_seconds() -> None:
    """A silent child inherits no aggregate generation timer from the SDK."""

    async def handler(call: Invocation) -> dict[str, JsonValue]:
        return (await call.call(endpoint(), {"input": "child"})).payload

    app = CapabilityApp()
    app.unary("example", handler)
    async with running(app) as peer:
        await peer.invoke()
        request, _body = await peer.receive()
        assert _OBJECT.validate_python(request["request"])["deadline_unix_ms"] is None
        for _ in range(32):
            await asyncio.sleep(1)
            await peer.health()
        await reply(peer, request)
        response, _body = await peer.receive()
        assert response["error"] is None
