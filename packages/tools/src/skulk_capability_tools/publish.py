"""Publish only verified release bytes into a new HTTPS server document directory."""

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

from skulk_capability_sdk.contracts import Denied

from .catalog import signed_record
from .inspection import inspect_release, read_public
from .releases import RELEASE_METADATA_MAX_BYTES
from .runtime_release import SignedRuntimeRelease


def publish(
    source: Path, trust: Path, destination: Path
) -> dict[str, bool | int | str]:
    """Snapshot, verify and publish an allowlisted release; never copy key files."""
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise Denied("Published destination must be new; existing bytes are immutable")
    raw = read_public(source / "release.json", RELEASE_METADATA_MAX_BYTES)
    record = signed_record(raw)
    release = (
        record.runtime.release
        if isinstance(record, SignedRuntimeRelease)
        else record.release
    )
    names = ["bundle.pyz", "release.json"]
    sizes = [release.artifact_size, len(raw)]
    if isinstance(record, SignedRuntimeRelease):
        names.extend(wheel.filename for wheel in record.runtime.wheels)
        sizes.extend(wheel.size for wheel in record.runtime.wheels)
    if len(set(names)) != len(names) or sum(sizes) > 256 * 1024 * 1024:
        raise Denied("Publication exceeds its bounded artifact inventory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=".skulk-cap-publish-", dir=destination.parent)
    )
    try:
        for name, size in zip(names, sizes, strict=True):
            content = (
                raw if name == "release.json" else read_public(source / name, size)
            )
            if len(content) != size:
                raise Denied("Publication differs from the signed artifact size")
            path = staging / name
            path.write_bytes(content)
            path.chmod(0o600)
        inspect_release(staging / "release.json", trust, artifacts=staging)
        # Exclusive reservation never replaces an existing directory, including
        # one created concurrently. Only positively verified public files leave
        # the private staging directory.
        destination.mkdir(mode=0o700)
        for name in names:
            os.rename(staging / name, destination / name)
            (destination / name).chmod(0o644)
        destination.chmod(0o755)
    finally:
        shutil.rmtree(staging)
    return {
        "scope": "web-root-publication",
        "files": len(names),
        "network_verified": False,
    }


def main(arguments: list[str]) -> None:
    """Publish into an explicitly chosen web root; TLS serving remains the owner's."""
    parser = argparse.ArgumentParser(prog="skulk-cap publish")
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--trust", type=Path, required=True)
    parser.add_argument("--web-root", type=Path, required=True)
    options = parser.parse_args(arguments)
    print(
        json.dumps(
            publish(options.release, options.trust, options.web_root), sort_keys=True
        )
    )
