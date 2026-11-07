"""Positive source/artifact audit for the public author tools, not the owner."""

import ast
import hashlib
import json
import tarfile
import tomllib
import zipfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "packages/tools"
PACKAGE = "skulk_capability_tools"
MODULES = {
    "__init__.py",
    "_schema_worker.py",
    "_manifest_worker.py",
    "_host_metadata.py",
    "assemble_feed.py",
    "build_catalog.py",
    "build_release.py",
    "catalog.py",
    "cli.py",
    "describe.py",
    "development.py",
    "host.py",
    "inspection.py",
    "packaging.py",
    "project.py",
    "publication_admission.py",
    "publish.py",
    "py.typed",
    "release_runtime.py",
    "releases.py",
    "runtime.py",
    "runtime_release.py",
    "starter.py",
    "wheelhouse.py",
}
DATA = {
    f"runtime_recipes/{name}.json"
    for name in (
        "macos-arm64",
        "linux-glibc-x86_64",
        "linux-glibc-aarch64",
    )
}
TESTS = {
    f"tests/{name}.py"
    for name in (
        "test_acquisition",
        "test_development",
        "test_host",
        "test_inspection",
        "test_project",
        "test_release_runtime",
        "test_wheelhouse",
    )
}
SOURCE_FILES = (
    {f"src/{PACKAGE}/{name}" for name in MODULES | DATA}
    | {
        "LICENSE.md",
        "README.md",
        "pyproject.toml",
        "EXTRACTION.json",
    }
    | TESTS
)


class Extraction(BaseModel):
    """One reviewed generic module's original and current source identities."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    extracted_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    adjustment: str


class Provenance(BaseModel):
    """Generic extraction receipt; private runtime history is never exported."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    source_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    modules: dict[str, Extraction]
    metadata_projections: str
    tests: dict[str, Extraction]
    format: Literal[1] = 1


def validate_source() -> None:
    """Check generic provenance and forbid Core/private owner imports."""
    manifest = Provenance.model_validate_json((TOOLS / "EXTRACTION.json").read_bytes())
    for mapping, prefix in (
        (manifest.modules, f"src/{PACKAGE}"),
        (manifest.tests, "tests"),
    ):
        for name, record in mapping.items():
            path = f"{prefix}/{name}"
            if path not in SOURCE_FILES:
                raise ValueError("Undeclared extraction source member")
            if (
                hashlib.sha256((TOOLS / path).read_bytes()).hexdigest()
                != record.extracted_sha256
            ):
                raise ValueError(f"Extraction source hash differs: {name}")
    for name in MODULES:
        if not name.endswith(".py"):
            continue
        tree = ast.parse((TOOLS / f"src/{PACKAGE}/{name}").read_bytes())
        for node in ast.walk(tree):
            imports: list[str] = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imports = [node.module or ""]
            if any(
                value.split(".")[0] in {"skulk", "skulk_capability_nodes"}
                for value in imports
            ):
                raise ValueError(
                    "Author tools import Core or private owner implementation"
                )
    if (TOOLS / "LICENSE.md").read_bytes() != (ROOT / "LICENSE.md").read_bytes():
        raise ValueError("Tools must carry the accepted MIT license")


def validate_artifacts() -> dict[str, str]:
    """Require exact portable archives, current source bytes and positive members."""
    project = tomllib.loads((TOOLS / "pyproject.toml").read_text())
    prefix = PACKAGE + "-" + str(project["project"]["version"])
    metadata = prefix + ".dist-info"
    files = {f"{PACKAGE}/{name}" for name in MODULES | DATA}
    wheel = ROOT / "dist" / f"{prefix}-py3-none-any.whl"
    with zipfile.ZipFile(wheel) as archive:
        allowed = files | {
            f"{metadata}/{name}"
            for name in (
                "METADATA",
                "WHEEL",
                "RECORD",
                "entry_points.txt",
                "licenses/LICENSE.md",
            )
        }
        if (
            len(archive.namelist()) != len(allowed)
            or set(archive.namelist()) != allowed
        ):
            raise ValueError("Unexpected public-tools wheel members")
        if b"Tag: py3-none-any" not in archive.read(f"{metadata}/WHEEL"):
            raise ValueError("Author tools must install without a compiler")
        for name in files:
            if archive.read(name) != (TOOLS / "src" / name).read_bytes():
                raise ValueError("Tools wheel differs from audited source")
        if (
            archive.read(f"{metadata}/licenses/LICENSE.md")
            != (TOOLS / "LICENSE.md").read_bytes()
        ):
            raise ValueError("Tools wheel license differs")
    source_archive = ROOT / "dist" / f"{prefix}.tar.gz"
    # Tests are repository-only here; the SDK workspace source archive carries
    # the full member so its uv workspace remains self-contained.
    allowed = (SOURCE_FILES - TESTS) | {".gitignore", "PKG-INFO"}
    with tarfile.open(source_archive, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) != len(allowed) or {m.name for m in members} != {
            f"{prefix}/{name}" for name in allowed
        }:
            raise ValueError("Unexpected public-tools source archive members")
        for member in members:
            if not member.isfile():
                raise ValueError("Tools source archive contains a non-file")
            relative = member.name.removeprefix(prefix + "/")
            if relative == "PKG-INFO":
                continue
            stream = archive.extractfile(member)
            expected = (
                ROOT / ".gitignore" if relative == ".gitignore" else TOOLS / relative
            )
            if stream is None or stream.read() != expected.read_bytes():
                raise ValueError("Tools source archive differs from audited source")
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (wheel, source_archive)
    }


def main() -> None:
    """Verify the public tools export before any publication."""
    validate_source()
    print(
        json.dumps({"passed": True, "artifacts": validate_artifacts()}, sort_keys=True)
    )


if __name__ == "__main__":
    main()
