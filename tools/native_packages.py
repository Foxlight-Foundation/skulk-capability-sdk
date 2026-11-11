"""Inspect compiler-free native packages against a closed distribution contract."""

import hashlib
import json
import tarfile
import tomllib
import zipfile
from pathlib import Path
from typing import Literal, cast

from packaging.utils import parse_wheel_filename
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
Platform = Literal["macos-arm64", "linux-glibc-x86_64", "linux-glibc-aarch64"]
PLATFORMS: dict[Platform, tuple[str, str]] = {
    "macos-arm64": ("darwin", "arm64"),
    "linux-glibc-x86_64": ("linux", "x86_64"),
    "linux-glibc-aarch64": ("linux", "aarch64"),
}
FACADE = (
    b"from ._skulk_capability_kernel import *\n\n"
    b"__doc__ = _skulk_capability_kernel.__doc__\n"
    b'if hasattr(_skulk_capability_kernel, "__all__"):\n'
    b"    __all__ = _skulk_capability_kernel.__all__"
)
MAX_ASSET_BYTES = 32 * 1024 * 1024


def versions(root: Path = ROOT) -> tuple[str, str]:
    """Require one equivalent prerelease version across Rust, Python and Node."""
    rust = cast(
        str,
        tomllib.loads((root / "kernel/Cargo.toml").read_text())["package"]["version"],
    )
    python = cast(
        str,
        tomllib.loads((root / "kernel/bindings/python/pyproject.toml").read_text())[
            "project"
        ]["version"],
    )
    node = json.loads((root / "kernel/bindings/node/package.json").read_text())
    if (
        not Version(python).is_prerelease
        or Version(rust) != Version(python)
        or node["version"] != rust
    ):
        raise ValueError("Native candidate versions differ or are not prereleases")
    for name in ["python", "node"]:
        cargo = tomllib.loads((root / f"kernel/bindings/{name}/Cargo.toml").read_text())
        if cargo["package"]["version"] != rust:
            raise ValueError("Native binding crate version differs")
    return python, rust


def platform_name(system: str, architecture: str) -> Platform:
    """Resolve only the three artifact families this candidate actually builds."""
    for name, identity in PLATFORMS.items():
        if identity == (system, architecture):
            return name
    raise ValueError("Unsupported native candidate artifact family")


def asset_digest(path: Path) -> str:
    """Hash one bounded regular artifact without following an input symlink."""
    if (
        path.is_symlink()
        or not path.is_file()
        or not 0 < path.stat().st_size <= MAX_ASSET_BYTES
    ):
        raise ValueError("Native artifact must be a bounded regular file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_wheel_platform(name: str, family: Platform) -> None:
    """Refuse a version, ABI or machine mismatch before selecting an asset."""
    distribution, version, _, tags = parse_wheel_filename(name)
    expected, _ = versions()
    if distribution != "skulk-capability-kernel" or version != Version(expected):
        raise ValueError("Native wheel distribution/version differs")

    def matches(platform: str) -> bool:
        if family == "macos-arm64":
            return platform.startswith("macosx_") and platform.endswith("_arm64")
        suffix = "_x86_64" if family == "linux-glibc-x86_64" else "_aarch64"
        return platform.startswith(("linux_", "manylinux")) and platform.endswith(
            suffix
        )

    if not any(
        t.interpreter == "cp313" and t.abi == "abi3" and matches(t.platform)
        for t in tags
    ):
        raise ValueError("Native wheel ABI/platform differs")


def inspect_paths(data: bytes) -> None:
    """Refuse build-host paths in text metadata and stripped binary strings."""
    for marker in [
        str(ROOT).encode(),
        str(Path.home()).encode(),
        b"file:///",
        b"/Users/",
        b"/home/runner/",
    ]:
        if marker in data:
            raise ValueError("Build-host path leaked into native artifact")


def inspect_wheel(path: Path) -> None:
    """Require the native ABI, declarations, exact shim and complete license notices."""
    version, _ = versions()
    metadata = f"skulk_capability_kernel-{version}.dist-info"
    approved = {
        "_skulk_capability_kernel/__init__.py",
        "_skulk_capability_kernel/__init__.pyi",
        "_skulk_capability_kernel/_skulk_capability_kernel.abi3.so",
        "_skulk_capability_kernel/py.typed",
        *[
            f"{metadata}/{name}"
            for name in [
                "METADATA",
                "WHEEL",
                "RECORD",
                "licenses/LICENSE.md",
                "licenses/THIRD_PARTY_NOTICES.txt",
            ]
        ],
    }
    asset_digest(path)
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) != len(approved) or {i.filename for i in entries} != approved:
            raise ValueError("Unexpected native wheel contents")
        if archive.read("_skulk_capability_kernel/__init__.py") != FACADE:
            raise ValueError("Unexpected readable native implementation")
        if (
            archive.read(f"{metadata}/licenses/THIRD_PARTY_NOTICES.txt")
            != (ROOT / "kernel/bindings/python/THIRD_PARTY_NOTICES.txt").read_bytes()
        ):
            raise ValueError("Native wheel dependency notices differ")
        for entry in entries:
            if (entry.external_attr >> 16) & 0o170000 not in (0, 0o100000):
                raise ValueError("Native wheel member is not a regular file")
            if not 0 <= entry.file_size <= MAX_ASSET_BYTES:
                raise ValueError("Native wheel member exceeds its budget")
            inspect_paths(archive.read(entry))


def inspect_npm(path: Path) -> None:
    """Refuse source/build hooks and require the same native dependency notices."""
    _, version = versions()
    approved = {
        "package/package.json",
        *[
            f"package/dist/{name}"
            for name in [
                "codec.js",
                "codec.d.ts",
                "portable.js",
                "portable.d.ts",
                "kernel.node",
                "loader.cjs",
                "loader.d.cts",
                "LICENSE.md",
                "THIRD_PARTY_NOTICES.txt",
            ]
        ],
    }
    asset_digest(path)
    with tarfile.open(path, "r:gz") as archive:
        entries = archive.getmembers()
        if len(entries) != len(approved) or {i.name for i in entries} != approved:
            raise ValueError("Unexpected native npm contents")
        for entry in entries:
            if not entry.isfile() or not 0 <= entry.size <= MAX_ASSET_BYTES:
                raise ValueError("Native npm member is not a bounded regular file")
            source = archive.extractfile(entry)
            if source is None:
                raise ValueError("Unreadable native npm member")
            data = source.read(MAX_ASSET_BYTES + 1)
            inspect_paths(data)
            if entry.name == "package/package.json":
                package = json.loads(data)
                if (
                    package["version"] != version
                    or package["name"]
                    != "@foxlight/skulk-capability-kernel-qualification"
                ):
                    raise ValueError("Native npm package identity differs")
                if set(package.get("scripts", {})) - {"build", "test"} or package.get(
                    "dependencies"
                ):
                    raise ValueError("Native npm archive requires installation effects")
            if (
                entry.name == "package/dist/THIRD_PARTY_NOTICES.txt"
                and data
                != (
                    ROOT / "kernel/bindings/python/THIRD_PARTY_NOTICES.txt"
                ).read_bytes()
            ):
                raise ValueError("Native npm dependency notices differ")
