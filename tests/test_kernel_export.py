"""Public source custody refuses undeclared code and unqualified changes."""

import hashlib
import json
from pathlib import Path

import pytest

from tools import check_kernel_export


@pytest.fixture
def exported_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Copy only the reviewed source set, excluding build caches and runtimes."""
    source = check_kernel_export.ROOT
    names = (
        check_kernel_export.SOURCE_FILES
        | check_kernel_export.GENERATED_FILES
        | {"LICENSE.md", "KERNEL-SOURCE-MANIFEST.json"}
    )
    for name in names:
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((source / name).read_bytes())
    monkeypatch.setattr(check_kernel_export, "ROOT", tmp_path)
    return tmp_path


def test_source_archive_needs_no_owner_or_git_history(exported_source: Path) -> None:
    """The complete positive export verifies without a private checkout."""
    assert not (exported_source / ".git").exists()
    assert check_kernel_export.validate_source()["source_files"] == 95


def test_undeclared_kernel_source_refuses(exported_source: Path) -> None:
    """An accidental extra implementation cannot enter the source export."""
    (exported_source / "kernel/unexpected.py").write_text("# inert fixture\n")
    with pytest.raises(ValueError, match="Undeclared kernel source"):
        check_kernel_export.validate_source()


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
    with pytest.raises(ValueError, match="Qualified kernel implementation changed"):
        check_kernel_export.validate_source()
