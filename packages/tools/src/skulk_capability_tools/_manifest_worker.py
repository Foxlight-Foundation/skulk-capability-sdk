"""Validate one selected source manifest in a fresh, bounded author process."""

import json
import resource
import sys
from pathlib import Path

from skulk_capability_sdk.contracts import Manifest

from .packaging import load_spec, validate_manifest
from .project import project_imports


def main() -> None:
    """Refresh project modules without ambient credentials or retained payloads."""
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    raw = sys.stdin.buffer.read(524289)
    if len(raw) > 524288:
        raise SystemExit(1)
    project_text, manifest_text = json.loads(raw)
    project = Path(project_text).resolve(strict=True)
    manifest = Manifest.model_validate_json(manifest_text)
    spec, _ = load_spec(project / "capability.toml")
    with project_imports(project / "capability.toml"):
        validate_manifest(spec, manifest)


if __name__ == "__main__":
    main()
