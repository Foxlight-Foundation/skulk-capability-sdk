"""Bounded local build/fingerprint worker; never a managed owner or installer."""

import argparse
import hashlib
import json
import os
import stat
import zipfile
from pathlib import Path

from pydantic import Field

from skulk_capability_sdk.contracts import Contract, Denied
from skulk_capability_sdk.node_application import (
    MAX_APPLICATION_BYTES,
    MAX_APPLICATION_FILES,
    MAX_ASSET_BYTES,
    NodeApplication,
    generated_asset,
    verify_application_assets,
)
from skulk_capability_sdk.node_runtime import measured_file
from skulk_capability_sdk.storage import atomic_private, secure_directory

from .inspection import read_public
from .native_author import Language
from .native_publishing import NativeManifest
from .node_application import NodeProject, build_node_project
from .packaging import load_spec
from .project import build_native


class BuiltSource(Contract):
    """Exact local generation metadata; this is not an installation record."""

    language: Language
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    manifest: NativeManifest
    application: NodeApplication | None = None


def source_language(project: Path) -> Language:
    """Require one explicit native project format without trial source execution."""
    node = (project / "node-capability.toml").is_file()
    python = (project / "capability.toml").is_file()
    if node == python:
        raise Denied("Select exactly one capability.toml or node-capability.toml")
    return "typescript" if node else "python"


def fingerprint(project: Path, language: Language) -> str:
    """Measure declared source bytes and execute status, excluding install caches."""
    selected = {project / "manifest.json"}
    roots: list[Path] = []
    if language == "typescript":
        import tomllib

        selected.add(project / "node-capability.toml")
        spec = NodeProject.model_validate(
            tomllib.loads(read_public(project / "node-capability.toml", 65536).decode())
        )
        selected.add(project / spec.license)
        roots.append(project / spec.assets)
    else:
        specification = project / "capability.toml"
        selected.add(specification)
        spec, _ = load_spec(specification)
        roots.extend(
            project / item
            for item in (
                spec.package_root,
                *(one.package_root for one in spec.source_packages),
            )
        )
        selected.update(project / item for item in spec.data_files.values())
    for root in roots:
        if root.is_symlink() or not root.is_dir():
            raise Denied("Source root must be a regular directory")
        folders = 0
        for parent, directories, names in os.walk(root, followlinks=False):
            folders += 1
            if folders > 2 * MAX_APPLICATION_FILES:
                raise Denied("Source directory inventory exceeds its bound")
            for name in directories[:]:
                child = Path(parent) / name
                if child.is_symlink():
                    raise Denied("Source directory links are not supported")
                if name == "__pycache__":
                    directories.remove(name)
            for name in names:
                if generated_asset(name) or name.endswith(".pyc"):
                    continue
                selected.add(Path(parent) / name)
                if len(selected) > MAX_APPLICATION_FILES:
                    raise Denied("Source file inventory exceeds its bound")
    digest = hashlib.sha256()
    total = 0
    for path in sorted(selected):
        relative = path.relative_to(project).as_posix()
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise Denied("Source inputs must be regular files")
        total += info.st_size
        if total > MAX_APPLICATION_BYTES:
            raise Denied("Source bytes exceed their bound")
        actual = measured_file(path, maximum=MAX_ASSET_BYTES)
        digest.update(
            json.dumps(
                [relative, actual, bool(info.st_mode & 0o111)], separators=(",", ":")
            ).encode()
        )
    return digest.hexdigest()


def build_source(project: Path, output: Path) -> BuiltSource:
    """Build an immutable generation and materialize only exact Node asset members."""
    language = source_language(project)
    # Capture before building: edits during startup remain pending for the next
    # request instead of being silently marked as already applied.
    stamp = fingerprint(project, language)
    if language == "python":
        build_native(project, output)
        result = BuiltSource(
            language=language,
            fingerprint=stamp,
            manifest=NativeManifest(
                canonical_json=(output / "manifest.json").read_text()
            ),
        )
    else:
        report = build_node_project(project, output)
        root = output / "application"
        secure_directory(root)
        with zipfile.ZipFile(output / report.assets.filename) as archive:
            for item in report.application.files:
                path = root / item.path
                path.parent.mkdir(parents=True, exist_ok=True)
                raw = archive.read(report.application.namespace + "/" + item.path)
                if (
                    len(raw) != item.size
                    or hashlib.sha256(raw).hexdigest() != item.sha256
                ):
                    raise Denied("Built application asset differs")
                path.write_bytes(raw)
                path.chmod(0o755 if item.executable else 0o644)
        verify_application_assets(report.application, root)
        result = BuiltSource(
            language=language,
            fingerprint=stamp,
            manifest=report.manifest,
            application=report.application,
        )
    atomic_private(output / "author-build.json", result.model_dump_json().encode())
    return result


def main() -> None:
    """Run an explicit account-free source operation without inherited secrets."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fingerprint-output", type=Path)
    options = parser.parse_args()
    project = options.project.resolve(strict=True)
    if options.output is None:
        if options.fingerprint_output is None:
            parser.error("Select --output or --fingerprint-output")
        atomic_private(
            options.fingerprint_output,
            json.dumps(
                {"fingerprint": fingerprint(project, source_language(project))}
            ).encode(),
        )
    else:
        build_source(project, options.output)


if __name__ == "__main__":
    main()
