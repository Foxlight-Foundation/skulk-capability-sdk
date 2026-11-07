"""Exact resolved host dependency inventory for private release compatibility."""

import hashlib
import importlib.metadata
import json
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

LAUNCHER_GROUP = "skulk.capability_runtime"
"""Entry point group a wheel uses to declare the runtime launchers it carries."""

ROOT_DEPENDENCIES = (
    # The public authoring SDK is the common core every capability is written
    # against; every runtime carries it as an ordinary pinned wheel.
    "skulk-capability-sdk",
    "pydantic",
    "jsonschema",
    "packaging",
    "referencing",
    "cryptography",
    "httpx",
    "keyring",
)


def declared_requirements(values: tuple[str, ...]) -> tuple[Requirement, ...]:
    """Parse bounded named runtime roots, excluding resolver and installer inputs.

    Extras, markers and URLs cannot be represented by the signed root-name list.
    Authors declare their required extra distributions explicitly instead.
    """
    if len(values) > 32:
        raise ValueError("at most 32 capability dependencies may be declared")
    requirements: list[Requirement] = []
    names: set[str] = set()
    for value in values:
        if len(value) > 512:
            raise ValueError("runtime requirement exceeds bound")
        requirement = Requirement(value)
        name = str(canonicalize_name(requirement.name))
        if (
            len(name) > 128
            or name == "pip"
            or name in names
            or requirement.url is not None
            or requirement.extras
            or requirement.marker is not None
        ):
            raise ValueError("runtime roots must be unique named requirements")
        names.add(name)
        requirements.append(requirement)
    return tuple(requirements)


def dependency_roots(values: tuple[str, ...]) -> tuple[str, ...]:
    """Return sorted canonical root names for the immutable signed manifest."""
    return tuple(
        sorted(
            str(canonicalize_name(one.name)) for one in declared_requirements(values)
        )
    )


def dependency_inventory(
    *, launcher_roots: bool = True, additional: tuple[str, ...] = ()
) -> dict[str, str]:
    """Resolve installed transitive runtime requirements for this host platform.

    No packages are installed or updated. Optional extras are excluded because
    the plugin runtime does not request them. Platform-specific dependencies are
    included, so publishers produce metadata for each supported runtime lock.

    With ``launcher_roots``, a distribution that declares runtime launchers is a
    root of its own: the owner starts from it and nothing in the fixed root list
    requires it. The host's wheel verification treats such a wheel the same way,
    so the signed wheel list and this closure agree on a runtime that carries
    the SDK as a wheel. The v1 dependency digest is the fixed-root closure only;
    a v1 release never carries the SDK as a wheel, and its signed digest must
    not move when the SDK happens to be installed alongside.
    """
    pending = [Requirement(name) for name in ROOT_DEPENDENCIES]
    pending.extend(declared_requirements(additional))
    if launcher_roots:
        for entry in importlib.metadata.entry_points(group=LAUNCHER_GROUP):
            if entry.dist is not None:
                pending.append(Requirement(entry.dist.name))
    versions: dict[str, str] = {}
    environment: dict[str, str] = {
        key: str(value) for key, value in default_environment().items()
    }
    environment["extra"] = ""
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        distribution = importlib.metadata.distribution(name)
        if (
            name == "pip"
            or requirement.url is not None
            or requirement.extras
            or Version(distribution.version) not in requirement.specifier
        ):
            raise ValueError("installed runtime dependency is incompatible")
        if name in versions:
            continue
        versions[name] = distribution.version
        for raw in distribution.requires or []:
            requirement = Requirement(raw)
            if requirement.marker is None or requirement.marker.evaluate(environment):
                pending.append(requirement)
    return dict(sorted(versions.items()))


def dependency_digest() -> str:
    """Hash the actual resolved runtime rather than a caller-supplied lock label."""
    return hashlib.sha256(
        json.dumps(
            dependency_inventory(launcher_roots=False),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def skulk_build_digest() -> str:
    """Hash actual Skulk package sources and native bindings without importing them.

    Relative names and file contents bind the runnable build, not its installation
    path or reported version. Bytecode, test fixtures and desktop metadata are
    excluded, as Skulk's own fingerprint excludes them. Missing packages,
    symlinked members and excessively large inventories fail closed.
    """
    import importlib.util

    from skulk_capability_sdk.contracts import Denied

    roots: list[tuple[str, Path]] = []
    for package in ("skulk", "skulk_pyo3_bindings"):
        specification = importlib.util.find_spec(package)
        if specification is None or specification.origin is None:
            raise Denied("qualified Skulk build is unavailable")
        roots.append((package, Path(specification.origin).parent))
    return _measure(roots)


def skulk_source_digest(source: Path) -> str:
    """Hash the Skulk Python package in a source tree, for release provenance.

    ``source`` is a Skulk checkout's ``src`` directory, the one holding the
    ``skulk`` package. A source tree has no native bindings, so this measures
    the Python package alone, with exactly the rules ``skulk_build_digest``
    applies to it. It therefore never equals the digest a host measures for
    itself, which includes its bindings. That is acceptable because the field
    it fills is provenance: a host accepts a release by its Skulk version range
    (the manifest's ``skulk_requires``), never by this digest. It lets a
    publisher with no Skulk installed, such as a CI runner, record which Skulk
    sources it qualified against, and anyone can recompute it from that commit.
    """
    from skulk_capability_sdk.contracts import Denied

    package = source / "skulk"
    if (
        source.is_symlink()
        or package.is_symlink()
        or not (package / "__init__.py").is_file()
    ):
        raise Denied(f"{source} does not hold the Skulk package")
    return _measure([("skulk", package)])


def _measure(roots: list[tuple[str, Path]]) -> str:
    """Hash named package directories the way Skulk fingerprints its own build."""
    from skulk_capability_sdk.contracts import Denied

    digest = hashlib.sha256()
    count, total = 0, 0
    for package, root in roots:
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if any(
                part in ("__pycache__", "tests", ".pytest_cache")
                for part in relative.parts
            ):
                continue
            if path.is_symlink():
                raise Denied("Skulk build contains an unsupported file")
            # The regular files a desktop file browser leaves behind (Finder's
            # .DS_Store and AppleDouble ._ companions) are not part of the build.
            # This must match Skulk's own fingerprint exactly
            # (skulk.extensions.runtime_files.is_desktop_metadata in
            # measure_host): a checkout browsed in Finder otherwise pins releases
            # to a build no host reports.
            if (
                path.name == ".DS_Store" or path.name.startswith("._")
            ) and path.is_file():
                continue
            if path.suffix in (".pyc", ".pyo") or path.is_dir():
                continue
            if not path.is_file():
                raise Denied("Skulk build contains an unsupported file")
            count += 1
            total += path.stat().st_size
            if count > 20000 or total > 512 * 1024 * 1024:
                raise Denied("Skulk build exceeds verification bound")
            content = hashlib.sha256()
            with path.open("rb") as source:
                while block := source.read(65536):
                    content.update(block)
            digest.update((package + "/" + relative.as_posix()).encode() + b"\0")
            digest.update(content.digest())
    return digest.hexdigest()
