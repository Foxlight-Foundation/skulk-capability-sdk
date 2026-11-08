"""Public source custody refuses undeclared code and unqualified changes."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def exported_source(tmp_path: Path) -> Path:
    """Copy only the reviewed source set, excluding build caches and runtimes."""
    source = Path(__file__).resolve().parents[1]
    manifest = json.loads((source / "KERNEL-SOURCE-MANIFEST.json").read_text())
    names = (
        set(manifest["files"])
        | set(manifest["generated_files"])
        | {"LICENSE.md", "KERNEL-SOURCE-MANIFEST.json", "tools/check_kernel_export.py"}
    )
    for name in names:
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((source / name).read_bytes())
    return tmp_path


def run_export_check(root: Path) -> subprocess.CompletedProcess[str]:
    """Exercise the real archive CLI independently of pytest's import path."""
    return subprocess.run(
        [sys.executable, str(root / "tools/check_kernel_export.py")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_source_archive_needs_no_owner_or_git_history(exported_source: Path) -> None:
    """The complete positive export verifies without a private checkout."""
    assert not (exported_source / ".git").exists()
    result = run_export_check(exported_source)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["source_files"] == 95


def test_undeclared_kernel_source_refuses(exported_source: Path) -> None:
    """An accidental extra implementation cannot enter the source export."""
    (exported_source / "kernel/unexpected.py").write_text("# inert fixture\n")
    result = run_export_check(exported_source)
    assert result.returncode != 0
    assert "Undeclared kernel source" in result.stderr


def test_relabelled_source_change_refuses(exported_source: Path) -> None:
    """Refreshing the export hash cannot disguise changed qualified Rust code."""
    name = "kernel/src/portable.rs"
    path = exported_source / name
    path.write_bytes(path.read_bytes() + b"\n// changed synthetic export\n")
    manifest_path = exported_source / "KERNEL-SOURCE-MANIFEST.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][name]["exported_sha256"] = hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    result = run_export_check(exported_source)
    assert result.returncode != 0
    assert "Qualified kernel implementation changed" in result.stderr
