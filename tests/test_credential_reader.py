"""Public-only credential reads, containment and actual helper-process cleanup."""

import base64
import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from skulk_capability_sdk import credentials
from skulk_capability_sdk.contracts import (
    CredentialInput,
    Descriptor,
    Manifest,
    Startup,
)
from skulk_capability_sdk.credentials import (
    CredentialReader,
    CredentialSelection,
    CredentialUnavailable,
)
from skulk_capability_sdk.storage import atomic_private, secure_directory


def installation(
    root: Path, *, required: bool = True
) -> tuple[CredentialReader, CredentialSelection]:
    """Provision the documented protected shape without any private-host imports."""
    secure_directory(root)
    vault = root / "vault"
    secure_directory(vault)
    secure_directory(vault / "versions")
    database = vault / "credentials.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE aliases (reference TEXT PRIMARY KEY,digest TEXT NOT NULL);"
            "CREATE TABLE versions (reference TEXT,digest TEXT,entry TEXT,"
            "PRIMARY KEY(reference,digest));"
        )
    database.chmod(0o600)
    selection = CredentialSelection(root=str(vault.absolute()), namespace=uuid4().hex)
    atomic_private(root / "selection.json", selection.model_dump_json().encode())
    manifest = Manifest(
        bundle_id="test.capability",
        bundle_version="1.0.0",
        executable=str(root / "child"),
        executable_sha256="0" * 64,
        arguments=(),
        descriptors=(
            Descriptor(
                id="test.call",
                version="1.0.0",
                title="Test",
                description="Test call",
                input_schema={"type": "object"},
            ),
        ),
        credential_inputs=(
            CredentialInput(
                credential_id="api-token",
                title="API token",
                description="Test API credential",
                required=required,
            ),
        ),
    )
    startup = Startup(
        socket=str(root / "socket"),
        token="0" * 64,
        node_id="test.node",
        host_id="host",
        transport_id="transport",
        manifest=manifest,
        credential_config=str(root / "selection.json"),
    )
    return CredentialReader(startup), selection


def replace(selection: CredentialSelection, content: bytes) -> str:
    """Make an immutable backend version and switch its synthetic alias atomically."""
    root = Path(selection.root)
    entry = uuid4().hex
    digest = hashlib.sha256(content).hexdigest()
    atomic_private(root / "versions" / entry, content)
    with sqlite3.connect(root / "credentials.db") as connection:
        connection.execute(
            "INSERT OR REPLACE INTO versions VALUES (?,?,?)",
            ("api-token", digest, entry),
        )
        connection.execute(
            "INSERT OR REPLACE INTO aliases VALUES (?,?)", ("api-token", digest)
        )
    return entry


def test_public_reader_observes_rotation_and_optional_retirement(
    tmp_path: Path,
) -> None:
    """Active aliases change without rewriting or forgetting old versions."""
    reader, selection = installation(tmp_path, required=False)
    assert reader.read("api-token") is None
    first_entry = replace(selection, b"first-synthetic-token")
    value = reader.read("api-token")
    assert value is not None and value.get_secret_value() == b"first-synthetic-token"
    assert "first-synthetic-token" not in repr(value)
    replace(selection, b"replacement-token")
    value = reader.read("api-token")
    assert value is not None and value.get_secret_value() == b"replacement-token"
    with sqlite3.connect(Path(selection.root) / "credentials.db") as connection:
        connection.execute("DELETE FROM aliases")
    assert reader.read("api-token") is None
    assert (
        Path(selection.root) / "versions" / first_entry
    ).read_bytes() == b"first-synthetic-token"


def test_required_and_undeclared_inputs_are_distinct(tmp_path: Path) -> None:
    """Absence of a required input cannot become an anonymous fallback."""
    reader, _ = installation(tmp_path)
    with pytest.raises(CredentialUnavailable, match="Required"):
        reader.read("api-token")
    with pytest.raises(ValueError, match="not declared"):
        reader.read("another-nodes-token")


def test_readonly_journal_uri_preserves_literal_directory_characters(
    tmp_path: Path,
) -> None:
    """Spaces, Unicode and URI punctuation cannot change the selected journal."""
    reader, selected = installation(tmp_path / "private #? 音楽")
    replace(selected, b"synthetic-token")
    value = reader.read("api-token")
    assert value is not None and value.get_secret_value() == b"synthetic-token"


@pytest.mark.parametrize(
    "failure",
    [
        "missing-selection",
        "missing-vault",
        "missing-journal",
        "missing-version",
        "changed-version",
        "symlink",
        "public-mode",
    ],
)
def test_missing_or_damaged_storage_never_reinitializes_or_falls_back(
    tmp_path: Path,
    failure: str,
) -> None:
    """Optional inputs also require exact, private, verified bytes."""
    reader, selection = installation(tmp_path, required=False)
    entry = replace(selection, b"private-synthetic-token")
    root = Path(selection.root)
    version = root / "versions" / entry
    if failure == "missing-selection":
        (tmp_path / "selection.json").unlink()
    elif failure == "missing-vault":
        shutil.rmtree(root)
    elif failure == "missing-journal":
        (root / "credentials.db").unlink()
    elif failure == "missing-version":
        version.unlink()
    elif failure == "changed-version":
        version.write_bytes(b"corrupt")
    elif failure == "symlink":
        outside = tmp_path / "outside"
        outside.write_bytes(b"private-synthetic-token")
        outside.chmod(0o600)
        version.unlink()
        version.symlink_to(outside)
    elif failure == "public-mode":
        version.chmod(0o644)
    with pytest.raises(CredentialUnavailable) as error:
        reader.read("api-token")
    assert str(tmp_path) not in str(error.value)
    assert "private-synthetic-token" not in str(error.value)
    if failure == "missing-vault":
        assert not root.exists()
    if failure == "missing-journal":
        assert not (root / "credentials.db").exists()


def test_broken_alias_join_is_not_an_unconfigured_optional_input(
    tmp_path: Path,
) -> None:
    """A dangling selected alias is corruption, not permission to send anonymously."""
    reader, selection = installation(tmp_path, required=False)
    replace(selection, b"token")
    with sqlite3.connect(Path(selection.root) / "credentials.db") as connection:
        connection.execute("DELETE FROM versions")
    with pytest.raises(CredentialUnavailable):
        reader.read("api-token")


def native_fixture(tmp_path: Path) -> tuple[CredentialReader, CredentialSelection]:
    """Select a native backend with a synthetic journal entry, never a real account."""
    reader, selected = installation(tmp_path)
    replace(selected, b"native-synthetic-token")
    native = CredentialSelection(
        root=selected.root, namespace=selected.namespace, backend="linux_secret_service"
    )
    atomic_private(tmp_path / "selection.json", native.model_dump_json().encode())
    return reader, native


def test_native_helper_uses_private_pipes_and_reaps_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real child receives scoped metadata and returns bounded bytes."""
    reader, _ = native_fixture(tmp_path)
    encoded = base64.b64encode(b"native-synthetic-token").decode()
    script = (
        "import sys,json,os; request=json.load(sys.stdin); "
        f"assert os.getpgrp() == {os.getpgrp()}; "
        "assert request['service'].startswith('ai.foxlight.skulk.'); "
        f"sys.stdout.write(json.dumps({{'value': '{encoded}'}}))"
    )
    monkeypatch.setattr(
        credentials, "_helper_command", lambda: [sys.executable, "-c", script]
    )
    value = reader.read("api-token")
    assert value is not None and value.get_secret_value() == b"native-synthetic-token"


@pytest.mark.parametrize("failure", ["oversize", "hang", "invalid", "wrong-digest"])
def test_native_helper_failure_is_bounded_reaped_and_has_no_file_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """A helper cannot retain a process, leak diagnostics or select another backend."""
    reader, _ = native_fixture(tmp_path)
    pid_path = tmp_path / "helper.pid"
    action = {
        "oversize": "sys.stdout.write('x'*100000); sys.stdout.flush(); time.sleep(10)",
        "hang": "time.sleep(10)",
        "invalid": "sys.stdout.write('bad-json')",
        "wrong-digest": "sys.stdout.write(json.dumps({'value':'d3Jvbmc='}))",
    }[failure]
    script = (
        "import os,sys,time,json; "
        "open(sys.argv[1],'w').write(str(os.getpid())); " + action
    )
    monkeypatch.setattr(
        credentials,
        "_helper_command",
        lambda: [sys.executable, "-c", script, str(pid_path)],
    )
    monkeypatch.setattr(credentials, "NATIVE_SECONDS", 0.5)
    with pytest.raises(CredentialUnavailable) as error:
        reader.read("api-token")
    assert "native-synthetic-token" not in str(error.value)
    assert str(tmp_path) not in str(error.value)
    pid = int(pid_path.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_public_reader_imports_without_private_host() -> None:
    """A fresh interpreter proves the authoring module has no hidden host imports."""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys; "
            "from skulk_capability_sdk.credentials import CredentialReader; "
            "assert not any(name.startswith('skulk_capability_nodes') "
            "for name in sys.modules)",
        ],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode()
