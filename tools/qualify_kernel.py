"""Qualify the MIT kernel and prebuilt Python/Node artifacts without an owner.

This maintainer command builds local artifacts and verifies portable-contract
observations. It neither publishes packages nor changes an installed SDK.
"""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import cast

from check_kernel_export import validate_source

ROOT = Path(__file__).resolve().parents[1]
NODE = ROOT / "kernel/bindings/node"
OUTPUT = ROOT / ".runtime/native-qualification"
FACADE = (
    b"from ._skulk_capability_kernel import *\n\n"
    b"__doc__ = _skulk_capability_kernel.__doc__\n"
    b'if hasattr(_skulk_capability_kernel, "__all__"):\n'
    b"    __all__ = _skulk_capability_kernel.__all__"
)


def run(args: list[str], env: dict[str, str], cwd: Path = ROOT) -> str:
    """Bound each build/test command and stop immediately on failure."""
    result = subprocess.run(
        args, cwd=cwd, env=env, check=False, capture_output=True, text=True, timeout=600
    )
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode:
        print(result.stdout, end="")
    result.check_returncode()
    return result.stdout


def inspect_wheel(path: Path) -> None:
    """Allow only native implementation, declarations, one import shim and metadata."""
    approved = {
        "_skulk_capability_kernel/__init__.py",
        "_skulk_capability_kernel/__init__.pyi",
        "_skulk_capability_kernel/_skulk_capability_kernel.abi3.so",
        "_skulk_capability_kernel/py.typed",
        "skulk_capability_kernel-0.1.0.dist-info/METADATA",
        "skulk_capability_kernel-0.1.0.dist-info/WHEEL",
        "skulk_capability_kernel-0.1.0.dist-info/RECORD",
        "skulk_capability_kernel-0.1.0.dist-info/licenses/LICENSE.md",
    }
    with zipfile.ZipFile(path) as archive:
        if set(archive.namelist()) != approved:
            raise ValueError("unexpected native wheel contents")
        if archive.read("_skulk_capability_kernel/__init__.py") != FACADE:
            raise ValueError("unexpected readable native implementation")
        for name in archive.namelist():
            inspect_paths(archive.read(name))


def inspect_paths(data: bytes) -> None:
    """Refuse local source identity in text metadata and stripped binary strings."""
    for marker in [
        str(ROOT).encode(),
        str(Path.home()).encode(),
        b"file:///",
        b"/Users/",
        b"/home/runner/",
    ]:
        if marker in data:
            raise ValueError("build-host path leaked into native artifact")


def inspect_npm(path: Path) -> None:
    """A positive list excludes native source, test fixtures and build material."""
    approved = {
        "package/package.json",
        "package/dist/codec.js",
        "package/dist/codec.d.ts",
        "package/dist/portable.js",
        "package/dist/portable.d.ts",
        "package/dist/kernel.node",
        "package/dist/loader.cjs",
        "package/dist/loader.d.cts",
        "package/dist/LICENSE.md",
    }
    with tarfile.open(path, "r:gz") as archive:
        if {member.name for member in archive.getmembers()} != approved:
            raise ValueError("unexpected native npm contents")
        for member in archive.getmembers():
            if not member.isfile():
                raise ValueError("non-file native npm member")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("unreadable native npm member")
            inspect_paths(source.read())


def main() -> None:
    """Build native artifacts, enforce their contents and compare every outcome."""
    source = validate_source()
    if sys.platform not in {"darwin", "linux"}:
        raise ValueError("Native qualification requires macOS or Linux")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    # An ambient target selection could pair an archive with another workspace.
    env["CARGO_TARGET_DIR"] = str(ROOT / "kernel/target")
    env["RUSTFLAGS"] = (
        f"--remap-path-prefix={ROOT}=/skulk-kernel "
        f"--remap-path-prefix={Path.home()}=/build-home"
    )
    env["PYO3_PYTHON"] = sys.executable
    env["SKULK_NATIVE_TEST_PYTHON"] = sys.executable
    node_version = run(["node", "--version"], env).strip()
    if not node_version.startswith("v22."):
        raise ValueError("Native qualification requires Node 22")
    rust_version = run(["rustc", "--version"], env).strip()
    for command in [
        ["cargo", "fmt", "--manifest-path", "kernel/Cargo.toml", "--all", "--check"],
        [
            "cargo",
            "test",
            "--manifest-path",
            "kernel/Cargo.toml",
            "--workspace",
            "--locked",
        ],
        [
            "cargo",
            "clippy",
            "--manifest-path",
            "kernel/Cargo.toml",
            "--workspace",
            "--all-targets",
            "--locked",
            "--",
            "-D",
            "warnings",
        ],
        [
            "cargo",
            "build",
            "--manifest-path",
            "kernel/Cargo.toml",
            "--locked",
            "--example",
            "portable_probe",
        ],
    ]:
        print(run(command, env), end="")
    print(
        run(
            [
                sys.executable,
                "-m",
                "tools.check_portable_profile",
                "--report",
                str(OUTPUT / "portable-rust.json"),
            ],
            env,
        ),
        end="",
    )
    wheels = OUTPUT / "wheels"
    wheels.mkdir(exist_ok=True)
    print(
        run(
            [
                "uvx",
                "--from",
                "maturin==1.13.1",
                "maturin",
                "build",
                "--manifest-path",
                "kernel/bindings/python/Cargo.toml",
                "--release",
                "--locked",
                "--out",
                str(wheels),
                "--interpreter",
                sys.executable,
            ],
            env,
        ),
        end="",
    )
    candidates = list(wheels.glob("*.whl"))
    if len(candidates) != 1:
        raise ValueError("Exactly one current-platform wheel required")
    wheel = candidates[0]
    inspect_wheel(wheel)
    installed = OUTPUT / "installed"
    print(
        run(
            [
                "uv",
                "pip",
                "install",
                "--no-deps",
                "--no-index",
                "--reinstall",
                "--target",
                str(installed),
                str(wheel),
            ],
            env,
        ),
        end="",
    )
    python_env = {**env, "PYTHONPATH": str(installed)}
    print(
        run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "kernel/bindings/python/tests",
                "--basetemp",
                str(OUTPUT / "python-test-scratch"),
            ],
            python_env,
        ),
        end="",
    )
    print(
        run(
            [
                sys.executable,
                "-m",
                "tools.check_portable_profile",
                "--backend",
                "python",
                "--report",
                str(OUTPUT / "portable-python.json"),
            ],
            python_env,
        ),
        end="",
    )
    print(
        run(
            [
                "cargo",
                "build",
                "--manifest-path",
                "kernel/bindings/node/Cargo.toml",
                "--release",
                "--locked",
            ],
            env,
        ),
        end="",
    )
    native = (
        ROOT
        / "kernel/target/release"
        / (
            "libskulk_capability_node.dylib"
            if sys.platform == "darwin"
            else "libskulk_capability_node.so"
        )
    )
    print(run(["npm", "ci", "--ignore-scripts"], env, NODE), end="")
    print(run(["npm", "run", "build"], env, NODE), end="")
    shutil.copyfile(native, NODE / "dist/kernel.node")
    if sys.platform == "darwin":
        # Rust's dylib install ID otherwise exposes the absolute output path.
        # Changing the Mach-O header requires a new ad-hoc signature; release
        # signing/notarization remains a separate delivery qualification.
        print(
            run(
                [
                    "install_name_tool",
                    "-id",
                    "@rpath/kernel.node",
                    str(NODE / "dist/kernel.node"),
                ],
                env,
            ),
            end="",
        )
        print(
            run(
                ["codesign", "--force", "--sign", "-", str(NODE / "dist/kernel.node")],
                env,
            ),
            end="",
        )
    shutil.copyfile(NODE / "dist/kernel.node", NODE / "kernel.node")
    for name in ["loader.cjs", "loader.d.cts"]:
        shutil.copyfile(NODE / name, NODE / "dist" / name)
    shutil.copyfile(ROOT / "kernel/LICENSE.md", NODE / "dist/LICENSE.md")
    print(run(["npm", "test"], env, NODE), end="")
    print(
        run(
            [
                sys.executable,
                "-m",
                "tools.check_portable_profile",
                "--backend",
                "node",
                "--report",
                str(OUTPUT / "portable-node.json"),
            ],
            python_env,
        ),
        end="",
    )
    npm_output = OUTPUT / "npm"
    npm_output.mkdir(exist_ok=True)
    packed = cast(
        list[dict[str, object]],
        json.loads(
            run(
                [
                    "npm",
                    "pack",
                    "--json",
                    "--pack-destination",
                    str(npm_output),
                ],
                env,
                NODE,
            )
        ),
    )
    if len(packed) != 1 or not isinstance(packed[0].get("filename"), str):
        raise ValueError("Unexpected npm archive record")
    package = npm_output / str(packed[0]["filename"])
    inspect_npm(package)
    reports = [
        json.loads((OUTPUT / f"portable-{backend}.json").read_text())
        for backend in ["rust", "python", "node"]
    ]
    for current in reports[1:]:
        for field in [
            "profile",
            "profile_digest",
            "cases",
            "input_sha256",
            "observations",
        ]:
            if current[field] != reports[0][field]:
                raise ValueError("Complete portable observations differ")
    report = {
        "scope": (
            "Shared-kernel/native ABI qualification; not installed SDK 1.0 acceptance"
        ),
        "platform": sys.platform,
        "architecture": platform.machine(),
        "python": sys.version.split()[0],
        "node": node_version,
        "rust": rust_version,
        "source": source,
        "profile": reports[0]["profile"],
        "profile_digest": reports[0]["profile_digest"],
        "cases_per_backend": reports[0]["cases"],
        "input_sha256": reports[0]["input_sha256"],
        "wheel": {
            "name": wheel.name,
            "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        },
        "npm": {
            "name": package.name,
            "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        },
        "runtime_adoption": False,
    }
    (OUTPUT / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
