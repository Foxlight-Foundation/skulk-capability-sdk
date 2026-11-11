"""Publication admission refuses stale, incomplete and substituted native assets."""

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from tools.native_candidates import assemble, read_bundle, validate_corpora
from tools.native_notices import render
from tools.native_packages import require_wheel_platform, versions

SOURCE = "a" * 40


def bundle(path: Path, *, clean: bool = True, source: str = SOURCE) -> Path:
    """Make synthetic metadata; no fixture here claims native runtime execution."""
    python, node = versions()
    wheel = f"skulk_capability_kernel-{python}-cp313-abi3-macosx_11_0_arm64.whl"
    package = "kernel.tgz"
    data = b"synthetic container bytes"
    asset = {
        "name": wheel,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }
    npm = {**asset, "name": package}
    candidate = {
        "format": 1,
        "platform": "macos-arm64",
        "version": python,
        "node_version": node,
        "source_commit": source,
        "source_tree": "b" * 40,
        "source_clean": clean,
        "wheel": asset,
        "npm": npm,
        "compiler_free_install_verified": True,
        "runtime_adoption": False,
    }
    report = {
        "platform": "darwin",
        "architecture": "arm64",
        "runtime_adoption": False,
        "wheel": {k: v for k, v in asset.items() if k != "size"},
        "npm": {k: v for k, v in npm.items() if k != "size"},
    }
    files = {
        "candidate.json": json.dumps(candidate).encode(),
        "report.json": json.dumps(report).encode(),
        "compiler-free-install.json": json.dumps(
            {
                "format": 1,
                "wheel_sha256": asset["sha256"],
                "npm_sha256": npm["sha256"],
                "compilers_available": False,
                "network_required": False,
                "runtime_adoption": False,
            }
        ).encode(),
        wheel: data,
        package: data,
        **{
            f"{kind}-{backend}.json": b"{}"
            for kind in ["portable", "managed", "handshake", "transport"]
            for backend in ["rust", "python", "node"]
        },
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return path


@pytest.mark.parametrize("clean,source", [(False, SOURCE), (True, "c" * 40)])
def test_unapproved_source_refuses_before_publication_effects(
    tmp_path: Path, clean: bool, source: str
) -> None:
    """A dirty or foreign source claim cannot create a release staging directory."""
    archive = bundle(tmp_path / "candidate.zip", clean=clean, source=source)
    output = tmp_path / "release"
    with pytest.raises(ValueError, match="approved clean revision"):
        assemble([archive, archive, archive], output, SOURCE)
    assert not output.exists()


def test_substituted_binary_refuses_before_archive_installation(tmp_path: Path) -> None:
    """Changing package bytes cannot inherit a previously measured report."""
    original = bundle(tmp_path / "candidate.zip")
    with zipfile.ZipFile(original) as archive:
        files = {i.filename: archive.read(i) for i in archive.infolist()}
    files["kernel.tgz"] = b"different synthetic bytes"
    altered = tmp_path / "altered.zip"
    with zipfile.ZipFile(altered, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    with pytest.raises(ValueError, match="tested bytes"):
        read_bundle(altered, SOURCE)


def test_traversal_or_duplicate_bundle_entries_refuse(tmp_path: Path) -> None:
    """An artifact cannot select a path outside its declared flat inventory."""
    archive = bundle(tmp_path / "candidate.zip")
    with zipfile.ZipFile(archive) as zipped:
        files = {i.filename: zipped.read(i) for i in zipped.infolist()}
    data = files.pop("candidate.json")
    files["../candidate.json"] = data
    altered = tmp_path / "traversal.zip"
    with zipfile.ZipFile(altered, "w") as zipped:
        for name, raw in files.items():
            zipped.writestr(name, raw)
    with pytest.raises(ValueError, match="Invalid or duplicate"):
        read_bundle(altered, SOURCE)


def test_foreign_machine_wheel_never_satisfies_platform_selection() -> None:
    """Valid package syntax does not make an ARM wheel usable on x86-64."""
    version, _ = versions()
    name = f"skulk_capability_kernel-{version}-cp313-abi3-linux_aarch64.whl"
    require_wheel_platform(name, "linux-glibc-aarch64")
    with pytest.raises(ValueError, match="ABI/platform"):
        require_wheel_platform(name, "linux-glibc-x86_64")


def test_packaged_dependency_notices_match_pinned_sources() -> None:
    """The binary distribution's notices are complete, reviewed source material."""
    root = Path(__file__).resolve().parents[1]
    assert (
        render()
        == (root / "kernel/bindings/python/THIRD_PARTY_NOTICES.txt").read_bytes()
    )


def test_consistent_but_false_observations_do_not_qualify_a_candidate() -> None:
    """Three matching self-reports cannot replace the independent source oracle."""
    root = Path(__file__).resolve().parents[1]
    fixture = json.loads((root / "kernel/contracts/portable-v1-cases.json").read_text())
    common = {
        "profile": fixture["profile"],
        "profile_digest": fixture["profile_digest"],
        "cases": 1,
        "input_sha256": "0" * 64,
        "observations": [{"case": "invented", "sha256": "0" * 64}],
        "scope": "synthetic negative witness",
        "runtime_adoption": False,
    }
    files = {
        f"portable-{backend}.json": json.dumps({**common, "backend": backend}).encode()
        for backend in ["rust", "python", "node"]
    }
    with pytest.raises(ValueError, match="independent source corpus"):
        validate_corpora(files)


def test_dependency_change_requires_notice_refresh(tmp_path: Path) -> None:
    """New locked source cannot inherit a stale distribution notice receipt."""
    root = Path(__file__).resolve().parents[1]
    for name in [
        "kernel/Cargo.toml",
        "kernel/Cargo.lock",
        "kernel/bindings/python/Cargo.toml",
        "kernel/bindings/node/Cargo.toml",
        "kernel/dependency-notices.json",
    ]:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / name).read_bytes())
    with (tmp_path / "kernel/Cargo.lock").open("a") as output:
        output.write("\n# synthetic dependency refresh\n")
    with pytest.raises(ValueError, match="dependency refresh"):
        render(tmp_path)
