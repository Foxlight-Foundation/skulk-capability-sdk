"""Inspect public release signatures and bytes without granting host admission."""

import hashlib
import os
import stat
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.specifiers import SpecifierSet
from pydantic import JsonValue

from skulk_capability_sdk.contracts import Denied, canonical

from .catalog import signed_claims, signed_record
from .releases import RELEASE_METADATA_MAX_BYTES, PublisherTrust
from .runtime_release import SignedRuntimeRelease


def read_public(path: Path, limit: int) -> bytes:
    """Read bounded public metadata/artifacts without following links or FIFOs."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise Denied("public artifact must be a regular file")
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise Denied("public artifact exceeds its byte bound")
    return raw


def inspect_release(
    path: Path,
    trust_path: Path,
    *,
    artifacts: Path | None = None,
    declared_host_version: str | None = None,
) -> dict[str, JsonValue]:
    """Verify publisher/expiry/revocation/signature, optionally hashing all bytes.

    The returned host-version result checks only an explicitly supplied version.
    Installation and execution require the host's independent authorization.
    """
    record = signed_record(read_public(path, RELEASE_METADATA_MAX_BYTES))
    trust = PublisherTrust.model_validate_json(read_public(trust_path, 65536))
    claims = signed_claims(record)
    release = (
        record.runtime.release
        if isinstance(record, SignedRuntimeRelease)
        else record.release
    )
    now = int(time.time())
    public = trust.publishers.get(release.publisher)
    revoked = {release.manifest.executable_sha256}
    if isinstance(record, SignedRuntimeRelease):
        revoked.add(record.runtime.digest)
        revoked.update(wheel.sha256 for wheel in record.runtime.wheels)
    if (
        public is None
        or now >= trust.expires_at
        or release.publisher in trust.revoked_publishers
        or revoked.intersection(trust.revoked_artifacts)
        or not release.created_at <= now < release.expires_at
    ):
        raise Denied("release trust, expiry or revocation refused")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public)).verify(
            bytes.fromhex(record.signature), canonical(claims)
        )
    except (InvalidSignature, ValueError):
        raise Denied("release signature refused") from None
    if artifacts is not None:
        bundle = read_public(artifacts / "bundle.pyz", release.artifact_size)
        if (
            len(bundle) != release.artifact_size
            or hashlib.sha256(bundle).hexdigest() != release.manifest.executable_sha256
        ):
            raise Denied("bundle differs from signed bytes")
        if isinstance(record, SignedRuntimeRelease):
            for wheel in record.runtime.wheels:
                content = read_public(artifacts / wheel.filename, wheel.size)
                if (
                    len(content) != wheel.size
                    or hashlib.sha256(content).hexdigest() != wheel.sha256
                ):
                    raise Denied("runtime wheel differs from signed bytes")
    return {
        "signature_verified": True,
        "artifact_bytes_verified": artifacts is not None,
        "bundle_id": release.manifest.bundle_id,
        "bundle_version": release.manifest.bundle_version,
        "publisher": release.publisher,
        "sequence": release.sequence,
        "release_sha256": claims.digest,
        "declared_host_version_compatible": (
            declared_host_version in SpecifierSet(release.manifest.skulk_requires)
            if declared_host_version is not None
            else None
        ),
        "permissions": list(release.permissions),
    }
