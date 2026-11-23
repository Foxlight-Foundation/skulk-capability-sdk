"""Common descriptor, original-caller read and inert request-proposal interfaces.

Native readers validate declarations and arguments. Discovery and dispatch stay
on the supplied invocation; this module has no root credential or host adapter.
Proposals are data for review, never approval or permission to execute.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Literal, Protocol, cast, final

from pydantic import JsonValue, TypeAdapter

from .application import CallReply, CapabilityEndpoint, Invocation
from .runtime import native_module

type Behavior = Literal["read", "inference", "proposal", "execute"]
type Mode = Literal["unary", "server_streaming", "client_streaming", "bidirectional"]
type Domain = Literal["local", "private_site", "external"]
type InterfaceErrorCode = Literal[
    "invalid_descriptor",
    "invalid_payload",
    "unavailable",
    "not_read",
    "unsupported_mode",
]
_OBJECT = TypeAdapter(dict[str, JsonValue])
_TEXT = TypeAdapter(str)
_BEHAVIOR: TypeAdapter[Behavior] = TypeAdapter(Behavior)
_MODE: TypeAdapter[Mode] = TypeAdapter(Mode)
_DOMAIN: TypeAdapter[Domain] = TypeAdapter(Domain)


class _Schema(Protocol):
    def matches(self, source: str) -> bool: ...


class _Native(Protocol):
    def canonical_json(self, source: str) -> bytes: ...
    def portable_canonical_json(self, source: str) -> bytes: ...
    def PortableSchema(self, source: str) -> _Schema: ...


@final
class CapabilityInterfaceError(ValueError):
    """A bounded interface refusal without descriptor, argument or caller data."""

    def __init__(self, code: InterfaceErrorCode) -> None:
        """Retain only a stable refusal code; do not retain the failed input."""
        self.code = code
        super().__init__("capability interface " + code)


@final
@dataclass(frozen=True, slots=True, repr=False)
class CapabilityDescription:
    """An owned, native-validated declaration, not proof of current permission."""

    endpoint: CapabilityEndpoint = field(repr=False)

    @classmethod
    def from_endpoint(cls, destination: CapabilityEndpoint) -> CapabilityDescription:
        """Read a declaration through the native reader, without contacting a host.

        The input may come from configuration or a directory observation. It
        supplies no signed custody, current readiness or execution authority.
        """
        try:
            if re.fullmatch(r"[A-Za-z0-9._:@-]{1,128}", destination.node_id) is None:
                raise ValueError("invalid destination identifier")
            endpoint = CapabilityEndpoint.from_descriptor(
                destination.node_id, destination.descriptor()
            )
            return cls(endpoint)
        except (ValueError, TypeError, UnicodeError):
            raise CapabilityInterfaceError("invalid_descriptor") from None

    def descriptor(self) -> dict[str, JsonValue]:
        """Return a defensive copy of the complete native portable declaration."""
        return self.endpoint.descriptor()

    @property
    def behavior(self) -> Behavior:
        """Return the signed declaration's effect classification without relabelling."""
        return _BEHAVIOR.validate_python(self.descriptor()["behavior"], strict=True)

    @property
    def io_mode(self) -> Mode:
        """Return the exact declared input/output lifecycle."""
        return _MODE.validate_python(self.descriptor()["io_mode"], strict=True)

    @property
    def processing_domain(self) -> Domain:
        """Return the declared processing boundary; this is not caller consent."""
        return _DOMAIN.validate_python(
            self.descriptor()["processing_domain"], strict=True
        )

    @property
    def summary(self) -> str:
        """Return the declaration's descriptive text, never an authority decision."""
        return _TEXT.validate_python(self.descriptor()["description"], strict=True)


@final
@dataclass(frozen=True, slots=True, repr=False)
class CapabilityProposal:
    """Immutable request data for review; it contains no caller or approval proof.

    Use ``prepare`` to construct an intent. Export is explicit because arguments
    and request hashes may be private. No method submits or executes this object.
    A host must independently authenticate the caller and admit any later work.
    """

    _canonical: bytes = field(repr=False)
    _request: bytes = field(repr=False)

    @classmethod
    def prepare(
        cls, destination: CapabilityEndpoint, payload: dict[str, JsonValue]
    ) -> CapabilityProposal:
        """Validate and copy an inert JSON request without I/O or dispatch.

        Supports unary and server-output requests. Input-media streams cannot
        be represented by an initial JSON object alone and are refused. A
        governed declaration can be described here but gains no execution path.
        """
        description = CapabilityDescription.from_endpoint(destination)
        if description.io_mode not in ("unary", "server_streaming"):
            raise CapabilityInterfaceError("unsupported_mode")
        native = cast(_Native, native_module())
        descriptor = description.descriptor()
        try:
            request = native.portable_canonical_json(
                json.dumps(payload, allow_nan=False)
            )
            arguments = _OBJECT.validate_json(request, strict=True)
            if not native.PortableSchema(
                json.dumps(descriptor["input_schema"], allow_nan=False)
            ).matches(request.decode()):
                raise ValueError("proposal input differs")
            endpoint = description.endpoint
            intent: dict[str, JsonValue] = {
                "format": 1,
                "kind": "capability_request_proposal",
                "destination": {
                    "node_id": endpoint.node_id,
                    "capability_id": endpoint.capability_id,
                    "version": endpoint.version,
                    "descriptor_revision": endpoint.descriptor_revision,
                },
                "descriptor": descriptor,
                "payload": arguments,
                "request_sha256": hashlib.sha256(request).hexdigest(),
            }
            # The envelope is an inert SDK data export, not an issued native
            # execution/approval contract. Both bindings use the native codec.
            canonical = native.canonical_json(json.dumps(intent, allow_nan=False))
            return cls(canonical, request)
        except (ValueError, TypeError, UnicodeError):
            raise CapabilityInterfaceError("invalid_payload") from None

    def canonical(self) -> bytes:
        """Explicitly export exact intent bytes; these bytes grant no authority."""
        return self._canonical

    @property
    def payload(self) -> dict[str, JsonValue]:
        """Return an owned argument copy without exposing internal storage."""
        return _OBJECT.validate_json(self._request, strict=True)

    @property
    def request_bytes(self) -> bytes:
        """Explicitly export the exact canonical request bound by the intent."""
        return self._request

    @property
    def digest(self) -> str:
        """Identify this exact intent for comparison; the digest is not approval."""
        return hashlib.sha256(self._canonical).hexdigest()


@final
class CapabilityClient:
    """Common interfaces bound to one actual invocation and its original caller."""

    def __init__(self, invocation: Invocation) -> None:
        """Retain the invocation only; no replacement caller or bearer is accepted."""
        self._invocation = invocation

    async def describe(self, destination: CapabilityEndpoint) -> CapabilityDescription:
        """Freshly resolve one exact permitted descriptor without caching authority."""
        expected = CapabilityDescription.from_endpoint(destination).endpoint
        directory = await self._invocation.discover()
        matches = tuple(
            offer
            for offer in directory.offers
            if offer.endpoint.node_id == expected.node_id
            and offer.endpoint.canonical_descriptor() == expected.canonical_descriptor()
        )
        if len(matches) != 1:
            raise CapabilityInterfaceError("unavailable")
        return CapabilityDescription.from_endpoint(matches[0].endpoint)

    async def read(
        self,
        destination: CapabilityEndpoint,
        payload: dict[str, JsonValue] | bytes,
        *,
        deadline_unix_ms: int | None = None,
    ) -> CallReply:
        """Perform one declared unary read using original-caller native dispatch.

        Reject inference/proposal/execute declarations before transport. Current
        permissions and signed destination are independently checked at dispatch;
        cancellation, revocation and uncertain outcomes retain existing custody.
        A configured read does not require the optional discovery channel.
        """
        description = CapabilityDescription.from_endpoint(destination)
        if description.behavior != "read":
            raise CapabilityInterfaceError("not_read")
        if description.io_mode != "unary":
            raise CapabilityInterfaceError("unsupported_mode")
        return await self._invocation.call(
            description.endpoint, payload, deadline_unix_ms=deadline_unix_ms
        )

    async def propose(
        self, destination: CapabilityEndpoint, payload: dict[str, JsonValue]
    ) -> CapabilityProposal:
        """Prepare an inert intent after current caller-filtered observation.

        No handler is invoked and no child-effect sequence is consumed. Hosts
        that do not expose governed destinations still refuse them; this method
        cannot replace that boundary with a service identity or an approval.
        """
        current = await self.describe(destination)
        return CapabilityProposal.prepare(current.endpoint, payload)
