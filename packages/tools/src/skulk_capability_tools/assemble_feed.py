"""Assemble a capability release run's feed: signed releases and their catalog.

The release pipeline builds one runtime release per platform, each on its own
platform, because the publisher verifies wheels against the interpreter it runs
in. Those builds sign with a key that exists only inside their own job, which
proves the whole build path without the publisher key. This step is the only
one that holds the publisher key, and it signs nothing it can only take on
trust from the build jobs. Before signing it requires:

1. every byte a release names to match that release's claims;
2. the releases to be exactly one release for exactly the expected platforms,
   with the expected publisher, sequence, expiry, bundle, permissions and
   state schemas (the values the pipeline chose, not the ones the builds
   report);
3. every registry wheel of every platform to be exactly the wheel the
   caller's lock selects for that platform's floor, by name and SHA-256;
4. the bundle, the manifest and every wheel built from locked source to match
   an independent rebuild in a job that never saw these releases.

It then signs each release's unchanged claims and a protocol 2 or 3 catalog
listing every platform with its Skulk range, lays the result out as served
(``<bundle>/<sequence>/<platform>/`` beside ``catalog.json``), and reads it
back the way a host does.

The catalog written here describes one run's releases. It is the run's
qualification artifact; the store's catalog, which merges every published
release, is assembled and signed by publication.
"""

import argparse
import hashlib
import json
import os
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Final, final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from skulk_capability_sdk.contracts import Denied, Manifest, canonical
from skulk_capability_sdk.storage import atomic_private, read_private, secure_directory

from .catalog import (
    CATALOG_MAX_BYTES,
    Catalog,
    CatalogEntry,
    SignedCatalog,
    entry_from_record,
    verify_catalog,
)
from .describe import validate_discovery_compatibility
from .publication_admission import validate_publication_metadata
from .releases import (
    RELEASE_METADATA_MAX_BYTES,
    PublisherTrust,
    validate_release_metadata_compatibility,
)
from .runtime_release import RuntimeRelease, SignedRuntimeRelease, canonical_platform
from .wheelhouse import (
    DEFAULT_ROOT,
    Lockfile,
    load_lock,
    platform_environment,
    platform_policy,
    runtime_closure,
    select_wheel,
    supported_tags,
)

CLOCK_SKEW_SECONDS: Final = 300
"""How far ahead of this machine a build runner's clock may have been."""

_SEGMENT = re.compile(r"^[A-Za-z0-9._~-]+$")
"""Characters a feed URL path segment may hold, as the catalog's feed URL allows."""

_HEX_KEY = re.compile(r"^[0-9a-f]{64}$")

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


@final
class ReferenceWheel(BaseModel):
    """The digest and size of one wheel an independent rebuild produced."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    sha256: Sha256 = Field(description="SHA-256 of the wheel bytes.")
    size: int = Field(ge=1, description="Exact size in bytes.")


@final
class ReleaseReference(BaseModel):
    """What an independent rebuild of the release produced.

    The bundle and the wheels built from locked source cannot be derived from
    the lock alone, so a separate job rebuilds them from the same inputs and
    hands the signing job only these digests to compare against.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    bundle_sha256: Sha256 = Field(description="SHA-256 of bundle.pyz.")
    bundle_size: int = Field(ge=1, description="Size of bundle.pyz in bytes.")
    manifest_sha256: Sha256 = Field(
        description="SHA-256 of the canonical signed manifest."
    )
    built_wheels: dict[str, ReferenceWheel] = Field(
        description="Wheels built from locked source, by filename."
    )


@final
class ReleaseExpectation(BaseModel):
    """Everything the signing job requires of the releases before it signs."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    publisher: str = Field(description="Publisher identity.")
    sequence: int = Field(ge=1, description="The run's sequence.")
    expires_at: int = Field(gt=0, description="The run's release expiry.")
    bundle_id: str = Field(description="The capability's bundle identity.")
    state_schema: str = Field(description="Durable state schema.")
    compatible_state_schemas: tuple[str, ...] = Field(
        description="State schemas the release reads, in order."
    )
    permissions: tuple[str, ...] = Field(
        min_length=1, description="Signed permission summaries, in order."
    )
    platforms: tuple[str, ...] = Field(
        min_length=1, description="Exactly the release platforms to sign."
    )
    reference: ReleaseReference = Field(description="The independent rebuild.")


@final
@dataclass(frozen=True)
class BuiltRelease:
    """One platform's verified release record and the bytes it names."""

    record: SignedRuntimeRelease
    artifacts: dict[str, bytes]

    @property
    def runtime(self) -> RuntimeRelease:
        """The signed runtime claims."""
        return self.record.runtime


def read_built(directory: Path) -> BuiltRelease:
    """Read one built release and check every byte it names against its claims.

    The signature is not checked: it was made with a key that existed only in
    the job that built the release, so it proves nothing here. What matters is
    that the claims about to be signed with the publisher key describe exactly
    the bytes that will be served beside them.
    """
    try:
        raw = read_private(directory / "release.json", RELEASE_METADATA_MAX_BYTES)
        record = SignedRuntimeRelease.model_validate_json(raw)
        manifest = record.runtime.release.manifest
        validate_discovery_compatibility(manifest)
        validate_release_metadata_compatibility(manifest.skulk_requires, raw)
        validate_publication_metadata(record.runtime.release, managed=True)
    except (OSError, ValidationError, ValueError) as invalid:
        raise Denied(f"{directory} holds no runtime release: {invalid}") from None
    runtime = record.runtime
    release = runtime.release
    artifacts: dict[str, bytes] = {}
    bundle = read_private(directory / "bundle.pyz", release.artifact_size)
    if (
        len(bundle) != release.artifact_size
        or hashlib.sha256(bundle).hexdigest() != release.manifest.executable_sha256
    ):
        raise Denied(f"{directory}/bundle.pyz differs from its signed claims")
    artifacts["bundle.pyz"] = bundle
    for wheel in runtime.wheels:
        content = read_private(directory / wheel.filename, wheel.size)
        if len(content) != wheel.size or hashlib.sha256(content).hexdigest() != (
            wheel.sha256
        ):
            raise Denied(f"{directory}/{wheel.filename} differs from its claims")
        artifacts[wheel.filename] = content
    return BuiltRelease(record=record, artifacts=artifacts)


def manifest_digest(manifest: Manifest) -> str:
    """SHA-256 of a manifest exactly as it is signed."""
    return hashlib.sha256(canonical(manifest)).hexdigest()


def _registry_names(lock: Lockfile) -> set[str]:
    return {
        str(canonicalize_name(package.name))
        for package in lock.package
        if "registry" in package.source or "url" in package.source
    }


def release_reference(directory: Path, lock: Lockfile) -> ReleaseReference:
    """Describe a built release as an independent rebuild, for comparison.

    Every wheel of a package the lock does not take from a registry was built
    from locked source; those, the bundle and the manifest are what the lock
    cannot pin and a rebuild must.
    """
    built = read_built(directory)
    release = built.runtime.release
    registry = _registry_names(lock)
    return ReleaseReference(
        bundle_sha256=release.manifest.executable_sha256,
        bundle_size=release.artifact_size,
        manifest_sha256=manifest_digest(release.manifest),
        built_wheels={
            wheel.filename: ReferenceWheel(sha256=wheel.sha256, size=wheel.size)
            for wheel in built.runtime.wheels
            if wheel.distribution[0] not in registry
        },
    )


def expected_wheels(
    lock: Lockfile,
    root: str,
    family: str,
    reference: ReleaseReference,
    additional: tuple[str, ...] = (),
) -> dict[str, ReferenceWheel]:
    """The exact wheels a release for one platform must carry, by filename.

    Registry packages are selected from the lock for the platform's floor,
    exactly as the wheelhouse recipe selects them, and carry the lock's
    digest. A package built from locked source must have exactly one wheel of
    its locked name and version in the independent rebuild.
    """
    tags = supported_tags(platform_policy(family))
    expected: dict[str, ReferenceWheel] = {}
    for package in runtime_closure(
        lock, root, platform_environment(family), additional
    ):
        if "registry" in package.source or "url" in package.source:
            file = select_wheel(package, tags)
            if file.size is None:
                raise Denied(f"the lock records no size for {file.filename}")
            expected[file.filename] = ReferenceWheel(sha256=file.sha256, size=file.size)
            continue
        matches = [
            filename
            for filename in reference.built_wheels
            if parse_wheel_filename(filename)[0] == canonicalize_name(package.name)
            and parse_wheel_filename(filename)[1] == Version(package.version or "0")
        ]
        if len(matches) != 1:
            raise Denied(
                f"the rebuild holds {len(matches)} wheels of {package.name} "
                f"{package.version}; exactly one is required"
            )
        expected[matches[0]] = reference.built_wheels[matches[0]]
    return expected


def check_release_set(
    releases: Sequence[BuiltRelease],
    expectation: ReleaseExpectation,
    lock: Lockfile,
    root: str = DEFAULT_ROOT,
) -> None:
    """Refuse anything but exactly the expected release for the expected platforms.

    The values compared against come from the pipeline (its sequence and
    expiry, the caller's declared bundle, permissions and state schemas, and
    platform set), the caller's lock, and an independent rebuild; never from
    the build jobs whose output is being signed.
    """
    if not releases:
        raise Denied("name at least one built release")
    reference = expectation.reference
    platforms: set[str] = set()
    for built in releases:
        runtime = built.runtime
        release = runtime.release
        manifest = release.manifest
        family = canonical_platform(runtime.platform)
        observed = {
            "publisher": release.publisher,
            "sequence": release.sequence,
            "release expiry": release.expires_at,
            "bundle": manifest.bundle_id,
            "state schema": release.state_schema,
            "compatible state schemas": release.compatible_state_schemas,
            "permissions": release.permissions,
        }
        wanted = {
            "publisher": expectation.publisher,
            "sequence": expectation.sequence,
            "release expiry": expectation.expires_at,
            "bundle": expectation.bundle_id,
            "state schema": expectation.state_schema,
            "compatible state schemas": expectation.compatible_state_schemas,
            "permissions": expectation.permissions,
        }
        for field, value in observed.items():
            if value != wanted[field]:
                raise Denied(
                    f"the {family} release states {field} {value!r}, "
                    f"not {wanted[field]!r}"
                )
        if family in platforms:
            raise Denied(f"two releases are built for {family}")
        platforms.add(family)
        if (
            manifest.executable_sha256 != reference.bundle_sha256
            or release.artifact_size != reference.bundle_size
        ):
            raise Denied(f"the {family} bundle differs from the independent rebuild")
        if manifest_digest(manifest) != reference.manifest_sha256:
            raise Denied(f"the {family} manifest differs from the independent rebuild")
        carried = {
            wheel.filename: ReferenceWheel(sha256=wheel.sha256, size=wheel.size)
            for wheel in runtime.wheels
        }
        expected = expected_wheels(
            lock, root, family, reference, manifest.runtime_dependencies
        )
        if carried != expected:
            different = sorted(
                name
                for name in set(carried) | set(expected)
                if carried.get(name) != expected.get(name)
            )
            raise Denied(
                f"the {family} wheels differ from the lock and the rebuild: "
                + ", ".join(different[:5])
            )
    wanted_platforms = {canonical_platform(item) for item in expectation.platforms}
    if platforms != wanted_platforms:
        raise Denied(
            f"the releases cover {sorted(platforms)}, not {sorted(wanted_platforms)}"
        )


def load_signing_key(
    *, path: Path | None, variable: str | None, public_key: str
) -> Ed25519PrivateKey:
    """Load the signing key and refuse it unless it has the expected public key.

    The key comes from an owner-only file of 32 raw bytes, or from an
    environment variable holding those bytes as 64 hexadecimal characters,
    which is how the pipeline hands over a protected secret without writing it
    to disk. Refusals never echo key material.
    """
    if (path is None) == (variable is None):
        raise Denied("name the signing key by exactly one of a file or a variable")
    expected = public_key.strip().lower()
    if not _HEX_KEY.fullmatch(expected):
        raise Denied("the expected public key must be 64 hexadecimal characters")
    if path is not None:
        material = read_private(path, 32)
    else:
        assert variable is not None
        value = os.environ.get(variable, "").strip().lower()
        if not _HEX_KEY.fullmatch(value):
            raise Denied(
                f"{variable} must hold the 32-byte Ed25519 private key as 64 "
                "hexadecimal characters"
            )
        material = bytes.fromhex(value)
    if len(material) != 32:
        raise Denied("the signing key must be 32 bytes")
    key = Ed25519PrivateKey.from_private_bytes(material)
    actual = key.public_key().public_bytes_raw().hex()
    if actual != expected:
        raise Denied(
            f"the signing key's public key is {actual}, not the expected {expected}"
        )
    return key


def release_path(runtime: RuntimeRelease) -> PurePosixPath:
    """Where one platform's release lives under the feed root."""
    segments = (
        runtime.release.manifest.bundle_id,
        str(runtime.release.sequence),
        runtime.platform,
    )
    for segment in segments:
        if not _SEGMENT.fullmatch(segment):
            raise Denied(f"{segment} cannot be a feed URL path segment")
    return PurePosixPath(*segments)


def assemble(
    releases: Sequence[BuiltRelease],
    key: Ed25519PrivateKey,
    *,
    expectation: ReleaseExpectation,
    lock: Lockfile,
    root: str = DEFAULT_ROOT,
    feed_url: str,
    revision: int,
    expires_at: int,
    output: Path,
    now: int,
) -> SignedCatalog:
    """Check, then sign every release and the catalog; write the feed to ``output``.

    Everything is checked, signed and validated in memory first, and the
    output must be new or empty: published bytes are immutable, so a feed is
    written whole or not at all.
    """
    if not feed_url.startswith("https://") or not feed_url.endswith("/"):
        raise Denied("the feed URL must be an HTTPS directory ending in /")
    check_release_set(releases, expectation, lock, root)
    for built in releases:
        release = built.runtime.release
        # Build runners' clocks may run a little ahead of this one; a release
        # from the future beyond that, or one already expired, is refused.
        if release.created_at > now + CLOCK_SKEW_SECONDS or now >= release.expires_at:
            raise Denied(f"release {release.sequence} is outside its validity window")
    planned: list[tuple[PurePosixPath, bytes, BuiltRelease]] = []
    entries: list[CatalogEntry] = []
    for built in sorted(releases, key=lambda item: item.runtime.platform):
        signed = SignedRuntimeRelease(
            runtime=built.runtime, signature=key.sign(canonical(built.runtime)).hex()
        )
        raw = signed.model_dump_json().encode()
        try:
            validate_discovery_compatibility(built.runtime.release.manifest)
            validate_publication_metadata(built.runtime.release, managed=True)
            validate_release_metadata_compatibility(
                built.runtime.release.manifest.skulk_requires, raw
            )
        except ValueError as invalid:
            raise Denied(str(invalid)) from None
        relative = release_path(built.runtime)
        planned.append((relative, raw, built))
        entries.append(entry_from_record(raw, f"{feed_url}{relative.as_posix()}/"))
    catalog = Catalog(
        protocol=3 if any(len(entry.descriptors) > 8 for entry in entries) else 2,
        publisher=expectation.publisher,
        revision=revision,
        created_at=now,
        expires_at=expires_at,
        entries=tuple(entries),
    )
    signed_catalog = SignedCatalog(
        catalog=catalog, signature=key.sign(canonical(catalog)).hex()
    )
    document = signed_catalog.model_dump_json().encode()
    if len(document) > CATALOG_MAX_BYTES:
        raise Denied(f"the catalog is {len(document)} bytes; hosts read 256 KiB")
    secure_directory(output)
    if any(output.iterdir()):
        raise Denied(f"{output} must be empty; a feed is written whole")
    for relative, raw, built in planned:
        directory = output.joinpath(*relative.parts)
        secure_directory(directory)
        atomic_private(directory / "release.json", raw)
        for filename, content in built.artifacts.items():
            atomic_private(directory / filename, content)
    atomic_private(output / "catalog.json", document)
    return signed_catalog


def verify_feed(
    output: Path, feed_url: str, trust: PublisherTrust, *, now: int
) -> Catalog:
    """Read an assembled feed the way a host does, refusing what fails to verify.

    The catalog is checked with the SDK's reader against ``trust``; every entry
    is then followed to its directory, where the served record must hash to the
    listed digest, carry a valid publisher signature over the listed claims, and
    sit beside exactly the bytes it names.
    """
    document = read_private(output / "catalog.json", CATALOG_MAX_BYTES)
    catalog = verify_catalog(
        SignedCatalog.model_validate_json(document), trust, clock=lambda: now
    )
    public = Ed25519PublicKey.from_public_bytes(
        bytes.fromhex(trust.publishers[catalog.publisher])
    )
    for entry in catalog.entries:
        if not entry.feed_url.startswith(feed_url):
            raise Denied(f"{entry.feed_url} is outside the feed")
        directory = output / entry.feed_url.removeprefix(feed_url)
        raw = read_private(directory / "release.json", RELEASE_METADATA_MAX_BYTES)
        if hashlib.sha256(raw).hexdigest() != entry.release_sha256:
            raise Denied(f"{entry.feed_url}release.json differs from the catalog")
        built = read_built(directory)
        try:
            public.verify(
                bytes.fromhex(built.record.signature), canonical(built.runtime)
            )
        except (InvalidSignature, ValueError):
            raise Denied(f"{entry.feed_url} is not signed by the publisher") from None
        if (
            built.runtime.digest != entry.release_digest
            or built.runtime.platform != entry.runtime_platform
            or sum(len(content) for content in built.artifacts.values())
            != entry.transfer_bytes
        ):
            raise Denied(f"{entry.feed_url} differs from its catalog entry")
    return catalog


def _summary(catalog: Catalog, document: bytes, public: str) -> Mapping[str, object]:
    return {
        "publisher": catalog.publisher,
        "public_key": public,
        "catalog_revision": catalog.revision,
        "catalog_sha256": hashlib.sha256(document).hexdigest(),
        "entries": [
            {
                "bundle_id": entry.bundle_id,
                "sequence": entry.sequence,
                "runtime_platform": entry.runtime_platform,
                "skulk_requires": entry.skulk_requires,
                "feed_url": entry.feed_url,
                "release_sha256": entry.release_sha256,
                "transfer_bytes": entry.transfer_bytes,
            }
            for entry in catalog.entries
        ],
    }


def main(argv: list[str] | None = None) -> None:
    """Check built releases, sign them and their catalog into a feed directory.

    Installed as ``capability-feed``. Prints a JSON summary. ``--trust-output``
    writes the publisher trust record matching the key, so anyone holding the
    feed can verify it; the pipeline ships it beside a feed signed with a
    throwaway key.
    """
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    key_source = parser.add_mutually_exclusive_group(required=True)
    key_source.add_argument("--private-key", type=Path, help="Owner-only key file")
    key_source.add_argument(
        "--private-key-env",
        metavar="VARIABLE",
        help="Environment variable holding the key as 64 hex characters",
    )
    parser.add_argument(
        "--public-key",
        required=True,
        help="The Ed25519 public key (hex) the signing key must have",
    )
    parser.add_argument("--publisher", required=True)
    parser.add_argument(
        "--feed-url",
        required=True,
        help="The HTTPS directory the output will be served at, ending in /",
    )
    parser.add_argument("--catalog-revision", type=int, required=True)
    parser.add_argument("--catalog-expires-at", type=int, required=True)
    parser.add_argument("--sequence", type=int, required=True)
    parser.add_argument("--release-expires-at", type=int, required=True)
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--state-schema", required=True)
    parser.add_argument("--compatible-state-schema", action="append", default=[])
    parser.add_argument("--permission", action="append", required=True)
    parser.add_argument(
        "--platform",
        action="append",
        required=True,
        help="A release platform to sign; the set must be exactly these",
    )
    parser.add_argument("--lock", type=Path, required=True, help="The caller's uv lock")
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument(
        "--reference",
        type=Path,
        required=True,
        help="The independent rebuild's ReleaseReference, as JSON",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trust-output", type=Path)
    parser.add_argument(
        "releases", nargs="+", type=Path, help="Built release directories"
    )
    args = parser.parse_args(argv)
    now = int(time.time())
    try:
        key = load_signing_key(
            path=args.private_key,
            variable=args.private_key_env,
            public_key=args.public_key,
        )
        expectation = ReleaseExpectation(
            publisher=args.publisher,
            sequence=args.sequence,
            expires_at=args.release_expires_at,
            bundle_id=args.bundle_id,
            state_schema=args.state_schema,
            compatible_state_schemas=tuple(args.compatible_state_schema),
            permissions=tuple(args.permission),
            platforms=tuple(args.platform),
            reference=ReleaseReference.model_validate_json(args.reference.read_bytes()),
        )
        lock = load_lock(args.lock)
        built = [read_built(directory) for directory in args.releases]
        assemble(
            built,
            key,
            expectation=expectation,
            lock=lock,
            root=args.root,
            feed_url=args.feed_url,
            revision=args.catalog_revision,
            expires_at=args.catalog_expires_at,
            output=args.output,
            now=now,
        )
        public = key.public_key().public_bytes_raw().hex()
        trust = PublisherTrust(
            revision=1,
            expires_at=args.catalog_expires_at,
            publishers={args.publisher: public},
        )
        catalog = verify_feed(args.output, args.feed_url, trust, now=now)
    except (Denied, ValidationError, ValueError, OSError) as refusal:
        parser.error(str(refusal))
    if args.trust_output is not None:
        atomic_private(args.trust_output, trust.model_dump_json().encode())
    document = (args.output / "catalog.json").read_bytes()
    print(json.dumps(_summary(catalog, document, public), indent=2))


if __name__ == "__main__":
    main()
