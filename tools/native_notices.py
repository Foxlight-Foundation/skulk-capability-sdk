"""Verify pinned native dependency notices without a network or owner checkout."""

import argparse
import hashlib
import json
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[1]
NOTICE = ROOT / "kernel/bindings/python/THIRD_PARTY_NOTICES.txt"


class Frozen(BaseModel):
    """Strict immutable maintainer input, never runtime authorization."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class NoticeText(Frozen):
    """Complete upstream notice text with public source provenance."""

    text: str
    origins: tuple[str, ...] = Field(min_length=1)


class Dependency(Frozen):
    """One resolved normal/build dependency in the supported-target union."""

    name: str
    version: str
    license: str
    notices: tuple[str, ...] = Field(min_length=1)


class StandardLibrary(Frozen):
    """The official toolchain's complete library copyright notice."""

    version: Literal["1.94.1"]
    source_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    notice: str = Field(pattern=r"^[a-f0-9]{64}$")


class Notices(Frozen):
    """Pinned source inputs and complete notices shipped with both native packages."""

    format: Literal[1]
    scope: str
    targets: tuple[str, ...]
    inputs: dict[str, str]
    packages: tuple[Dependency, ...]
    rust_standard_library: StandardLibrary
    texts: dict[str, NoticeText]


def render(root: Path = ROOT) -> bytes:
    """Refuse stale build inputs or notice bytes before rendering distribution text."""
    notices = Notices.model_validate_json(
        (root / "kernel/dependency-notices.json").read_bytes()
    )
    expected_inputs = {
        "kernel/Cargo.lock",
        "kernel/Cargo.toml",
        "kernel/bindings/python/Cargo.toml",
        "kernel/bindings/node/Cargo.toml",
    }
    if set(notices.inputs) != expected_inputs:
        raise ValueError("Native notice input inventory differs")
    for name, digest in notices.inputs.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Native notices require reviewed dependency refresh")
    locked = tomllib.loads((root / "kernel/Cargo.lock").read_text())
    available = {(p["name"], p["version"]) for p in locked["package"]}
    seen: set[tuple[str, str]] = set()
    used = {notices.rust_standard_library.notice}
    for package in notices.packages:
        identity = (package.name, package.version)
        if identity not in available or identity in seen:
            raise ValueError("Native notice package inventory differs")
        seen.add(identity)
        used.update(package.notices)
    if used != set(notices.texts):
        raise ValueError("Missing or unexpected native notice text")
    for digest, block in notices.texts.items():
        if hashlib.sha256(block.text.encode()).hexdigest() != digest:
            raise ValueError("Native notice text identity differs")
    parts = [
        "Skulk capability kernel: bundled third-party notices",
        "",
        notices.scope,
        "",
        "Skulk-authored code is MIT. Bundled components retain their own terms.",
        "The following inventory is a conservative union for the declared targets:",
        *notices.targets,
        "",
    ]
    for package in notices.packages:
        parts.extend(
            [
                f"{package.name} {package.version}: {package.license}",
                "Notice blocks: " + ", ".join(package.notices),
            ]
        )
    parts.extend(
        [
            "",
            f"Rust standard library {notices.rust_standard_library.version}",
            "Source: " + notices.rust_standard_library.source_commit,
            "Notice block: " + notices.rust_standard_library.notice,
        ]
    )
    for digest, block in sorted(notices.texts.items()):
        parts.extend(
            ["", "=" * 72, "Notice block " + digest, *block.origins, "", block.text]
        )
    # Keep the upstream source text hash intact in the receipt; only normalize
    # trailing layout whitespace in the readable, generated distribution copy.
    return (
        "\n".join(line.rstrip() for line in "\n".join(parts).splitlines()).rstrip()
        + "\n"
    ).encode()


def main() -> None:
    """Generate intentionally or verify exact checked-in distribution notices."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    content = render()
    if args.write:
        NOTICE.write_bytes(content)
    elif NOTICE.read_bytes() != content:
        raise ValueError("Packaged native notices differ from reviewed inventory")
    print(json.dumps({"notice_sha256": hashlib.sha256(content).hexdigest()}))


if __name__ == "__main__":
    main()
