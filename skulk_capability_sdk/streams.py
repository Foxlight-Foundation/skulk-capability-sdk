"""Bounded duplex media framing and lifecycle validation for managed children.

Headers are finite JSON capped at 64 KiB; inline media follows as raw bytes,
capped at 1 MiB. A fresh authenticated connection belongs to one invocation.
The child emits ``StreamEnd`` only after its handler iterator has finished
cleanup; an output terminal alone does not release the owner's admission.
"""

import asyncio
import json
import struct
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import aclosing
from typing import Annotated, Literal, Self, cast, final

from pydantic import Field, JsonValue, TypeAdapter, model_validator

from .contracts import (
    MAX_FRAME_BYTES,
    Contract,
    Startup,
    StreamHello,
    StreamInvoke,
    manifest_digest,
)
from .wire import ProtocolError, write_message

MAX_MEDIA_BYTES = 1_048_576
"""Largest raw inline media attachment, matching the Fabric provider contract."""


@final
class StreamError(Contract):
    """Sanitized machine-readable terminal outcome."""

    code: Literal[
        "provider_error",
        "transport_error",
        "unreachable",
        "cancelled",
        "timeout",
        "invalid_frame",
    ]
    message: str = Field(max_length=2048)


@final
class InlineMedia(Contract):
    """Raw media plus format metadata; never encode the bytes as JSON."""

    kind: Literal["inline"] = "inline"
    data: bytes = Field(max_length=MAX_MEDIA_BYTES)
    media_type: str = Field(max_length=256)
    codec: str | None = None
    sample_rate: int | None = Field(default=None, gt=0)
    channels: int | None = Field(default=None, gt=0)
    timestamp_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)


@final
class BlobMedia(Contract):
    """Opaque immutable blob reference; resolution remains Fabric-owned."""

    kind: Literal["blob"] = "blob"
    blob_id: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_type: str


@final
class StreamFrame(Contract):
    """One sequenced input or output frame, aligned with Skulk's public contract."""

    call_id: str = Field(min_length=1, max_length=128)
    direction: Literal["caller_to_provider", "provider_to_caller"]
    sequence: int = Field(ge=0)
    kind: Literal["started", "chunk", "completed", "failed", "cancelled"]
    synthetic: Literal[False] = False
    payload: dict[str, JsonValue] | None = None
    media: Annotated[InlineMedia | BlobMedia, Field(discriminator="kind")] | None = None
    error: StreamError | None = None

    @model_validator(mode="after")
    def lifecycle_shape(self) -> Self:
        """Refuse malformed lifecycle frames before they reach the transport."""
        if self.sequence == 0 and self.kind != "started":
            raise ValueError("sequence zero must start a direction")
        if self.kind == "started":
            if self.sequence != 0 or any(
                item is not None for item in (self.payload, self.media, self.error)
            ):
                raise ValueError("started is an empty sequence-zero frame")
        elif self.kind == "chunk":
            if (self.payload is None and self.media is None) or self.error is not None:
                raise ValueError("chunk requires content and forbids an error")
        elif self.kind == "completed":
            if self.error is not None:
                raise ValueError("completed cannot carry an error")
        else:
            if self.error is None or (self.error.code == "cancelled") != (
                self.kind == "cancelled"
            ):
                raise ValueError("terminal error does not match its kind")
            if self.payload is not None or self.media is not None:
                raise ValueError("failure carries no content")
        return self

    @property
    def is_terminal(self) -> bool:
        """Whether this frame closes its own direction."""
        return self.kind in ("completed", "failed", "cancelled")


@final
class StreamEnd(Contract):
    """Child cleanup acknowledgment following exactly one output terminal."""

    kind: Literal["end"] = "end"
    call_id: str = Field(min_length=1, max_length=128)


type StreamPacket = StreamFrame | StreamEnd
STREAM_PACKET: TypeAdapter[StreamPacket] = TypeAdapter(StreamPacket)
"""Language-neutral schema adapter for framed lifecycle packets."""


@final
class _Header(Contract):
    protocol: Literal[4] = 4
    frame: dict[str, JsonValue]
    media_size: int = Field(ge=0, le=MAX_MEDIA_BYTES)


async def write_packet(writer: asyncio.StreamWriter, frame: StreamPacket) -> None:
    """Write one bounded header and raw attachment, awaiting socket backpressure."""
    media = frame.media if isinstance(frame, StreamFrame) else None
    data = media.data if isinstance(media, InlineMedia) else b""
    # model_dump(mode=json) cannot encode arbitrary binary PCM; omit bytes first.
    projection = frame.model_dump(mode="python", exclude={"media": {"data"}})
    header = json.dumps(
        {"protocol": 4, "frame": projection, "media_size": len(data)},
        allow_nan=False,
        separators=(",", ":"),
    ).encode()
    if len(header) > MAX_FRAME_BYTES:
        raise ProtocolError("stream header exceeds bound")
    writer.write(struct.pack("!I", len(header)) + header + data)
    await writer.drain()


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate stream JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ProtocolError("nonfinite stream JSON value")


async def read_packet(reader: asyncio.StreamReader) -> StreamPacket:
    """Bound lengths before reading and reconstruct media without base64 expansion."""
    length = struct.unpack("!I", await reader.readexactly(4))[0]
    if not 2 <= length <= MAX_FRAME_BYTES:
        raise ProtocolError("stream header exceeds bound")
    raw = await reader.readexactly(length)
    try:
        json.loads(
            raw, object_pairs_hook=_unique_pairs, parse_constant=_reject_constant
        )
        header = _Header.model_validate_json(raw)
    except (ValueError, RecursionError) as error:
        raise ProtocolError("invalid stream header") from error
    projection: dict[str, object] = dict(header.frame)
    media_value = projection.get("media")
    media = (
        cast(dict[str, object], media_value) if isinstance(media_value, dict) else None
    )
    if isinstance(media, dict) and media.get("kind") == "inline":
        if "data" in media:
            raise ProtocolError("inline bytes must follow the header")
        projection["media"] = {
            **media,
            "data": await reader.readexactly(header.media_size),
        }
    elif header.media_size:
        raise ProtocolError("unexpected stream attachment")
    # JSON decoding produces Python containers; strict model validation keeps
    # numbers, modes and optional fields identical across both runtimes.
    return STREAM_PACKET.validate_python(projection)


@final
class Sequence:
    """Fence one connection by call identity, direction, sequence and terminal."""

    def __init__(
        self,
        call_id: str,
        direction: Literal["caller_to_provider", "provider_to_caller"],
        *,
        next_sequence: int = 0,
    ) -> None:
        """Set the initial sequence for one independently closed stream direction."""
        self.call_id = call_id
        self.direction = direction
        self.next_sequence = next_sequence
        self.terminal = False

    def accept(self, frame: StreamFrame) -> None:
        """Reject gaps, duplicate terminals, wrong direction and stale call frames."""
        if self.terminal or (frame.call_id, frame.direction, frame.sequence) != (
            self.call_id,
            self.direction,
            self.next_sequence,
        ):
            raise ProtocolError("stream identity, sequence or terminal mismatch")
        self.next_sequence += 1
        self.terminal = frame.is_terminal


type StreamHandler = Callable[
    [StreamInvoke, AsyncIterator[StreamFrame]], AsyncGenerator[StreamFrame]
]


async def execute_stream(
    startup: Startup, call: StreamInvoke, handler: StreamHandler
) -> None:
    """Serve one admitted stream while the child's main loop answers health.

    Call this in an owned task from the main IPC loop. The handler receives
    caller lifecycle frames; completed input is a half-close. It yields output
    starting at sequence one (Skulk owns output started) and exactly one terminal.
    Disconnect, invalid input, or deadline expiry cancels and closes its iterator.
    The bounded eight-frame ingress queue applies backpressure independently of
    health. The owner additionally validates all declared payload schemas.
    After handler cleanup, half-close output and discard in-flight transport
    bytes until owner closure, within the same deadline and without handler access.
    """
    if startup.stream_socket is None:
        raise ProtocolError("owner did not provision a streaming channel")
    async with asyncio.timeout(call.remaining_seconds):
        reader, writer = await asyncio.open_unix_connection(startup.stream_socket)
        incoming: asyncio.Queue[StreamFrame] = asyncio.Queue(maxsize=8)
        input_sequence = Sequence(call.call_id, "caller_to_provider")
        output_sequence = Sequence(call.call_id, "provider_to_caller", next_sequence=1)
        descriptor = next(
            item
            for item in startup.manifest.descriptors
            if item.qualified_id == f"{call.capability_id}@{call.version}"
            and item.revision == call.descriptor_revision
        )

        async def receive() -> None:
            while True:
                frame = await read_packet(reader)
                if (
                    not isinstance(frame, StreamFrame)
                    or descriptor.io_mode == "server_streaming"
                ):
                    raise ProtocolError("unexpected caller input")
                input_sequence.accept(frame)
                if frame.kind == "completed" and (
                    frame.payload is not None or frame.media is not None
                ):
                    raise ProtocolError("input half-close carries no data")
                if frame.kind in ("failed", "cancelled"):
                    raise ProtocolError("caller terminated the stream")
                await incoming.put(frame)

        async def inputs() -> AsyncIterator[StreamFrame]:
            while True:
                frame = await incoming.get()
                yield frame
                if frame.is_terminal:
                    return

        async def output() -> StreamFrame:
            terminal: StreamFrame | None = None
            async with aclosing(handler(call, inputs())) as iterator:
                async for frame in iterator:
                    output_sequence.accept(frame)
                    if (
                        descriptor.io_mode == "client_streaming"
                        and frame.kind == "chunk"
                    ):
                        raise ProtocolError(
                            "client streaming produces one final result"
                        )
                    if frame.is_terminal:
                        terminal = frame
                    else:
                        await write_packet(writer, frame)
            if terminal is None:
                raise ProtocolError("handler omitted its output terminal")
            return terminal

        receiver: asyncio.Task[None] | None = None
        execution: asyncio.Task[StreamFrame] | None = None
        try:
            await write_message(
                writer,
                StreamHello(
                    call_id=call.call_id,
                    token=startup.token,
                    capability_node_id=startup.node_id,
                    manifest_sha256=manifest_digest(startup.manifest),
                ),
            )
            receiver = asyncio.create_task(receive())
            execution = asyncio.create_task(output())
            done, _ = await asyncio.wait(
                (receiver, execution), return_when=asyncio.FIRST_COMPLETED
            )
            # Cleanup alone cannot make a call successful when caller ingress
            # already failed. Arbitrate both directions before publishing.
            if receiver in done:
                await receiver
            terminal = await execution
            if receiver.done():
                await receiver
            await write_packet(writer, terminal)
            await write_packet(writer, StreamEnd(call_id=call.call_id))
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)
            receiver = None
            # Closing a Unix socket with unread upload bytes can reset its
            # peer and discard already-buffered output. Half-close output so
            # the owner can verify EOF, stop input and close its side. Discard
            # remaining transport bytes in constant space under the existing
            # call deadline; rejected media never reaches the handler.
            writer.write_eof()
            await writer.drain()
            while await reader.read(65536):
                pass
        finally:
            for task in (receiver, execution):
                if task is not None:
                    task.cancel()
            if receiver is not None and execution is not None:
                await asyncio.gather(receiver, execution, return_exceptions=True)
            elif receiver is not None:
                await asyncio.gather(receiver, return_exceptions=True)
            elif execution is not None:
                await asyncio.gather(execution, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
