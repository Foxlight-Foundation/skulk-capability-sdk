"""One public author command with compatible legacy publisher entry points."""

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
from typing import Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import TypeAdapter

from skulk_capability_sdk.contracts import Denied, Result
from skulk_capability_sdk.storage import secure_directory
from skulk_capability_sdk.wire import ProtocolError

from . import (
    assemble_feed,
    build_catalog,
    build_release,
    host,
    project,
    publish,
    runtime,
    wheelhouse,
)
from .development import call, serve
from .inspection import inspect_release
from .starter import initialize

type Command = Literal[
    "build",
    "sign",
    "wheelhouse",
    "catalog",
    "feed",
    "inspect",
    "keygen",
    "init",
    "run",
    "watch",
    "invoke",
    "debug",
    "test",
    "review",
    "install",
    "update",
    "activate",
    "rollback",
    "uninstall",
    "status",
    "register",
    "source",
    "runtime",
    "publish",
]


def development(command: str, arguments: list[str]) -> None:
    """Create or exercise an explicit source project in a private local session."""
    parser = argparse.ArgumentParser(prog=f"skulk-cap {command}")
    if command == "init":
        parser.add_argument("destination", type=Path)
        parser.add_argument("--name", default="hello_capability")
        options = parser.parse_args(arguments)
        initialize(options.destination, options.name)
        return
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--state", type=Path)
    if command in {"invoke", "test"}:
        parser.add_argument("--input", default='{"text":"Hello"}')
        parser.add_argument("--capability")
    options = parser.parse_args(arguments)
    project: Path = options.project.resolve(strict=True)
    suffix = hashlib.sha256(str(project).encode()).hexdigest()[:20]
    state: Path = options.state or Path(f"/tmp/skulk-cap-{os.getuid()}-{suffix}")
    if command in {"run", "watch"}:
        try:
            asyncio.run(serve(project, state, watch=command == "watch"))
        except KeyboardInterrupt:
            pass
    else:
        from pydantic import JsonValue

        payload = (
            TypeAdapter(dict[str, JsonValue]).validate_json(options.input, strict=True)
            if command != "debug"
            else None
        )
        response = asyncio.run(
            call(
                project,
                state,
                capability=options.capability if command != "debug" else None,
                payload=payload,
            )
        )
        print(response.model_dump_json())
        if isinstance(response, Result) and response.error is not None:
            raise Denied(f"Local capability reported {response.error}")


def keygen(arguments: list[str]) -> None:
    """Create one owner-only signing key, refusing replacement and symlink paths."""
    parser = argparse.ArgumentParser(prog="skulk-cap keygen")
    parser.add_argument("--private-key", type=Path, required=True)
    options = parser.parse_args(arguments)
    path: Path = options.private_key
    secure_directory(path.parent)
    key = Ed25519PrivateKey.generate()
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "wb") as target:
        target.write(key.private_bytes_raw())
        target.flush()
        os.fsync(target.fileno())
    print(json.dumps({"public_key": key.public_key().public_bytes_raw().hex()}))


def inspect(arguments: list[str]) -> None:
    """Check independently supplied public trust, signatures and optional artifacts."""
    parser = argparse.ArgumentParser(prog="skulk-cap inspect")
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--trust", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--declared-host-version")
    options = parser.parse_args(arguments)
    print(
        json.dumps(
            inspect_release(
                options.release,
                options.trust,
                artifacts=options.artifacts,
                declared_host_version=options.declared_host_version,
            ),
            sort_keys=True,
        )
    )


def main() -> None:
    """Dispatch explicit author actions; SDK package accounts grant no authority."""
    parser = argparse.ArgumentParser(prog="skulk-cap", description=__doc__)
    parser.add_argument(
        "--version",
        action="version",
        version=importlib.metadata.version("skulk-capability-tools"),
    )
    parser.add_argument(
        "command",
        choices=(
            "build",
            "sign",
            "wheelhouse",
            "catalog",
            "feed",
            "inspect",
            "keygen",
            "init",
            "run",
            "watch",
            "invoke",
            "debug",
            "test",
            "review",
            "install",
            "update",
            "activate",
            "rollback",
            "uninstall",
            "status",
            "register",
            "source",
            "runtime",
            "publish",
        ),
    )
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    try:
        command: Command = TypeAdapter[Command](Command).validate_python(
            options.command, strict=True
        )
        match command:
            case "build":
                project.main(options.arguments)
            case "sign":
                if "--help" in options.arguments or "-h" in options.arguments:
                    build_release.main(options.arguments)
                else:
                    selected = argparse.ArgumentParser(add_help=False)
                    selected.add_argument("--spec", type=Path, required=True)
                    specification, _ = selected.parse_known_args(options.arguments)
                    with project.project_imports(specification.spec):
                        build_release.main(options.arguments)
            case "wheelhouse":
                wheelhouse.main(options.arguments)
            case "catalog":
                build_catalog.main(options.arguments)
            case "feed":
                assemble_feed.main(options.arguments)
            case "inspect":
                inspect(options.arguments)
            case "keygen":
                keygen(options.arguments)
            case "runtime":
                runtime.main(options.arguments)
            case "publish":
                publish.main(options.arguments)
            case "init" | "run" | "watch" | "invoke" | "debug" | "test":
                development(command, options.arguments)
            case (
                "review"
                | "install"
                | "update"
                | "activate"
                | "rollback"
                | "uninstall"
                | "status"
                | "register"
                | "source"
            ):
                host.main(command, options.arguments)
    except (ProtocolError, asyncio.IncompleteReadError, TimeoutError):
        parser.error("Local session unavailable or uncertain; no request was retried")
    except (Denied, ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
