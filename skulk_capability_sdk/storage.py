"""Owner-only installation and identity records, with exclusive host ownership."""

import fcntl
import hashlib
import os
import stat
from pathlib import Path
from typing import final
from uuid import UUID, uuid4

from .contracts import (
    MAX_FRAME_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_STARTUP_BYTES,
    Manifest,
    Startup,
)


def secure_directory(path: Path) -> None:
    """Create or validate a private directory without accepting a symlink."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise ValueError("directory must be owner-only and owned by this user")


def read_private(path: Path, limit: int = MAX_FRAME_BYTES) -> bytes:
    """Read a bounded owner-only regular file, rejecting symlink substitution."""
    # A damaged path may be a FIFO. Do not block before fstat can reject it.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ValueError("file must be owner-only and owned by this user")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            data = source.read(limit + 1)
        if len(data) > limit:
            raise ValueError("file exceeds contract bound")
        return data
    finally:
        os.close(descriptor)


def read_startup(descriptor: int) -> Startup:
    """Read and close the startup record the owner handed this child.

    Bounded by ``MAX_STARTUP_BYTES`` (the manifest's own bound plus one frame
    for the installation's identity and settings) and validated strictly: a
    child starts from exactly the record its owner wrote, or not at all.
    """
    with os.fdopen(descriptor, "rb") as source:
        data = source.read(MAX_STARTUP_BYTES + 1)
    if len(data) > MAX_STARTUP_BYTES:
        raise ValueError("startup record exceeds bound")
    return Startup.model_validate_json(data, strict=True)


def identity(path: Path) -> str:
    """Persist a UUID once; callers must hold the host lock before creation."""
    if not path.exists():
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "w") as target:
            target.write(str(uuid4()))
            target.flush()
            os.fsync(target.fileno())
    return str(UUID(read_private(path).decode()))


@final
class HostLock:
    """Fence concurrent supervisors sharing one private installation directory."""

    def __init__(self, root: Path) -> None:
        secure_directory(root)
        self.descriptor = os.open(
            root / "supervisor.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.descriptor)
            raise

    def close(self) -> None:
        """Release installation ownership after every child has been reaped."""
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1


def executable_digest(path: Path) -> str:
    """Hash an installed executable without loading its complete bytes in memory."""
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def load_manifest(path: Path) -> Manifest:
    """Validate an explicit private manifest and absolute pinned executable."""
    manifest = Manifest.model_validate_json(
        read_private(path, MAX_MANIFEST_BYTES), strict=True
    )
    executable = Path(manifest.executable)
    if not executable.is_absolute() or not executable.is_file():
        raise ValueError("executable must be an explicit absolute installed file")
    if executable_digest(executable) != manifest.executable_sha256:
        raise ValueError("executable digest mismatch")
    return manifest


def atomic_private(path: Path, value: bytes, *, executable: bool = False) -> None:
    """Fsync a new owner-only file before atomic replacement and directory flush."""
    secure_directory(path.parent)
    staged = path.parent / (".stage-" + uuid4().hex)
    descriptor = os.open(
        staged,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o700 if executable else 0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(value)
            target.flush()
            os.fsync(target.fileno())
        os.replace(staged, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        staged.unlink(missing_ok=True)
