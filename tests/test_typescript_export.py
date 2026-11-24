"""The public TypeScript export refuses changed or accidentally included bytes."""

import io
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from pydantic import TypeAdapter


@pytest.fixture
def public_source(tmp_path: Path) -> Path:
    """Copy only recorded MIT source; no Node install, compiler or private repo."""
    root = Path(__file__).resolve().parents[1]
    record = TypeAdapter(dict[str, dict[str, str] | int]).validate_json(
        (root / "packages/typescript/SOURCE-MANIFEST.json").read_bytes()
    )
    files = TypeAdapter(dict[str, str]).validate_python(record["files"])
    names = {f"packages/typescript/{name}" for name in files} | {
        "packages/typescript/SOURCE-MANIFEST.json",
        "tools/check_typescript_export.py",
        "LICENSE.md",
        "pyproject.toml",
    }
    names |= {
        f"skulk_capability_sdk/{name}"
        for name in (
            "_credential_bridge.py",
            "_credential_reader.py",
            "credentials.py",
            "storage.py",
            "contracts.py",
        )
    }
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((root / name).read_bytes())
    return tmp_path


def check(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Exercise the real export guard in a separate account-free process."""
    return subprocess.run(
        [sys.executable, "tools/check_typescript_export.py", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_typescript_extra_source_is_refused(public_source: Path) -> None:
    """An inert extra module is still outside the explicit public export."""
    (public_source / "packages/typescript/src/unexpected.ts").write_text("export {};\n")
    result = check(public_source)
    assert result.returncode != 0 and "runtime inventory differs" in result.stderr


def test_typescript_changed_source_is_refused(public_source: Path) -> None:
    """Runtime edits require a deliberately reviewed source identity refresh."""
    (public_source / "packages/typescript/src/application.ts").write_text(
        "export {};\n"
    )
    result = check(public_source)
    assert result.returncode != 0 and "source digest differs" in result.stderr


def test_typescript_archive_extra_member_is_refused(public_source: Path) -> None:
    """An archive cannot smuggle implementation outside the positive member set."""
    archive = public_source / "unexpected.tgz"
    with tarfile.open(archive, "w:gz") as output:
        raw = b"synthetic private implementation"
        member = tarfile.TarInfo("package/private.js")
        member.size = len(raw)
        output.addfile(member, io.BytesIO(raw))
    result = check(public_source, str(archive))
    assert result.returncode != 0 and "archive inventory differs" in result.stderr
