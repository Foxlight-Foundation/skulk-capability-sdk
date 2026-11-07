"""Qualified build fingerprints bind content independently of path and version."""

from importlib.machinery import ModuleSpec
from pathlib import Path

import pytest
from skulk_capability_tools.release_runtime import (
    skulk_build_digest,
    skulk_source_digest,
)

from skulk_capability_sdk.contracts import Denied


@pytest.mark.parametrize(
    "changed", ["source", "native", "cache", "finder", "symlink", "directory-symlink"]
)
def test_build_fingerprint_checks_executable_sources_and_native_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    """A same-version build cannot substitute source or native implementation bytes."""
    roots: dict[str, Path] = {}
    for name in ("skulk", "skulk_pyo3_bindings"):
        root = tmp_path / name
        root.mkdir()
        (root / "__init__.py").write_text("__version__ = '2.0.1'\n")
        roots[name] = root
    native = roots["skulk_pyo3_bindings"] / "binding.so"
    native.write_bytes(b"qualified native bytes")

    def find(name: str) -> ModuleSpec:
        return ModuleSpec(name, loader=None, origin=str(roots[name] / "__init__.py"))

    monkeypatch.setattr("importlib.util.find_spec", find)
    original = skulk_build_digest()
    if changed == "source":
        (roots["skulk"] / "main.py").write_text("modified_implementation = True\n")
    elif changed == "native":
        native.write_bytes(b"different native bytes")
    elif changed == "cache":
        cache = roots["skulk"] / "__pycache__"
        cache.mkdir()
        (cache / "main.pyc").write_bytes(b"machine-local bytecode")
    elif changed == "finder":
        # A checkout browsed in Finder is the same build, as Skulk measures it.
        (roots["skulk"] / ".DS_Store").write_bytes(b"finder")
        (roots["skulk"] / "._main.py").write_bytes(b"appledouble")
    elif changed == "symlink":
        (roots["skulk"] / "alias.py").symlink_to(native)
    else:
        (roots["skulk"] / "alias").symlink_to(roots["skulk_pyo3_bindings"])
    if changed in ("symlink", "directory-symlink"):
        with pytest.raises(Denied):
            skulk_build_digest()
    else:
        assert (skulk_build_digest() == original) is (changed in ("cache", "finder"))


@pytest.mark.parametrize("changed", ["source", "cache", "finder", "symlink"])
def test_source_digest_measures_the_python_package_by_the_same_rules(
    tmp_path: Path, changed: str
) -> None:
    """A source tree's digest moves with its sources and ignores what a build does."""
    package = tmp_path / "src" / "skulk"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("__version__ = '2.0.1'\n")
    (package / "main.py").write_text("qualified = True\n")
    original = skulk_source_digest(tmp_path / "src")
    if changed == "source":
        (package / "main.py").write_text("qualified = False\n")
    elif changed == "cache":
        (package / "__pycache__").mkdir()
        (package / "__pycache__" / "main.pyc").write_bytes(b"bytecode")
    elif changed == "finder":
        (package / ".DS_Store").write_bytes(b"finder")
    else:
        (package / "alias.py").symlink_to(package / "main.py")
        with pytest.raises(Denied):
            skulk_source_digest(tmp_path / "src")
        return
    assert (skulk_source_digest(tmp_path / "src") == original) is (changed != "source")


def test_source_digest_refuses_a_directory_without_skulk(tmp_path: Path) -> None:
    """Neither an empty directory nor a symlinked package stands in for Skulk."""
    with pytest.raises(Denied, match="does not hold the Skulk package"):
        skulk_source_digest(tmp_path)
    real = tmp_path / "real" / "skulk"
    real.mkdir(parents=True)
    (real / "__init__.py").write_text("")
    (tmp_path / "linked").mkdir()
    (tmp_path / "linked" / "skulk").symlink_to(real)
    with pytest.raises(Denied, match="does not hold the Skulk package"):
        skulk_source_digest(tmp_path / "linked")
