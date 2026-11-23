"""Own one optional private metadata exchange; never supply caller credentials."""

import asyncio
import importlib.metadata
import json
from collections.abc import Callable
from typing import Protocol, cast

from pydantic import JsonValue

from .discovery import CapabilityDirectory, DiscoveryError
from .runtime import finish_owned, native_module


class NativeDiscovery(Protocol):
    """Compiled framing only; actual parent authority belongs to the host."""

    def discovery_socket(self, startup: str) -> str: ...
    def discovery_request(
        self, startup: str, context: str, parent: str, sequence: int
    ) -> bytes: ...
    def discovery_payload_size(self, source: str) -> int: ...
    def decode_discovery_frame(self, packet: bytes) -> tuple[bytes, bytes]: ...
    def discovery_response(
        self, startup: str, request: str, packet: bytes
    ) -> tuple[str | None, bytes]: ...


async def query(
    startup: bytes,
    context: dict[str, JsonValue],
    parent: str,
    sequence: int,
    expires_at: float | None,
    check: Callable[[], None],
) -> CapabilityDirectory:
    """Use only the actual invocation startup, with no bearer or alternate path.

    The metadata resource lifetime is thirty seconds, attenuated by the parent.
    The caller owns cancellation and must join this query before completing.
    """
    if importlib.metadata.version("skulk-capability-kernel") != "0.7.0rc7":
        raise DiscoveryError("unsupported")
    native = cast(NativeDiscovery, native_module())
    source = startup.decode()
    packet = native.discovery_request(
        source, json.dumps(context, allow_nan=False), parent, sequence
    )
    request, _ = native.decode_discovery_frame(packet)
    writer: asyncio.StreamWriter | None = None
    deadline = asyncio.get_running_loop().time() + 30
    if expires_at is not None:
        deadline = min(deadline, expires_at)
    try:
        async with asyncio.timeout_at(deadline):
            check()
            reader, writer = await asyncio.open_unix_connection(
                native.discovery_socket(source), limit=8192
            )
            check()
            writer.write(packet)
            await writer.drain()
            writer.write_eof()
            prefix = await reader.readexactly(4)
            size = int.from_bytes(prefix, "big")
            if not 2 <= size <= 4096:
                raise DiscoveryError("invalid_directory")
            metadata = await reader.readexactly(size)
            body_size = native.discovery_payload_size(metadata.decode())
            body = await reader.readexactly(body_size)
            if await reader.read(1):
                raise DiscoveryError("invalid_directory")
            check()
            error, body = native.discovery_response(
                source, request.decode(), prefix + metadata + body
            )
            if error is not None:
                if error not in ("denied", "unavailable", "limit"):
                    raise DiscoveryError("invalid_directory")
                raise DiscoveryError(error)
            result = CapabilityDirectory.from_json(body)
            check()
            return result
    except FileNotFoundError:
        raise DiscoveryError("unsupported") from None
    except TimeoutError:
        raise DiscoveryError("timeout") from None
    except (OSError, asyncio.IncompleteReadError):
        raise DiscoveryError("unavailable") from None
    except (ValueError, UnicodeError):
        raise DiscoveryError("invalid_directory") from None
    finally:
        if writer is not None:
            writer.close()

            async def closed() -> None:
                assert writer is not None
                try:
                    async with asyncio.timeout(3):
                        await writer.wait_closed()
                except (OSError, TimeoutError):
                    pass  # The closed descriptor owns no further query writes.

            await finish_owned(asyncio.create_task(closed()))
