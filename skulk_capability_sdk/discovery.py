"""Typed, bounded observations from Core's caller-filtered native directory."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import re
import ssl
from dataclasses import dataclass, field
from typing import Annotated, Literal, Protocol, cast, final

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from .application import CapabilityEndpoint
from .runtime import finish_owned, native_module

MAXIMUM_DIRECTORY_BYTES = 8 * 1024 * 1024
"""Decoded metadata transfer bound; it is unrelated to inference output limits."""
MAXIMUM_DIRECTORY_OFFERS = 4096
"""Maximum observations admitted atomically from one directory response."""
type DiscoveryErrorCode = Literal[
    "invalid_directory", "limit", "denied", "unsupported", "unavailable", "timeout"
]
type Mode = Literal["unary", "server_streaming", "client_streaming", "bidirectional"]
type Domain = Literal["local", "private_site", "external"]
_Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9._:@-]{1,128}$")]
_Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
_Seconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]
_MODE: TypeAdapter[Mode] = TypeAdapter(Mode)


class _Native(Protocol):
    def canonical_json(self, source: str) -> bytes: ...
    def portable_v2_contract_digest(self, name: str, source: str) -> str: ...


@final
class DiscoveryError(RuntimeError):
    """A sanitized discovery refusal; responses, URLs and credentials stay private."""

    def __init__(self, code: DiscoveryErrorCode) -> None:
        """Expose a bounded error code without retaining the failed response."""
        self.code = code
        super().__init__("capability discovery " + code)


class _Record(BaseModel):
    """Strict HTTP metadata projection; parsing supplies no signed custody."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class _Target(_Record):
    """Observed compiled destination binding, independently rechecked by Core."""

    node_id: _Identifier
    capability_id: _Identifier
    version: str
    descriptor_revision: _Digest = Field(
        description="Compiled portable/2 declaration digest."
    )
    behavior: Literal["read", "inference"]
    processing_domain: Domain


class _Offer(_Record):
    """Existing Core directory fields, with explicit nullable inference lifetime."""

    plugin_id: _Identifier
    runtime_digest: _Digest = Field(description="Observed signed runtime generation.")
    incarnation: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")] = Field(
        description="Observed native child channel lifetime."
    )
    target: _Target = Field(description="Exact destination observation, never a grant.")
    descriptor: dict[str, JsonValue] = Field(
        description="Native portable/2 declaration."
    )
    stream_seconds: _Seconds = Field(description="Signed partial-I/O resource window.")
    cleanup_seconds: _Seconds = Field(description="Signed cancellation/cleanup grace.")
    startup_seconds: _Seconds = Field(description="Signed connection resource window.")
    inference_seconds: _Seconds | None = Field(
        description="Explicit signed owner ceiling; null retains uncapped inference."
    )


@final
@dataclass(frozen=True, slots=True, repr=False)
class CapabilityOffer:
    """An immutable observation, with exact descriptor binding and no grant.

    Runtime and incarnation describe the observed generation. Core independently
    authenticates the actual caller and rechecks current custody at dispatch.
    """

    plugin_id: str
    runtime_digest: str
    incarnation: str
    endpoint: CapabilityEndpoint
    behavior: Literal["read", "inference"]
    processing_domain: Domain
    io_mode: Mode
    stream_seconds: float
    cleanup_seconds: float
    startup_seconds: float
    inference_seconds: float | None


@final
@dataclass(frozen=True, slots=True, repr=False)
class CapabilityDirectory:
    """A complete validated observation; it is neither cached authority nor routing."""

    offers: tuple[CapabilityOffer, ...] = field(repr=False)

    @classmethod
    def from_json(cls, source: bytes) -> CapabilityDirectory:
        """Admit one bounded Core directory atomically through native descriptors.

        Reject duplicate JSON keys, unsupported fields, inconsistent identities,
        duplicate destinations and any malformed offer without returning a subset.
        """
        if len(source) > MAXIMUM_DIRECTORY_BYTES:
            raise DiscoveryError("limit")
        native = cast(_Native, native_module())
        try:
            # The lossless native codec handles the outer HTTP document. Every
            # descriptor then goes through the issued portable/2 contract reader.
            canonical = native.canonical_json(source.decode("utf-8"))
            values = TypeAdapter(list[JsonValue]).validate_json(canonical, strict=True)
            if len(values) > MAXIMUM_DIRECTORY_OFFERS:
                raise DiscoveryError("limit")
            offers: list[CapabilityOffer] = []
            identities: set[tuple[str, str, str, str]] = set()
            for value in values:
                wire = _Offer.model_validate_json(json.dumps(value, allow_nan=False))
                target = wire.target
                endpoint = CapabilityEndpoint.from_descriptor(
                    target.node_id, wire.descriptor
                )
                descriptor = endpoint.descriptor()
                if (
                    descriptor["id"] != target.capability_id
                    or descriptor["version"] != target.version
                    or descriptor["behavior"] != target.behavior
                    or descriptor["processing_domain"] != target.processing_domain
                    or native.portable_v2_contract_digest(
                        "Descriptor", endpoint.canonical_descriptor().decode()
                    )
                    != target.descriptor_revision
                ):
                    raise DiscoveryError("invalid_directory")
                identity = (
                    wire.plugin_id,
                    target.node_id,
                    target.capability_id,
                    target.version,
                )
                if identity in identities:
                    raise DiscoveryError("invalid_directory")
                identities.add(identity)
                offers.append(
                    CapabilityOffer(
                        wire.plugin_id,
                        wire.runtime_digest,
                        wire.incarnation,
                        endpoint,
                        target.behavior,
                        target.processing_domain,
                        _MODE.validate_python(descriptor["io_mode"], strict=True),
                        wire.stream_seconds,
                        wire.cleanup_seconds,
                        wire.startup_seconds,
                        wire.inference_seconds,
                    )
                )
            return cls(tuple(offers))
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise DiscoveryError("invalid_directory") from None

    def select(
        self,
        plugin_id: str,
        capability_id: str,
        version: str,
        *,
        node_id: str | None = None,
    ) -> CapabilityOffer:
        """Select an exact installed identity; absent or ambiguous matches refuse."""
        matches = tuple(
            offer
            for offer in self.offers
            if offer.plugin_id == plugin_id
            and offer.endpoint.capability_id == capability_id
            and offer.endpoint.version == version
            and (node_id is None or offer.endpoint.node_id == node_id)
        )
        if len(matches) != 1:
            raise ValueError("discovery requires exactly one matching destination")
        return matches[0]


def _directory_url(base_url: str) -> str:
    match = re.fullmatch(
        r"(https?)://(\[[A-Za-z0-9:.]+\]|[A-Za-z0-9.-]+)(?::([0-9]{1,5}))?/?",
        base_url,
    )
    if match is None:
        raise ValueError("discovery requires a root HTTP(S) origin")
    scheme, host, port = match.groups()
    if port is not None and not 1 <= int(port) <= 65535:
        raise ValueError("discovery origin port is invalid")
    address: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        if (
            host.startswith("[")
            or not all(
                re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part)
                for part in host.removesuffix(".").split(".")
            )
            or len(host) > 253
        ):
            raise ValueError("discovery origin host is invalid") from None
    if scheme == "http" and (address is None or not address.is_loopback):
        raise ValueError("remote discovery requires HTTPS")
    return base_url.rstrip("/") + "/v1/native-inference"


async def _read_directory(
    url: str, bearer: str, seconds: float, verification: ssl.SSLContext | bool
) -> CapabilityDirectory:
    import httpx

    try:
        async with httpx.AsyncClient(
            timeout=seconds,
            follow_redirects=False,
            trust_env=False,
            verify=verification,
        ) as client:
            async with client.stream(
                "GET", url, headers={"authorization": "Bearer " + bearer}
            ) as response:
                if response.status_code in (401, 403):
                    raise DiscoveryError("denied")
                if response.status_code == 404:
                    raise DiscoveryError("unsupported")
                if response.status_code != 200:
                    raise DiscoveryError("unavailable")
                content_type = response.headers.get("content-type", "")
                if content_type.split(";")[0].strip().lower() != "application/json":
                    raise DiscoveryError("invalid_directory")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > MAXIMUM_DIRECTORY_BYTES:
                        raise DiscoveryError("limit")
                    data.extend(chunk)
                return CapabilityDirectory.from_json(bytes(data))
    except DiscoveryError:
        raise
    except httpx.TimeoutException:
        raise DiscoveryError("timeout") from None
    except httpx.HTTPError:
        raise DiscoveryError("unavailable") from None


async def _drain(operation: asyncio.Task[CapabilityDirectory]) -> None:
    await asyncio.gather(operation, return_exceptions=True)


async def discover_capabilities(
    base_url: str,
    bearer: str,
    *,
    timeout_seconds: float = 30,
    certificate_authority: str | None = None,
) -> CapabilityDirectory:
    """Read fresh caller-authorized offers, owning HTTP cleanup on every outcome.

    Install the ``native-discovery`` extra and an admitted native kernel. Supply
    an enrolled Skulk client bearer, never provider credentials. Remote origins
    require HTTPS; redirects and environment proxies are disabled. The optional
    timeout bounds this metadata read, independently of all inference lifetimes.
    ``certificate_authority`` optionally supplies the cluster's trusted PEM CA;
    certificate and hostname verification remain enabled. No request,
    authorization failure or stale observation is retried automatically.
    """
    url = _directory_url(base_url)
    if (
        not bearer
        or len(bearer) > 4096
        or any(ord(character) < 33 or ord(character) > 126 for character in bearer)
    ):
        raise ValueError("discovery bearer is invalid")
    try:
        seconds = TypeAdapter(float).validate_python(timeout_seconds, strict=True)
    except ValueError:
        raise ValueError("discovery metadata timeout is invalid") from None
    if not math.isfinite(seconds) or not 0 < seconds <= 86400:
        raise ValueError("discovery metadata timeout is invalid")
    verification: ssl.SSLContext | bool = True
    if certificate_authority is not None:
        try:
            if len(certificate_authority.encode("ascii")) > 1048576:
                raise ValueError("certificate authority exceeds the bound")
            verification = ssl.create_default_context(cadata=certificate_authority)
        except (ValueError, TypeError, ssl.SSLError):
            raise ValueError("invalid discovery certificate authority") from None
    native_module()
    operation = asyncio.create_task(_read_directory(url, bearer, seconds, verification))
    try:
        async with asyncio.timeout(seconds):
            return await asyncio.shield(operation)
    except TimeoutError:
        raise DiscoveryError("timeout") from None
    finally:
        if not operation.done():
            operation.cancel()
        await finish_owned(asyncio.create_task(_drain(operation)))
