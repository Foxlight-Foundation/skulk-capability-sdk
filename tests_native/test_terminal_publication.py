"""Exercise host-visible terminal publication before local send settlement."""

import asyncio
import json

import pytest
from pydantic import JsonValue

from skulk_capability_sdk.application import CapabilityApp, Invocation
from tests_native.test_application import native, running


async def test_reuses_slot_after_observable_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real terminal bytes permit reuse without abandoning their send custody."""
    original = native.open_call_v2_control
    release = asyncio.Event()

    class DelayedControl:
        """Delay completion, while retaining the real native framing and socket."""

        def __init__(self, control: native.NativeCallControlV2) -> None:
            self.control = control

        async def receive(self) -> tuple[bytes, bytes]:
            """Unblock the prior send only after the next invoke becomes visible."""
            packet = await self.control.receive()
            request = json.loads(packet[0])
            if request["kind"] == "invoke" and request["call_id"] == "second":
                asyncio.get_running_loop().call_soon(release.set)
            return packet

        async def send(self, metadata: str, payload: bytes) -> None:
            """Publish genuine bytes before waiting for the controlled interleave."""
            await self.control.send(metadata, payload)
            request = json.loads(metadata)
            if request["kind"] == "result" and request["call_id"] == "first":
                await release.wait()

        async def close(self) -> None:
            """Close only the actual acquired native channel."""
            await self.control.close()

    async def open_control(
        startup: native.ManagedV2Contract, seconds: float
    ) -> DelayedControl:
        """Wrap the actual native channel without replacing validation or IPC."""
        return DelayedControl(await original(startup, seconds))

    monkeypatch.setattr(native, "open_call_v2_control", open_control)
    app = CapabilityApp()

    async def handler(call: Invocation) -> dict[str, JsonValue]:
        return {"id": call.call_id}

    app.unary("example", handler)
    async with running(app) as peer:
        await peer.invoke("first")
        first, body = await asyncio.wait_for(peer.receive(), 3)
        assert first["error"] is None and json.loads(body) == {"id": "first"}
        await peer.invoke("second")
        second, body = await asyncio.wait_for(peer.receive(), 3)
        assert second["error"] is None and json.loads(body) == {"id": "second"}
        await peer.health()
