"""Actual public invocation lifecycle and optional private metadata framing."""

import asyncio
import importlib
import importlib.metadata
import json
from typing import Protocol, cast

import pytest
from pydantic import JsonValue, TypeAdapter
from test_application import document, header, running

from skulk_capability_sdk.application import CapabilityApp, Invocation
from skulk_capability_sdk.discovery import DiscoveryError
from tools.check_discovery_profile import Native


class DiscoveryNative(Native, Protocol):
    """Actual separately built codec; this peer supplies no production authority."""

    def discovery_socket(self, startup: str) -> str: ...


@pytest.mark.parametrize("fault", ["none", "denied", "corrupt", "trailing"])
@pytest.mark.requires_discovery_v1
async def test_scoped_discovery_owns_exact_parent_and_one_complete_response(
    fault: str,
) -> None:
    """The real SDK app authenticates replies and never emits caller replacements."""
    assert importlib.metadata.version("skulk-capability-kernel") == "0.7.0rc7"
    native = cast(DiscoveryNative, importlib.import_module("_skulk_capability_kernel"))
    app = CapabilityApp()
    observed: list[dict[str, JsonValue]] = []
    owned: set[asyncio.Task[None]] = set()
    outcomes: list[str] = []

    async def handler(call: Invocation) -> dict[str, JsonValue]:
        assert call.context.export()["caller_id"] == "client:synthetic-private"
        try:
            directory = await call.discover()
            assert directory.offers == ()
            outcomes.append("complete")
            # A fresh query uses its own increasing sequence, without a child
            # effect or an ambient root credential introduced by the app.
            directory = await call.discover()
            assert directory.offers == ()
        except DiscoveryError as error:
            outcomes.append(error.code)
        return {"ok": True}

    app.unary("example", handler)
    async with running(app) as peer:
        startup = document(peer.startup).decode()

        async def serve(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                metadata = await header(reader)
                assert await reader.read(1) == b""
                query = TypeAdapter(dict[str, JsonValue]).validate_json(
                    native.admit_discovery_request(
                        startup, len(metadata).to_bytes(4, "big") + metadata
                    )
                )
                assert set(query) == {
                    "parent_call_id",
                    "parent_scope_id",
                    "parent_request_sha256",
                    "request_id",
                }
                assert query["parent_call_id"] == "c" * 64
                observed.append(query)
                response = native.discovery_reply(
                    startup,
                    metadata.decode(),
                    b"" if fault == "denied" else b"[]",
                    "denied" if fault == "denied" else None,
                )
                if fault == "corrupt":
                    response = response[:-1] + b"}"
                if fault == "trailing":
                    response += b"x"
                writer.write(response)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            task = asyncio.create_task(serve(reader, writer))
            owned.add(task)

        server = await asyncio.start_unix_server(
            accept, native.discovery_socket(startup)
        )
        try:
            context = await peer.invoke("c" * 64)
            async with asyncio.timeout(5):
                if fault in ("corrupt", "trailing"):
                    # An interrupted/invalid observation conservatively retires
                    # its source parent; no successful result may cross it.
                    value, _ = await peer.receive()
                    assert value["error"] is not None
                else:
                    value, body = await peer.receive()
                    assert value["error"] is None and json.loads(body) == {"ok": True}
            assert outcomes == [
                {"none": "complete", "denied": "denied"}.get(fault, "invalid_directory")
            ]
            for query in observed:
                assert query["parent_scope_id"] == context["scope_id"]
                assert query["parent_request_sha256"] == context["request_sha256"]
            assert [int(str(q["request_id"]), 16) for q in observed] == (
                [1, 2] if fault == "none" else [1]
            )
        finally:
            server.close()
            server.close_clients()
            await server.wait_closed()
            await asyncio.gather(*owned)


@pytest.mark.pre_discovery_v1
async def test_old_native_cohort_reports_unsupported_before_socket_access() -> None:
    """Existing applications keep their issued ABI and cannot guess another endpoint."""
    assert importlib.metadata.version("skulk-capability-kernel") != "0.7.0rc7"
    app = CapabilityApp()

    async def handler(call: Invocation) -> dict[str, JsonValue]:
        with pytest.raises(DiscoveryError) as error:
            await call.discover()
        assert error.value.code == "unsupported"
        return {"ok": True}

    app.unary("example", handler)
    async with running(app) as peer:
        await peer.invoke("c" * 64)
        value, body = await peer.receive()
        assert value["error"] is None and json.loads(body) == {"ok": True}
