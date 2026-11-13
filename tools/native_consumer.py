"""Install prebuilt native archives offline with compilers absent from PATH."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from tools.native_packages import ROOT, asset_digest


def _run(command: list[str], env: dict[str, str], cwd: Path = ROOT) -> None:
    """Bound each isolated installer/probe and surface a real failure without replay."""
    subprocess.run(command, env=env, cwd=cwd, check=True, timeout=180)


def verify_install(wheel: Path, package: Path, output: Path) -> None:
    """Verify both archive installations and complete native observations offline.

    This proves package acquisition/ABI use without a compiler. It does not
    authorize a capability or qualify the private owner/Core installation path.
    """
    (output / "compiler-free-install.json").unlink(missing_ok=True)
    work = output / "compiler-free-consumer"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    binaries = work / "bin"
    binaries.mkdir()
    for name in ["node", "npm", "uv"]:
        executable = shutil.which(name)
        if executable is None:
            raise ValueError("Native consumer prerequisite is unavailable")
        (binaries / name).symlink_to(Path(executable).resolve())
    env = {**os.environ, "PATH": str(binaries), "UV_LINK_MODE": "copy"}
    for name in ["cargo", "rustc", "cc", "clang", "gcc", "cmake"]:
        if shutil.which(name, path=env["PATH"]) is not None:
            raise ValueError("Compiler unexpectedly available to native consumer")
    installed = work / "python"
    _run(
        [
            str(binaries / "uv"),
            "pip",
            "install",
            "--no-deps",
            "--no-index",
            "--python",
            sys.executable,
            "--target",
            str(installed),
            str(wheel),
        ],
        env,
    )
    python_env = {**env, "PYTHONPATH": str(installed)}
    _run(
        [
            sys.executable,
            "-c",
            (
                "import _skulk_capability_kernel as k; from pathlib import Path; "
                "import sys; assert Path(k.__file__).resolve().is_relative_to("
                "Path(sys.argv[1]).resolve())"
            ),
            str(installed),
        ],
        python_env,
    )
    node = work / "node"
    node.mkdir()
    (node / "package.json").write_text('{"private":true,"type":"module"}\n')
    _run(
        [
            str(binaries / "npm"),
            "install",
            "--ignore-scripts",
            "--offline",
            "--no-audit",
            "--no-fund",
            "--package-lock=false",
            str(package),
        ],
        env,
        node,
    )
    facade = (
        node
        / "node_modules"
        / "@foxlight/skulk-capability-kernel-qualification"
        / "dist/portable.js"
    )
    if not facade.is_file():
        raise ValueError("Installed Node facade is absent")
    for kind in [
        "portable",
        "managed",
        "handshake",
        "transport",
        "composition",
        "calls",
        "inference-author",
        "inference-composition",
        "inference-execution",
        "inference-transport",
        "inference-calls",
        "inference-managed",
        "inference-handshake",
    ]:
        for backend in ["python", "node"]:
            command = [
                sys.executable,
                "-m",
                "tools.check_inference_profiles"
                if kind
                in ("inference-author", "inference-composition", "inference-execution")
                else f"tools.check_{kind.removeprefix('inference-')}_profile",
                "--backend",
                backend,
                "--report",
                str(work / f"{kind}-{backend}.json"),
            ]
            if kind in (
                "inference-author",
                "inference-composition",
                "inference-execution",
            ):
                command += ["--kind", kind]
            elif kind.startswith("inference-"):
                command.append("--inference")
            if backend == "node":
                command += ["--node-facade", str(facade)]
            _run(command, python_env)
    for kind in [
        "portable",
        "managed",
        "handshake",
        "transport",
        "composition",
        "calls",
        "inference-author",
        "inference-composition",
        "inference-execution",
        "inference-transport",
        "inference-calls",
        "inference-managed",
        "inference-handshake",
    ]:
        expected = json.loads((output / f"{kind}-rust.json").read_text())
        for backend in ["python", "node"]:
            observed = json.loads((work / f"{kind}-{backend}.json").read_text())
            if {k: v for k, v in expected.items() if k != "backend"} != {
                k: v for k, v in observed.items() if k != "backend"
            }:
                raise ValueError("Installed native package observations differ")
    (output / "compiler-free-install.json").write_text(
        json.dumps(
            {
                "format": 1,
                "wheel_sha256": asset_digest(wheel),
                "npm_sha256": asset_digest(package),
                "compilers_available": False,
                "network_required": False,
                "runtime_adoption": False,
            },
            indent=2,
        )
        + "\n"
    )
