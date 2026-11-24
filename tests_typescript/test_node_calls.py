"""Actual Node invocation clients with an independent compiled synthetic host."""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Literal

import pytest
from pydantic import JsonValue, TypeAdapter

from tests_native.test_application import (
    Peer,
    document,
    header,
    native,
    running,
    startup_value,
)
from tests_typescript.test_node_application import NodeApplication

_OBJECT = TypeAdapter(dict[str, JsonValue])
_OBJECTS = TypeAdapter(list[dict[str, JsonValue]])


async def start(
    peer: Peer, request: dict[str, JsonValue], mode: str
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Independently admit the actual Node caller's native media hello."""
    await peer.send(
        "delegate_started",
        parent_call_id=request["parent_call_id"],
        request_id=_OBJECT.validate_python(request["request"])["request_id"],
        media_call_id="host-media",
        mode=mode,
        remaining_seconds=None,
        deadline_unix_ms=None,
    )
    reader, writer = await asyncio.wait_for(peer.streams.get(), 2)
    native.ManagedV2Contract.from_json(
        "Startup", document(peer.startup).decode()
    ).admit_hello((await header(reader)).decode(), "host-media")
    return reader, writer


async def input_packet(
    reader: asyncio.StreamReader,
) -> tuple[dict[str, JsonValue], bytes]:
    """Decode actual binary input with the independent released Python binding."""
    raw = await header(reader)
    media = await reader.readexactly(native.transport_v2_media_size(raw))
    projection, media = native.decode_transport_v2_packet(
        len(raw).to_bytes(4, "big") + raw + media
    )
    return _OBJECT.validate_json(projection), media


async def output(
    writer: asyncio.StreamWriter, kind: str, sequence: int, *, media: bytes = b""
) -> None:
    """Send a separately framed provider packet, never a JavaScript mock."""
    frame: dict[str, JsonValue] = {
        "call_id": "host-media",
        "direction": "provider_to_caller",
        "sequence": sequence,
        "kind": kind,
        "payload": {"output": kind},
    }
    if media:
        frame["media"] = {"kind": "inline", "media_type": "audio/pcm"}
    writer.write(native.encode_transport_v2_packet(document(frame).decode(), media))
    await writer.drain()


async def end(writer: asyncio.StreamWriter) -> None:
    """End remains distinct from the stream terminal."""
    writer.write(
        native.encode_transport_v2_packet('{"call_id":"host-media","kind":"end"}', b"")
    )
    await writer.drain()


async def finished(peer: Peer, request: dict[str, JsonValue]) -> None:
    """Control completion names the exact root and admitted child request."""
    await peer.send(
        "delegate_finished",
        parent_call_id=request["parent_call_id"],
        request_id=_OBJECT.validate_python(request["request"])["request_id"],
        error=None,
    )


def endpoint(
    mode: Literal[
        "unary", "server_streaming", "client_streaming", "bidirectional"
    ] = "unary",
    *,
    constrained: bool = True,
) -> tuple[str, dict[str, JsonValue]]:
    """Use an actual compiled declaration, not author-created caller authority."""
    startup = startup_value("/tmp/unused", "/tmp/unused-media", mode)
    manifest = _OBJECT.validate_python(startup["manifest"])
    envelope = _OBJECT.validate_python(manifest["contracts"])
    descriptor = _OBJECTS.validate_python(envelope["descriptors"])[0]
    descriptor["input_schema"] = (
        {"type": "object", "required": ["input"]} if constrained else {}
    )
    target: dict[str, JsonValue] = {
        "node_id": "receiver",
        "capability_id": descriptor["id"],
        "version": descriptor["version"],
        "behavior": descriptor["behavior"],
        "processing_domain": descriptor["processing_domain"],
        "descriptor_revision": native.portable_v2_contract_digest(
            "Descriptor", document(descriptor).decode()
        ),
    }
    return "const endpoint = new CapabilityEndpoint('receiver'," + json.dumps(
        descriptor
    ) + ");\n", target


async def reply(
    peer: Peer,
    frame: dict[str, JsonValue],
    *,
    error: str | None = None,
    payload: bytes = b'{ "output" : "child" }',
) -> None:
    """Finish only the pending original parent's exact request correlation."""
    await peer.send(
        "delegate_result",
        payload if error is None else b"",
        parent_call_id=frame["parent_call_id"],
        request_id=_OBJECT.validate_python(frame["request"])["request_id"],
        error=error,
    )


async def test_node_unary_child_preserves_request_reply_bytes_and_health(
    tmp_path: Path,
) -> None:
    """The child call goes over calls/2, not a fake JavaScript transport."""
    source, target = endpoint()
    app = NodeApplication(
        tmp_path,
        source
        + """const app = new CapabilityApp();
app.unary('example', async call => {
  const child = await call.call(endpoint,
    new TextEncoder().encode('{ "input" : "raw child" }'));
  return {raw:new TextDecoder().decode(child.rawPayload), output:child.payload.output};
});""",
    )
    async with running(app) as peer:
        await peer.invoke(allowed_targets=(target,))
        frame, body = await peer.receive()
        assert frame["kind"] == "delegate" and body == b'{ "input" : "raw child" }'
        request = _OBJECT.validate_python(frame["request"])
        assert request["request_sha256"] == hashlib.sha256(body).hexdigest()
        assert request["deadline_unix_ms"] is None and request["destination"] == target
        await peer.health()
        await reply(peer, frame)
        frame, body = await peer.receive()
        assert frame["error"] is None and json.loads(body) == {
            "raw": '{ "output" : "child" }',
            "output": "child",
        }


async def test_node_unconstrained_descriptor_allows_exact_child_request(
    tmp_path: Path,
) -> None:
    """An unconstrained schema must accept inputs without the fixture's usual key."""
    source, target = endpoint(constrained=False)
    app = NodeApplication(
        tmp_path,
        source
        + """const app = new CapabilityApp();
app.unary('example', async call => (await call.call(endpoint,{value:1})).payload);
""",
    )
    async with running(app) as peer:
        await peer.invoke(allowed_targets=(target,))
        child, body = await peer.receive()
        assert child["kind"] == "delegate" and json.loads(body) == {"value": 1}
        await reply(peer, child)
        frame, body = await peer.receive()
        assert frame["error"] is None and json.loads(body) == {"output": "child"}


async def test_node_local_refusal_and_host_denial_preserve_sequence(
    tmp_path: Path,
) -> None:
    """Local invalid input sends nothing; definite denial permits a later child."""
    source, target = endpoint()
    app = NodeApplication(
        tmp_path,
        source
        + """const app = new CapabilityApp();
app.unary('example', async call => {
  try {await call.call(endpoint,{wrong:true});throw Error('invalid input accepted');}
  catch(error) {if (!(error instanceof TypeError)) throw error;}
  try {await call.call(endpoint,{input:'denied'});throw Error('denial missing');}
  catch(error) {
    if (!(error instanceof CapabilityCallError) || error.code!=='denied') throw error;
  }
  return (await call.call(endpoint,{input:'allowed'})).payload;
});""",
    )
    async with running(app) as peer:
        await peer.invoke(allowed_targets=(target,))
        for sequence in (1, 2):
            frame, _ = await peer.receive()
            assert frame["kind"] == "delegate"
            assert (
                _OBJECT.validate_python(frame["request"])["request_id"]
                == f"{sequence:064x}"
            )
            await reply(peer, frame, error="denied" if sequence == 1 else None)
        frame, body = await peer.receive()
        assert frame["error"] is None and json.loads(body) == {"output": "child"}


async def test_node_uncertain_child_cannot_authorize_replacement(
    tmp_path: Path,
) -> None:
    """An uncertainty report is a one-way authority fence even if authors catch it."""
    source, target = endpoint()
    app = NodeApplication(
        tmp_path,
        source
        + """const app = new CapabilityApp();
app.unary('example', async call => {
  try {await call.call(endpoint,{input:'first'});throw Error('missing uncertainty');}
  catch(error) {
    if (!(error instanceof CapabilityCallError) || error.code!=='outcome_unknown')
      throw error;
  }
  try {
    await call.call(endpoint,{input:'replacement'});
    throw Error('replacement accepted');
  }
  catch(error) {if (!(error instanceof TypeError)) throw error;}
  return {retired:true};
});""",
    )
    async with running(app) as peer:
        await peer.invoke(allowed_targets=(target,))
        frame, _ = await peer.receive()
        await reply(peer, frame, error="outcome_unknown")
        frame, body = await peer.receive()
        assert frame["kind"] == "result" and frame["error"] is None
        assert json.loads(body) == {"retired": True}
        await peer.health()


@pytest.mark.parametrize(
    "mode", ["server_streaming", "client_streaming", "bidirectional"]
)
async def test_node_stream_half_close_and_joint_finalization(
    tmp_path: Path,
    mode: Literal["server_streaming", "client_streaming", "bidirectional"],
) -> None:
    """Actual child streams wait for separate end and host finalization."""
    source, target = endpoint(mode)
    app = NodeApplication(
        tmp_path,
        source
        + """const app = new CapabilityApp();
app.unary('example', async call => {
  const frames = [];
  let saved;
  const input = new TextEncoder().encode('{ "input" : "exact child" }');
  await call.stream(endpoint, input, async stream => {
    saved = stream;
    try {
      await call.call(endpoint,{input:'racing'});
      throw Error('concurrent child accepted');
    }
    catch(error) {if (!(error instanceof TypeError)) throw error;}
    if (stream.mode !== 'server_streaming') {
      try {await stream.send({wrong:true});throw Error('invalid chunk accepted');}
      catch(error) {if (!(error instanceof TypeError)) throw error;}
      await stream.send({input:'valid'}, {media:new Uint8Array([0,255]),
        mediaMetadata:{kind:'inline',media_type:'audio/pcm'}});
      await stream.completeInput();
    }
    for await (const frame of stream) {
      frames.push(frame.kind);
      if(frame.kind==='chunk' && (frame.media[0]!==255 || frame.media[1]!==0))
        throw Error('media changed');
    }
    if (!stream.finished) throw Error('terminal not finalized');
  });
  try {await saved.nextOutput();throw Error('closed stream remained usable');}
  catch(error) {if (!(error instanceof TypeError)) throw error;}
  return {output:frames.join(',')};
});""",
    )
    async with running(app) as peer:
        await peer.invoke(allowed_targets=(target,))
        request, body = await peer.receive()
        assert request["kind"] == "delegate" and body == b'{ "input" : "exact child" }'
        reader, writer = await start(peer, request, mode)
        if mode != "server_streaming":
            for sequence, kind in enumerate(("started", "chunk", "completed")):
                frame, media = await input_packet(reader)
                assert frame["kind"] == kind and frame["sequence"] == sequence
                assert media == (b"\x00\xff" if kind == "chunk" else b"")
        sequence = 1
        if mode != "client_streaming":
            await output(writer, "chunk", sequence, media=b"\xff\x00")
            sequence += 1
        await output(writer, "completed", sequence)
        await peer.health()
        await end(writer)
        await peer.health()
        await finished(peer, request)
        frame, body = await asyncio.wait_for(peer.receive(), 2)
        assert frame["error"] is None and json.loads(body) == {
            "output": "completed" if mode == "client_streaming" else "chunk,completed"
        }


async def test_node_unawaited_child_is_retired_and_late_reply_correlated(
    tmp_path: Path,
) -> None:
    """Returning from a handler cannot leave a dropped Promise with fresh authority."""
    source, target = endpoint()
    app = NodeApplication(
        tmp_path,
        source
        + """const app = new CapabilityApp();
app.unary('example', async call => {
  if(call.payload.input==='first') {
    void call.call(endpoint,{input:'dropped'});
    return {output:'returned before child'};
  }
  return {output:'independent'};
});""",
    )
    async with running(app, concurrency=2) as peer:
        await peer.invoke(payload=b'{"input":"first"}', allowed_targets=(target,))
        child, _ = await peer.receive()
        frame, _ = await peer.receive()
        assert child["kind"] == "delegate" and frame["error"] == "cancelled"
        await reply(peer, child)
        await peer.health()
        await peer.invoke(call_id="independent")
        frame, body = await peer.receive()
        assert frame["error"] is None and json.loads(body) == {"output": "independent"}


async def test_node_stream_refusal_does_not_create_media_or_consume_parent(
    tmp_path: Path,
) -> None:
    """A definite pre-start refusal preserves the next admitted child sequence."""
    source, target = endpoint("server_streaming")
    app = NodeApplication(
        tmp_path,
        source
        + """const app = new CapabilityApp();
app.unary('example', async call => {
  try {await call.stream(endpoint,{input:'first'},async () => {
    throw Error('refusal opened stream');
  });}
  catch(error) {
    if (!(error instanceof CapabilityCallError) || error.code!=='denied') throw error;
  }
  await call.stream(endpoint,{input:'second'},async stream => {
    for await (const frame of stream) {}
  });
  return {output:'second'};
});""",
    )
    async with running(app) as peer:
        await peer.invoke(allowed_targets=(target,))
        request, _ = await peer.receive()
        await reply(peer, request, error="denied")
        second, _ = await peer.receive()
        assert _OBJECT.validate_python(second["request"])["request_id"] == f"{2:064x}"
        _, writer = await start(peer, second, "server_streaming")
        await output(writer, "completed", 1)
        await end(writer)
        await finished(peer, second)
        frame, body = await peer.receive()
        assert frame["error"] is None and json.loads(body) == {"output": "second"}


async def test_node_cancelled_child_keeps_late_correlation_and_other_root_live(
    tmp_path: Path,
) -> None:
    """Repeated cancellation preserves correlation and another parent's custody."""
    source, target = endpoint()
    app = NodeApplication(
        tmp_path,
        source
        + """const app = new CapabilityApp();
app.unary('example', async call =>
  (await call.call(endpoint,{input:call.callId})).payload);""",
    )
    async with running(app, concurrency=2) as peer:
        await peer.invoke(allowed_targets=(target,))
        first, _ = await peer.receive()
        await peer.send("cancel", call_id="call-1")
        frame, body = await peer.receive()
        assert frame["error"] == "cancelled" and body == b""
        await peer.send("cancel", call_id="call-1")
        await peer.health()
        await peer.invoke("call-2", allowed_targets=(target,))
        second, _ = await peer.receive()
        await reply(peer, first)
        await peer.health()
        await reply(peer, second, payload=b'{"output":"second"}')
        frame, body = await peer.receive()
        assert frame["call_id"] == "call-2" and frame["error"] is None
        assert json.loads(body) == {"output": "second"}
