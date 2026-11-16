"""Verify and sign native feeds without installing or executing application wheels.

The signing boundary consumes exact locks and an independent rebuild receipt.
It never translates native records through the issued legacy release models.
"""

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import final

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from packaging.utils import canonicalize_name
from pydantic import JsonValue, TypeAdapter

from skulk_capability_sdk.contracts import Denied, canonical
from skulk_capability_sdk.storage import atomic_private, read_private, secure_directory

from .assemble_feed import (
    CLOCK_SKEW_SECONDS,
    ReferenceWheel,
    ReleaseExpectation,
    ReleaseReference,
    expected_wheels,
)
from .native_catalog import (
    NativeCatalog,
    NativeCatalogEntry,
    SignedNativeCatalog,
    native_catalog_message,
    native_entry_from_record,
    verify_native_catalog,
)
from .native_publishing import (
    NativeRuntimeRelease,
    SignedNativeRuntimeRelease,
    native_runtime_message,
)
from .publication_admission import validate_publication_metadata
from .publication_records import read_publication_record
from .releases import PublisherTrust
from .runtime_release import canonical_platform
from .wheelhouse import Lockfile

CATALOG_MAX_BYTES = 262144
RECORD_MAX_BYTES = 262144


@final
@dataclass(frozen=True)
class NativeBuiltRelease:
    """An explicit native record and verified, bounded artifact snapshot."""

    record: SignedNativeRuntimeRelease
    artifacts: dict[str, bytes]

    @property
    def runtime(self) -> NativeRuntimeRelease:
        """Return the native claims without legacy conversion."""
        return self.record.runtime


def read_native_built(directory: Path) -> NativeBuiltRelease:
    """Hash every candidate artifact before any publisher key is used.

    The build runner's throwaway signature confers no publisher trust. The
    independent receipt and lock are checked separately before signing.
    """
    raw = read_private(directory / "release.json", RECORD_MAX_BYTES)
    record = read_publication_record(raw)
    if not isinstance(record, SignedNativeRuntimeRelease):
        raise Denied("native feed requires explicit runtime-five candidates")
    runtime, release = record.runtime, record.runtime.release
    validate_publication_metadata(release, managed=True)
    if len(raw) > RECORD_MAX_BYTES:
        raise Denied("native release exceeds host metadata admission")
    expected = {
        "bundle.pyz": ReferenceWheel(
            size=release.artifact_size, sha256=release.manifest.executable_sha256
        ),
        **{
            wheel.filename: ReferenceWheel(size=wheel.size, sha256=wheel.sha256)
            for wheel in runtime.wheels
        },
    }
    if sum(item.size for item in expected.values()) > 256 * 1024 * 1024:
        raise Denied("native candidate exceeds the signing snapshot budget")
    artifacts: dict[str, bytes] = {}
    for name, wanted in expected.items():
        content = read_private(directory / name, wanted.size)
        if (
            len(content) != wanted.size
            or hashlib.sha256(content).hexdigest() != wanted.sha256
        ):
            raise Denied("native candidate bytes differ from their claims")
        artifacts[name] = content
    return NativeBuiltRelease(record, artifacts)


def native_release_reference(directory: Path, lock: Lockfile) -> ReleaseReference:
    """Record an independent bundle, exact native wrapper and source-built wheels."""
    built = read_native_built(directory)
    registry = {
        str(canonicalize_name(package.name))
        for package in lock.package
        if "registry" in package.source or "url" in package.source
    }
    release = built.runtime.release
    return ReleaseReference(
        bundle_sha256=release.manifest.executable_sha256,
        bundle_size=release.artifact_size,
        manifest_sha256=hashlib.sha256(canonical(release.manifest)).hexdigest(),
        built_wheels={
            wheel.filename: ReferenceWheel(size=wheel.size, sha256=wheel.sha256)
            for wheel in built.runtime.wheels
            if wheel.distribution[0] not in registry
        },
    )


def check_native_release_set(
    releases: Sequence[NativeBuiltRelease],
    expectation: ReleaseExpectation,
    lock: Lockfile,
    root: str,
) -> None:
    """Compare candidates to independently chosen policy, lock and rebuild bytes."""
    if not releases:
        raise Denied("native feed requires built candidates")
    platforms: set[str] = set()
    reference = expectation.reference
    cohorts = {canonical(item.runtime.kernel) for item in releases}
    if len(cohorts) != 1:
        raise Denied("native candidates select different ABI cohorts")
    for built in releases:
        runtime, release = built.runtime, built.runtime.release
        manifest = release.manifest
        family = canonical_platform(runtime.platform)
        if family in platforms:
            raise Denied("native feed repeats an artifact family")
        platforms.add(family)
        if (
            release.publisher != expectation.publisher
            or release.sequence != expectation.sequence
            or release.expires_at != expectation.expires_at
            or manifest.bundle_id != expectation.bundle_id
            or release.state_schema != expectation.state_schema
            or release.compatible_state_schemas != expectation.compatible_state_schemas
            or release.permissions != expectation.permissions
        ):
            raise Denied("native candidate differs from the independent release policy")
        if (
            manifest.executable_sha256 != reference.bundle_sha256
            or release.artifact_size != reference.bundle_size
            or hashlib.sha256(canonical(manifest)).hexdigest()
            != reference.manifest_sha256
        ):
            raise Denied(
                "native bundle or declaration differs from the independent rebuild"
            )
        carried = {
            wheel.filename: ReferenceWheel(size=wheel.size, sha256=wheel.sha256)
            for wheel in runtime.wheels
        }
        if carried != expected_wheels(
            lock,
            root,
            family,
            reference,
            manifest.runtime_dependencies,
            native_candidate=True,
        ):
            raise Denied(
                "native wheels differ from the exact lock and independent rebuild"
            )
    if platforms != {canonical_platform(item) for item in expectation.platforms}:
        raise Denied("native candidate platform set differs from the release policy")


def native_release_path(runtime: NativeRuntimeRelease) -> PurePosixPath:
    """Return a safe immutable feed location without normalizing native IDs."""
    segments = (
        runtime.release.manifest.bundle_id,
        str(runtime.release.sequence),
        runtime.platform,
    )
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", part) for part in segments):
        raise Denied("native identity cannot be represented as a feed path segment")
    return PurePosixPath(*segments)


def assemble_native(
    releases: Sequence[NativeBuiltRelease],
    key: Ed25519PrivateKey,
    *,
    expectation: ReleaseExpectation,
    lock: Lockfile,
    root: str,
    feed_url: str,
    revision: int,
    expires_at: int,
    output: Path,
    now: int,
) -> SignedNativeCatalog:
    """Verify all candidates, then sign exact runtime-five and catalog-four bytes.

    Every refusal occurs before output creation. Existing feed bytes are never
    overwritten; publication and installation authorization remain separate.
    """
    if not feed_url.startswith("https://") or not feed_url.endswith("/"):
        raise Denied("native feed URL must be an HTTPS directory")
    check_native_release_set(releases, expectation, lock, root)
    planned: list[tuple[PurePosixPath, bytes, NativeBuiltRelease]] = []
    entries: list[NativeCatalogEntry] = []
    for built in sorted(releases, key=lambda item: item.runtime.platform):
        runtime, release = built.runtime, built.runtime.release
        if release.created_at > now + CLOCK_SKEW_SECONDS or now >= release.expires_at:
            raise Denied("native candidate is outside its validity window")
        raw = (
            SignedNativeRuntimeRelease(
                runtime=runtime,
                signature=key.sign(native_runtime_message(runtime)).hex(),
            )
            .model_dump_json()
            .encode()
        )
        if len(raw) > RECORD_MAX_BYTES:
            raise Denied("native record exceeds host metadata admission")
        relative = native_release_path(runtime)
        entries.append(
            native_entry_from_record(raw, f"{feed_url}{relative.as_posix()}/")
        )
        planned.append((relative, raw, built))
    catalog = NativeCatalog(
        publisher=expectation.publisher,
        revision=revision,
        created_at=now,
        expires_at=expires_at,
        entries=tuple(entries),
    )
    signed = SignedNativeCatalog(
        catalog=catalog, signature=key.sign(native_catalog_message(catalog)).hex()
    )
    document = signed.model_dump_json().encode()
    if len(document) > CATALOG_MAX_BYTES:
        raise Denied("native catalog exceeds host admission")
    secure_directory(output)
    if any(output.iterdir()):
        raise Denied("native feed destination must be empty")
    for relative, raw, built in planned:
        directory = output.joinpath(*relative.parts)
        secure_directory(directory)
        atomic_private(directory / "release.json", raw)
        for name, content in built.artifacts.items():
            atomic_private(directory / name, content)
    atomic_private(output / "catalog-v4.json", document)
    return signed


def verify_native_feed(
    output: Path, feed_url: str, trust: PublisherTrust, *, now: int
) -> NativeCatalog:
    """Verify every served byte, publisher signature and consent listing."""
    document = read_private(output / "catalog-v4.json", CATALOG_MAX_BYTES)
    signed = SignedNativeCatalog.model_validate_json(document)
    value = TypeAdapter(dict[str, JsonValue]).validate_json(document)
    if value != TypeAdapter(dict[str, JsonValue]).validate_json(
        signed.model_dump_json()
    ):
        raise Denied("native catalog claims differ from exact input")
    catalog = verify_native_catalog(signed, trust, clock=lambda: now)
    for entry in catalog.entries:
        if not entry.feed_url.startswith(feed_url):
            raise Denied("native listing is outside the assembled feed")
        relative = entry.feed_url.removeprefix(feed_url).rstrip("/")
        # Derive local paths from the verified record instead of interpreting a
        # catalog URL as a filesystem path supplied by a publisher.
        parts = PurePosixPath(relative).parts
        if len(parts) != 3 or any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", part) for part in parts
        ):
            raise Denied("native feed path is outside its immutable layout")
        directory = output.joinpath(*parts)
        built = read_native_built(directory)
        raw = read_private(directory / "release.json", RECORD_MAX_BYTES)
        if native_release_path(built.runtime).parts != parts:
            raise Denied("native feed path differs from its release")
        if native_entry_from_record(raw, entry.feed_url) != entry:
            raise Denied("native release differs from its complete consent listing")
        public = trust.publishers.get(built.runtime.release.publisher)
        if (
            public is None
            or built.runtime.release.publisher in trust.revoked_publishers
        ):
            raise Denied("native release publisher is not trusted")
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public)).verify(
            bytes.fromhex(built.record.signature), native_runtime_message(built.runtime)
        )
        revoked = {
            hashlib.sha256(raw).hexdigest(),
            built.runtime.digest,
            built.runtime.release.digest,
            built.runtime.release.manifest.executable_sha256,
            *(wheel.sha256 for wheel in built.runtime.wheels),
        }
        if (
            not built.runtime.release.created_at
            <= now
            < built.runtime.release.expires_at
            or revoked.intersection(trust.revoked_artifacts)
        ):
            raise Denied("native release is expired or revoked")
    return catalog
