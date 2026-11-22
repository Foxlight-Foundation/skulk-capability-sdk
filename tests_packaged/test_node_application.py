"""Actual installed asset wheels run on the explicitly selected Node interpreter.

Run after tools/prepare_node_qualification.py, with SKULK_TYPESCRIPT_ENTRY pointing
at the independently installed npm package. This is not signed owner admission.
"""

import asyncio
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import JsonValue, TypeAdapter
from skulk_capability_tools.native_publishing import NativeManifest
from skulk_capability_tools.node_application import build_node_project

from skulk_capability_sdk.node_application import NODE_LAUNCHER
from skulk_capability_sdk.node_runtime import select_node
from tests.test_credential_reader import installation, replace
from tests_native.test_application import Mode, document, header, native, running
from tests_native.test_node_packaging import project

_OBJECT = TypeAdapter(dict[str, JsonValue])
_VARIANTS: tuple[tuple[str, Mode], ...] = (
    ("1.0.0", "unary"),
    ("1.0.1", "server_streaming"),
    ("1.0.2", "client_streaming"),
    ("1.0.3", "bidirectional"),
)
_SOURCE = """import {CapabilityApp,OutputFrame} from '@foxlight/skulk-capability-sdk';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {fileURLToPath} from 'node:url';
const execute=promisify(execFile);
export default runtime => {
  let reader;
  const app = new CapabilityApp({credentialHelper:runtime.credentialHelper,
    async setup(startup) {reader=startup.credentials;}});
  app.unary('example', async call => {
    if (call.payload.helper) {
      const file=fileURLToPath(new URL('./helper', import.meta.url));
      const {stdout}=await execute(file, [], {signal:call.signal});
      return {output:stdout};
    }
    if (call.payload.cancel && !call.signal.aborted) await new Promise(resolve =>
      call.signal.addEventListener('abort', resolve, {once:true}));
    if (call.payload.credential) {
      const value = await reader.read('api-token', {signal:call.signal});
      const bytes=value.getSecretValue();
      const matched=new TextDecoder().decode(bytes)==='synthetic selected secret';
      bytes.fill(0); return {output:'hello',matched};
    }
    return {output:call.payload.input,pid:process.pid,version:process.version,
      caller:call.context.export().caller_id};
  }, {version:'1.0.0'});
  for (const version of ['1.0.1','1.0.2','1.0.3']) {
    app.stream('example', async function* (call, inputs) {
      if (version !== '1.0.1') {
        const frames=[]; for await (const frame of inputs) frames.push(frame);
        if (frames.map(f=>f.projection.kind).join(',')!=='started,chunk,completed'
          || Array.from(frames[1].media).join(',')!=='0,255,1') throw Error('input');
      }
      if (version !== '1.0.2') yield new OutputFrame('chunk', {output:'hello'}, {
        media:new Uint8Array([0,255,2]),
        mediaMetadata:{kind:'inline',media_type:'audio/pcm'}});
      yield new OutputFrame('completed', {output:'hello'});
    }, {version});
  }
  return app;
};
"""


@pytest.fixture(scope="module")
def bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build and install real staged npm assets once in the selected test venv."""
    select_node(Path(sys.prefix))
    entry = Path(os.environ["SKULK_TYPESCRIPT_ENTRY"])
    assert entry.is_absolute() and entry.is_file()
    source = project(tmp_path_factory.mktemp("node-project") / "project")
    shutil.rmtree(source / "stage/node_modules")
    modules = entry.parents[3]
    assert modules.name == "node_modules"
    shutil.copytree(modules, source / "stage/node_modules", symlinks=True)
    assert (source / "stage" / NODE_LAUNCHER).is_file()
    (source / "stage/app.mjs").write_text(_SOURCE)
    helper = source / "stage/helper"
    helper.write_text("#!/bin/sh\nprintf 'hello from helper'\n")
    helper.chmod(0o755)
    selected_output = os.environ.get("SKULK_NODE_QUALIFICATION_OUTPUT")
    output = Path(selected_output) if selected_output else source.parent / "build"
    report = build_node_project(source, output)
    assert isinstance(report.manifest, NativeManifest)
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "--no-deps",
            "--no-index",
            "--reinstall",
            str(output / report.assets.filename),
        ],
        check=True,
    )
    return output / "bundle.pyz"


class PackagedApplication:
    """Keep ownership of one real zipapp/Node process through every terminal path."""

    def __init__(self, bundle: Path) -> None:
        self.bundle = bundle
        self.process: asyncio.subprocess.Process | None = None

    async def serve(self, configuration_descriptor: int) -> None:
        """Use the normal Python zipapp argv and preserve the owning process PID."""

        async def spawn() -> None:
            self.process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-B",
                str(self.bundle),
                "--config-fd",
                str(configuration_descriptor),
                pass_fds=(configuration_descriptor,),
                start_new_session=True,
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
            if self.process is not None:
                if self.process.returncode is None:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    await self.process.wait()
                else:
                    with pytest.raises(ProcessLookupError):
                        os.killpg(self.process.pid, 0)


async def test_packaged_node_exact_runtime_caller_restart_and_shutdown(
    bundle: Path,
) -> None:
    """The selected interpreter replaces Python and leaves no descendants twice."""
    for _ in range(2):
        app = PackagedApplication(bundle)
        async with running(app, variants=_VARIANTS, startup_seconds=30) as peer:
            await peer.invoke(caller_id="client:original")
            frame, body = await peer.receive()
            assert frame["error"] is None
            assert app.process is not None
            assert _OBJECT.validate_json(body) == {
                "output": "hello",
                "pid": app.process.pid,
                "version": "v22.23.5",
                "caller": "client:original",
            }
            await peer.health()


@pytest.mark.parametrize("version", ["1.0.1", "1.0.2", "1.0.3"])
async def test_packaged_node_media_and_halfclose(bundle: Path, version: str) -> None:
    """All three streaming modes traverse real native binary media sockets."""
    app = PackagedApplication(bundle)
    async with running(app, variants=_VARIANTS, startup_seconds=30) as peer:
        await peer.invoke(version=version)
        reader, writer = await asyncio.wait_for(peer.streams.get(), 3)
        hello = _OBJECT.validate_json(await header(reader))
        assert hello["protocol"] == 6 and hello["call_id"] == "call-1"
        if version != "1.0.1":
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
            # Input completion is the admitted terminal frame. Closing this
            # shared media socket would also withdraw provider output.
        if version != "1.0.2":
            raw = await header(reader)
            frame = _OBJECT.validate_json(raw)
            assert _OBJECT.validate_python(frame["frame"])["kind"] == "chunk"
            media_size = native.transport_v2_media_size(raw)
            assert await reader.readexactly(media_size) == b"\x00\xff\x02"
        terminal = _OBJECT.validate_json(await header(reader))
        end = _OBJECT.validate_json(await header(reader))
        assert _OBJECT.validate_python(terminal["frame"])["kind"] == "completed"
        assert _OBJECT.validate_python(end["frame"])["kind"] == "end"
        writer.close()
        await writer.wait_closed()
        frame, _ = await peer.receive()
        assert frame["error"] is None
        await peer.health()


async def test_packaged_node_cancellation_leaves_control_healthy(bundle: Path) -> None:
    """Cancelling active native work permits the next call before normal shutdown."""
    app = PackagedApplication(bundle)
    async with running(app, variants=_VARIANTS, startup_seconds=30) as peer:
        await peer.invoke(payload=b'{"input":"hello","cancel":true}')
        await peer.send("cancel", call_id="call-1")
        frame, _ = await peer.receive()
        assert frame["error"] is not None
        await peer.health()
        await peer.invoke("call-2")
        frame, body = await peer.receive()
        assert (
            frame["error"] is None and _OBJECT.validate_json(body)["output"] == "hello"
        )


async def test_packaged_node_selected_python_credential_reader(
    bundle: Path, tmp_path: Path
) -> None:
    """Node uses the selected Python helper without exposing protected values."""
    _, selection = installation(tmp_path / "store")
    replace(selection, b"synthetic selected secret")
    app = PackagedApplication(bundle)
    async with running(
        app,
        variants=_VARIANTS,
        startup_seconds=30,
        credential_inputs=[
            {
                "credential_id": "api-token",
                "required": True,
                "title": "Token",
                "description": "Synthetic token",
            }
        ],
        credential_config=str(tmp_path / "store/selection.json"),
    ) as peer:
        await peer.invoke(payload=b'{"input":"hello","credential":true}')
        frame, body = await peer.receive()
        assert frame["error"] is None
        assert _OBJECT.validate_json(body) == {"output": "hello", "matched": True}


async def test_packaged_node_executes_its_staged_helper(bundle: Path) -> None:
    """A staged executable remains executable after real wheel installation."""
    app = PackagedApplication(bundle)
    async with running(app, variants=_VARIANTS, startup_seconds=30) as peer:
        await peer.invoke(payload=b'{"input":"hello","helper":true}')
        frame, body = await peer.receive()
        assert frame["error"] is None
        assert _OBJECT.validate_json(body) == {"output": "hello from helper"}
        await peer.health()
