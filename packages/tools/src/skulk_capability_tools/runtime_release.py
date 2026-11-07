"""Signed offline runtime artifacts; legacy bundle signatures remain unchanged."""

import hashlib
import importlib.metadata
import io
import json
import platform
import stat
import sys
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self, final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.requirements import Requirement
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version
from pydantic import AfterValidator, Field, StringConstraints, model_validator

from skulk_capability_sdk.contracts import Contract, Denied, Digest, canonical
from skulk_capability_sdk.storage import read_private

from .release_runtime import LAUNCHER_GROUP, ROOT_DEPENDENCIES
from .releases import (
    RELEASE_METADATA_MAX_BYTES,
    PublisherTrust,
    Release,
    verify_release_compatibility,
)

RuntimePlatform = Annotated[
    str, StringConstraints(pattern=r"^[a-z0-9]+(?:[.-][a-z0-9_]+)*$", max_length=64)
]
"""An artifact family: the operating system, its C library where that matters,
and the processor architecture.

This was a closed pair of names, which meant a capability could be installed on
Apple Silicon or x86_64 Linux and nowhere else. A Grace Blackwell node runs
Linux on aarch64 and could not host a capability at all, which contradicts the
requirement that a capability run on any hardware that can join the fabric.

Opening it is safe because the platform string never was the security boundary.
Each wheel's own tags are checked against the live interpreter before it is
installed, so a wheel that cannot run here is refused whatever the family says.
The family selects which artifact set to fetch and states what the publisher
qualified."""

LEGACY_PLATFORMS: dict[str, str] = {"ubuntu-24.04-x86_64": "linux-glibc-x86_64"}
"""Names already written into signed releases, mapped to what they mean. The
Ubuntu label never denoted that distribution; it meant glibc Linux on x86_64."""


@final
class RuntimeWheel(Contract):
    """One exact wheel, addressed by safe basename and publisher-bound bytes."""

    filename: str = Field(pattern=r"^[A-Za-z0-9_.+-]+\.whl$", max_length=240)
    sha256: Digest = Field(description="SHA-256 of the exact wheel artifact.")
    size: int = Field(
        ge=1, le=64 * 1024 * 1024, description="Exact artifact byte length."
    )

    @property
    def distribution(self) -> tuple[str, str]:
        """Read canonical distribution name and version from the wheel filename."""
        name, version, _, _ = parse_wheel_filename(self.filename)
        return str(name), str(version)


RUNTIME_PROTOCOL = 2
"""The isolated runtime envelope protocol this SDK writes."""

ACCEPTED_RUNTIME_PROTOCOLS: tuple[int, ...] = (2,)
"""Runtime envelope protocols this SDK verifies: the current and the previous."""


def accepted_runtime_protocol(value: int) -> int:
    """Refuse a runtime envelope outside the window, naming what is accepted."""
    if value not in ACCEPTED_RUNTIME_PROTOCOLS:
        accepted = " or ".join(str(item) for item in ACCEPTED_RUNTIME_PROTOCOLS)
        raise ValueError(
            f"runtime protocol {value} is not accepted; this SDK accepts {accepted}"
        )
    return value


RuntimeProtocol = Annotated[
    int,
    AfterValidator(accepted_runtime_protocol),
    Field(json_schema_extra={"enum": list(ACCEPTED_RUNTIME_PROTOCOLS)}),
]


@final
class RuntimeRelease(Contract):
    """Version-two signed payload binding bundle, platform and complete wheels."""

    protocol: RuntimeProtocol = Field(
        default=RUNTIME_PROTOCOL, description="Isolated runtime envelope version."
    )
    release: Release = Field(
        description="Underlying bundle and compatibility contract."
    )
    platform: RuntimePlatform = Field(
        description="Artifact family; the legacy Ubuntu label denotes Linux x86_64."
    )
    implementation: Literal["cpython"] = Field(
        default="cpython", description="Supported interpreter implementation."
    )
    wheels: tuple[RuntimeWheel, ...] = Field(
        min_length=1,
        max_length=64,
        description="Complete SDK dependency closure as exact wheels.",
    )

    @model_validator(mode="after")
    def complete_inventory(self) -> Self:
        """Reject ambiguous packages and require the exact signed dependency lock."""
        if len(canonical(self)) > RELEASE_METADATA_MAX_BYTES:
            raise ValueError("runtime metadata exceeds bound")
        names = [wheel.distribution[0] for wheel in self.wheels]
        if "pip" in names:
            raise ValueError("runtime must not include installer tools")
        if len(set(names)) != len(names):
            raise ValueError("runtime has duplicate distributions")
        if sum(wheel.size for wheel in self.wheels) > 512 * 1024 * 1024:
            raise ValueError("runtime artifacts exceed total bound")
        if not {canonicalize_name(name) for name in ROOT_DEPENDENCIES} <= set(names):
            raise ValueError("runtime omits a required SDK dependency")
        if not set(self.release.manifest.runtime_dependencies) <= set(names):
            raise ValueError("runtime omits a declared capability dependency")
        if self.inventory_digest != self.release.dependency_lock_sha256:
            raise ValueError("runtime inventory differs from signed bundle lock")
        family = canonical_platform(self.platform)
        expected = "darwin" if family.startswith("macos") else family.split("-")[0]
        if expected not in self.release.platforms:
            raise ValueError("bundle and runtime platforms differ")
        return self

    @property
    def inventory(self) -> dict[str, str]:
        """Return expected dependency versions, excluding installer tools."""
        return dict(sorted(wheel.distribution for wheel in self.wheels))

    @property
    def inventory_digest(self) -> str:
        """Use the same canonical dependency digest as the legacy runtime contract."""
        return hashlib.sha256(
            json.dumps(self.inventory, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @property
    def digest(self) -> str:
        """Bind every runtime artifact and the complete underlying bundle contract."""
        return hashlib.sha256(canonical(self)).hexdigest()


@final
class SignedRuntimeRelease(Contract):
    """Publisher signature over the v2 runtime payload, with external trust only."""

    runtime: RuntimeRelease = Field(description="Canonical signed runtime payload.")
    signature: str = Field(
        pattern=r"^[a-f0-9]{128}$",
        description="Ed25519 signature of the canonical payload.",
    )


def canonical_platform(name: str) -> str:
    """Resolve a declared family name, including the ones already signed."""
    return LEGACY_PLATFORMS.get(name, name)


def _family_parts(name: str) -> tuple[str, str | None, str]:
    """Split a family into operating system, C library if named, and machine."""
    # At most three parts: a hyphen inside a machine name must not shift the
    # architecture into the library slot.
    parts = canonical_platform(name).split("-", 2)
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    return parts[0], None, parts[-1]


def platform_matches(declared: str, host: str) -> bool:
    """Whether an artifact family describes this host.

    The legacy Ubuntu label matched every Linux x86_64 host, whatever its C
    library, and the wheels' own tags decided whether they could run. That is
    preserved: a legacy name compares on operating system and machine only.
    So does a library reported as ``unknown``, which means ``libc_ver()`` could
    not identify it rather than that it differs; refusing on it would turn a
    detection gap into an installation failure on a host the wheels suit.
    """
    left, right = _family_parts(declared), _family_parts(host)
    if left[0] != right[0] or left[2] != right[2]:
        return False
    if declared in LEGACY_PLATFORMS or host in LEGACY_PLATFORMS:
        return True
    return left[1] == right[1] or "unknown" in (left[1], right[1])


def current_platform() -> RuntimePlatform:
    """Name this host's artifact family from its kernel, C library and machine.

    Nothing is refused here. A host whose family no publisher has qualified
    simply finds no runtime it can install, which is an honest absence rather
    than an interpreter that declares itself unsupported.
    """
    machine = platform.machine().lower().replace("-", "_")
    architecture = {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    if sys.platform == "darwin":
        # Apple keeps calling it arm64 and the existing signed name says arm64,
        # so the normalization above is not applied to macOS.
        return f"macos-{'arm64' if machine == 'arm64' else architecture}"
    if sys.platform == "linux":
        library, _ = platform.libc_ver()
        return f"linux-{library or 'unknown'}-{architecture}"
    return f"{sys.platform}-{architecture}"


def verify_runtime_release(
    signed: SignedRuntimeRelease,
    trust: PublisherTrust,
    *,
    skulk_version: str,
    now: int,
) -> RuntimeRelease:
    """Authenticate the v2 envelope before inspecting or executing any artifact.

    Legacy signature validation remains in its original interface. V2 signs the
    entire runtime payload, so unsigned wheel lists cannot extend v1 authority.
    """
    runtime = signed.runtime
    release = runtime.release
    public = trust.publishers.get(release.publisher)
    if (
        public is None
        or not platform_matches(runtime.platform, current_platform())
        or sys.implementation.name != runtime.implementation
        or any(wheel.sha256 in trust.revoked_artifacts for wheel in runtime.wheels)
        or runtime.digest in trust.revoked_artifacts
    ):
        raise Denied("runtime trust or platform refused")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public)).verify(
            bytes.fromhex(signed.signature), canonical(runtime)
        )
    except (InvalidSignature, ValueError):
        raise Denied("runtime signature refused") from None
    # Share the legacy compatibility policy without forging a legacy signature.
    # The helper below accepts only the already authenticated payload.
    verify_release_compatibility(
        release,
        trust,
        platform=sys.platform,
        skulk_version=skulk_version,
        python_version=platform.python_version(),
        dependency_lock_sha256=runtime.inventory_digest,
        now=now,
    )
    return runtime


"""Entry-point group under which a runtime wheel declares the owner and
management launchers Skulk runs. Read from the wheel as data, never imported."""


def valid_launcher(target: str) -> bool:
    """Whether ``module:attribute`` names something ``EntryPoint.load`` resolves.

    The shape importlib accepts: dotted module and dotted attribute paths with
    optional whitespace around the colon. Extras are refused; a launcher is
    loaded from the pinned wheelhouse, never resolved against extras. Skulk
    reads a wheel's declaration with the same grammar. The match runs through
    importlib's own pattern because its character class is narrower than
    ``str.isidentifier``: a combining mark passes the latter and would only
    fail at ``EntryPoint.load``, after staging.
    """
    match = importlib.metadata.EntryPoint.pattern.match(target)
    if match is None or match.group("attr") is None or match.group("extras"):
        return False
    return all(
        part.isidentifier()
        for side in (match.group("module"), match.group("attr"))
        for part in side.split(".")
    )


def declared_launchers(archive: zipfile.ZipFile) -> dict[str, str]:
    """Launchers a wheel declares in its ``entry_points.txt``, name to target."""
    found: dict[str, str] = {}
    for item in archive.infolist():
        parts = PurePosixPath(item.filename).parts
        if (
            len(parts) != 2
            or not parts[0].endswith(".dist-info")
            or parts[1] != "entry_points.txt"
            or item.file_size > 65536
        ):
            continue
        section = None
        try:
            # importlib.metadata reads the installed copy as strict UTF-8; a
            # byte tolerated here would become an owner that never starts.
            text = archive.read(item).decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("wheel entry point declaration is not UTF-8") from error
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                # importlib.metadata drops blank and "#" lines and nothing
                # else; a ";" line is a declaration to it, so it is one here.
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip()
            elif section == LAUNCHER_GROUP and "=" in line:
                name, _, target = line.partition("=")
                found[name.strip()] = target.strip()
    return found


def verify_wheels(runtime: RuntimeRelease, directory: Path) -> dict[str, bytes]:
    """Verify wheel bytes, native compatibility and complete dependency closure.

    Metadata is read as data before pip runs. External requirement URLs, extras,
    unsafe paths, symlinks and oversized archives are refused. This performs no
    resolution, download, import or executable invocation.
    """
    artifacts: dict[str, bytes] = {}
    providers: list[str] = []
    requirements: dict[str, tuple[Requirement, ...]] = {}
    supported = set(sys_tags())
    expanded_bytes = 0
    for wheel in runtime.wheels:
        raw = read_private(directory / wheel.filename, limit=wheel.size)
        if len(raw) != wheel.size or hashlib.sha256(raw).hexdigest() != wheel.sha256:
            raise Denied("runtime wheel differs from signed metadata")
        name, version, _, tags = parse_wheel_filename(wheel.filename)
        if not tags & supported:
            raise Denied("runtime wheel does not support this interpreter")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            expanded_bytes += sum(item.file_size for item in entries)
            if expanded_bytes > 512 * 1024 * 1024:
                raise Denied("expanded runtime exceeds total bound")
            if (
                len(entries) > 20000
                or sum(i.file_size for i in entries) > 256 * 1024 * 1024
            ):
                raise Denied("runtime wheel contents exceed bound")
            for item in entries:
                path = PurePosixPath(item.filename)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in item.filename
                    or stat.S_ISLNK(item.external_attr >> 16)
                ):
                    raise Denied("runtime wheel contains an unsafe path")
            metadata = [
                i for i in entries if i.filename.endswith(".dist-info/METADATA")
            ]
            if len(metadata) != 1 or metadata[0].file_size > 1024 * 1024:
                raise Denied("runtime wheel metadata is missing or oversized")
            parsed = BytesParser().parsebytes(archive.read(metadata[0]))
            if (
                canonicalize_name(str(parsed.get("Name", ""))) != name
                or Version(str(parsed.get("Version", "0"))) != version
            ):
                raise Denied("runtime wheel metadata identity differs")
            requested = tuple(
                Requirement(str(value)) for value in parsed.get_all("Requires-Dist", [])
            )
            requirements[str(name)] = requested
            if declared_launchers(archive):
                providers.append(str(name))
        artifacts[wheel.filename] = raw
    visited: set[str] = set()
    # The SDK wheel is not reachable from its own dependency roots, yet it is
    # what provides the launchers Skulk runs; a wheel declaring them is a root.
    pending = [
        str(canonicalize_name(name))
        for name in (
            *ROOT_DEPENDENCIES,
            *providers,
            *runtime.release.manifest.runtime_dependencies,
        )
    ]
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        for requirement in requirements[name]:
            if requirement.marker is not None and not requirement.marker.evaluate(
                {"extra": ""}
            ):
                continue
            dependency = str(canonicalize_name(requirement.name))
            if (
                requirement.url is not None
                or requirement.extras
                or dependency not in runtime.inventory
                or Version(runtime.inventory[dependency]) not in requirement.specifier
            ):
                raise Denied("runtime dependency closure is incomplete or incompatible")
            pending.append(dependency)
    if visited != set(runtime.inventory):
        raise Denied("runtime contains undeclared dependency packages")
    return artifacts
