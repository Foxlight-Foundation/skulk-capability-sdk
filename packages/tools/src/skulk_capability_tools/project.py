"""Build deterministic unsigned source separately from publisher signing."""

import argparse
import hashlib
import json
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from skulk_capability_sdk.contracts import MAX_MANIFEST_BYTES, Denied, Manifest
from skulk_capability_sdk.storage import atomic_private, secure_directory

from .describe import validate_discovery_compatibility
from .inspection import read_public
from .packaging import archive, load_spec, validate_manifest, verify_self_contained


@contextmanager
def project_imports(specification: Path) -> Generator[None]:
    """Resolve explicitly selected project source while invoking its build checks."""
    spec, root = load_spec(specification)
    paths = [root, (root / spec.package_root).parent]
    paths.extend((root / item.package_root).parent for item in spec.source_packages)
    previous = sys.path.copy()
    sys.path[:0] = [str(path.resolve(strict=True)) for path in paths]
    try:
        yield
    finally:
        sys.path[:] = previous


def build(project: Path, output: Path) -> dict[str, str]:
    """Build and import-check source; this creates no signing or host authority."""
    project = project.resolve(strict=True)
    spec_path = project / "capability.toml"
    spec, root = load_spec(spec_path)
    manifest = Manifest.model_validate_json(
        read_public(project / "manifest.json", MAX_MANIFEST_BYTES)
    )
    with project_imports(spec_path):
        validate_manifest(spec, manifest)
        validate_discovery_compatibility(manifest)
        raw = archive(spec, root)
        secure_directory(output)
        if any(output.iterdir()):
            raise Denied("Build output must be empty; existing bytes are immutable")
        verify_self_contained(spec, raw, output / ".verify.pyz")
    digest = hashlib.sha256(raw).hexdigest()
    resolved = manifest.model_copy(
        update={
            "executable": "bundle.pyz",
            "executable_sha256": digest,
        }
    )
    atomic_private(output / "bundle.pyz", raw, executable=True)
    atomic_private(output / "manifest.json", resolved.model_dump_json().encode())
    return {"bundle_sha256": digest, "scope": "unsigned-source-build"}


def main(arguments: list[str]) -> None:
    """Build a selected source project without a private publisher key."""
    parser = argparse.ArgumentParser(prog="skulk-cap build")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args(arguments)
    print(json.dumps(build(options.project, options.output), sort_keys=True))
