"""Select measured Node assets inside an explicitly selected Python runtime.

This module owns neither installation nor caller admission. The signed runtime
installer verifies its complete wheel closure; these checks select the exact
interpreter that a public language bootstrap may execute inside that closure.
"""

import hashlib
import os
import platform
import stat
import sys
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Literal, final

from pydantic import Field

from .contracts import Contract, Denied, canonical

type NodePlatform = Literal["macos-arm64", "linux-glibc-x86_64", "linux-glibc-aarch64"]
NODE_VERSION = "22.23.5"
_COHORT_SHA256 = "cd795056c6c1752e3687b4d03960deb46db8b93e75fd7586280eafd299ea57b2"
_MAX_BINARY = 128 * 1024 * 1024


@final
class NodeSource(Contract):
    """Exact upstream files; acquisition is separate from runtime authority."""

    platform: NodePlatform = Field(description="Selected runtime architecture.")
    version: Literal["22.23.5"] = Field(description="Explicit Node security patch.")
    archive_name: str = Field(description="Immutable upstream archive basename.")
    archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    archive_size: int = Field(gt=0, le=64 * 1024 * 1024)
    binary_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    binary_size: int = Field(gt=0, le=_MAX_BINARY)
    license_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    license_size: int = Field(gt=0, le=1024 * 1024)
    url: str = Field(description="Reviewed anonymous upstream HTTPS archive URL.")


@final
class _Cohort(Contract):
    format: Literal[1]
    version: Literal["22.23.5"]
    sources: tuple[NodeSource, ...] = Field(min_length=3, max_length=3)


@final
class _Metadata(Contract):
    format: Literal[1] = 1
    source: NodeSource


def node_source(family: NodePlatform) -> NodeSource:
    """Return the reviewed upstream identity for one explicit platform family."""
    raw = files("skulk_capability_sdk").joinpath("node_cohort.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != _COHORT_SHA256:
        raise Denied("Node runtime cohort differs from the reviewed source")
    cohort = _Cohort.model_validate_json(raw)
    matches = [item for item in cohort.sources if item.platform == family]
    if len(matches) != 1:
        raise Denied("Node runtime platform is unavailable")
    return matches[0]


def node_platform() -> NodePlatform:
    """Resolve the current supported ABI family without selecting an executable."""
    machine = platform.machine().lower()
    if sys.platform == "darwin" and machine in {"arm64", "aarch64"}:
        return "macos-arm64"
    if sys.platform == "linux" and platform.libc_ver()[0] == "glibc":
        if machine in {"x86_64", "amd64"}:
            return "linux-glibc-x86_64"
        if machine in {"aarch64", "arm64"}:
            return "linux-glibc-aarch64"
    raise Denied("Node runtime has no supported platform")


def measured_file(
    path: Path,
    *,
    maximum: int,
    executable: bool = False,
    follow_selected_link: bool = False,
) -> str:
    """Hash a bounded regular selected file, refusing final links by default.

    Root or the runtime user must own it and other users must not be able to
    replace its bytes. A selected virtualenv Python may explicitly follow its
    final link while measuring the actual target. This is not attestation of
    the containing installation.
    """
    # A venv's selected Python executable can be a symlink. Keep its original
    # path (so Python retains that venv) while measuring its actual regular
    # target, exactly as the existing TypeScript credential reader does. Node
    # and application assets use the stricter no-link default.
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_NONBLOCK | (0 if follow_selected_link else os.O_NOFOLLOW),
    )
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {0, os.geteuid()}
            or before.st_mode & 0o022
            or not 0 <= before.st_size <= maximum
            or (executable and before.st_mode & 0o111 == 0)
        ):
            raise Denied("Selected runtime file has unsafe custody or size")
        digest = hashlib.sha256()
        consumed = 0
        while block := stream.read(min(65536, maximum + 1 - consumed)):
            consumed += len(block)
            if consumed > maximum:
                raise Denied("Selected runtime file exceeds its bound")
            digest.update(block)
        after = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise Denied("Selected runtime file changed during measurement")
        return digest.hexdigest()


def node_metadata(source: NodeSource) -> bytes:
    """Return deterministic wheel metadata shared by the builder and selector."""
    return canonical(_Metadata(source=source))


@final
@dataclass(frozen=True, slots=True, repr=False)
class SelectedNode:
    """Measured interpreter selection, not an installation or execution grant."""

    executable: Path
    source: NodeSource

    def __repr__(self) -> str:
        return "SelectedNode([selected runtime])"


def select_node(runtime: Path) -> SelectedNode:
    """Select only the pinned Node wheel inside the named CPython 3.13 runtime.

    No PATH, ambient package search, discovery or mutation is performed. The
    caller must supply the runtime selected by its installer or local tool.
    """
    if not runtime.is_absolute():
        raise Denied("Node selection requires an absolute runtime directory")
    source = node_source(node_platform())
    site = runtime / "lib/python3.13/site-packages"
    root = site / "_skulk_node_runtime"
    for directory in (runtime, site, root, root / "bin"):
        info = directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in {0, os.geteuid()}
            or info.st_mode & 0o022
        ):
            raise Denied("Selected Node directory has unsafe custody")
    expected = node_metadata(source)
    metadata = root / "runtime.json"
    if measured_file(metadata, maximum=65536) != hashlib.sha256(expected).hexdigest():
        raise Denied("Selected Node metadata differs from its cohort")
    # Read no unchecked data to select a program. Fixed paths and exact content
    # bind the binary and complete upstream license to this reviewed cohort.
    if measured_file(root / "LICENSE", maximum=1024 * 1024) != source.license_sha256:
        raise Denied("Selected Node license differs from its cohort")
    executable = root / "bin/node"
    if (
        measured_file(executable, maximum=_MAX_BINARY, executable=True)
        != source.binary_sha256
    ):
        raise Denied("Selected Node executable differs from its cohort")
    return SelectedNode(executable=executable, source=source)
