"""Selected Node metadata and bounded asset custody, without downloads or execution."""

import hashlib
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from skulk_capability_sdk.contracts import Denied
from skulk_capability_sdk.node_application import (
    NODE_LAUNCHER,
    NodeApplication,
    NodeAsset,
    asset_path,
    verify_application,
)
from skulk_capability_sdk.node_runtime import (
    NodePlatform,
    measured_file,
    node_source,
    select_node,
)


def application() -> NodeApplication:
    """Describe two small files; no fake native or Node implementation is used."""
    return NodeApplication(
        bundle_id="example.assets",
        bundle_version="1.0.0",
        entry="app.mjs",
        files=tuple(
            NodeAsset(path=name, size=3, sha256=hashlib.sha256(b"abc").hexdigest())
            for name in ("app.mjs", NODE_LAUNCHER)
        ),
    )


@pytest.mark.parametrize(
    "family", ["macos-arm64", "linux-glibc-x86_64", "linux-glibc-aarch64"]
)
def test_node_sources_pin_complete_official_input_identities(
    family: NodePlatform,
) -> None:
    """Pin upstream archive, binary and full-license identities for every platform."""
    source = node_source(family)
    assert source.version == "22.23.5" and source.platform == family
    assert source.url == (
        "https://nodejs.org/download/release/v22.23.5/" + source.archive_name
    )
    assert source.binary_size > 100 * 1024 * 1024
    assert source.license_size > 100000


@pytest.mark.parametrize(
    "value", [".", "", "../escape", "/absolute", "a//b", "a/./b", "a\\b"]
)
def test_node_asset_paths_refuse_aliases_and_escapes(value: str) -> None:
    """A signed inventory cannot escape or name one file with different aliases."""
    with pytest.raises(ValueError):
        asset_path(value)


@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_node_application_format_is_an_exact_integer(value: object) -> None:
    """Pydantic Literal aliases must not change the new record's wire meaning."""
    raw = application().model_dump(mode="json")
    raw["format"] = value
    with pytest.raises(ValidationError):
        NodeApplication.model_validate(raw)


@pytest.mark.parametrize(
    "kind", ["symlink", "fifo", "public-write", "oversized", "not-executable"]
)
def test_measured_runtime_files_refuse_unsafe_custody(
    tmp_path: Path, kind: str
) -> None:
    """Special files refuse before blocking reads or selection can execute anything."""
    path = tmp_path / "program"
    if kind == "symlink":
        target = tmp_path / "target"
        target.write_bytes(b"abc")
        path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(path, mode=0o600)
    else:
        path.write_bytes(b"abc")
        path.chmod(0o666 if kind == "public-write" else 0o600)
    with pytest.raises((Denied, OSError)):
        measured_file(path, maximum=2 if kind == "oversized" else 16, executable=True)


def test_node_selection_never_uses_an_ambient_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unrelated node in PATH cannot satisfy a missing selected runtime."""
    executable = tmp_path / "node"
    executable.write_text("#!/bin/sh\ntouch should-never-exist\n")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises((Denied, OSError)):
        select_node(tmp_path / "missing-runtime")
    assert not (tmp_path / "should-never-exist").exists()


def test_selected_python_link_measures_actual_target_without_resolving_venv(
    tmp_path: Path,
) -> None:
    """Venv selection preserves its path while applying regular-file/hash checks."""
    target, selected = tmp_path / "interpreter", tmp_path / "python"
    target.write_bytes(b"synthetic interpreter")
    target.chmod(0o755)
    selected.symlink_to(target)
    assert (
        measured_file(
            selected, maximum=1024, executable=True, follow_selected_link=True
        )
        == hashlib.sha256(target.read_bytes()).hexdigest()
    )
    assert selected.is_symlink()


@pytest.mark.parametrize(
    "damage", ["missing", "changed", "extra", "link", "directory-link"]
)
def test_selected_application_verifies_all_declared_assets_before_import(
    tmp_path: Path, damage: str
) -> None:
    """Verified entry bytes alone cannot conceal a changed dependency or tree escape."""
    spec = application()
    root = tmp_path / "lib/python3.13/site-packages" / spec.namespace
    for item in spec.files:
        path = root / item.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"abc")
    assert verify_application(spec, tmp_path) == root
    target = root / NODE_LAUNCHER
    if damage == "missing":
        target.unlink()
    elif damage == "changed":
        target.write_bytes(b"bad")
    elif damage == "extra":
        (root / "unexpected.mjs").write_bytes(b"abc")
    elif damage == "link":
        target.unlink()
        target.symlink_to(root / "app.mjs")
    else:
        target.unlink()
        target.parent.rmdir()
        target.parent.symlink_to(root)
    with pytest.raises((Denied, OSError)):
        verify_application(spec, tmp_path)
