"""Build a capability's deterministic zipapp and sign its exact host release contract.

This is the publisher every capability uses, in its own repository or in this
one, installed as ``capability-build``. A capability describes itself in a
``capability.toml`` and the publisher reads that; nothing here names one."""

import argparse
import hashlib
import io
import json
import sys
import time
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from packaging.utils import canonicalize_name
from packaging.version import Version

from skulk_capability_sdk.contracts import (
    MAX_MANIFEST_BYTES,
    Denied,
    Manifest,
    canonical,
)
from skulk_capability_sdk.storage import (
    atomic_private,
    read_private,
    secure_directory,
)
from skulk_capability_tools.describe import (
    validate_discovery_compatibility,
)
from skulk_capability_tools.inspection import read_public
from skulk_capability_tools.packaging import (
    archive,
    load_spec,
    validate_manifest,
    validate_template,
    verify_self_contained,
)
from skulk_capability_tools.publication_admission import validate_publication_metadata
from skulk_capability_tools.release_runtime import (
    declared_requirements,
    dependency_digest,
    skulk_build_digest,
    skulk_source_digest,
)
from skulk_capability_tools.releases import (
    Release,
    SignedRelease,
    validate_release_metadata_compatibility,
)
from skulk_capability_tools.runtime_release import (
    RuntimeRelease,
    RuntimeWheel,
    SignedRuntimeRelease,
    current_platform,
    declared_launchers,
    valid_launcher,
    verify_wheels,
)


def resolve_spec(kind: str | None, spec: Path | None, nodes: Path | None) -> Path:
    """Locate the capability spec from an explicit path or a repository-local name.

    ``--spec`` is the only form the installed ``capability-build`` command
    offers, because a capability in its own repository has nothing else to
    name. ``--kind`` remains for the SDK repository's own tree, which passes
    its ``nodes`` directory through the ``tools/build_release.py`` shim.
    """
    if (kind is None) == (spec is None):
        raise Denied("pass exactly one of --kind or --spec")
    if spec is not None:
        return spec
    if nodes is None:
        raise Denied(
            "--kind names a capability shipped in the SDK repository; use --spec"
        )
    candidate = nodes / str(kind) / "capability.toml"
    if not candidate.is_file():
        names = sorted(p.parent.name for p in nodes.glob("*/capability.toml"))
        raise Denied(f"unknown capability {kind}; this repository ships {names}")
    return candidate


def main(argv: list[str] | None = None, nodes: Path | None = None) -> None:
    """Sign a host-qualified artifact; signing authority never enters the artifact.

    Installed as the ``capability-build`` console script so a capability in its
    own repository publishes with the SDK's publisher rather than a fork of it.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--publisher", required=True)
    parser.add_argument("--sequence", type=int, required=True)
    parser.add_argument("--expires-at", type=int, required=True)
    parser.add_argument("--state-schema", required=True)
    parser.add_argument("--compatible-state-schema", action="append", default=[])
    parser.add_argument("--permission", action="append", required=True)
    if nodes is not None:
        # Only the SDK repository's own shim can name a capability by
        # directory; the installed command has no such directory, so it does
        # not offer the option at all rather than advertise one that fails.
        parser.add_argument(
            "--kind", help="A capability shipped in this repository, by directory name"
        )
    parser.add_argument(
        "--spec",
        type=Path,
        required=nodes is None,
        help="Path to a capability.toml describing what to build",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--cleanup-template",
        type=Path,
        help="Qualified public template to bind into the signed runtime",
    )
    parser.add_argument(
        "--wheelhouse",
        type=Path,
        help="Complete protected wheels for a v2 isolated runtime",
    )
    parser.add_argument(
        "--skulk-source",
        type=Path,
        help=(
            "Record the provenance digest of the Skulk sources in this directory "
            "(a Skulk checkout's src, holding the skulk package) instead of "
            "measuring an installed Skulk build. Hosts accept a release by its "
            "Skulk version range, so the digest is provenance; this lets a "
            "publisher with no Skulk installed, such as a CI runner, build."
        ),
    )
    args = parser.parse_args(argv)
    if sys.platform not in ("darwin", "linux"):
        parser.error("unsupported release host")
    try:
        spec, source = load_spec(
            resolve_spec(getattr(args, "kind", None), args.spec, nodes)
        )
    except Denied as refusal:
        parser.error(str(refusal))
    manifest = Manifest.model_validate_json(
        read_public(args.manifest, MAX_MANIFEST_BYTES)
    )
    validate_manifest(spec, manifest)
    if spec.dependencies and args.wheelhouse is None:
        parser.error("declared capability dependencies require a v2 wheelhouse")
    # The owner answers describe with every descriptor, in one reply the host
    # reads with a fixed bound; refuse here rather than ship a capability its
    # host cannot list.
    try:
        validate_discovery_compatibility(manifest)
    except ValueError as error:
        parser.error(str(error))
    template = (
        validate_template(spec, read_private(args.cleanup_template))
        if args.cleanup_template
        else None
    )
    # Measured before any output exists, so a missing Skulk fails the build
    # without leaving a half-written release directory behind.
    try:
        skulk_build = (
            skulk_source_digest(args.skulk_source)
            if args.skulk_source is not None
            else skulk_build_digest()
        )
    except Denied as refusal:
        parser.error(str(refusal))
    raw = archive(spec, source, template)
    secure_directory(args.output)
    if any(args.output.iterdir()):
        parser.error("release output must be empty; published bytes are immutable")
    verify_self_contained(spec, raw, args.output / ".verify.pyz")
    wheels: list[RuntimeWheel] = []
    if args.wheelhouse is not None:
        paths = sorted(args.wheelhouse.glob("*.whl"))
        if not 1 <= len(paths) <= 64:
            parser.error("runtime requires a complete bounded wheelhouse")
        # The bundle no longer carries the owner and management shims, so the
        # runtime must install a wheel that declares those launchers or nothing
        # can start the owner. Build the SDK wheel into the wheelhouse.
        launchers: dict[str, str] = {}
        for path in paths:
            with zipfile.ZipFile(path) as wheel_archive:
                launchers.update(declared_launchers(wheel_archive))
        missing = {"owner", "manage"} - set(launchers)
        malformed = {
            name for name, target in launchers.items() if not valid_launcher(target)
        }
        if missing or malformed:
            parser.error(
                "the wheelhouse must carry a wheel declaring valid owner and "
                f"manage launchers; missing {sorted(missing)}, malformed "
                f"{sorted(malformed)}; add the SDK wheel (uv build --wheel in "
                "the SDK checkout)"
            )
        for path in paths:
            content = read_private(path, 64 * 1024 * 1024)
            wheels.append(
                RuntimeWheel(
                    filename=path.name,
                    size=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                )
            )
    inventory = dict(sorted(wheel.distribution for wheel in wheels))
    for requirement in declared_requirements(spec.dependencies):
        name = str(canonicalize_name(requirement.name))
        if (
            name not in inventory
            or Version(inventory[name]) not in requirement.specifier
        ):
            parser.error("wheelhouse does not satisfy the capability requirements")
    lock = (
        hashlib.sha256(
            json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if wheels
        else dependency_digest()
    )
    release = Release(
        publisher=args.publisher,
        sequence=args.sequence,
        created_at=int(time.time()),
        expires_at=args.expires_at,
        artifact_size=len(raw),
        manifest=manifest.model_copy(
            update={
                "executable": "bundle.pyz",
                "executable_sha256": hashlib.sha256(raw).hexdigest(),
            }
        ),
        platforms=("darwin" if sys.platform == "darwin" else "linux",),
        dependency_lock_sha256=lock,
        skulk_build_sha256=skulk_build,
        state_schema=args.state_schema,
        compatible_state_schemas=tuple(args.compatible_state_schema),
        permissions=tuple(args.permission),
    )
    key = Ed25519PrivateKey.from_private_bytes(read_private(args.private_key, 32))
    signed: SignedRelease | SignedRuntimeRelease
    runtime_artifacts: dict[str, bytes] = {}
    if wheels:
        if args.wheelhouse is None:
            parser.error("dependency artifacts require a wheelhouse")
        runtime = RuntimeRelease(
            release=release, platform=current_platform(), wheels=tuple(wheels)
        )
        runtime_artifacts = verify_wheels(runtime, args.wheelhouse)
        drifted = modules_differing_from_wheels(raw, runtime_artifacts)
        if drifted:
            parser.error(
                "the bundle packs modules that differ from the runtime wheel "
                f"carrying the same path ({', '.join(drifted[:5])}); build the "
                "bundle and the wheelhouse from the same source"
            )
        signed = SignedRuntimeRelease(
            runtime=runtime, signature=key.sign(canonical(runtime)).hex()
        )
    else:
        signed = SignedRelease(
            release=release, signature=key.sign(canonical(release)).hex()
        )
    metadata = signed.model_dump_json().encode()
    # Hosts read the signed metadata, which carries the whole manifest, with a
    # fixed bound; this is the ceiling on how much a capability can declare.
    try:
        validate_release_metadata_compatibility(manifest.skulk_requires, metadata)
        validate_publication_metadata(release, managed=bool(wheels))
    except ValueError as error:
        parser.error(str(error))
    atomic_private(args.output / "bundle.pyz", raw, executable=True)
    atomic_private(args.output / "release.json", metadata)
    for filename, content in runtime_artifacts.items():
        atomic_private(args.output / filename, content)
    print(signed.model_dump_json() if wheels else release.model_dump_json())


def modules_differing_from_wheels(bundle: bytes, wheels: dict[str, bytes]) -> list[str]:
    """Python sources the bundle packs that a runtime wheel carries differently.

    The owner imports the SDK from its wheel while the child imports whatever
    the bundle packs first on its path; a module present in both must be the
    same bytes, or the two sides of one installation run different code.
    """
    carried: dict[str, bytes] = {}
    for content in wheels.values():
        with zipfile.ZipFile(io.BytesIO(content)) as wheel:
            for name in wheel.namelist():
                if name.endswith(".py"):
                    carried[name] = wheel.read(name)
    drifted: list[str] = []
    with zipfile.ZipFile(io.BytesIO(bundle)) as packed:
        for name in sorted(packed.namelist()):
            if name in carried and packed.read(name) != carried[name]:
                drifted.append(name)
    return drifted


if __name__ == "__main__":
    main()
