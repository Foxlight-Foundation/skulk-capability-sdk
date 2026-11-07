"""Unsigned source artifacts and runtime recipes stay separate from trust grants."""

import hashlib
import zipfile
from pathlib import Path

import pytest
from packaging.utils import parse_wheel_filename
from skulk_capability_tools.project import build
from skulk_capability_tools.runtime import RuntimeRecipe
from skulk_capability_tools.starter import initialize
from skulk_capability_tools.wheelhouse import (
    LockedPackage,
    platform_policy,
    select_wheel,
    supported_tags,
)

from skulk_capability_sdk.contracts import Denied, Manifest


def test_starter_archive_is_deterministic_excludes_secrets_and_imports_without_source(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    initialize(project, "built_capability")
    (project / "publisher.key").write_bytes(b"synthetic secret never packed")
    first, second = tmp_path / "first", tmp_path / "second"
    assert build(project, first) == build(project, second)
    raw = (first / "bundle.pyz").read_bytes()
    assert raw == (second / "bundle.pyz").read_bytes()
    assert b"synthetic secret never packed" not in raw
    with zipfile.ZipFile(first / "bundle.pyz") as archive:
        assert set(archive.namelist()) == {
            "__main__.py",
            "built_capability/__init__.py",
            "built_capability/app.py",
        }
    manifest = Manifest.model_validate_json((first / "manifest.json").read_bytes())
    assert manifest.executable_sha256 == hashlib.sha256(raw).hexdigest()
    with pytest.raises(Denied, match="empty"):
        build(project, first)


@pytest.mark.parametrize("module", ["skulk", "skulk_capability_nodes"])
def test_public_build_refuses_private_owner_or_core_imports(
    tmp_path: Path,
    module: str,
) -> None:
    project = tmp_path / "project"
    package = "boundary_" + module
    initialize(project, package)
    source = project / package / "app.py"
    source.write_text(f"import {module}\n" + source.read_text())
    with pytest.raises(Denied, match="public capability SDK"):
        build(project, tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    "family", ["macos-arm64", "linux-glibc-x86_64", "linux-glibc-aarch64"]
)
def test_published_runtime_recipe_has_only_exact_platform_floor_wheels(
    family: str,
) -> None:
    from skulk_capability_tools import runtime

    path = Path(runtime.__file__).with_name("runtime_recipes") / f"{family}.json"
    recipe = RuntimeRecipe.model_validate_json(path.read_bytes())
    assert recipe.platform == family
    assert recipe.owner_version == "0.10.5" and recipe.sdk_version == "0.5.5"
    for wheel in recipe.wheels:
        name, version, _, _ = parse_wheel_filename(wheel.filename)
        selected = select_wheel(
            LockedPackage(
                name=str(name),
                version=str(version),
                source={"url": str(wheel.url)},
                wheels=(wheel,),
            ),
            supported_tags(platform_policy(family)),
        )
        assert selected == wheel
        assert wheel.hash.startswith("sha256:") and wheel.size is not None
        assert str(wheel.url).startswith("https://skulkapps.foxlight.ai/releases/")
