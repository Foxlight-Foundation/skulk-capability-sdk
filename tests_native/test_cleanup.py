"""Cancellation arriving during natural cleanup must not abandon owned resources."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

import pytest
from pydantic import JsonValue
from test_application import header, running

from skulk_capability_sdk.application import (
    ApplicationStartup,
    CapabilityApp,
    InputFrame,
    Invocation,
    OutputFrame,
)


@pytest.mark.parametrize("kind", ["unary_resource", "terminal", "schema_refusal"])
@pytest.mark.parametrize("cause", ["cancel", "timeout"])
async def test_cancellation_during_natural_cleanup(kind: str, cause: str) -> None:
    """A terminal/refusal/return transfers cleanup ownership before cancellation."""
    cleaning, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def resource() -> AsyncGenerator[None]:
        try:
            yield
        finally:
            cleaning.set()
            await release.wait()
            cleaned.set()

    async def unary(call: Invocation) -> dict[str, JsonValue]:
        await call.enter_resource(resource())
        return {"ok": True}

    async def stream(
        _call: Invocation, _inputs: AsyncIterator[InputFrame]
    ) -> AsyncGenerator[OutputFrame]:
        try:
            yield (
                OutputFrame("completed", {"output": "hello"})
                if kind == "terminal"
                else OutputFrame("chunk", {"wrong": True})
            )
        finally:
            cleaning.set()
            await release.wait()
            cleaned.set()

    app = CapabilityApp()
    if kind == "unary_resource":
        app.unary("example", unary)
    else:
        app.stream("example", stream)
    async with running(
        app, "unary" if kind == "unary_resource" else "server_streaming"
    ) as peer:
        await peer.invoke(seconds=0.1 if cause == "timeout" else None)
        if kind != "unary_resource":
            reader, _writer = await asyncio.wait_for(peer.streams.get(), 3)
            await header(reader)
        await asyncio.wait_for(cleaning.wait(), 2)
        if cause == "cancel":
            await peer.send("cancel", call_id="call-1")
        else:
            await asyncio.sleep(0.15)
        await peer.health()
        assert not cleaned.is_set()
        release.set()
        value, body = await peer.receive()
        assert value["error"] == ("cancelled" if cause == "cancel" else "timeout")
        assert not body and cleaned.is_set()


async def test_cancellation_during_natural_application_shutdown() -> None:
    """Outer cancellation cannot interrupt setup teardown already begun by shutdown."""
    cleaning, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def setup(_startup: ApplicationStartup) -> AsyncGenerator[None]:
        try:
            yield
        finally:
            cleaning.set()
            await release.wait()
            cleaned.set()

    async def handler(_call: Invocation) -> dict[str, JsonValue]:
        return {}

    app = CapabilityApp(setup=setup)
    app.unary("example", handler)
    async with running(app) as peer:
        await peer.send("shutdown")
        await cleaning.wait()
        peer.app_task.cancel()
        peer.app_task.cancel()
        await asyncio.sleep(0)
        assert not peer.app_task.done() and not cleaned.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await peer.app_task
        assert cleaned.is_set()
