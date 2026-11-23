"""Four native I/O modes using only the public capability SDK."""

import argparse
import asyncio
from collections.abc import AsyncGenerator, AsyncIterator

from pydantic import JsonValue

from skulk_capability_sdk.application import (
    CapabilityApp,
    InputFrame,
    Invocation,
    OutputFrame,
)

MAXIMUM_TEXT_CHARACTERS = 4096


def _text(payload: JsonValue) -> str:
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        raise ValueError("Expected a text object")
    value = payload["text"]
    if not isinstance(value, str):
        raise ValueError("Expected text")
    return value


async def echo(call: Invocation) -> dict[str, JsonValue]:
    """Return text after normal native input and caller admission."""
    return {"text": _text(call.payload)}


async def stream(
    call: Invocation, inputs: AsyncIterator[InputFrame]
) -> AsyncGenerator[OutputFrame]:
    """Stream text or bounded caller input, retaining exact optional media."""
    if call.capability_id.endswith(".stream"):
        payload: dict[str, JsonValue] = {"text": _text(call.payload)}
        yield OutputFrame("chunk", payload)
        yield OutputFrame("completed", payload)
        return
    texts: list[str] = []
    characters = 0
    async for frame in inputs:
        if frame.projection["kind"] != "chunk":
            continue
        text = _text(frame.projection["payload"])
        characters += len(text)
        # The final output schema has the same bound. Refuse before retaining
        # an input sequence that could never produce a valid final result.
        if characters > MAXIMUM_TEXT_CHARACTERS:
            yield OutputFrame("failed", error="provider_error")
            return
        if text:
            texts.append(text)
        if call.capability_id.endswith(".duplex"):
            metadata = frame.projection.get("media")
            if metadata is not None and not isinstance(metadata, dict):
                raise ValueError("Expected native media metadata")
            yield OutputFrame(
                "chunk", {"text": text}, media=frame.media, media_metadata=metadata
            )
    yield OutputFrame("completed", {"text": "".join(texts)})


def application() -> CapabilityApp:
    """Register the four versioned handlers without manufacturing caller identity."""
    app = CapabilityApp()
    app.unary("foxlight.sdk-reference.echo", echo, version="1.0.0")
    for suffix in ("stream", "collect", "duplex"):
        app.stream("foxlight.sdk-reference." + suffix, stream, version="1.0.0")
    return app


def main() -> None:
    """Serve only the inherited managed startup descriptor."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-fd", type=int, required=True)
    asyncio.run(application().serve(parser.parse_args().config_fd))


if __name__ == "__main__":
    main()
