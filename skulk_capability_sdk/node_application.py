"""Verified Node application assets and a same-process selected-runtime bootstrap.

The signed installer still admits bundle.pyz and its complete wheel closure.
This is a public language bridge, not an owner, installer or caller grant.
"""

import argparse
import hashlib
import os
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Literal, Self, final

from pydantic import Field, field_validator, model_validator

from .contracts import Contract, Denied, Identifier, canonical
from .node_runtime import measured_file, select_node

NODE_LAUNCHER = "node_modules/@foxlight/skulk-capability-sdk/dist/launcher.js"
MAX_APPLICATION_BYTES = 256 * 1024 * 1024
MAX_APPLICATION_FILES = 8192
MAX_APPLICATION_METADATA = 2 * 1024 * 1024
MAX_ASSET_BYTES = 64 * 1024 * 1024


def generated_asset(name: str) -> bool:
    """Exclude generated install/desktop bookkeeping, never application modules."""
    return name in {".DS_Store", ".package-lock.json"} or name.startswith("._")


def asset_path(value: str) -> str:
    """Admit one normalized relative member without platform-specific aliases."""
    path = PurePosixPath(value)
    if (
        not value
        or value == "."
        or len(value) > 1024
        or "\\" in value
        or "\x00" in value
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
        or len(path.parts) > 64
    ):
        raise ValueError("Node asset path must be a normalized relative path")
    return value


@final
class NodeAsset(Contract):
    """One exact regular application file in a signed runtime asset wheel."""

    path: str = Field(max_length=1024, description="Namespace-relative member.")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=0, le=MAX_ASSET_BYTES)
    executable: bool = Field(
        default=False, description="Whether the regular asset may execute directly."
    )

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        """Refuse aliases and escapes before any filesystem access."""
        return asset_path(value)


@final
class NodeApplication(Contract):
    """Versioned public bootstrap metadata; signing/host admission is separate."""

    format: Literal[1] = 1
    bundle_id: Identifier
    bundle_version: str = Field(min_length=1, max_length=128)
    entry: str = Field(description="Module exporting the application's factory.")
    files: tuple[NodeAsset, ...] = Field(
        min_length=2,
        max_length=MAX_APPLICATION_FILES,
        description="Complete regular-file inventory in the selected namespace.",
    )

    @field_validator("format", mode="before")
    @classmethod
    def exact_format(cls, value: object) -> object:
        """Refuse numeric/boolean aliases of the bootstrap record version."""
        if type(value) is not int or value != 1:
            raise ValueError("Node application format must be integer1")
        return value

    @property
    def namespace(self) -> str:
        """Return a project-specific namespace independent of its version."""
        return (
            "_skulk_node_app_"
            + hashlib.sha256(self.bundle_id.encode()).hexdigest()[:24]
        )

    @property
    def distribution(self) -> str:
        """Return the dependency root the signed native manifest must declare."""
        return self.namespace.removeprefix("_").replace("_", "-")

    @model_validator(mode="after")
    def complete_assets(self) -> Self:
        """Require exact entry/SDK members, bounded data and an unambiguous tree."""
        asset_path(self.entry)
        names = [item.path for item in self.files]
        known = set(names)
        if len(known) != len(names) or names != sorted(names):
            raise ValueError("Node application inventory must be unique and sorted")
        if self.entry not in names or NODE_LAUNCHER not in names:
            raise ValueError("Node application entry or SDK launcher is missing")
        for name in names:
            if any(
                parent.as_posix() in known
                for parent in PurePosixPath(name).parents
                if parent.as_posix() != "."
            ):
                raise ValueError(
                    "Node application inventory has a file/directory collision"
                )
        if sum(item.size for item in self.files) > MAX_APPLICATION_BYTES:
            raise ValueError("Node application assets exceed their byte bound")
        return self


def verify_application(application: NodeApplication, runtime: Path) -> Path:
    """Verify the entire selected asset namespace before importing application code.

    No ancestor package search or mutable discovery selects these files. The
    separate signed installer remains responsible for the complete runtime.
    """
    root = runtime / "lib/python3.13/site-packages" / application.namespace
    expected = {item.path: item for item in application.files}
    seen: set[str] = set()
    folders = 0
    for parent, directories, names in os.walk(root, followlinks=False):
        folders += 1
        if folders > 2 * MAX_APPLICATION_FILES:
            raise Denied("Node application directory inventory exceeds its bound")
        folder = Path(parent)
        info = folder.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in {0, os.geteuid()}
            or info.st_mode & 0o022
        ):
            raise Denied("Node application directory has unsafe custody")
        for name in directories:
            if (folder / name).is_symlink():
                raise Denied("Node application contains a directory link")
        for name in names:
            path = folder / name
            if generated_asset(name):
                if not stat.S_ISREG(path.lstat().st_mode):
                    raise Denied("Node application bookkeeping is not a regular file")
                continue
            relative = path.relative_to(root).as_posix()
            selected = expected.get(relative)
            if selected is None:
                raise Denied("Node application contains an undeclared file")
            if len(seen) >= MAX_APPLICATION_FILES:
                raise Denied("Node application file inventory exceeds its bound")
            if (
                path.lstat().st_size != selected.size
                or bool(path.lstat().st_mode & 0o111) != selected.executable
                or measured_file(path, maximum=MAX_ASSET_BYTES) != selected.sha256
            ):
                raise Denied("Node application file differs from its declaration")
            seen.add(relative)
    if seen != set(expected):
        raise Denied("Node application is missing declared assets")
    return root


@final
class _Helper(Contract):
    executable: str
    executableSha256: str


@final
class _Selection(Contract):
    format: Literal[1] = 1
    entry: str
    entrySha256: str
    credentialHelper: _Helper


def launch_node_application(
    application: NodeApplication, configuration_fd: int
) -> None:
    """Replace this bootstrap with exact selected Node, retaining owner/startup FDs.

    The private selection FD supplies only the selected public credential-reader
    interpreter, never account settings or credential values. The caller owns
    startup authority; this function neither reads it nor manufactures context.
    """
    runtime = Path(sys.prefix).absolute()
    selected = select_node(runtime)
    root = verify_application(application, runtime)
    python = Path(sys.executable).absolute()
    helper = _Helper(
        executable=str(python),
        executableSha256=measured_file(
            python,
            maximum=128 * 1024 * 1024,
            executable=True,
            follow_selected_link=True,
        ),
    )
    entry = next(item for item in application.files if item.path == application.entry)
    selection = _Selection(
        entry=str(root / entry.path), entrySha256=entry.sha256, credentialHelper=helper
    )
    with tempfile.TemporaryFile() as descriptor:
        descriptor.write(canonical(selection))
        descriptor.flush()
        descriptor.seek(0)
        os.set_inheritable(descriptor.fileno(), True)
        # exec keeps the original process group, log pipes and inherited owner
        # fence; there is no detached launcher whose death could orphan Node.
        os.execve(
            selected.executable,
            [
                str(selected.executable),
                str(root / NODE_LAUNCHER),
                str(configuration_fd),
                str(descriptor.fileno()),
            ],
            {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        )


def main() -> None:
    """Read only this exact bootstrap archive's versioned application declaration."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-fd", type=int, required=True)
    options = parser.parse_args()
    with zipfile.ZipFile(sys.argv[0]) as archive:
        if len(archive.infolist()) != 2 or set(archive.namelist()) != {
            "__main__.py",
            "node-application.json",
        }:
            raise Denied("Node bootstrap archive has an unexpected inventory")
        info = archive.getinfo("node-application.json")
        if info.file_size > MAX_APPLICATION_METADATA:
            raise Denied("Node bootstrap metadata exceeds its bound")
        application = NodeApplication.model_validate_json(archive.read(info))
    launch_node_application(application, options.config_fd)
