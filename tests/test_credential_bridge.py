"""Actual public credential bridge processes and shared native-reader dispatch."""

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest
from keyring.backend import KeyringBackend
from keyring.compat import properties

from skulk_capability_sdk import _credential_reader
from skulk_capability_sdk._credential_bridge import reader_identity
from skulk_capability_sdk.credentials import (
    CredentialAccess,
    CredentialSelection,
    read_contained_credential,
)
from skulk_capability_sdk.storage import atomic_private
from tests.test_credential_reader import installation, replace


def request(configuration: Path, *, required: bool = False) -> dict[str, object]:
    """Provide only already-admitted references, never backend values in settings."""
    return {
        "protocol": 1,
        "configuration": str(configuration),
        "declarations": [{"credential_id": "api-token", "required": required}],
        "reference": "api-token",
    }


def run_bridge(value: dict[str, object]) -> subprocess.CompletedProcess[bytes]:
    """Run the installed public wheel in an isolated selected interpreter."""
    return subprocess.run(
        [sys.executable, "-I", "-B", "-m", "skulk_capability_sdk._credential_bridge"],
        input=json.dumps(value).encode(),
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        timeout=10,
        check=False,
    )


def test_bridge_reads_rotated_values_and_optional_retirement(tmp_path: Path) -> None:
    """Actual subprocesses observe current aliases and own no mutable store API."""
    _, selected = installation(tmp_path, required=False)
    configuration = tmp_path / "selection.json"
    for content in (None, b"first-synthetic-secret", b"replacement-secret", None):
        if content is None:
            import sqlite3

            with sqlite3.connect(Path(selected.root) / "credentials.db") as connection:
                connection.execute("DELETE FROM aliases")
        else:
            replace(selected, content)
        result = run_bridge(request(configuration))
        assert result.returncode == 0 and result.stderr == b""
        hello, reply = [json.loads(line) for line in result.stdout.splitlines()]
        assert hello == {
            "protocol": 1,
            "sdk_version": "1.0.1",
            "reader_sha256": reader_identity(),
        }
        assert reply == {
            "protocol": 1,
            "value": None if content is None else base64.b64encode(content).decode(),
        }


def test_reader_never_invokes_directory_provisioning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ordinary read cannot recreate storage if its directory disappears."""
    reader, selected = installation(tmp_path)
    replace(selected, b"read-only-synthetic-token")

    def refuse_provisioning(*args: object, **kwargs: object) -> None:
        raise AssertionError("credential reads must not provision directories")

    monkeypatch.setattr(Path, "mkdir", refuse_provisioning)
    value = reader.read("api-token")
    assert (
        value is not None and value.get_secret_value() == b"read-only-synthetic-token"
    )


@pytest.mark.parametrize(
    "failure",
    [
        "required",
        "undeclared",
        "missing",
        "unsupported",
        "duplicate",
        "extra",
        "boolean",
        "floating",
        "oversized",
    ],
)
def test_bridge_refuses_with_no_private_diagnostics(
    tmp_path: Path, failure: str
) -> None:
    """No malformed request or unavailable store becomes a fallback or diagnostic."""
    installation(tmp_path, required=False)
    configuration = tmp_path / "selection.json"
    value = request(configuration, required=failure == "required")
    if failure == "undeclared":
        value["reference"] = "other-token"
    elif failure == "missing":
        configuration.unlink()
    elif failure == "unsupported":
        atomic_private(configuration, b'{"backend":"unsupported-secret-backend"}')
    elif failure == "duplicate":
        value["declarations"] = [
            {"credential_id": "api-token", "required": True},
            {"credential_id": "api-token", "required": False},
        ]
    elif failure == "extra":
        value["write"] = "never-allowed"
    elif failure == "boolean":
        value["protocol"] = True
    elif failure == "floating":
        value["protocol"] = 1.0
    elif failure == "oversized":
        value["configuration"] = "private-token" * 4000
    result = run_bridge(value)
    assert result.returncode == 1 and result.stderr == b""
    lines = result.stdout.splitlines()
    assert (
        len(lines) == 1 and json.loads(lines[0])["reader_sha256"] == reader_identity()
    )
    assert str(tmp_path).encode() not in result.stdout + result.stderr
    if failure == "missing":
        assert not configuration.exists()


@pytest.mark.parametrize("backend_name", ["macos_keychain", "linux_secret_service"])
def test_contained_native_read_starts_no_descendants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend_name: str
) -> None:
    """Both containing processes use the exact same explicit native backend call."""
    _, selected = installation(tmp_path)
    entry = replace(selected, b"native-synthetic-secret")
    native = CredentialSelection.model_validate(
        {
            "root": selected.root,
            "namespace": selected.namespace,
            "backend": backend_name,
        }
    )
    atomic_private(tmp_path / "selection.json", native.model_dump_json().encode())
    observed: list[tuple[str, str]] = []

    class Backend(KeyringBackend):
        """One synthetic native API implementation, never OS account access."""

        @properties.classproperty
        def priority(self) -> float:
            return 1.0

        def get_password(self, service: str, username: str) -> str:
            observed.append((service, username))
            return base64.b64encode(b"native-synthetic-secret").decode()

        def set_password(self, service: str, username: str, password: str) -> None:
            raise AssertionError("mutation is forbidden")

        def delete_password(self, service: str, username: str) -> None:
            raise AssertionError("mutation is forbidden")

    def backend(value: _credential_reader.NativeRead) -> KeyringBackend:
        assert value.backend == backend_name
        return Backend()

    def no_subprocess(*args: object, **kwargs: object) -> None:
        raise AssertionError("a contained read must not spawn a grandchild")

    monkeypatch.setattr(_credential_reader, "_backend", backend)
    monkeypatch.setattr(subprocess, "Popen", no_subprocess)
    access = CredentialAccess(str(tmp_path / "selection.json"), (("api-token", True),))
    value = read_contained_credential(access, "api-token")
    assert value is not None and value.get_secret_value() == b"native-synthetic-secret"
    assert observed == [("ai.foxlight.skulk." + selected.namespace, entry)]
