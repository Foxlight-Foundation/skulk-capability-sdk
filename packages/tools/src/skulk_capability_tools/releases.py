"""Signed portable private bundle metadata; publisher trust is provisioned locally."""

import hashlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Final, Literal, Self, final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.specifiers import SpecifierSet
from pydantic import AfterValidator, Field, model_validator

from skulk_capability_sdk.contracts import (
    Contract,
    Denied,
    Digest,
    Identifier,
    Manifest,
    canonical,
    require_extended_contracts_reader,
)
from skulk_capability_sdk.storage import read_private

RELEASE_METADATA_MAX_BYTES: Final = 524288
"""Largest signed release metadata, the bound a host reads it with.

Hosts read ``release.json`` and the staged runtime metadata with this bound,
and it carries the whole manifest, so it is the practical ceiling on how much
a capability can declare. The publisher refuses a larger release before
writing anything.
"""


def validate_release_metadata_compatibility(
    skulk_requires: str, metadata: bytes
) -> None:
    """Refuse a signed wire envelope exceeding its declared reader's bound.

    Args:
        skulk_requires: Version range from the validated manifest.
        metadata: Exact complete signed JSON bytes the publisher will serve.

    Raises:
        ValueError: The envelope exceeds the current bound or the release
            admits an older reader that cannot read these bytes.
    """
    if len(metadata) > RELEASE_METADATA_MAX_BYTES:
        raise ValueError(
            f"the signed release is {len(metadata)} bytes; hosts read at most "
            f"{RELEASE_METADATA_MAX_BYTES}"
        )
    if len(metadata) > 131072:
        require_extended_contracts_reader(skulk_requires)


@final
class PublisherTrust(Contract):
    """Explicit trusted public keys and current revocation view; never bundle-owned."""

    revision: int = Field(ge=1)
    expires_at: int = Field(gt=0)
    publishers: dict[Identifier, Digest] = Field(min_length=1, max_length=16)
    revoked_publishers: tuple[Identifier, ...] = Field(default=(), max_length=16)
    revoked_artifacts: tuple[Digest, ...] = Field(default=(), max_length=128)


RELEASE_PROTOCOL = 1
"""The release record protocol this SDK writes."""

ACCEPTED_RELEASE_PROTOCOLS: tuple[int, ...] = (1,)
"""Release record protocols this SDK verifies: the current and the previous."""


def accepted_release_protocol(value: int) -> int:
    """Refuse a release record outside the window, naming what is accepted."""
    if value not in ACCEPTED_RELEASE_PROTOCOLS:
        accepted = " or ".join(str(item) for item in ACCEPTED_RELEASE_PROTOCOLS)
        raise ValueError(
            f"release protocol {value} is not accepted; this SDK accepts {accepted}"
        )
    return value


ReleaseProtocol = Annotated[
    int,
    AfterValidator(accepted_release_protocol),
    Field(json_schema_extra={"enum": list(ACCEPTED_RELEASE_PROTOCOLS)}),
]


@final
class Release(Contract):
    """Signed metadata for one immutable Python zipapp and exact host contract."""

    protocol: ReleaseProtocol = RELEASE_PROTOCOL
    publisher: Identifier
    sequence: int = Field(ge=1)
    created_at: int = Field(gt=0)
    expires_at: int = Field(gt=0)
    artifact_name: Literal["bundle.pyz"] = "bundle.pyz"
    artifact_size: int = Field(ge=1, le=64 * 1024 * 1024)
    manifest: Manifest
    platforms: tuple[Literal["darwin", "linux"], ...] = Field(
        min_length=1, max_length=2
    )
    python_requires: Literal["==3.13.*"] = "==3.13.*"
    skulk_build_sha256: Digest
    dependency_lock_sha256: Digest
    state_schema: Identifier
    compatible_state_schemas: tuple[Identifier, ...] = Field(max_length=8)
    permissions: tuple[str, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def portable(self) -> Self:
        """Keep signed metadata portable and bounded before local path resolution."""
        if self.manifest.executable != self.artifact_name:
            raise ValueError("release executable must be the relative artifact name")
        if not self.created_at < self.expires_at:
            raise ValueError("release expiry must follow creation")
        if any(not 0 < len(value) <= 512 for value in self.permissions):
            raise ValueError("permission summary exceeds bound")
        return self

    @property
    def digest(self) -> str:
        """Bind all artifact, permission, compatibility and state-migration metadata."""
        return hashlib.sha256(canonical(self)).hexdigest()


@final
class SignedRelease(Contract):
    """Publisher signature over canonical release metadata, without embedded trust."""

    release: Release
    signature: str = Field(pattern=r"^[a-f0-9]{128}$")


def verify_release(
    signed: SignedRelease,
    trust: PublisherTrust,
    *,
    platform: str,
    skulk_version: str,
    python_version: str,
    dependency_lock_sha256: str,
    clock: Callable[[], int] | None = None,
) -> Release:
    """Authenticate and check host compatibility before installing or enabling code."""
    now = (clock or (lambda: int(time.time())))()
    release = signed.release
    if release.manifest.runtime_dependencies:
        raise Denied("capability dependencies require an isolated v2 runtime")
    verify_release_compatibility(
        release,
        trust,
        platform=platform,
        skulk_version=skulk_version,
        python_version=python_version,
        dependency_lock_sha256=dependency_lock_sha256,
        now=now,
    )
    public = trust.publishers[release.publisher]
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public)).verify(
            bytes.fromhex(signed.signature), canonical(release)
        )
    except (InvalidSignature, ValueError):
        raise Denied("release signature refused") from None
    return release


def verify_release_compatibility(
    release: Release,
    trust: PublisherTrust,
    *,
    platform: str,
    skulk_version: str,
    python_version: str,
    dependency_lock_sha256: str,
    now: int,
) -> None:
    """Check shared compatibility policy for an independently signed envelope.

    This does not authenticate a signature. Callers must verify the signature
    for their envelope before treating the payload as executable authority.
    A release fits any Skulk whose version is in its manifest's range; the
    build its publisher qualified against is recorded, never required, so a
    Skulk update keeps installed capabilities running.
    """
    public = trust.publishers.get(release.publisher)
    if (
        public is None
        or now >= trust.expires_at
        or release.publisher in trust.revoked_publishers
        or release.manifest.executable_sha256 in trust.revoked_artifacts
        or not release.created_at <= now < release.expires_at
        or platform not in release.platforms
        or skulk_version not in SpecifierSet(release.manifest.skulk_requires)
        or python_version not in SpecifierSet(release.python_requires)
        or dependency_lock_sha256 != release.dependency_lock_sha256
    ):
        raise Denied("release trust, revocation or host compatibility refused")


def verify_artifact(release: Release, artifact: Path) -> bytes:
    """Read bounded protected artifact bytes and reject any publisher-hash mismatch."""
    raw = read_private(artifact, limit=release.artifact_size)
    if (
        len(raw) != release.artifact_size
        or hashlib.sha256(raw).hexdigest() != release.manifest.executable_sha256
    ):
        raise Denied("release artifact differs from signed metadata")
    return raw
