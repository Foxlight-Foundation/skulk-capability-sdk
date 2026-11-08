"""Audit the explicit MIT shared-kernel source export without importing an owner."""

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = frozenset(
    [
        "kernel/Cargo.lock",
        "kernel/Cargo.toml",
        "kernel/LICENSE.md",
        "kernel/bindings/node/Cargo.toml",
        "kernel/bindings/node/build.rs",
        "kernel/bindings/node/codec.ts",
        "kernel/bindings/node/loader.cjs",
        "kernel/bindings/node/loader.d.cts",
        "kernel/bindings/node/package-lock.json",
        "kernel/bindings/node/package.json",
        "kernel/bindings/node/portable.ts",
        "kernel/bindings/node/src/lib.rs",
        "kernel/bindings/node/tests/codec.test.mjs",
        "kernel/bindings/node/tests/constructors.test.mjs",
        "kernel/bindings/node/tests/contracts.test.mjs",
        "kernel/bindings/node/tests/journal.test.mjs",
        "kernel/bindings/node/tests/operations.test.mjs",
        "kernel/bindings/node/tests/portable-consumer.ts",
        "kernel/bindings/node/tests/portable.test.mjs",
        "kernel/bindings/node/tests/schema.test.mjs",
        "kernel/bindings/node/tests/streams.test.mjs",
        "kernel/bindings/node/tools/portable_probe.mjs",
        "kernel/bindings/node/tools/schema_probe.mjs",
        "kernel/bindings/node/tsconfig.consumer.json",
        "kernel/bindings/node/tsconfig.json",
        "kernel/bindings/python/Cargo.toml",
        "kernel/bindings/python/LICENSE.md",
        "kernel/bindings/python/_skulk_capability_kernel.pyi",
        "kernel/bindings/python/pyproject.toml",
        "kernel/bindings/python/src/lib.rs",
        "kernel/bindings/python/tests/test_native.py",
        "kernel/bindings/python/tests/test_native_contracts.py",
        "kernel/bindings/python/tests/test_native_operations.py",
        "kernel/bindings/python/tests/test_native_schema.py",
        "kernel/bindings/python/tests/test_native_streams.py",
        "kernel/contracts/author-v1.json",
        "kernel/contracts/generated/author-contracts.d.ts",
        "kernel/contracts/generated/author_contracts.pyi",
        "kernel/contracts/generated/schemas/ApprovedEffect.json",
        "kernel/contracts/generated/schemas/Bounds.json",
        "kernel/contracts/generated/schemas/CredentialInput.json",
        "kernel/contracts/generated/schemas/Descriptor.json",
        "kernel/contracts/generated/schemas/Manifest.json",
        "kernel/contracts/generated/schemas/OperationBounds.json",
        "kernel/contracts/generated/schemas/OperationCall.json",
        "kernel/contracts/generated/schemas/OperationLog.json",
        "kernel/contracts/generated/schemas/OperationPage.json",
        "kernel/contracts/generated/schemas/OperationPlan.json",
        "kernel/contracts/generated/schemas/OperationStatus.json",
        "kernel/contracts/generated/schemas/Startup.json",
        "kernel/contracts/generated/schemas/StewardExposure.json",
        "kernel/contracts/generated/schemas/StreamPacket.json",
        "kernel/contracts/generated/schemas/Surface.json",
        "kernel/contracts/generated/schemas/SurfaceReport.json",
        "kernel/contracts/generated/schemas/WireMessage.json",
        "kernel/contracts/portable-v1-cases.json",
        "kernel/contracts/portable-v1.json",
        "kernel/examples/codec_probe.rs",
        "kernel/examples/contract_probe.rs",
        "kernel/examples/portable_probe.rs",
        "kernel/examples/schema_probe.rs",
        "kernel/src/binding.rs",
        "kernel/src/codec.rs",
        "kernel/src/contracts.rs",
        "kernel/src/journal.rs",
        "kernel/src/lib.rs",
        "kernel/src/operation_logs.rs",
        "kernel/src/operations.rs",
        "kernel/src/portable.rs",
        "kernel/src/portable_json.rs",
        "kernel/src/portable_schema.rs",
        "kernel/src/schema.rs",
        "kernel/src/schema_alias.rs",
        "kernel/src/schema_graph.rs",
        "kernel/src/schema_legacy.rs",
        "kernel/src/schema_pattern.rs",
        "kernel/src/schema_reference.rs",
        "kernel/src/schema_scoped.rs",
        "kernel/src/schema_unicode.rs",
        "kernel/src/streams.rs",
        "kernel/tests/binding.rs",
        "kernel/tests/codec.rs",
        "kernel/tests/contracts.rs",
        "kernel/tests/journal.rs",
        "kernel/tests/operations.rs",
        "kernel/tests/portable.rs",
        "kernel/tests/schema.rs",
        "kernel/tests/streams.rs",
        "tests/fixtures/author-baseline/sdk-0.4.0-journal.sql",
        "tests/fixtures/author-baseline/sdk-0.4.0.json",
        "tests/fixtures/author-baseline/sdk-0.5.0-streams.json",
        "tests/fixtures/author-baseline/sdk-0.5.0.json",
        "tests/fixtures/author-baseline/sdk-0.5.5.json",
        "tools/check_portable_profile.py",
        "tools/portable_profile_cases.py",
    ]
)
GENERATED_FILES = frozenset({"kernel/README.md"})
BUILD_DIRECTORIES = frozenset(
    {
        "kernel/target",
        "kernel/bindings/node/node_modules",
        "kernel/bindings/node/dist",
    }
)
REPLACEMENTS = {
    "kernel/Cargo.toml": [
        (
            "Private shared implementation of Skulk capability contracts and lifecycle",
            "Shared implementation of Skulk capability contracts and lifecycle",
        )
    ],
    "kernel/bindings/python/pyproject.toml": [
        (
            "Private native capability kernel qualification package",
            "Native capability kernel qualification package",
        ),
        ("Private dependency evidence is", "Dependency evidence is"),
    ],
    "kernel/bindings/python/tests/test_native.py": [
        (
            "from pydantic import JsonValue\nfrom skulk_capability_sdk",
            "from pydantic import JsonValue\n\nfrom skulk_capability_sdk",
        )
    ],
    "kernel/bindings/python/tests/test_native_operations.py": [
        (
            "import pytest\nfrom skulk_capability_sdk",
            "import pytest\n\nfrom skulk_capability_sdk",
        )
    ],
    "kernel/bindings/python/tests/test_native_streams.py": [
        (
            "import pytest\nfrom skulk_capability_sdk",
            "import pytest\n\nfrom skulk_capability_sdk",
        )
    ],
}


class ExportFile(BaseModel):
    """One original source identity and its reviewed exported counterpart."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    exported_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    adjustment: str | None = None


class KernelExport(BaseModel):
    """Positive source custody; neither installation authority nor adoption."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    format: Literal[1]
    source_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    scope: str
    profile: Literal["skulk.portable/1"]
    profile_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    files: dict[str, ExportFile]
    generated_files: dict[str, str]


def _kernel_files() -> set[str]:
    """Find source files in a checkout or an anonymous source archive."""
    if (ROOT / ".git").exists():
        command_result = subprocess.run(
            [
                "git",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                "kernel",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return set(command_result.stdout.splitlines())
    source_files: set[str] = set()
    for directory, children, files in os.walk(ROOT / "kernel", followlinks=False):
        parent = Path(directory)
        children[:] = [
            name
            for name in children
            if (parent / name).relative_to(ROOT).as_posix() not in BUILD_DIRECTORIES
            and name != "__pycache__"
        ]
        source_files.update(
            (parent / name).relative_to(ROOT).as_posix() for name in files
        )
    return source_files


def validate_source() -> dict[str, str | int]:
    """Refuse undeclared kernel files, changed code or non-MIT license copies."""
    manifest = KernelExport.model_validate_json(
        (ROOT / "KERNEL-SOURCE-MANIFEST.json").read_bytes()
    )
    if (
        frozenset(manifest.files) != SOURCE_FILES
        or frozenset(manifest.generated_files) != GENERATED_FILES
    ):
        raise ValueError("Kernel source export differs from the reviewed positive list")
    expected_kernel = {
        name for name in SOURCE_FILES if name.startswith("kernel/")
    } | GENERATED_FILES
    if _kernel_files() != expected_kernel:
        raise ValueError("Undeclared kernel source files")
    for name, record in manifest.files.items():
        path = ROOT / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("Kernel source must be a regular file")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != record.exported_sha256:
            raise ValueError("Kernel source identity differs")
        if name in REPLACEMENTS:
            reconstructed = raw.decode()
            for original, exported in REPLACEMENTS[name]:
                if reconstructed.count(exported) != 1:
                    raise ValueError("Unexpected kernel export adjustment")
                reconstructed = reconstructed.replace(exported, original)
            if (
                hashlib.sha256(reconstructed.encode()).hexdigest()
                != record.source_sha256
            ):
                raise ValueError("Kernel adjustment changes qualified source")
        elif (
            record.source_sha256 != record.exported_sha256
            or record.adjustment is not None
        ):
            raise ValueError("Qualified kernel implementation changed")
    for name, digest in manifest.generated_files.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Generated kernel reference differs")
    license_bytes = (ROOT / "LICENSE.md").read_bytes()
    for name in ["kernel/LICENSE.md", "kernel/bindings/python/LICENSE.md"]:
        if (ROOT / name).read_bytes() != license_bytes:
            raise ValueError("Kernel MIT license differs")
    spec = json.loads((ROOT / "kernel/contracts/portable-v1.json").read_text())
    canonical = json.dumps(
        spec, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode()
    fingerprint = hashlib.sha256(
        b"skulk.portable.profile" + bytes([0]) + canonical
    ).hexdigest()
    if fingerprint != manifest.profile_digest:
        raise ValueError("Kernel profile differs from the qualified source")
    return {
        "source_files": len(manifest.files),
        "profile": manifest.profile,
        "profile_digest": fingerprint,
    }


if __name__ == "__main__":
    print(json.dumps(validate_source(), sort_keys=True))
