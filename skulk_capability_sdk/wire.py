"""Bounded length-prefixed local IPC with strict frame validation."""

import asyncio
import json
import struct

from .contracts import MAX_FRAME_BYTES, WIRE, WireMessage, canonical


class ProtocolError(Exception):
    """The child violated the authenticated private framing contract."""


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ProtocolError("non-finite JSON value")


async def read_message(reader: asyncio.StreamReader) -> WireMessage:
    """Read one capped frame; caller owns the encompassing deadline."""
    length = struct.unpack("!I", await reader.readexactly(4))[0]
    if length < 2 or length > MAX_FRAME_BYTES:
        raise ProtocolError("frame exceeds contract bound")
    data = await reader.readexactly(length)
    # The second parser prevents last-key-wins disagreement between runtimes.
    try:
        json.loads(
            data, object_pairs_hook=_unique_pairs, parse_constant=_reject_constant
        )
    except RecursionError as error:
        raise ProtocolError("JSON nesting exceeds parser bounds") from error
    return WIRE.validate_json(data, strict=True)


async def write_message(writer: asyncio.StreamWriter, message: WireMessage) -> None:
    """Write one capped frame and await backpressure within caller's deadline."""
    data = canonical(message)
    if len(data) > MAX_FRAME_BYTES:
        raise ProtocolError("frame exceeds contract bound")
    writer.write(struct.pack("!I", len(data)) + data)
    await writer.drain()
