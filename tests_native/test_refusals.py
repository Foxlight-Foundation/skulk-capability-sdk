"""Refusal and cleanup boundaries of the public native application runtime."""

import asyncio
import os
import tempfile
from collections.abc import AsyncGenerator, AsyncIterator

import _skulk_capability_kernel as native  # pyright: ignore[reportMissingModuleSource]
import pytest
from pydantic import JsonValue, TypeAdapter
from test_application import document, header, running, startup_value

from skulk_capability_sdk.application import (
    CapabilityApp,
    InputFrame,
    Invocation,
    OutputFrame,
)

_OBJECT = TypeAdapter(dict[str, JsonValue])
_OBJECTS = TypeAdapter(list[dict[str, JsonValue]])


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


async def test_ambiguous_unversioned_handler_refuses_before_setup_or_connect() -> None:
    """An ID shorthand cannot guess which signed version an author intended."""
    entered = False

    async def handler(_call: Invocation) -> dict[str, JsonValue]:
        nonlocal entered
        entered = True
        return {}

    app = CapabilityApp()
    app.unary("example", handler)
    value = startup_value(
        "/tmp/no-control-listener", "/tmp/no-stream-listener", "unary"
    )
    manifest = _OBJECT.validate_python(value["manifest"])
    contracts = _OBJECT.validate_python(manifest["contracts"])
    descriptor = _OBJECTS.validate_python(contracts["descriptors"])[0]
    contracts["descriptors"] = [descriptor, {**descriptor, "version": "2.0.0"}]
    manifest["contracts"] = contracts
    value["manifest"] = manifest
    value["manifest_digest"] = native.ManagedV2Contract.from_json(
        "Manifest", document(manifest).decode()
    ).digest
    record = native.ManagedV2Contract.from_json("Startup", document(value).decode())
    with tempfile.TemporaryFile() as source:
        source.write(record.canonical())
        source.seek(0)
        with pytest.raises(ValueError, match="ambiguous or undeclared"):
            await app.serve(os.dup(source.fileno()))
    assert not entered


@pytest.mark.parametrize("schema", [False, {"type": "object", "required": ["output"]}])
async def test_null_terminal_obeys_result_schema_and_cleanup(
    schema: dict[str, JsonValue] | bool,
) -> None:
    """A missing/null completed payload still has to satisfy the signed schema."""
    cleaned = asyncio.Event()

    async def handler(
        _call: Invocation, _inputs: AsyncIterator[InputFrame]
    ) -> AsyncGenerator[OutputFrame]:
        try:
            yield OutputFrame("completed")
        finally:
            cleaned.set()

    app = CapabilityApp()
    app.stream("example", handler)
    async with running(app, "server_streaming", result_schema=schema) as peer:
        await peer.invoke()
        reader, _writer = await asyncio.wait_for(peer.streams.get(), 3)
        await header(reader)
        response, body = await peer.receive()
        assert response["error"] == "invalid_payload" and not body and cleaned.is_set()


async def test_explicit_null_result_schema_accepts_null_terminal() -> None:
    """Authors may deliberately declare a no-value result without bypassing schema."""

    async def handler(
        _call: Invocation, _inputs: AsyncIterator[InputFrame]
    ) -> AsyncGenerator[OutputFrame]:
        yield OutputFrame("completed")

    app = CapabilityApp()
    app.stream("example", handler)
    async with running(app, "server_streaming", result_schema={"type": "null"}) as peer:
        await peer.invoke()
        reader, _writer = await asyncio.wait_for(peer.streams.get(), 3)
        await header(reader)
        for kind in ("completed", "end"):
            assert (
                _OBJECT.validate_python(
                    _OBJECT.validate_json(await header(reader))["frame"]
                )["kind"]
                == kind
            )
        response, body = await peer.receive()
        assert response["error"] is None and body == b"{}"
