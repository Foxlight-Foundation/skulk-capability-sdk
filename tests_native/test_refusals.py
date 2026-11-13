"""Refusal and cleanup boundaries of the public native application runtime."""

import asyncio
import os
import tempfile
from collections.abc import AsyncGenerator, AsyncIterator

import _skulk_capability_kernel as native  # pyright: ignore[reportMissingModuleSource]
import pytest
from test_application import document, header, running, startup_value

from skulk_capability_sdk.application import (
    CapabilityApp,
    InputFrame,
    Invocation,
    OutputFrame,
)


async def test_invalid_startup_permissions_close_consumed_descriptor() -> None:
    """A caller cannot use a public file to supply private startup material."""
    app = CapabilityApp()
    with tempfile.TemporaryFile() as source:
        os.fchmod(source.fileno(), 0o644)
        descriptor = os.dup(source.fileno())
        with pytest.raises(ValueError, match="private regular"):
            await app.serve(descriptor)
        with pytest.raises(OSError):
            os.fstat(descriptor)


async def test_wrong_registration_refuses_before_connect() -> None:
    """Only exact signed handlers may enter the app setup/control lifecycle."""
    app = CapabilityApp()
    value = startup_value(
        "/tmp/synthetic-no-listener", "/tmp/synthetic-no-stream", "unary"
    )
    record = native.ManagedV2Contract.from_json("Startup", document(value).decode())
    with tempfile.TemporaryFile() as source:
        source.write(record.canonical())
        source.seek(0)
        with pytest.raises(ValueError, match="registered handlers differ"):
            await app.serve(os.dup(source.fileno()))


@pytest.mark.parametrize(
    "fault", ["chunk_schema", "terminal_schema", "no_terminal", "backend"]
)
async def test_stream_failure_closes_iterator_before_control_result(fault: str) -> None:
    """Invalid output or backend failure cannot publish a successful terminal."""
    cleaned = asyncio.Event()

    async def handler(
        _call: Invocation, _inputs: AsyncIterator[InputFrame]
    ) -> AsyncGenerator[OutputFrame]:
        try:
            if fault == "backend":
                raise RuntimeError("synthetic-private-backend-data")
            if fault == "chunk_schema":
                yield OutputFrame("chunk", {"wrong": True})
            elif fault == "terminal_schema":
                yield OutputFrame("completed", {"wrong": True})
            else:
                yield OutputFrame("chunk", {"output": "hello"})
        finally:
            cleaned.set()

    app = CapabilityApp()
    app.stream("example", handler)
    async with running(app, "server_streaming") as peer:
        await peer.invoke()
        reader, _writer = await asyncio.wait_for(peer.streams.get(), 3)
        await header(reader)
        value, body = await asyncio.wait_for(peer.receive(), 3)
        assert value["error"] == (
            "provider_error" if fault == "backend" else "invalid_payload"
        )
        assert cleaned.is_set()
        assert "synthetic-private" not in document(value).decode()
        assert not body or body == b"{}"
