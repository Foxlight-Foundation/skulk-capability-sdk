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
import sysconfig
from pathlib import Path
from typing import cast

from tools.check_kernel_export import validate_source
from tools.native_candidates import create_bundle
from tools.native_consumer import verify_install
from tools.native_notices import NOTICE, render
from tools.native_packages import inspect_npm, inspect_wheel

ROOT = Path(__file__).resolve().parents[1]
NODE = ROOT / "kernel/bindings/node"
OUTPUT = ROOT / ".runtime/native-qualification"


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


def main() -> None:
    """Build native artifacts, enforce their contents and compare every outcome."""
    source = validate_source()
    if NOTICE.read_bytes() != render():
        raise ValueError("Native dependency notices differ")
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
    if sys.platform == "linux":
        # Cargo's standalone PyO3 test executable embeds Python, unlike an
        # extension loaded by Python. Find this interpreter's shared library
        # without changing the host environment or depending on a system Python.
        python_library = sysconfig.get_config_var("LIBDIR")
        if not isinstance(python_library, str) or not python_library:
            raise ValueError("Selected Python has no shared-library directory")
        env["LD_LIBRARY_PATH"] = os.pathsep.join(
            [python_library, *filter(None, [env.get("LD_LIBRARY_PATH")])]
        )
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
                "cargo",
                "build",
                "--manifest-path",
                "kernel/Cargo.toml",
                "--locked",
                "--example",
                "transport_probe",
            ],
            env,
        ),
        end="",
    )
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
    shutil.copyfile(NOTICE, NODE / "dist/THIRD_PARTY_NOTICES.txt")
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
    for backend in ["rust", "python", "node"]:
        print(
            run(
                [
                    sys.executable,
                    "-m",
                    "tools.check_managed_profile",
                    "--backend",
                    backend,
                    "--report",
                    str(OUTPUT / f"managed-{backend}.json"),
                ],
                python_env,
            ),
            end="",
        )
    managed_reports = [
        json.loads((OUTPUT / f"managed-{backend}.json").read_text())
        for backend in ["rust", "python", "node"]
    ]
    for current in managed_reports[1:]:
        for field in [
            "profile",
            "profile_digest",
            "cases",
            "input_sha256",
            "observations",
        ]:
            if current[field] != managed_reports[0][field]:
                raise ValueError("Complete managed observations differ")
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
    for backend in ["rust", "python", "node"]:
        print(
            run(
                [
                    sys.executable,
                    "-m",
                    "tools.check_handshake_profile",
                    "--backend",
                    backend,
                    "--report",
                    str(OUTPUT / f"handshake-{backend}.json"),
                ],
                python_env,
            ),
            end="",
        )
    for backend in ["rust", "python", "node"]:
        print(
            run(
                [
                    sys.executable,
                    "-m",
                    "tools.check_transport_profile",
                    "--backend",
                    backend,
                    "--report",
                    str(OUTPUT / f"transport-{backend}.json"),
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
                "kernel/Cargo.toml",
                "--locked",
                "--example",
                "composition_probe",
            ],
            env,
        ),
        end="",
    )
    for backend in ["rust", "python", "node"]:
        print(
            run(
                [
                    sys.executable,
                    "-m",
                    "tools.check_composition_profile",
                    "--backend",
                    backend,
                    "--report",
                    str(OUTPUT / f"composition-{backend}.json"),
                ],
                python_env,
            ),
            end="",
        )
    composition_report = json.loads((OUTPUT / "composition-rust.json").read_text())
    transport_report = json.loads((OUTPUT / "transport-rust.json").read_text())
    handshake_report = json.loads((OUTPUT / "handshake-rust.json").read_text())
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
        "composition_profile": composition_report["profile"],
        "composition_profile_digest": composition_report["profile_digest"],
        "composition_cases_per_backend": composition_report["cases"],
        "composition_input_sha256": composition_report["input_sha256"],
        "transport_profile": transport_report["profile"],
        "transport_profile_digest": transport_report["profile_digest"],
        "transport_cases_per_backend": transport_report["cases"],
        "transport_input_sha256": transport_report["input_sha256"],
        "handshake_profile": handshake_report["profile"],
        "handshake_profile_digest": handshake_report["profile_digest"],
        "handshake_cases_per_backend": handshake_report["cases"],
        "handshake_input_sha256": handshake_report["input_sha256"],
        "managed_profile": managed_reports[0]["profile"],
        "managed_profile_digest": managed_reports[0]["profile_digest"],
        "managed_cases_per_backend": managed_reports[0]["cases"],
        "managed_input_sha256": managed_reports[0]["input_sha256"],
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
    verify_install(wheel, package, OUTPUT)
    print(create_bundle(OUTPUT))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
