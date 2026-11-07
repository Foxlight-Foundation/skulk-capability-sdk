"""Acquire a reviewed, immutable runtime recipe without private Git access."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from skulk_capability_sdk.contracts import Denied
from skulk_capability_sdk.storage import atomic_private, secure_directory

from .inspection import read_public
from .wheelhouse import LockedFile, fetch_locked


class RuntimeRecipe(BaseModel):
    """Exact binary inputs; this grants no trust to an installed capability."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    format: Literal[1] = 1
    owner_version: str
    sdk_version: str
    platform: Literal["macos-arm64", "linux-glibc-x86_64", "linux-glibc-aarch64"]
    source_owner_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    source_runtime_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    wheels: tuple[LockedFile, ...] = Field(min_length=1, max_length=64)


def acquire(recipe: RuntimeRecipe, output: Path) -> dict[str, str | int]:
    """Verify every anonymous download before creating any output artifact."""
    if len({wheel.filename for wheel in recipe.wheels}) != len(recipe.wheels):
        raise Denied("Runtime recipe contains duplicate filenames")
    if any(
        wheel.size is None or wheel.size > 64 * 1024 * 1024 for wheel in recipe.wheels
    ):
        raise Denied("Runtime wheel lacks a bounded exact size")
    if sum(wheel.size or 0 for wheel in recipe.wheels) > 256 * 1024 * 1024:
        raise Denied("Runtime recipe exceeds its acquisition budget")
    content: dict[str, bytes] = {}
    with httpx.Client(trust_env=False, timeout=30) as client:
        fetch = fetch_locked(client)
        for wheel in recipe.wheels:
            raw = fetch(wheel, 64 * 1024 * 1024)
            if (
                len(raw) != wheel.size
                or f"sha256:{hashlib.sha256(raw).hexdigest()}" != wheel.hash
            ):
                raise Denied("Runtime wheel differs from its reviewed size or hash")
            content[wheel.filename] = raw
    secure_directory(output)
    if any(output.iterdir()):
        raise Denied("Runtime output must be empty; existing bytes are immutable")
    for filename, raw in content.items():
        atomic_private(output / filename, raw)
    return {
        "platform": recipe.platform,
        "wheels": len(content),
        "owner_version": recipe.owner_version,
    }


def main(arguments: list[str]) -> None:
    """Download compiler-free runtime inputs separately from the SDK tools."""
    parser = argparse.ArgumentParser(prog="skulk-cap runtime")
    parser.add_argument(
        "--platform",
        required=True,
        choices=("macos-arm64", "linux-glibc-x86_64", "linux-glibc-aarch64"),
    )
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args(arguments)
    path = Path(__file__).with_name("runtime_recipes") / f"{options.platform}.json"
    recipe = RuntimeRecipe.model_validate_json(read_public(path, 131072))
    print(json.dumps(acquire(recipe, options.output), sort_keys=True))
