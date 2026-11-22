"""Native Node packaging admits explicit assets without running author code."""

import hashlib
import importlib.metadata
import io
import json
import zipfile
from pathlib import Path

import pytest
from skulk_capability_tools.build_release import main as sign_release
from skulk_capability_tools.native_publishing import NativeManifest
from skulk_capability_tools.node_application import (
    build_node_project,
    node_project_artifacts,
)
from skulk_capability_tools.project import build_native
from skulk_capability_tools.starter import initialize

from skulk_capability_sdk.contracts import Denied
from skulk_capability_sdk.node_application import NODE_LAUNCHER, NodeApplication
from tests_native.test_application import startup_value


def project(path: Path) -> Path:
    """Stage data-only fixtures; executable acceptance uses the real npm package."""
    assets = path / "stage"
    launcher = assets / NODE_LAUNCHER
    launcher.parent.mkdir(parents=True)
    launcher.write_text("throw Error('packaging must never execute this');\n")
    (assets / "app.mjs").write_text("throw Error('author code must not execute');\n")
    (launcher.parent.parent / "package.json").write_text(
        json.dumps(
            {
                "name": "@foxlight/skulk-capability-sdk",
                "version": importlib.metadata.version("skulk-capability-sdk"),
            }
        )
    )
    (path / "LICENSE").write_text("MIT synthetic author fixture\n")
    (path / "node-capability.toml").write_text(
        'format = 1\nassets = "stage"\nentry = "app.mjs"\n'
    )
    declaration = startup_value("/tmp/control", "/tmp/stream", "unary")["manifest"]
    assert isinstance(declaration, dict)
    declaration["runtime_dependencies"] = [
        "skulk-node-app-" + hashlib.sha256(b"example.bundle").hexdigest()[:24]
    ]
    manifest = NativeManifest(canonical_json=json.dumps(declaration))
    (path / "manifest.json").write_text(manifest.canonical_json)
    return path


def test_node_project_build_is_deterministic_and_binds_complete_assets(
    tmp_path: Path,
) -> None:
    """The real zipapp and data wheel bind every staged file with no build hooks."""
    source = project(tmp_path / "project")
    first = node_project_artifacts(source)
    assert first == node_project_artifacts(source)
    report, bundle, wheel = first
    assert report.manifest.executable_sha256 == hashlib.sha256(bundle).hexdigest()
    assert report.assets.sha256 == hashlib.sha256(wheel).hexdigest()
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        assert set(archive.namelist()) == {"__main__.py", "node-application.json"}
        assert (
            NodeApplication.model_validate_json(archive.read("node-application.json"))
            == report.application
        )
    with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
        metadata = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        assert b"Requires-Dist: skulk-node-runtime==22.23.5" in archive.read(metadata)
        for item in report.application.files:
            raw = archive.read(report.application.namespace + "/" + item.path)
            assert len(raw) == item.size
            assert hashlib.sha256(raw).hexdigest() == item.sha256
    output = tmp_path / "output"
    assert build_node_project(source, output) == report
    with pytest.raises(Denied, match="empty"):
        build_node_project(source, output)


@pytest.mark.parametrize(
    "fault", ["dependency", "sdk-version", "entry", "source-link", "directory-link"]
)
def test_node_project_refuses_incomplete_or_aliased_selection(
    tmp_path: Path, fault: str
) -> None:
    """Packaging cannot invent a root, mix SDK cohorts or follow staging links."""
    source = project(tmp_path / "project")
    if fault == "dependency":
        path = source / "manifest.json"
        raw = json.loads(path.read_text())
        raw["runtime_dependencies"] = []
        path.write_text(json.dumps(raw))
    elif fault == "sdk-version":
        path = source / "stage/node_modules/@foxlight/skulk-capability-sdk/package.json"
        path.write_text('{"name":"@foxlight/skulk-capability-sdk","version":"0.0.0"}')
    elif fault == "entry":
        (source / "stage/app.mjs").unlink()
    elif fault == "source-link":
        (source / "stage/app.mjs").unlink()
        (source / "stage/app.mjs").symlink_to(source / "LICENSE")
    else:
        (source / "stage/linked").symlink_to(source, target_is_directory=True)
    with pytest.raises((Denied, ValueError, OSError)):
        node_project_artifacts(source)


@pytest.mark.parametrize("name", [".env", ".env.production", ".npmrc", "publisher.key"])
def test_node_project_refuses_private_configuration(tmp_path: Path, name: str) -> None:
    """The explicit staging boundary still refuses known local secret files."""
    source = project(tmp_path / "project")
    (source / "stage" / name).write_text("synthetic private value")
    with pytest.raises(Denied, match="private configuration"):
        node_project_artifacts(source)


def test_python_native_build_uses_actual_compiled_manifest(tmp_path: Path) -> None:
    """Explicit native Python builds avoid projection into the legacy manifest."""
    source = tmp_path / "python-project"
    initialize(source, "example_source")
    declaration = startup_value("/tmp/control", "/tmp/stream", "unary")["manifest"]
    assert isinstance(declaration, dict)
    declaration["runtime_dependencies"] = ["skulk-capability-sdk"]
    manifest = NativeManifest(canonical_json=json.dumps(declaration))
    (source / "manifest.json").write_text(manifest.canonical_json)
    (source / "example_source/app.py").write_text(
        '"""Public native application source."""\n'
        "import argparse\nimport asyncio\n"
        "from skulk_capability_sdk.application import CapabilityApp\n"
        "app = CapabilityApp()\n"
        "def main() -> None:\n"
        '    """Serve the inherited native startup descriptor."""\n'
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('--config-fd', type=int, required=True)\n"
        "    asyncio.run(app.serve(parser.parse_args().config_fd))\n"
    )
    output = tmp_path / "python-build"
    report = build_native(source, output)
    actual = NativeManifest(canonical_json=(output / "manifest.json").read_text())
    assert actual.executable_sha256 == report["bundle_sha256"]
    assert (
        actual.executable_sha256
        == hashlib.sha256((output / "bundle.pyz").read_bytes()).hexdigest()
    )


@pytest.mark.parametrize("fault", ["legacy", "python-spec"])
def test_node_signer_requires_explicit_native_source_selection(
    tmp_path: Path, fault: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Node source cannot enter the legacy signer or mix Python hook inputs."""
    source = project(tmp_path / "project")
    arguments = [
        "--node-project",
        str(source),
        "--manifest",
        str(source / "manifest.json"),
        "--private-key",
        str(tmp_path / "unused.key"),
        "--publisher",
        "example",
        "--sequence",
        "1",
        "--expires-at",
        "2000000000",
        "--state-schema",
        "v1",
        "--permission",
        "Synthetic fixture",
        "--output",
        str(tmp_path / "output"),
        "--wheelhouse",
        str(tmp_path / "unused-wheels"),
    ]
    if fault == "python-spec":
        arguments += ["--inference-runtime", "--spec", str(source / "unused.toml")]
    with pytest.raises((ValueError, SystemExit)):
        sign_release(arguments)
    if fault == "python-spec":
        assert "cannot be mixed" in capsys.readouterr().err
    assert not (tmp_path / "output").exists()
