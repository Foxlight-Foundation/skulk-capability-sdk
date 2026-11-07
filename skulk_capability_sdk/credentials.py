"""Read declared credentials through the exact owner-provisioned backend."""

import base64
import contextlib
import hashlib
import hmac
import json
import os
import selectors
import sqlite3
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal, cast, final
from urllib.parse import quote

from pydantic import Field, SecretBytes, TypeAdapter

from .contracts import Contract, Digest, Startup
from .storage import read_private, secure_directory

BackendName = Literal["file", "macos_keychain", "linux_secret_service"]
MAX_CREDENTIAL_BYTES = 4096
NATIVE_SECONDS = 8.0
_REPLY = TypeAdapter[dict[str, str]](dict[str, str])


class CredentialUnavailable(RuntimeError):
    """A declared credential cannot be resolved; messages contain no secret or path."""


class CredentialSelection(Contract):
    """Protected backend selection shared with the owner, never ordinary settings.

    This model is a base for the private owner's management interface. Capability
    authors use CredentialReader, which cannot create, rotate or retire entries.
    """

    root: str = Field(
        min_length=1, max_length=4096, description="Absolute protected vault directory."
    )
    backend: BackendName = Field(
        default="file", description="Explicit backend; no discovery or fallback."
    )
    namespace: str = Field(
        pattern=r"^[a-f0-9]{32}$", description="Owner-provisioned native namespace."
    )


@final
class _Entry(Contract):
    digest: Digest
    entry: str = Field(pattern=r"^[a-f0-9]{32}$")


def _existing_directory(path: Path) -> None:
    if not path.exists():
        raise CredentialUnavailable("Selected credential storage is unavailable")
    secure_directory(path)


def _private_database(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_size > 128 * 1024**2
        ):
            raise CredentialUnavailable("Selected credential journal is unavailable")
    finally:
        os.close(descriptor)


def _helper_command() -> list[str]:
    return (
        [sys.executable, "--sdk-credential-reader"]
        if getattr(sys, "frozen", False)
        else [sys.executable, "-I", "-m", "skulk_capability_sdk._credential_reader"]
    )


def _native(selection: CredentialSelection, entry: str) -> bytes:
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    if sys.platform == "linux":
        environment["DBUS_SESSION_BUS_ADDRESS"] = (
            f"unix:path=/run/user/{os.getuid()}/bus"
        )
    request = json.dumps(
        {
            "backend": selection.backend,
            "service": "ai.foxlight.skulk." + selection.namespace,
            "entry": entry,
        }
    ).encode()
    try:
        process = subprocess.Popen(
            _helper_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            # The managed owner's process-group fence must include this helper.
            # Our timeout targets only its exact PID, never the caller's group.
            start_new_session=False,
            env=environment,
        )
    except OSError:
        raise CredentialUnavailable(
            "Selected native credential backend is unavailable"
        ) from None
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(request)
        process.stdin.close()
        deadline = time.monotonic() + NATIVE_SECONDS
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise CredentialUnavailable(
                        "Native credential resolution timed out"
                    )
                part = os.read(process.stdout.fileno(), 8193 - len(output))
                if not part:
                    break
                output.extend(part)
                if len(output) > 8192:
                    raise CredentialUnavailable(
                        "Native credential response exceeds its bound"
                    )
        process.wait(timeout=max(0.01, deadline - time.monotonic()))
        if process.returncode != 0:
            raise CredentialUnavailable(
                "Selected native credential backend is unavailable"
            )
        encoded = _REPLY.validate_json(output).get("value")
        if encoded is None:
            raise CredentialUnavailable("Selected native credential is unavailable")
        result = base64.b64decode(encoded, validate=True)
        if not 0 < len(result) <= MAX_CREDENTIAL_BYTES:
            raise CredentialUnavailable("Selected credential exceeds its bound")
        return result
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise CredentialUnavailable(
            "Selected native credential could not be read"
        ) from None
    finally:
        if process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        process.wait()
        if process.stdin is not None:
            with contextlib.suppress(OSError):
                process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()


@final
class CredentialReader:
    """Resolve only this child's declared references without importing the host.

    Call read off the event loop: journal lock waits have a two-second timeout and
    native resolution uses a bounded, reaped helper. Each read observes the
    active alias again, including rotation and retirement. Values are SecretBytes;
    errors reveal no values, fingerprints, backend paths or native diagnostics.
    Missing selected storage never creates a new store or falls back to another.
    """

    def __init__(self, startup: Startup) -> None:
        """Retain only trusted startup selection and signed credential declarations."""
        self._configuration = startup.credential_config
        self._declared = {
            item.credential_id: item.required
            for item in startup.manifest.credential_inputs
        }

    def read(self, reference: str) -> SecretBytes | None:
        """Read an active value; None means a valid optional input is unconfigured.

        An undeclared reference raises ValueError. Required absence, damaged
        metadata, missing selected storage and unavailable native backends raise
        CredentialUnavailable. The method never changes aliases or versions.
        """
        if reference not in self._declared:
            raise ValueError("Credential reference is not declared by this capability")
        try:
            if self._configuration is None:
                raise CredentialUnavailable(
                    "Owner did not provision credential storage"
                )
            selection = CredentialSelection.model_validate_json(
                read_private(Path(self._configuration))
            )
            root = Path(selection.root)
            if not root.is_absolute():
                raise CredentialUnavailable(
                    "Selected credential storage is unavailable"
                )
            _existing_directory(root)
            path = root / "credentials.db"
            _private_database(path)
            with contextlib.closing(
                sqlite3.connect(
                    "file:" + quote(str(path), safe="/") + "?mode=ro",
                    uri=True,
                    timeout=2,
                )
            ) as connection:
                row = cast(
                    tuple[object, ...] | None,
                    connection.execute(
                        "SELECT a.digest,v.entry FROM aliases a LEFT JOIN versions v "
                        "ON v.reference=a.reference AND v.digest=a.digest "
                        "WHERE a.reference=?",
                        (reference,),
                    ).fetchone(),
                )
            if row is None:
                if self._declared[reference]:
                    raise CredentialUnavailable("Required credential is not configured")
                return None
            version = _Entry.model_validate({"digest": row[0], "entry": row[1]})
            if selection.backend == "file":
                _existing_directory(root / "versions")
                raw = read_private(
                    root / "versions" / version.entry, MAX_CREDENTIAL_BYTES
                )
            else:
                raw = _native(selection, version.entry)
            if not raw or not hmac.compare_digest(
                hashlib.sha256(raw).hexdigest(), version.digest
            ):
                raise CredentialUnavailable("Credential bytes failed verification")
            return SecretBytes(raw)
        except (OSError, ValueError, sqlite3.Error):
            raise CredentialUnavailable(
                "Selected credential could not be resolved"
            ) from None


def run_credential_helper() -> bool:
    """Handle the SDK-only native reader command before a frozen child's normal CLI.

    A child entry point calls this first and returns when it is True. Native
    values use private pipes, never argv. Normal child invocations return False.
    Pure-Python installations launch the same helper as an isolated module.
    """
    if sys.argv[1:] != ["--sdk-credential-reader"]:
        return False
    from ._credential_reader import main

    main()
    return True
