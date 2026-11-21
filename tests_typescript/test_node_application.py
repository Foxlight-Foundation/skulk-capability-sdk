"""Public Node SDK behavior through real native control and media sockets."""

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Literal

import pytest
from pydantic import JsonValue, TypeAdapter

from tests_native.test_application import document, header, native, running

_OBJECT = TypeAdapter(dict[str, JsonValue])


class NodeApplication:
    """Acquire, own and reap only this fixture's genuine Node application process."""

    def __init__(self, path: Path, source: str) -> None:
        self.script = path / "application.mjs"
        package = Path(
            os.environ.get(
                "SKULK_TYPESCRIPT_ENTRY",
                str(Path(__file__).parents[1] / "packages/typescript/dist/index.js"),
            )
        ).resolve()
        self.script.write_text(
            "import {CapabilityApp,OutputFrame,CapabilityEndpoint,CapabilityCallError}"
            f" from {json.dumps(package.as_uri())};\n"
            + source
            + "\nawait app.serve(Number(process.argv.at(-1)));\n"
        )
        self.process: asyncio.subprocess.Process | None = None

    async def serve(self, configuration_descriptor: int) -> None:
        """Keep the acquired process through cancellation and close the parent FD."""
        node = shutil.which("node")
        assert node is not None

        async def spawn() -> None:
            self.process = await asyncio.create_subprocess_exec(
                node,
                str(self.script),
                str(configuration_descriptor),
                pass_fds=(configuration_descriptor,),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            )

        acquisition = asyncio.create_task(spawn())
        try:
            await asyncio.shield(acquisition)
            assert self.process is not None
            _, error = await self.process.communicate()
            assert self.process.returncode == 0, error.decode()
        finally:
            await acquisition
            os.close(configuration_descriptor)
            if self.process is not None and self.process.returncode is None:
                self.process.kill()
                await self.process.wait()


async def test_node_unary_preserves_exact_bytes_original_caller_and_health(
    tmp_path: Path,
) -> None:
    """A different language uses the actual startup and immutable native caller."""
    app = NodeApplication(
        tmp_path,
        """const app = new CapabilityApp();
app.unary('example', async call => ({
  raw: new TextDecoder().decode(call.rawPayload),
  caller: call.context.export().caller_id,
  version: call.version,
}));""",
    )
    async with running(
        app, input_schema={"type": "object", "required": ["input"]}
    ) as peer:
        await peer.invoke(payload=b'{ "input" : "hello" }', caller_id="client:original")
        frame, body = await peer.receive()
        assert frame["error"] is None
        assert json.loads(body) == {
            "raw": '{ "input" : "hello" }',
            "caller": "client:original",
            "version": "1.0.0",
        }
        await peer.health()


@pytest.mark.parametrize(
    "mode", ["server_streaming", "client_streaming", "bidirectional"]
)
async def test_node_streams_preserve_binary_media_and_halfclose(
    tmp_path: Path,
    mode: Literal["server_streaming", "client_streaming", "bidirectional"],
) -> None:
    """The issued Node addon owns actual separate media and terminal/end frames."""
    app = NodeApplication(
        tmp_path,
        """const app = new CapabilityApp();
app.stream('example', async function* (call, inputs) {
  if (MODE !== 'server_streaming') {
    const frames = []; for await (const input of inputs) frames.push(input);
    if (frames.map(f => f.projection.kind).join(',') !== 'started,chunk,completed'
      || Array.from(frames[1].media).join(',') !== '0,255,1')
        throw Error('input mismatch');
  }
  if (MODE !== 'client_streaming') yield new OutputFrame('chunk', {output:'hello'}, {
    media:new Uint8Array([0,255,2]),mediaMetadata:{kind:'inline',media_type:'audio/pcm'}
  });
  yield new OutputFrame('completed', {output:'hello'});
});""".replace("MODE", json.dumps(mode)),
    )
    async with running(app, mode) as peer:
        await peer.invoke()
        reader, writer = await asyncio.wait_for(peer.streams.get(), 3)
        hello = _OBJECT.validate_json(await header(reader))
        assert hello["protocol"] == 6 and hello["call_id"] == "call-1"
        if mode != "server_streaming":
            for sequence, kind in enumerate(("started", "chunk", "completed")):
                projection: dict[str, JsonValue] = {
                    "call_id": "call-1",
                    "direction": "caller_to_provider",
                    "sequence": sequence,
                    "kind": kind,
                }
                media = b""
                if kind == "chunk":
                    projection.update(
                        payload={"input": "audio"},
                        media={"kind": "inline", "media_type": "audio/pcm"},
                    )
                    media = b"\x00\xff\x01"
                writer.write(
                    native.encode_transport_v2_packet(
                        document(projection).decode(), media
                    )
                )
            await writer.drain()
        if mode != "client_streaming":
            raw = await header(reader)
            chunk = _OBJECT.validate_json(raw)
            assert _OBJECT.validate_python(chunk["frame"])["kind"] == "chunk"
            size = native.transport_v2_media_size(raw)
            assert await reader.readexactly(size) == b"\x00\xff\x02"
        terminal = _OBJECT.validate_json(await header(reader))
        end = _OBJECT.validate_json(await header(reader))
        assert _OBJECT.validate_python(terminal["frame"])["kind"] == "completed"
        assert _OBJECT.validate_python(end["frame"])["kind"] == "end"
        frame, body = await peer.receive()
        assert frame["error"] is None and json.loads(body) == {}
        await peer.health()


async def test_node_cancel_waits_for_cleanup_while_health_remains_live(
    tmp_path: Path,
) -> None:
    """Repeated cancellation cannot release resources or interrupt their cleanup."""
    cleaning, release = tmp_path / "cleaning", tmp_path / "release"
    app = NodeApplication(
        tmp_path,
        """import {writeFile,access} from 'node:fs/promises';
import {setTimeout as delay} from 'node:timers/promises';
const app = new CapabilityApp();
app.unary('example', async call => {
  if (call.callId === 'second') return {next:true};
  await call.enterResource(async () => ({async close() {
    await writeFile(CLEANING,'');
    for (let i=0;i<400;i++) {
      try {await access(RELEASE);return;}catch {await delay(10);}
    }
    throw Error('cleanup release missing');
  }}));
  if (!call.signal.aborted) await new Promise(resolve =>
    call.signal.addEventListener('abort',resolve,{once:true}));
  return {};
});""".replace("CLEANING", json.dumps(str(cleaning))).replace(
            "RELEASE", json.dumps(str(release))
        ),
    )
    async with running(app) as peer:
        await peer.invoke()
        await peer.send("cancel", call_id="call-1")
        for _ in range(300):
            if cleaning.exists():
                break
            await asyncio.sleep(0.01)
        assert cleaning.exists()
        await peer.send("cancel", call_id="call-1")
        await peer.health()
        release.write_bytes(b"")
        frame, body = await peer.receive()
        assert frame["error"] == "cancelled" and body == b""
        await peer.invoke("second")
        frame, body = await peer.receive()
        assert frame["error"] is None and json.loads(body) == {"next": True}


@pytest.mark.parametrize("fault", ["revision", "version", "schema"])
async def test_node_bad_binding_or_schema_never_runs_behavior(
    tmp_path: Path, fault: str
) -> None:
    """Native identity/schema checks precede JavaScript application effects."""
    app = NodeApplication(
        tmp_path,
        """const app = new CapabilityApp();
app.unary('example', async () => { throw new Error('handler must not run'); });""",
    )
    async with running(
        app, input_schema={"type": "object", "required": ["input"]}
    ) as peer:
        await peer.invoke(
            wrong_revision=fault == "revision",
            wrong_version=fault == "version",
            payload=b"{}" if fault == "schema" else b'{"input":"hello"}',
        )
        frame, body = await peer.receive()
        assert frame["error"] == "invalid_payload" and body == b""
        await peer.health()


async def test_node_uncapped_inference_remains_live_after_thirty_seconds(
    tmp_path: Path,
) -> None:
    """Health stays responsive and no generic thirty-second cap appears in Node."""
    app = NodeApplication(
        tmp_path,
        """import {setTimeout as delay} from 'node:timers/promises';
const app = new CapabilityApp();
app.unary('example', async () => {await delay(31000);return {output:'uncapped'};});""",
    )
    async with running(app) as peer:
        await peer.invoke()
        await peer.health()
        # Match the ordinary owner's health cadence; the control socket has a
        # liveness bound independent of the uncapped inference lifetime.
        for _ in range(30):
            await asyncio.sleep(1)
            await peer.health()
        await peer.health()
        frame, body = await asyncio.wait_for(peer.receive(), 3)
        assert frame["error"] is None and json.loads(body) == {"output": "uncapped"}
