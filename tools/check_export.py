"""Check the explicit SDK export and compiler-free distribution contents."""

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
PACKAGE = "skulk_capability_sdk"
MODULES = {
    "__init__.py",
    "_credential_reader.py",
    "contracts.py",
    "credentials.py",
    "database.py",
    "operations.py",
    "py.typed",
    "serving.py",
    "storage.py",
    "streams.py",
    "wire.py",
}


class ExportFile(BaseModel):
    """Original reviewed bytes and any explicit standalone test adjustment."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    exported_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    adjustment: str | None = None


class ExportManifest(BaseModel):
    """Positive export provenance; this document grants no runtime authority."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    format: Literal[1]
    source_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    license_decision_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    files: dict[str, ExportFile]


def validate_source() -> None:
    """Refuse changed runtime bytes, undeclared modules and private host imports."""
    manifest = ExportManifest.model_validate_json(
        (ROOT / "SOURCE-MANIFEST.json").read_bytes()
    )
    actual = {p.name for p in (ROOT / PACKAGE).iterdir() if p.is_file()}
    if actual != MODULES:
        raise ValueError("SDK package members differ from the reviewed export")
    for name, record in manifest.files.items():
        path = Path(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or path.parts[0] not in {PACKAGE, "tests"}
        ):
            raise ValueError("Invalid export path")
        raw = (ROOT / path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != record.exported_sha256:
            raise ValueError(f"Export hash differs: {name}")
        if path.parts[0] == PACKAGE and record.source_sha256 != record.exported_sha256:
            raise ValueError("Runtime export must retain qualified source bytes")
    for name in MODULES:
        path = ROOT / PACKAGE / name
        if f"{PACKAGE}/{name}" not in manifest.files:
            raise ValueError("Runtime member has no source provenance")
        if path.suffix != ".py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            imports: list[str] = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imports = [node.module or ""]
            if any(
                value.split(".")[0] in {"skulk", "skulk_capability_nodes"}
                for value in imports
            ):
                raise ValueError("Public SDK imports private host implementation")


def validate_artifacts() -> dict[str, str]:
    """Inspect exact wheel/sdist allowlists and report their immutable hashes."""
    with (ROOT / "pyproject.toml").open("rb") as source:
        version = str(tomllib.load(source)["project"]["version"])
    prefix = f"{PACKAGE}-{version}"
    metadata = prefix + ".dist-info"
    package_files = {f"{PACKAGE}/{name}" for name in MODULES}
    wheel = ROOT / "dist" / f"{prefix}-py3-none-any.whl"
    with zipfile.ZipFile(wheel) as archive:
        allowed = package_files | {
            f"{metadata}/{name}"
            for name in ["METADATA", "WHEEL", "RECORD", "licenses/LICENSE.md"]
        }
        if (
            len(archive.namelist()) != len(allowed)
            or set(archive.namelist()) != allowed
        ):
            raise ValueError("Unexpected wheel members")
        if b"Tag: py3-none-any" not in archive.read(f"{metadata}/WHEEL"):
            raise ValueError("SDK wheel must need no native build")
        for name in package_files:
            if archive.read(name) != (ROOT / name).read_bytes():
                raise ValueError("Wheel differs from reviewed source")
        if (
            archive.read(f"{metadata}/licenses/LICENSE.md")
            != (ROOT / "LICENSE.md").read_bytes()
        ):
            raise ValueError("Wheel license differs")
    source_archive = ROOT / "dist" / f"{prefix}.tar.gz"
    with tarfile.open(source_archive, "r:gz") as archive:
        allowed = package_files | {
            "SOURCE-MANIFEST.json",
            ".gitignore",
            "LICENSE.md",
            "README.md",
            "pyproject.toml",
            "PKG-INFO",
        }
        members = archive.getmembers()
        if len(members) != len(allowed) or {m.name for m in members} != {
            f"{prefix}/{name}" for name in allowed
        }:
            raise ValueError("Unexpected source archive members")
        for member in members:
            if not member.isfile():
                raise ValueError("Source archive contains a non-file member")
            if member.name.endswith("/PKG-INFO"):
                continue
            stream = archive.extractfile(member)
            if (
                stream is None
                or stream.read()
                != (ROOT / member.name.removeprefix(prefix + "/")).read_bytes()
            ):
                raise ValueError("Source archive differs from reviewed files")
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (wheel, source_archive)
    }


def main() -> None:
    """Check source and built artifacts without accounts, services or publishing."""
    validate_source()
    print(
        json.dumps({"passed": True, "artifacts": validate_artifacts()}, sort_keys=True)
    )


if __name__ == "__main__":
    main()
