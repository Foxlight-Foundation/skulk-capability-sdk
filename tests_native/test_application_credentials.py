"""Native-admitted public credential access without legacy startup coercion."""

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import JsonValue
from test_application import running

from skulk_capability_sdk.application import (
    ApplicationStartup,
    CapabilityApp,
    Invocation,
)
from skulk_capability_sdk.credentials import CredentialSelection, CredentialUnavailable
from skulk_capability_sdk.storage import atomic_private, secure_directory


def provision(root: Path) -> tuple[Path, Path]:
    """Create independent synthetic owner storage, never a host implementation."""
    secure_directory(root)
    vault = root / "vault"
    secure_directory(vault)
    secure_directory(vault / "versions")
    with sqlite3.connect(vault / "credentials.db") as connection:
        connection.executescript(
            "CREATE TABLE aliases (reference TEXT PRIMARY KEY,digest TEXT NOT NULL);"
            "CREATE TABLE versions (reference TEXT,digest TEXT,entry TEXT,"
            "PRIMARY KEY(reference,digest));"
        )
    (vault / "credentials.db").chmod(0o600)
    selection = root / "selection.json"
    atomic_private(
        selection,
        CredentialSelection(root=str(vault), namespace=uuid4().hex)
        .model_dump_json()
        .encode(),
    )
    return selection, vault


def replace(vault: Path, reference: str, value: bytes | None) -> None:
    """Rotate or retire one synthetic alias without deleting immutable versions."""
    with sqlite3.connect(vault / "credentials.db") as connection:
        if value is None:
            connection.execute("DELETE FROM aliases WHERE reference=?", (reference,))
            return
        entry = uuid4().hex
        digest = hashlib.sha256(value).hexdigest()
        atomic_private(vault / "versions" / entry, value)
        connection.execute(
            "INSERT INTO versions VALUES (?,?,?)", (reference, digest, entry)
        )
        connection.execute(
            "INSERT OR REPLACE INTO aliases VALUES (?,?)", (reference, digest)
        )


@pytest.mark.parametrize("required", [False, True])
async def test_native_reader_rotation_retirement_damage_and_restart(
    tmp_path: Path, required: bool
) -> None:
    """The actual app runtime admits declarations and never retains old aliases."""
    selection, vault = provision(tmp_path)
    declarations: list[dict[str, JsonValue]] = [
        {
            "credential_id": "api-token",
            "title": "API token",
            "description": "Synthetic declared input",
            "required": required,
        }
    ]
    observed: list[ApplicationStartup] = []

    @asynccontextmanager
    async def setup(startup: ApplicationStartup) -> AsyncGenerator[None]:
        observed.append(startup)
        yield

    async def handler(_call: Invocation) -> dict[str, JsonValue]:
        return {"ok": True}

    def application() -> CapabilityApp:
        app = CapabilityApp(setup=setup)
        app.unary("example", handler)
        return app

    async with running(
        application(), credential_inputs=declarations, credential_config=str(selection)
    ) as peer:
        startup = observed[-1]
        reader = startup.credentials
        assert str(selection) not in repr(startup) + repr(reader)
        with pytest.raises(ValueError, match="not declared"):
            await asyncio.to_thread(reader.read, "undeclared")
        if required:
            with pytest.raises(CredentialUnavailable):
                await asyncio.to_thread(reader.read, "api-token")
        else:
            assert await asyncio.to_thread(reader.read, "api-token") is None
        for value in (b"synthetic-first", b"synthetic-rotated"):
            replace(vault, "api-token", value)
            result = await asyncio.to_thread(reader.read, "api-token")
            assert result is not None and result.get_secret_value() == value
            assert value.decode() not in repr(result)
            await peer.health()
        replace(vault, "api-token", None)
        if required:
            with pytest.raises(CredentialUnavailable):
                await asyncio.to_thread(reader.read, "api-token")
        else:
            assert await asyncio.to_thread(reader.read, "api-token") is None
        replace(vault, "api-token", b"survives-restart")

    async with running(
        application(), credential_inputs=declarations, credential_config=str(selection)
    ) as peer:
        reader = observed[-1].credentials
        value = await asyncio.to_thread(reader.read, "api-token")
        assert value is not None and value.get_secret_value() == b"survives-restart"
        original = selection.read_bytes()
        selection.unlink()
        with pytest.raises(CredentialUnavailable) as error:
            await asyncio.to_thread(reader.read, "api-token")
        assert not selection.exists() and str(tmp_path) not in str(error.value)
        atomic_private(selection, b'{"backend":"unsupported"}')
        with pytest.raises(CredentialUnavailable):
            await asyncio.to_thread(reader.read, "api-token")
        assert json.loads(selection.read_bytes()) == {"backend": "unsupported"}
        atomic_private(selection, original)
        await peer.health()


async def test_native_app_without_selected_credentials_creates_nothing() -> None:
    """Declared optional absence is distinct from an owner failing to select storage."""
    observed: list[ApplicationStartup] = []

    @asynccontextmanager
    async def setup(startup: ApplicationStartup) -> AsyncGenerator[None]:
        observed.append(startup)
        yield

    app = CapabilityApp(setup=setup)

    async def handler(_call: Invocation) -> dict[str, JsonValue]:
        return {}

    app.unary("example", handler)
    declarations: list[dict[str, JsonValue]] = [
        {
            "credential_id": "optional",
            "title": "Optional",
            "description": "Synthetic input",
            "required": False,
        }
    ]
    async with running(app, credential_inputs=declarations):
        with pytest.raises(CredentialUnavailable, match="did not provision"):
            await asyncio.to_thread(observed[0].credentials.read, "optional")


async def test_installed_sdk_child_reads_rotation_retirement_and_restart(
    tmp_path: Path,
) -> None:
    """An actual separately launched public SDK process observes owner-store changes."""
    import os
    import sys

    class ProcessApplication:
        """Own one real child until compiled shutdown or forced cleanup completes."""

        async def serve(self, configuration_descriptor: int) -> None:
            """Pass startup only on its protected inherited descriptor, then reap."""
            process: asyncio.subprocess.Process | None = None
            try:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-I",
                    str(Path(__file__).parent / "fixtures/credential_application.py"),
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

    selection, vault = provision(tmp_path)
    declarations: list[dict[str, JsonValue]] = [
        {
            "credential_id": "api-token",
            "title": "Token",
            "description": "Synthetic only",
            "required": False,
        }
    ]
    async with running(
        ProcessApplication(),
        credential_inputs=declarations,
        credential_config=str(selection),
    ) as peer:
        for index, value in enumerate(
            (None, b"first-process-value", b"rotated-process-value", None)
        ):
            replace(vault, "api-token", value)
            await peer.invoke(f"read-{index}")
            result, body = await peer.receive()
            assert result["kind"] == "result" and result["error"] is None
            assert json.loads(body) == {
                "synthetic_value": value.decode() if value else None
            }
            await peer.health()
        replace(vault, "api-token", b"survives-process-restart")
    async with running(
        ProcessApplication(),
        credential_inputs=declarations,
        credential_config=str(selection),
    ) as peer:
        await peer.invoke("after-restart")
        _, body = await peer.receive()
        assert json.loads(body) == {"synthetic_value": "survives-process-restart"}
        selection.unlink()
        await peer.invoke("missing-selection")
        _, body = await peer.receive()
        assert json.loads(body) == {"unavailable": True}
        assert not selection.exists()
        await peer.health()
