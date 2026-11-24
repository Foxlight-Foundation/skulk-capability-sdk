"""Terminal bytes may reach the host before the native send promise settles."""

import asyncio
import json
import os
from pathlib import Path

from tests_native.test_application import running
from tests_typescript.test_node_application import NodeApplication


async def test_node_reuses_slot_after_observable_terminal(tmp_path: Path) -> None:
    """A real peer can immediately reuse capacity after observing cleaned output."""
    package = Path(
        os.environ.get(
            "SKULK_TYPESCRIPT_ENTRY",
            str(Path(__file__).parents[1] / "packages/typescript/dist/index.js"),
        )
    ).resolve()
    source = """import {native} from NATIVE;
const open = native.openCallV2Control;
native.openCallV2Control = async (...args) => {
  const control = await open(...args);
  let release;
  return {
    async receive() {
      const packet = await control.receive();
      const request = JSON.parse(packet.metadata.toString());
      if (request.kind === 'invoke' && request.call_id === 'second') {
        if (release === undefined) throw Error('terminal was not published');
        setImmediate(release);
      }
      return packet;
    },
    async send(metadata, payload) {
      const request = JSON.parse(metadata);
      if (request.kind === 'result' && request.call_id === 'first') {
        const observed = new Promise(resolve => {release = resolve;});
        await control.send(metadata, payload);
        await observed;
      } else {
        await control.send(metadata, payload);
      }
    },
    close: () => control.close(),
  };
};
const app = new CapabilityApp();
app.unary('example', async call => ({id:call.callId}));
""".replace("NATIVE", json.dumps((package.parent / "native.js").as_uri()))
    app = NodeApplication(tmp_path, source)
    async with running(app) as peer:
        await peer.invoke("first")
        first, body = await asyncio.wait_for(peer.receive(), 3)
        assert first["error"] is None and json.loads(body) == {"id": "first"}
        await peer.invoke("second")
        second, body = await asyncio.wait_for(peer.receive(), 3)
        assert second["error"] is None and json.loads(body) == {"id": "second"}
        await peer.health()
