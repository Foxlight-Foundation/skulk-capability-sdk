"""The committed wheelhouse recipe: the exact wheels a runtime release carries.

A runtime release installs on a host from its signed wheels alone, so the
wheelhouse decides what every installation of that release runs. This recipe
builds it from a capability's ``uv.lock`` and nothing else, which is what makes
a release reproducible on any machine instead of on the one Mac that held a
hand-written script:

- The closure is the lock's own resolution of the runtime root (the SDK host
  package, which declares the owner and management launchers), with markers
  evaluated for the release platform. No resolver runs, so no version the
  lock does not name can enter.
- A third-party package arrives only as a binary wheel the lock records, checked
  against the lock's size and SHA-256. A package with no wheel for the target
  is refused, never built from its source distribution.
- Prereleases are refused.
- Wheels are chosen for a pinned platform floor rather than for the build
  runner: macOS 14 on Apple Silicon (the oldest macOS Skulk's MLX supports)
  and ``manylinux_2_28`` (glibc 2.28) on Linux, below the glibc 2.35 Skulk's
  own engine wheels require. A release therefore installs on every host at or
  above the floor, not only on hosts as new as the runner that built it.
- Packages the lock takes from source (the SDK, pinned by git commit in a
  capability, or a workspace member in the SDK repository) are built here into
  pure Python wheels by a build backend locked by version and hash, so their
  bytes repeat.

It runs natively. The publisher checks every wheel's tags against its own
interpreter and takes the release platform from the machine it runs on, so
each platform's wheelhouse is built on that platform, with CPython 3.13.
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Final, Literal, final
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
from packaging.markers import InvalidMarker, Marker, UndefinedEnvironmentName
from packaging.tags import Tag, compatible_tags, cpython_tags, mac_platforms
from packaging.utils import (
    InvalidWheelFilename,
    canonicalize_name,
    parse_wheel_filename,
)
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from skulk_capability_sdk.contracts import Denied
from skulk_capability_sdk.storage import atomic_private, secure_directory

from .runtime_release import current_platform

PYTHON_VERSION: Final = (3, 13)
"""The interpreter every runtime release targets (``python_requires`` is
``==3.13.*``); wheels are chosen for CPython 3.13 and its ABI."""

MACOS_FLOOR: Final = (14, 0)
"""Oldest macOS a macOS wheelhouse installs on. MLX, which Skulk needs on a
Mac, publishes nothing older than macOS 14."""

MANYLINUX_FLOOR: Final = (2, 28)
"""Oldest glibc a Linux wheelhouse installs on. Skulk's own engine wheels need
glibc 2.35, so every Skulk Linux host clears this floor with room to spare."""

BUILD_BACKEND_LOCK: Final = (
    "hatchling==1.32.4 --hash=sha256:"
    "08ecf7548fb48205e7f213d70c71e67b8271b7242093dc3f1da578b42c734a2c",
    "packaging==26.3 --hash=sha256:"
    "d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c",
    "pathspec==1.1.1 --hash=sha256:"
    "a00ce642f577bf7f473932318056212bc4f8bfdf53128c78bbd5af0b9b20b189",
    "pluggy==1.6.0 --hash=sha256:"
    "e920276dd6813095e9377c0bc5566d94c932c33b27a3e3945d8389c374dd4746",
    "tomlkit==0.15.1 --hash=sha256:"
    "177a05aece5a8ca5266fd3c448abb47b8d352f09d477d3ca8332db4d89b24304",
    "trove-classifiers==2026.9.21.13 --hash=sha256:"
    "8b1ff4f9c191b1040b71c37f1e445ab99732911e3cd91de52838453a854d7a17",
)
"""The complete build backend for packages built from source, every
distribution pinned by version and wheel hash.

``uv build`` runs with these as build constraints in ``--require-hashes``
mode, so a build dependency that is missing from this list, or whose bytes
differ, is refused rather than resolved. The SDK wheels a release carries
therefore come from exactly this backend and repeat byte for byte across
runners and reruns. Advancing it is a reviewed change, like any lock bump."""

DEFAULT_ROOT: Final = "skulk-capability-nodes"
"""The runtime root: the SDK host package, which declares the owner and
management launchers and depends on everything else the runtime carries."""

MAX_WHEELS: Final = 64
"""The publisher's bound on how many wheels one runtime release may carry."""

MAX_WHEEL_BYTES: Final = 64 * 1024 * 1024
"""The publisher's bound on one wheel's size."""

_LEGACY_MANYLINUX: Final = {
    17: "manylinux2014",
    12: "manylinux2010",
    5: "manylinux1",
}
"""The pre-PEP 600 names for the glibc versions that had one."""

_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def manylinux_platforms(machine: str) -> tuple[str, ...]:
    """Linux platform tags a floor host accepts, most specific first.

    ``packaging`` derives this list from the running glibc. A release must
    instead target the pinned floor whatever the runner has, so the same walk
    runs from the floor downwards: the PEP 600 name for each glibc minor
    version, followed by its legacy alias where one exists. Plain
    ``linux_<machine>`` wheels are never accepted; they promise nothing about
    the host they were built on.
    """
    oldest = 4 if machine in ("x86_64", "i686") else 16
    platforms: list[str] = []
    for minor in range(MANYLINUX_FLOOR[1], oldest, -1):
        platforms.append(f"manylinux_2_{minor}_{machine}")
        legacy = _LEGACY_MANYLINUX.get(minor)
        if legacy is not None:
            platforms.append(f"{legacy}_{machine}")
    return tuple(platforms)


def platform_policy(family: str) -> tuple[str, ...]:
    """The wheel platform tags, in preference order, for one release family.

    ``family`` is the runtime platform the publisher signs, from
    ``current_platform``. Families without a policy are refused rather than
    guessed: a wheelhouse built without a floor would carry whatever the build
    machine happens to prefer.
    """
    if family == "macos-arm64":
        return tuple(mac_platforms(MACOS_FLOOR, "arm64"))
    if family == "macos-x86_64":
        # Local builds on Intel Macs only; no published release targets it.
        return tuple(mac_platforms(MACOS_FLOOR, "x86_64"))
    if family in ("linux-glibc-x86_64", "linux-glibc-aarch64"):
        return manylinux_platforms(family.rsplit("-", 1)[1])
    raise Denied(f"there is no wheelhouse policy for the {family} platform")


def supported_tags(platforms: Sequence[str]) -> tuple[Tag, ...]:
    """Every tag a CPython 3.13 floor host accepts, most preferred first.

    The order matches what an installer on that host prefers: the exact
    interpreter and ABI, then the stable ABI, then pure Python.
    """
    interpreter = f"cp{PYTHON_VERSION[0]}{PYTHON_VERSION[1]}"
    return (
        *cpython_tags(PYTHON_VERSION, abis=[interpreter], platforms=platforms),
        *compatible_tags(PYTHON_VERSION, interpreter, platforms),
    )


class _LockRecord(BaseModel):
    """A record read from ``uv.lock``.

    Lax rather than strict because the input is TOML written by another tool:
    its arrays arrive as lists and it carries fields this recipe does not use.
    Every field the recipe relies on is still validated.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")


@final
class LockedFile(_LockRecord):
    """One distribution file the lock records for a package version."""

    url: str | None = Field(default=None, description="Where the file is served.")
    hash: str = Field(description="Algorithm and digest, as 'sha256:<hex>'.")
    size: int | None = Field(default=None, description="Exact size in bytes.")

    @property
    def filename(self) -> str:
        """The file's basename, read from its URL."""
        if self.url is None:
            raise Denied("a locked wheel without a URL cannot be fetched")
        return unquote(urlsplit(self.url).path.rsplit("/", 1)[-1])

    @property
    def sha256(self) -> str:
        """The SHA-256 the lock records; any other algorithm is refused."""
        algorithm, _, digest = self.hash.partition(":")
        if algorithm != "sha256" or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise Denied(f"{self.filename} is not locked by a SHA-256 digest")
        return digest


@final
class LockedDependency(_LockRecord):
    """One requirement edge in the lock's resolution."""

    name: str = Field(description="The required package.")
    version: str | None = Field(
        default=None, description="Present when the lock holds several versions."
    )
    marker: str | None = Field(
        default=None, description="Environment marker gating the edge."
    )
    extra: tuple[str, ...] = Field(
        default=(), description="Extras requested of the required package."
    )


@final
class LockedPackage(_LockRecord):
    """One resolved package version and where its files come from."""

    name: str = Field(description="Package name as the lock spells it.")
    version: str | None = Field(default=None, description="Resolved version.")
    source: dict[str, str] = Field(
        description="One of registry, git, editable, directory, path, url, virtual."
    )
    dependencies: tuple[LockedDependency, ...] = Field(
        default=(), description="Runtime requirement edges."
    )
    wheels: tuple[LockedFile, ...] = Field(
        default=(), description="Binary wheels the registry publishes."
    )


@final
class Lockfile(_LockRecord):
    """The parts of a ``uv.lock`` this recipe reads."""

    version: Literal[1] = Field(description="Lock format version.")
    package: tuple[LockedPackage, ...] = Field(description="Resolved packages.")


@final
class WheelhouseEntry(BaseModel):
    """One wheel the recipe placed in the wheelhouse."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    filename: str = Field(description="Wheel basename in the wheelhouse.")
    sha256: str = Field(description="SHA-256 of the wheel bytes.")
    size: int = Field(ge=1, description="Exact size in bytes.")
    origin: Literal["locked", "built"] = Field(
        description="Fetched as the lock records it, or built from locked source."
    )


@final
class WheelhouseReport(BaseModel):
    """What one wheelhouse holds and the policy that chose it."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    platform: str = Field(description="Runtime platform family it was built for.")
    python: str = Field(description="Interpreter version the wheels target.")
    floor: str = Field(description="Most specific platform tag accepted.")
    wheels: tuple[WheelhouseEntry, ...] = Field(description="Every wheel, by name.")


Fetch = Callable[[LockedFile, int], bytes]
"""Return a locked file's bytes, read with at most the given bound."""

Build = Callable[[LockedPackage], tuple[str, bytes]]
"""Build a source-locked package; return the wheel's filename and bytes."""


def load_lock(path: Path) -> Lockfile:
    """Read a ``uv.lock``, refusing a format version this recipe does not know."""
    try:
        return Lockfile.model_validate(tomllib.loads(path.read_text()))
    except (tomllib.TOMLDecodeError, ValidationError) as invalid:
        raise Denied(f"{path} is not a uv lock this recipe reads: {invalid}") from None


_FAMILY_MARKERS: Final = {
    "macos-arm64": ("darwin", "Darwin", "arm64"),
    "macos-x86_64": ("darwin", "Darwin", "x86_64"),
    "linux-glibc-x86_64": ("linux", "Linux", "x86_64"),
    "linux-glibc-aarch64": ("linux", "Linux", "aarch64"),
}
"""Operating system and machine marker values for each release family."""


def platform_environment(family: str) -> dict[str, str]:
    """Marker values for a CPython 3.13 host of one release family.

    The closure is evaluated for the release family rather than for whatever
    machine runs the recipe. That keeps the native build and the signing job,
    which runs on another platform and recomputes the expected wheels for
    every family, on one answer. The publisher still checks every wheel's
    requirements against its own interpreter, so a lock whose markers depend
    on anything finer than these values fails loudly rather than silently.
    """
    values = _FAMILY_MARKERS.get(family)
    if values is None:
        raise Denied(f"there is no wheelhouse policy for the {family} platform")
    sys_platform, platform_system, machine = values
    python = ".".join(str(part) for part in PYTHON_VERSION)
    return {
        "implementation_name": "cpython",
        "implementation_version": f"{python}.0",
        "os_name": "posix",
        "platform_machine": machine,
        "platform_python_implementation": "CPython",
        "platform_release": "",
        "platform_system": platform_system,
        "platform_version": "",
        "python_full_version": f"{python}.0",
        "python_version": python,
        "sys_platform": sys_platform,
        "extra": "",
    }


def runtime_closure(
    lock: Lockfile,
    root: str,
    environment: Mapping[str, str],
    additional: tuple[str, ...] = (),
) -> tuple[LockedPackage, ...]:
    """The lock's resolution of ``root`` for one environment, sorted by name.

    Only edges whose marker holds in ``environment`` are followed. A requested
    extra is refused: a runtime installs no extras, and the host's closure check
    would refuse the release anyway. So is a package the lock holds at two
    versions in one environment.
    """
    by_name: dict[str, list[LockedPackage]] = {}
    for package in lock.package:
        by_name.setdefault(canonicalize_name(package.name), []).append(package)

    def resolve(name: str, version: str | None) -> LockedPackage:
        candidates = [
            package
            for package in by_name.get(canonicalize_name(name), [])
            if version is None or package.version == version
        ]
        if len(candidates) != 1:
            raise Denied(
                f"the lock does not name exactly one {name}"
                + (f" {version}" if version else "")
            )
        return candidates[0]

    from .release_runtime import declared_requirements

    selected: dict[str, LockedPackage] = {}
    pending = [resolve(root, None)]
    for requirement in declared_requirements(additional):
        package = resolve(requirement.name, None)
        if released_version(package) not in requirement.specifier:
            raise Denied("locked capability dependency is incompatible")
        pending.append(package)
    while pending:
        package = pending.pop()
        key = canonicalize_name(package.name)
        if key in selected:
            if selected[key] != package:
                raise Denied(f"the runtime would carry two versions of {key}")
            continue
        selected[key] = package
        for dependency in package.dependencies:
            if dependency.marker is not None:
                try:
                    applies = Marker(dependency.marker).evaluate(dict(environment))
                except (InvalidMarker, UndefinedEnvironmentName) as invalid:
                    raise Denied(
                        f"{package.name} requires {dependency.name} under a "
                        f"marker this recipe cannot evaluate: {invalid}"
                    ) from None
                if not applies:
                    continue
            if dependency.extra:
                raise Denied(
                    f"{package.name} requests the {', '.join(dependency.extra)} "
                    f"extra of {dependency.name}; a runtime installs no extras"
                )
            pending.append(resolve(dependency.name, dependency.version))
    return tuple(selected[name] for name in sorted(selected))


def released_version(package: LockedPackage) -> Version:
    """The package's version, refusing a prerelease or an unversioned package."""
    try:
        version = Version(package.version or "")
    except InvalidVersion:
        raise Denied(f"{package.name} has no released version in the lock") from None
    if version.is_prerelease:
        raise Denied(f"{package.name} {version} is a prerelease")
    return version


def select_wheel(package: LockedPackage, tags: Sequence[Tag]) -> LockedFile:
    """The locked wheel a floor host prefers, or a refusal naming the gap.

    Ranking follows ``tags``: the wheel whose best tag comes first wins, and a
    tie (one file listed under two spellings) falls to the filename so the
    choice never depends on the lock's order.
    """
    version = released_version(package)
    rank = {tag: index for index, tag in enumerate(tags)}
    best: tuple[int, str, LockedFile] | None = None
    for file in package.wheels:
        try:
            name, wheel_version, _, wheel_tags = parse_wheel_filename(file.filename)
        except InvalidWheelFilename:
            raise Denied(f"{file.filename} is not a wheel filename") from None
        if name != canonicalize_name(package.name) or wheel_version != version:
            raise Denied(f"{file.filename} does not belong to {package.name} {version}")
        scores = [rank[tag] for tag in wheel_tags if tag in rank]
        if not scores:
            continue
        candidate = (min(scores), file.filename, file)
        if best is None or candidate[:2] < best[:2]:
            best = candidate
    if best is None:
        raise Denied(
            f"{package.name} {version} has no binary wheel for this platform's "
            "floor; a runtime never builds a third-party package from source"
        )
    return best[2]


def fetch_locked(client: httpx.Client) -> Fetch:
    """Fetch hash-pinned wheels over bounded HTTPS redirects without credentials."""

    def fetch(file: LockedFile, limit: int) -> bytes:
        if file.url is None or urlsplit(file.url).scheme != "https":
            raise Denied(f"{file.filename} is not served over HTTPS")
        url = httpx.URL(file.url)
        for _ in range(6):
            if url.scheme != "https" or url.username or url.password:
                raise Denied("locked wheel redirects require credential-free HTTPS")
            # A fresh Request and explicit auth=None keep client defaults,
            # cookies and cross-origin credentials out of public acquisition.
            response = client.send(
                httpx.Request("GET", url),
                stream=True,
                auth=None,
                follow_redirects=False,
            )
            try:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise Denied("wheel redirect has no destination")
                    url = url.join(location)
                    continue
                if response.status_code != 200:
                    raise Denied(
                        f"{file.filename} answered HTTP {response.status_code}"
                    )
                received = bytearray()
                for block in response.iter_bytes():
                    received.extend(block)
                    if len(received) > limit:
                        raise Denied(f"{file.filename} exceeds {limit} bytes")
                return bytes(received)
            finally:
                response.close()
        raise Denied("locked wheel exceeded the HTTPS redirect limit")

    return fetch


def git_source(value: str) -> tuple[str, str | None, str]:
    """Split a lock's git source into repository URL, subdirectory and commit.

    uv records ``<url>?subdirectory=<dir>&rev=<ref>#<commit>``; the fragment is
    the resolved commit, the only part that pins bytes.
    """
    parts = urlsplit(value)
    commit = parts.fragment
    if not _COMMIT.fullmatch(commit):
        raise Denied(f"git source {value} is not pinned to a full commit")
    query = parse_qs(parts.query)
    subdirectory = query.get("subdirectory", [None])[0]
    if subdirectory is not None and (
        subdirectory.startswith("/") or ".." in Path(subdirectory).parts
    ):
        raise Denied(f"git source {value} names a subdirectory outside it")
    repository = parts._replace(query="", fragment="").geturl()
    return repository, subdirectory, commit


def build_from_source(lock_directory: Path, work: Path) -> Build:
    """Build source-locked packages with ``uv build`` and the pinned backend.

    Git sources are fetched at their locked commit (credentials, when the
    repository is private, come from the ambient git configuration). Editable
    and directory sources resolve against the lock's directory. The result must
    be a pure Python wheel of exactly the locked name and version: a native
    build would bind the release to the runner that compiled it.
    """
    uv = shutil.which("uv")
    if uv is None:
        raise Denied("building the SDK wheels needs uv on PATH")
    constraints = work / "build-constraints.txt"
    constraints.write_text("".join(f"{pin}\n" for pin in BUILD_BACKEND_LOCK))
    checkouts: dict[tuple[str, str], Path] = {}
    environment = {
        key: value for key, value in os.environ.items() if key != "SOURCE_DATE_EPOCH"
    }

    def run(*command: str) -> None:
        completed = subprocess.run(
            command, capture_output=True, text=True, env=environment, check=False
        )
        if completed.returncode != 0:
            raise Denied(
                f"{' '.join(command[:3])} failed: {completed.stderr.strip()[-2000:]}"
            )

    def checkout(repository: str, commit: str) -> Path:
        key = (repository, commit)
        if key not in checkouts:
            target = work / f"source-{len(checkouts)}"
            run("git", "init", "--quiet", str(target))
            run(
                "git",
                "-C",
                str(target),
                "fetch",
                "--quiet",
                "--depth=1",
                repository,
                commit,
            )
            run("git", "-C", str(target), "checkout", "--quiet", "FETCH_HEAD")
            head = subprocess.run(
                ["git", "-C", str(target), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            if head != commit:
                raise Denied(f"{repository} fetched {head}, not the locked {commit}")
            checkouts[key] = target
        return checkouts[key]

    def build(package: LockedPackage) -> tuple[str, bytes]:
        version = released_version(package)
        if "git" in package.source:
            repository, subdirectory, commit = git_source(package.source["git"])
            directory = checkout(repository, commit) / (subdirectory or ".")
        elif "editable" in package.source or "directory" in package.source:
            relative = package.source.get("editable") or package.source["directory"]
            directory = lock_directory / relative
        else:
            raise Denied(
                f"{package.name} comes from {', '.join(package.source)}; a runtime "
                "takes packages from a registry wheel or builds them from git or "
                "a workspace directory"
            )
        output = work / f"built-{canonicalize_name(package.name)}"
        run(
            uv,
            "build",
            "--no-config",
            "--wheel",
            "--quiet",
            "--python",
            sys.executable,
            "--build-constraints",
            str(constraints),
            "--require-hashes",
            "--out-dir",
            str(output),
            str(directory),
        )
        built = sorted(output.glob("*.whl"))
        if len(built) != 1:
            raise Denied(f"building {package.name} produced {len(built)} wheels")
        name, wheel_version, _, tags = parse_wheel_filename(built[0].name)
        if name != canonicalize_name(package.name) or wheel_version != version:
            raise Denied(f"{built[0].name} is not the locked {package.name} {version}")
        if tags != frozenset({Tag("py3", "none", "any")}):
            raise Denied(f"{built[0].name} is not a pure Python wheel")
        return built[0].name, built[0].read_bytes()

    return build


def build_wheelhouse(
    lock: Lockfile,
    output: Path,
    *,
    root: str,
    family: str,
    environment: Mapping[str, str],
    fetch: Fetch,
    build: Build,
    additional: tuple[str, ...] = (),
) -> WheelhouseReport:
    """Place the runtime closure's exact wheels in a new, owner-only directory.

    Effects arrive as ``fetch`` and ``build`` so the policy can be exercised
    without a network or a build backend. Nothing is written until every
    package has a wheel, so a refusal leaves no partial wheelhouse.
    """
    platforms = platform_policy(family)
    tags = supported_tags(platforms)
    closure = runtime_closure(lock, root, environment, additional)
    if len(closure) > MAX_WHEELS:
        raise Denied(f"the runtime needs {len(closure)} wheels; at most {MAX_WHEELS}")
    staged: list[tuple[str, bytes, Literal["locked", "built"]]] = []
    for package in closure:
        if "registry" in package.source or "url" in package.source:
            file = select_wheel(package, tags)
            content = fetch(file, min(file.size or MAX_WHEEL_BYTES, MAX_WHEEL_BYTES))
            if (file.size is not None and len(content) != file.size) or (
                hashlib.sha256(content).hexdigest() != file.sha256
            ):
                raise Denied(f"{file.filename} differs from the bytes the lock pins")
            staged.append((file.filename, content, "locked"))
        else:
            filename, content = build(package)
            if len(content) > MAX_WHEEL_BYTES:
                raise Denied(f"{filename} exceeds {MAX_WHEEL_BYTES} bytes")
            staged.append((filename, content, "built"))
    secure_directory(output)
    if any(output.iterdir()):
        raise Denied(f"{output} must be empty; a wheelhouse is written whole")
    entries: list[WheelhouseEntry] = []
    for filename, content, origin in staged:
        atomic_private(output / filename, content)
        entries.append(
            WheelhouseEntry(
                filename=filename,
                sha256=hashlib.sha256(content).hexdigest(),
                size=len(content),
                origin=origin,
            )
        )
    return WheelhouseReport(
        platform=family,
        python=".".join(str(part) for part in PYTHON_VERSION),
        floor=platforms[0],
        wheels=tuple(sorted(entries, key=lambda entry: entry.filename)),
    )


def main(argv: list[str] | None = None) -> None:
    """Build this platform's wheelhouse from a capability's lock; print the report.

    Installed as ``capability-wheelhouse``. Run it on each release platform with
    the CPython 3.13 that runs the publisher, then pass the directory to
    ``capability-build --wheelhouse``.
    """
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--lock", type=Path, default=Path("uv.lock"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--spec",
        type=Path,
        help="Capability spec declaring additional locked runtime requirements",
    )
    parser.add_argument(
        "--root",
        default=DEFAULT_ROOT,
        help="The package whose locked closure the runtime carries",
    )
    args = parser.parse_args(argv)
    if sys.implementation.name != "cpython" or sys.version_info[:2] != PYTHON_VERSION:
        parser.error("build the wheelhouse with CPython 3.13, the release interpreter")
    try:
        lock = load_lock(args.lock)
        from .packaging import load_spec

        additional = load_spec(args.spec)[0].dependencies if args.spec else ()
        with (
            tempfile.TemporaryDirectory() as work,
            httpx.Client(
                follow_redirects=False,
                timeout=httpx.Timeout(60.0, connect=15.0),
                transport=httpx.HTTPTransport(retries=3),
            ) as client,
        ):
            report = build_wheelhouse(
                lock,
                args.output,
                root=args.root,
                family=current_platform(),
                environment=platform_environment(current_platform()),
                fetch=fetch_locked(client),
                build=build_from_source(args.lock.resolve().parent, Path(work)),
                additional=additional,
            )
    except (Denied, httpx.HTTPError) as refusal:
        parser.error(str(refusal))
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
