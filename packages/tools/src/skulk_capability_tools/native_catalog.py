"""Catalog four: native consent facts with a separate, immutable signing domain.

The existing catalog-one/two/three classes remain unchanged. A catalog grants
neither execution rights nor account access; installation authenticates the
referenced native runtime and full compiled declaration independently.
"""

import hashlib
import json
import time
from collections.abc import Callable
from typing import Literal, Self, final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.specifiers import SpecifierSet
from pydantic import Field, JsonValue, TypeAdapter, field_validator, model_validator

from skulk_capability_sdk.contracts import (
    Contract,
    Denied,
    Digest,
    Identifier,
    canonical,
)

from .catalog import CatalogEntry, FeedUrl
from .native_publishing import NativeProfiles, SignedNativeRuntimeRelease
from .releases import PublisherTrust
from .runtime_release import RuntimePlatform, canonical_platform

_OBJECT = TypeAdapter(dict[str, JsonValue])
_OBJECTS = TypeAdapter(list[dict[str, JsonValue]])
_TEXT = TypeAdapter(str)
_DOMAIN: TypeAdapter[Literal["local", "private_site", "external"]] = TypeAdapter(
    Literal["local", "private_site", "external"]
)


@final
class NativeCatalogBinding(Contract):
    """Native identity and processing facts; never a caller authorization."""

    runtime_protocol: Literal[5] = Field(description="Exact native runtime envelope.")
    release_protocol: Literal[3] = Field(
        description="Exact native application envelope."
    )
    manifest_revision: Digest = Field(description="Full compiled managed/2 identity.")
    kernel: NativeProfiles = Field(
        description="Exact independently qualified ABI cohort."
    )
    processing_domains: tuple[Literal["local", "private_site", "external"], ...] = (
        Field(
            min_length=1,
            max_length=3,
            description="Sorted unique declared processing domains.",
        )
    )

    @field_validator("runtime_protocol", "release_protocol", mode="before")
    @classmethod
    def integer_protocol(cls, value: object) -> object:
        """Refuse float and boolean aliases before selecting either native envelope."""
        if type(value) is not int:
            raise ValueError("native catalog protocols must be exact integers")
        return value

    @model_validator(mode="after")
    def unique_domains(self) -> Self:
        """Keep the domain summary deterministic and unambiguous."""
        if self.processing_domains != tuple(sorted(set(self.processing_domains))):
            raise ValueError("native catalog domains must be sorted and unique")
        return self


@final
class NativeCatalogEntry(Contract):
    """Native release consent facts, without retyping its manifest as a legacy one."""

    bundle_id: str = Field(
        min_length=1, max_length=128, description="Native bundle ID."
    )
    bundle_version: str = Field(
        min_length=1, max_length=128, description="Native version."
    )
    title: str | None = Field(
        default=None, max_length=512, description="Declared title."
    )
    publisher: Identifier = Field(description="Publisher of the referenced release.")
    sequence: int = Field(ge=1, description="Immutable publisher sequence.")
    feed_url: FeedUrl = Field(
        description="HTTPS directory containing the signed release."
    )
    release_sha256: Digest = Field(
        description="Digest of the exact served record bytes."
    )
    release_digest: Digest = Field(
        description="Full domain-qualified runtime identity."
    )
    runtime_platform: RuntimePlatform = Field(
        description="Exact native artifact family."
    )
    artifact_sha256: Digest = Field(description="Exact bundle archive digest.")
    artifact_size: int = Field(ge=1, le=67108864, description="Exact bundle bytes.")
    transfer_bytes: int = Field(
        ge=1, le=67108864 + 536870912, description="Bundle plus complete wheel closure."
    )
    platforms: tuple[Literal["darwin", "linux"], ...] = Field(
        min_length=1, max_length=2, description="Declared host systems."
    )
    skulk_build_sha256: Digest = Field(description="Qualified Core source provenance.")
    skulk_requires: str = Field(
        max_length=128, description="Declared Core version range."
    )
    permissions: tuple[str, ...] = Field(
        min_length=1,
        max_length=16,
        description="Operator-reviewed permission summaries.",
    )
    descriptors: tuple[str, ...] = Field(
        min_length=1,
        max_length=16,
        description="Admitted native callable IDs and versions.",
    )
    surfaces: tuple[str, ...] = Field(
        default=(), max_length=4, description="Declared management surface titles."
    )
    operations: bool = Field(
        default=False, description="Whether durable work is declared."
    )
    steward_risks: tuple[Literal["observation", "billable", "effect"], ...] = Field(
        default=(), max_length=3, description="Declared Steward exposure risk classes."
    )
    expires_at: int = Field(gt=0, description="Referenced release expiry.")
    native: NativeCatalogBinding = Field(
        description="Exact native identity and ABI facts."
    )

    @model_validator(mode="after")
    def bounded_claims(self) -> Self:
        """Check discovery bounds without granting execution or altering the record."""
        SpecifierSet(self.skulk_requires)
        if self.transfer_bytes < self.artifact_size:
            raise ValueError("native catalog transfer size is below its artifact")
        if any(not 0 < len(value) <= 512 for value in self.permissions):
            raise ValueError("native catalog permission summary exceeds its bound")
        if any(not 0 < len(value) <= 512 for value in self.descriptors):
            raise ValueError("native catalog callable summary exceeds its bound")
        if any(not 0 < len(value) <= 512 for value in self.surfaces):
            raise ValueError("native catalog surface summary exceeds its bound")
        return self


@final
class NativeCatalog(Contract):
    """Separately versioned full catalog; legacy entries keep their actual contracts."""

    protocol: Literal[4] = Field(default=4, description="Native-aware catalog version.")
    publisher: Identifier = Field(description="The catalog and release publisher.")
    revision: int = Field(ge=1, description="Monotonic catalog revision.")
    created_at: int = Field(gt=0, description="Creation Unix seconds.")
    expires_at: int = Field(gt=0, description="Expiry Unix seconds.")
    entries: tuple[NativeCatalogEntry | CatalogEntry, ...] = Field(
        max_length=64, description="Native and unchanged legacy release consent facts."
    )

    @field_validator("protocol", mode="before")
    @classmethod
    def integer_protocol(cls, value: object) -> object:
        """Refuse numeric aliases for the new signed contract."""
        if type(value) is not int or value != 4:
            raise ValueError("native catalog protocol must be exact integer 4")
        return value

    @model_validator(mode="after")
    def coherent(self) -> Self:
        """Require one publisher and one unambiguous bundle sequence per family."""
        if self.created_at >= self.expires_at:
            raise ValueError("native catalog expiry must follow creation")
        seen: set[tuple[str, int, str | None]] = set()
        for entry in self.entries:
            if entry.publisher != self.publisher or entry.skulk_requires is None:
                raise ValueError("native catalog publisher or Core range differs")
            family = (
                canonical_platform(entry.runtime_platform)
                if entry.runtime_platform is not None
                else None
            )
            key = (entry.bundle_id, entry.sequence, family)
            if key in seen:
                raise ValueError("native catalog lists one bundle sequence twice")
            seen.add(key)
        return self


@final
class SignedNativeCatalog(Contract):
    """Native catalog signature; trust is independently supplied by the host."""

    catalog: NativeCatalog = Field(description="Canonical catalog-four claims.")
    signature: str = Field(
        pattern=r"^[a-f0-9]{128}$", description="Publisher signature."
    )


def native_catalog_message(catalog: NativeCatalog) -> bytes:
    """Bind catalog four to its own domain without changing issued legacy bytes."""
    return b"skulk.catalog/4\x00" + canonical(catalog)


def native_entry_from_record(raw: bytes, feed_url: str) -> NativeCatalogEntry:
    """Project compiled metadata; callers separately authenticate the signature."""
    record = SignedNativeRuntimeRelease.model_validate_json(raw)
    runtime, release = record.runtime, record.runtime.release
    manifest = release.manifest
    value = _OBJECT.validate_json(manifest.canonical_json)
    contracts = _OBJECT.validate_python(value["contracts"], strict=True)
    descriptors = _OBJECTS.validate_python(contracts["descriptors"], strict=True)
    surfaces = _OBJECTS.validate_python(value["surfaces"], strict=True)
    steward = _OBJECTS.validate_python(value["steward"], strict=True)
    return NativeCatalogEntry.model_validate_json(
        json.dumps(
            {
                "bundle_id": manifest.bundle_id,
                "bundle_version": manifest.bundle_version,
                "title": value.get("title"),
                "publisher": release.publisher,
                "sequence": release.sequence,
                "feed_url": feed_url,
                "release_sha256": hashlib.sha256(raw).hexdigest(),
                "release_digest": runtime.digest,
                "runtime_platform": runtime.platform,
                "artifact_sha256": manifest.executable_sha256,
                "artifact_size": release.artifact_size,
                "transfer_bytes": release.artifact_size
                + sum(wheel.size for wheel in runtime.wheels),
                "platforms": release.platforms,
                "skulk_build_sha256": release.skulk_build_sha256,
                "skulk_requires": manifest.skulk_requires,
                "permissions": release.permissions,
                "descriptors": tuple(
                    _TEXT.validate_python(item["id"], strict=True)
                    + "@"
                    + _TEXT.validate_python(item["version"], strict=True)
                    for item in descriptors
                ),
                "surfaces": tuple(item["title"] for item in surfaces),
                "operations": value["operations"] is not None,
                "steward_risks": tuple(
                    sorted({_TEXT.validate_python(item["risk"]) for item in steward})
                ),
                "expires_at": release.expires_at,
                "native": {
                    "runtime_protocol": 5,
                    "release_protocol": 3,
                    "manifest_revision": manifest.digest,
                    "kernel": runtime.kernel.model_dump(mode="json"),
                    "processing_domains": tuple(
                        sorted(
                            {
                                _DOMAIN.validate_python(
                                    item["processing_domain"], strict=True
                                )
                                for item in descriptors
                            }
                        )
                    ),
                },
            },
            allow_nan=False,
        )
    )


def verify_native_catalog(
    signed: SignedNativeCatalog,
    trust: PublisherTrust,
    *,
    clock: Callable[[], int] | None = None,
) -> NativeCatalog:
    """Authenticate catalog-four trust, expiry and its separate signature domain."""
    now = (clock or (lambda: int(time.time())))()
    catalog = signed.catalog
    public = trust.publishers.get(catalog.publisher)
    if (
        public is None
        or catalog.publisher in trust.revoked_publishers
        or trust.expires_at <= now
        or not catalog.created_at <= now < catalog.expires_at
    ):
        raise Denied("native catalog trust or validity window refused")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public)).verify(
            bytes.fromhex(signed.signature), native_catalog_message(catalog)
        )
    except (InvalidSignature, ValueError):
        raise Denied("native catalog signature refused") from None
    return catalog
