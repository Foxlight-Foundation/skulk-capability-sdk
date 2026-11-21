"""Audit the public TypeScript source and exact compiler-free npm archive."""

import hashlib
import json
import tarfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packages/typescript"
MODULES = {
    "application",
    "calls",
    "cohort",
    "contracts",
    "index",
    "json",
    "native",
    "resources",
}
SOURCE_FILES = {
    "package.json",
    "npm-shrinkwrap.json",
    "tsconfig.json",
    "README.md",
    "LICENSE.md",
    "tools/copy-types.mjs",
    "src/native-api.d.cts",
} | {f"src/{name}.ts" for name in MODULES}
DIST_FILES = {"dist/native-api.d.cts"} | {
    f"dist/{name}{extension}" for name in MODULES for extension in (".js", ".d.ts")
}
EXPORT_FILES = DIST_FILES | {
    "package.json",
    "npm-shrinkwrap.json",
    "README.md",
    "LICENSE.md",
    "SOURCE-MANIFEST.json",
}


class Provenance(BaseModel):
    """Positive source custody for newly authored public MIT facade code."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    format: int
    files: dict[str, str] = Field(description="Exact reviewed public source digests.")


def validate_source() -> None:
    """Refuse undeclared source, changed hashes, private imports or license drift."""
    record = Provenance.model_validate_json(
        (PACKAGE / "SOURCE-MANIFEST.json").read_bytes()
    )
    if record.format != 1 or set(record.files) != SOURCE_FILES:
        raise ValueError("TypeScript source inventory differs")
    if {path.name for path in (PACKAGE / "src").iterdir()} != {"native-api.d.cts"} | {
        f"{name}.ts" for name in MODULES
    }:
        raise ValueError("TypeScript runtime inventory differs")
    for name, digest in record.files.items():
        if hashlib.sha256((PACKAGE / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"TypeScript source digest differs: {name}")
        if name.startswith("src/"):
            raw = (PACKAGE / name).read_text()
            if (
                "skulk_capability_nodes" in raw
                or "from 'skulk'" in raw
                or 'from "skulk"' in raw
            ):
                raise ValueError("Public package imports private implementation")
    if (PACKAGE / "LICENSE.md").read_bytes() != (ROOT / "LICENSE.md").read_bytes():
        raise ValueError("TypeScript license differs")


def validate_archive(path: Path) -> dict[str, str]:
    """Inspect only positively inventoried regular members and exact built bytes."""
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) != len(EXPORT_FILES) or {member.name for member in members} != {
            f"package/{name}" for name in EXPORT_FILES
        }:
            raise ValueError("TypeScript archive inventory differs")
        for member in members:
            if not member.isfile():
                raise ValueError("TypeScript archive contains a non-file member")
            raw = archive.extractfile(member)
            if (
                raw is None
                or raw.read()
                != (PACKAGE / member.name.removeprefix("package/")).read_bytes()
            ):
                raise ValueError(
                    "TypeScript archive differs from reviewed source/build"
                )
    return {
        "archive": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def main() -> None:
    """Check the explicit public package without accounts or native compilers."""
    import sys

    validate_source()
    archives = [Path(value) for value in sys.argv[1:]]
    if not archives:
        raise ValueError("Supply the exact npm package archive to audit")
    print(json.dumps([validate_archive(path) for path in archives], sort_keys=True))


if __name__ == "__main__":
    main()
