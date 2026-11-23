"""Explicit real OS-session acceptance, separate from headless/file-backend tests.

Run only with an unlocked test session and SKULK_TEST_NATIVE_BACKEND selected.
These synthetic startup peers test public language processes and real storage;
they do not establish signed Core/owner caller admission or provider authority.
"""

import asyncio
import base64
import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Literal
from uuid import uuid4

import pytest
from pydantic import JsonValue, TypeAdapter

from skulk_capability_sdk.credentials import CredentialSelection
from skulk_capability_sdk.storage import atomic_private, secure_directory
from tests_native.test_application import Peer, running
from tests_typescript.test_node_application import NodeApplication
from tests_typescript.test_node_credentials import declarations, helper, source

type Backend = Literal["macos_keychain", "linux_secret_service"]
_BACKEND: TypeAdapter[Backend] = TypeAdapter(Backend)


class NativeVault:
    """Independent test journal with real, explicitly selected native entries."""

    def __init__(self, root: Path, backend: Backend) -> None:
        """Reserve a fresh opaque namespace and private public-reader selection."""
        secure_directory(root)
        self.root = root
        self.backend = backend
        self.namespace = uuid4().hex
        self.entries: set[str] = set()
        self.selection = root / "selection.json"
        with sqlite3.connect(root / "credentials.db") as connection:
            connection.executescript(
                "CREATE TABLE aliases(reference TEXT PRIMARY KEY,digest TEXT NOT NULL);"
                "CREATE TABLE versions(reference TEXT,digest TEXT,entry TEXT,"
                "PRIMARY KEY(reference,digest));"
            )
        (root / "credentials.db").chmod(0o600)
        atomic_private(
            self.selection,
            CredentialSelection(
                root=str(root), backend=backend, namespace=self.namespace
            )
            .model_dump_json()
            .encode(),
        )

    def native(
        self,
        operation: Literal["put", "delete"],
        entry: str,
        value: bytes | None = None,
    ) -> None:
        """Bound and reap the test-only mutation helper without logging its input."""
        request = {
            "backend": self.backend,
            "operation": operation,
            "service": "ai.foxlight.skulk." + self.namespace,
            "entry": entry,
            "value": base64.b64encode(value).decode() if value else None,
        }
        environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
        if sys.platform == "linux":
            environment["DBUS_SESSION_BUS_ADDRESS"] = (
                f"unix:path=/run/user/{os.getuid()}/bus"
            )
        result = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).with_name("backend.py"))],
            input=json.dumps(request).encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=8,
            env=environment,
            check=True,
        )
        assert result.stdout == b'{"passed": true}\n'

    def replace(self, value: bytes | None) -> None:
        """Switch the synthetic alias only after native write/read verification."""
        if value is None:
            with sqlite3.connect(self.root / "credentials.db") as connection:
                connection.execute("DELETE FROM aliases WHERE reference='api-token'")
            return
        entry = uuid4().hex
        self.entries.add(entry)
        self.native("put", entry, value)
        digest = hashlib.sha256(value).hexdigest()
        with sqlite3.connect(self.root / "credentials.db") as connection:
            connection.execute(
                "INSERT INTO versions VALUES (?,?,?)", ("api-token", digest, entry)
            )
            connection.execute(
                "INSERT OR REPLACE INTO aliases VALUES (?,?)", ("api-token", digest)
            )

    def lose_current(self) -> None:
        """Remove an owned native entry while preserving its selected journal alias."""
        with sqlite3.connect(self.root / "credentials.db") as connection:
            row = connection.execute(
                "SELECT entry FROM versions JOIN aliases USING(reference,digest) "
                "WHERE reference='api-token'"
            ).fetchone()
        assert row is not None
        self.native("delete", str(row[0]))

    def close(self) -> None:
        """Delete and independently verify absence of every owned synthetic entry."""
        for entry in self.entries:
            self.native("delete", entry)
        assert not (self.root / "versions").exists()


class PythonApplication:
    """Own a genuine isolated public SDK child and its inherited startup FD."""

    async def serve(self, configuration_descriptor: int) -> None:
        """Reap startup, ordinary shutdown and cancellation without owner imports."""
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                str(Path(__file__).with_name("child.py")),
                str(configuration_descriptor),
                pass_fds=(configuration_descriptor,),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            assert await process.wait() == 0
        finally:
            os.close(configuration_descriptor)
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()


@pytest.mark.parametrize("language", ["python", "typescript"])
@pytest.mark.parametrize("required", [False, True])
async def test_real_native_session_rotation_retirement_restart_and_unavailability(
    tmp_path: Path, language: str, required: bool
) -> None:
    """Both language processes observe native storage without a file fallback."""
    backend = _BACKEND.validate_python(os.environ["SKULK_TEST_NATIVE_BACKEND"])
    assert sys.platform == ("darwin" if backend == "macos_keychain" else "linux")
    vault = NativeVault(tmp_path / "vault", backend)

    def application() -> PythonApplication | NodeApplication:
        return (
            PythonApplication()
            if language == "python"
            else NodeApplication(tmp_path, source(helper()))
        )

    async def invoke(
        peer: Peer, expected: dict[str, JsonValue], value: bytes | None = None
    ) -> None:
        await peer.invoke(
            uuid4().hex,
            payload=json.dumps(
                {"expected": hashlib.sha256(value).hexdigest() if value else None}
            ).encode(),
        )
        result, body = await peer.receive()
        assert result["error"] is None and json.loads(body) == expected
        await peer.health()

    absent: dict[str, JsonValue] = (
        {"unavailable": True} if required else {"present": False}
    )
    matched: dict[str, JsonValue] = {"present": True, "matched": True, "safe": True}
    try:
        async with running(
            application(),
            credential_inputs=declarations(required),
            credential_config=str(vault.selection),
        ) as peer:
            await invoke(peer, absent)
            for _ in range(2):
                value = secrets.token_hex(24).encode()
                await asyncio.to_thread(vault.replace, value)
                await invoke(peer, matched, value)
            await peer.invoke("undeclared", payload=b'{"reference":"not-declared"}')
            result, body = await peer.receive()
            assert result["error"] is None and json.loads(body) == {"undeclared": True}
            vault.replace(None)
            await invoke(peer, absent)
            value = secrets.token_hex(24).encode()
            await asyncio.to_thread(vault.replace, value)
        async with running(
            application(),
            credential_inputs=declarations(required),
            credential_config=str(vault.selection),
        ) as peer:
            await invoke(peer, matched, value)
            await asyncio.to_thread(vault.lose_current)
            await invoke(peer, {"unavailable": True})
            assert not (vault.root / "versions").exists()
            value = secrets.token_hex(24).encode()
            await asyncio.to_thread(vault.replace, value)
            await invoke(peer, matched, value)
            saved = vault.selection.read_bytes()
            vault.selection.unlink()
            await invoke(peer, {"unavailable": True})
            assert not vault.selection.exists()
            atomic_private(vault.selection, saved)
            await invoke(peer, matched, value)
    finally:
        await asyncio.to_thread(vault.close)
