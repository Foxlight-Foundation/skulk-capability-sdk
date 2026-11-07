"""The signed catalog: what a publisher lists, verified against the trust a host holds.

A catalog is a flat signed index of releases the same publisher signed. It
carries the metadata an operator needs to decide before any transfer (what
the capability can do, what it can spend, how large it is) and points at the
feed where the release record and artifact live; installing still goes
through the verified release path, so the catalog adds discovery and consent,
not a second trust mechanism.
"""

import hashlib
import json
import time
from collections.abc import Callable
from typing import Annotated, Final, Literal, Self, final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from pydantic import AfterValidator, Field, model_validator

from skulk_capability_sdk.contracts import (
    Contract,
    Denied,
    Digest,
    Identifier,
    canonical,
)

from .releases import PublisherTrust, Release, SignedRelease
from .runtime_release import (
    RuntimePlatform,
    RuntimeRelease,
    SignedRuntimeRelease,
    canonical_platform,
)

CATALOG_MAX_BYTES: Final = 262144
"""Complete signed catalog envelope accepted by supported host readers."""

CATALOG_PROTOCOL = 2
"""Default catalog protocol for declarations within the original bounds.

Builders select protocol 3 when an entry has more than eight identifiers.

Protocol 3 additionally permits sixteen capability identifiers per release.
Protocol 2 listings carry each release's Skulk version range, so a host judges
fit by its version and a Skulk update keeps a release installable. Protocol 1
listings had no range; they were published for one exact Skulk build."""

ACCEPTED_CATALOG_PROTOCOLS: tuple[int, ...] = (1, 2, 3)
"""Catalog protocols this SDK verifies, including both retained legacy formats."""


def accepted_catalog_protocol(value: int) -> int:
    """Refuse a catalog protocol outside the window by name."""
    if value not in ACCEPTED_CATALOG_PROTOCOLS:
        raise ValueError(
            f"catalog protocol {value} is not accepted; "
            f"accepted: {', '.join(str(p) for p in ACCEPTED_CATALOG_PROTOCOLS)}"
        )
    return value


CatalogProtocol = Annotated[
    int,
    AfterValidator(accepted_catalog_protocol),
    Field(json_schema_extra={"enum": list(ACCEPTED_CATALOG_PROTOCOLS)}),
]

FeedUrl = Annotated[
    str,
    Field(
        pattern=r"^https://[A-Za-z0-9.\-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~\-]+)*/$",
        max_length=512,
        description="HTTPS directory serving release.json and the artifact.",
    ),
]


@final
class CatalogEntry(Contract):
    """One listed release: the consent facts and where its signed record lives."""

    bundle_id: Identifier
    bundle_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    title: str | None = Field(default=None, max_length=120)
    publisher: Identifier
    sequence: int = Field(ge=1)
    feed_url: FeedUrl
    release_sha256: Digest = Field(
        description="SHA-256 of the release record bytes as the feed serves them."
    )
    release_digest: Digest = Field(
        description="Digest of the signed claims as the host reviews them: the "
        "runtime envelope's for a runtime-bearing record, else the release's."
    )
    runtime_platform: RuntimePlatform | None = Field(
        default=None,
        description="Exact artifact family of a runtime-bearing record; None for a "
        "plain release.",
    )
    artifact_sha256: Digest
    artifact_size: int = Field(ge=1, le=64 * 1024 * 1024)
    transfer_bytes: int = Field(
        ge=1,
        le=64 * 1024 * 1024 + 512 * 1024 * 1024,
        description="Everything an install transfers: the artifact plus, for a "
        "runtime-bearing record, every wheel it lists.",
    )
    platforms: tuple[Literal["darwin", "linux"], ...] = Field(
        min_length=1, max_length=2
    )
    skulk_build_sha256: Digest = Field(
        description="Skulk build the release was qualified against; provenance."
    )
    skulk_requires: str | None = Field(
        default=None,
        max_length=128,
        exclude_if=lambda value: value is None,
        description="Skulk versions the release runs on, from its manifest. Every "
        "protocol 2 entry states it; a protocol 1 entry, signed without it, omits it.",
    )
    permissions: tuple[str, ...] = Field(min_length=1, max_length=16)
    descriptors: tuple[str, ...] = Field(
        min_length=1, max_length=16, description="Qualified capability ids."
    )
    surfaces: tuple[str, ...] = Field(default=(), max_length=4)
    operations: bool = Field(
        default=False, description="Whether the release declares durable operations."
    )
    steward_risks: tuple[Literal["observation", "billable", "effect"], ...] = Field(
        default=(), max_length=3
    )
    expires_at: int = Field(gt=0)

    @model_validator(mode="after")
    def readable_range(self) -> Self:
        """A stated Skulk range is a version range a host can evaluate."""
        if self.skulk_requires is not None:
            try:
                SpecifierSet(self.skulk_requires)
            except InvalidSpecifier:
                raise ValueError(
                    "catalog entry Skulk range is not a version range"
                ) from None
        return self


@final
class Catalog(Contract):
    """A publisher's signed listing; one publisher per catalog in this protocol."""

    protocol: CatalogProtocol = CATALOG_PROTOCOL
    publisher: Identifier
    revision: int = Field(ge=1)
    created_at: int = Field(gt=0)
    expires_at: int = Field(gt=0)
    entries: tuple[CatalogEntry, ...] = Field(max_length=64)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        """Keep the listing bounded, unambiguous and the signer's own."""
        if not self.created_at < self.expires_at:
            raise ValueError("catalog expiry must follow creation")
        if self.protocol < 3 and any(
            len(entry.descriptors) > 8 for entry in self.entries
        ):
            raise ValueError("more than eight catalog descriptors requires protocol 3")
        if any(
            (entry.skulk_requires is None) == (self.protocol >= 2)
            for entry in self.entries
        ):
            raise ValueError(
                "catalog protocol 2 entries state their Skulk range; "
                "protocol 1 entries do not"
            )
        seen: set[tuple[str, int, str | None]] = set()
        for entry in self.entries:
            if entry.publisher != self.publisher:
                raise ValueError("catalog entries must belong to the catalog publisher")
            # One sequence may ship one artifact family per runtime platform;
            # the same family twice is ambiguous, and platform aliases name
            # the same family (the host compares families canonically too).
            family = (
                canonical_platform(entry.runtime_platform)
                if entry.runtime_platform is not None
                else None
            )
            key = (entry.bundle_id, entry.sequence, family)
            if key in seen:
                raise ValueError("catalog lists one bundle sequence twice")
            seen.add(key)
        return self


@final
class SignedCatalog(Contract):
    """Publisher signature over the canonical catalog, without embedded trust."""

    catalog: Catalog
    signature: str = Field(pattern=r"^[a-f0-9]{128}$")


def signed_record(raw: bytes) -> SignedRuntimeRelease | SignedRelease:
    """Parse a served record by its top-level keys: runtime-bearing or plain."""
    document = json.loads(raw)
    if isinstance(document, dict) and "runtime" in document:
        return SignedRuntimeRelease.model_validate_json(raw)
    return SignedRelease.model_validate_json(raw)


def signed_claims(
    record: SignedRuntimeRelease | SignedRelease,
) -> RuntimeRelease | Release:
    """The claims the publisher signed: the runtime envelope, or the release."""
    return (
        record.runtime if isinstance(record, SignedRuntimeRelease) else record.release
    )


def release_from_record(raw: bytes) -> Release:
    """Read the release out of a served record, runtime-bearing or plain."""
    record = signed_record(raw)
    return (
        record.runtime.release
        if isinstance(record, SignedRuntimeRelease)
        else record.release
    )


def entry_from_record(raw: bytes, feed_url: str) -> CatalogEntry:
    """Build the entry for a release record exactly as its feed serves it."""
    record = signed_record(raw)
    claims = signed_claims(record)
    release = claims.release if isinstance(claims, RuntimeRelease) else claims
    manifest = release.manifest
    return CatalogEntry(
        bundle_id=manifest.bundle_id,
        bundle_version=manifest.bundle_version,
        title=manifest.title,
        publisher=release.publisher,
        sequence=release.sequence,
        feed_url=feed_url,
        release_sha256=hashlib.sha256(raw).hexdigest(),
        release_digest=claims.digest,
        runtime_platform=claims.platform
        if isinstance(claims, RuntimeRelease)
        else None,
        artifact_sha256=manifest.executable_sha256,
        artifact_size=release.artifact_size,
        transfer_bytes=release.artifact_size
        + (
            sum(wheel.size for wheel in claims.wheels)
            if isinstance(claims, RuntimeRelease)
            else 0
        ),
        platforms=release.platforms,
        skulk_build_sha256=release.skulk_build_sha256,
        skulk_requires=manifest.skulk_requires,
        permissions=release.permissions,
        descriptors=tuple(
            f"{descriptor.id}@{descriptor.version}"
            for descriptor in manifest.descriptors
        ),
        surfaces=tuple(surface.title for surface in manifest.surfaces),
        operations=manifest.operations is not None,
        steward_risks=tuple(sorted({exposure.risk for exposure in manifest.steward})),
        expires_at=release.expires_at,
    )


def verify_catalog(
    signed: SignedCatalog,
    trust: PublisherTrust,
    *,
    clock: Callable[[], int] | None = None,
) -> Catalog:
    """Authenticate a catalog against the publishers this host accepts."""
    now = (clock or (lambda: int(time.time())))()
    catalog = signed.catalog
    if catalog.publisher not in trust.publishers:
        raise Denied("catalog publisher is not trusted")
    if catalog.publisher in trust.revoked_publishers:
        raise Denied("catalog publisher is revoked")
    if trust.expires_at <= now:
        raise Denied("publisher trust expired")
    if not catalog.created_at <= now < catalog.expires_at:
        raise Denied("catalog window refused")
    public = trust.publishers[catalog.publisher]
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public)).verify(
            bytes.fromhex(signed.signature), canonical(catalog)
        )
    except (InvalidSignature, ValueError):
        raise Denied("catalog signature refused") from None
    return catalog


def newest(
    catalog: Catalog, bundle_id: str, runtime_platform: str | None
) -> CatalogEntry | None:
    """The highest listed sequence of one bundle for one artifact family.

    ``runtime_platform`` is the exact family of a runtime-bearing record, or
    None for plain releases; one sequence may be listed once per family, so
    the family is part of what is asked for, never guessed from order.
    """
    wanted = (
        canonical_platform(runtime_platform) if runtime_platform is not None else None
    )
    entries = [
        entry
        for entry in catalog.entries
        if entry.bundle_id == bundle_id
        and (
            canonical_platform(entry.runtime_platform)
            if entry.runtime_platform is not None
            else None
        )
        == wanted
    ]
    if not entries:
        return None
    return max(entries, key=lambda entry: entry.sequence)
