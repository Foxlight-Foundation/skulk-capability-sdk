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
    parser.add_argument(
        "--native",
        action="store_true",
        help="Select compiled native author contracts explicitly",
    )
    if command == "init":
        parser.add_argument("destination", type=Path)
        parser.add_argument("--name", default="hello_capability")
        parser.add_argument(
            "--language", choices=("python", "typescript"), default="python"
        )
        parser.add_argument("--sdk-package", type=Path)
        options = parser.parse_args(arguments)
        if options.native:
            from .native_author import Language
            from .native_starter import initialize_native

            initialize_native(
                options.destination,
                options.name,
                TypeAdapter[Language](Language).validate_python(options.language),
                options.sdk_package,
            )
        elif options.language != "python" or options.sdk_package is not None:
            parser.error("Language/package selection requires --native")
        else:
            initialize(options.destination, options.name)
        return
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--state", type=Path)
    if command in {"run", "watch"}:
        parser.add_argument("--configuration", type=Path)
        parser.add_argument("--credential-selection", type=Path)
    if command in {"invoke", "test"}:
        parser.add_argument("--input", default='{"text":"Hello"}')
        parser.add_argument("--capability")
        parser.add_argument("--input-chunk", action="append", default=[])
        parser.add_argument("--input-media", type=Path)
        parser.add_argument("--media-type", default="application/octet-stream")
        parser.add_argument("--output-media", type=Path)
        parser.add_argument("--seconds", type=float)
    options = parser.parse_args(arguments)
    project: Path = options.project.resolve(strict=True)
    suffix = hashlib.sha256(
        (str(project) + ("\x00native" if options.native else "")).encode()
    ).hexdigest()[:20]
    state: Path = options.state or Path(f"/tmp/skulk-cap-{os.getuid()}-{suffix}")
    if options.native:
        from .native_development import Chunk, Request
        from .native_development import call as native_call
        from .native_development import serve as native_serve

        if command in {"run", "watch"}:
            try:
                asyncio.run(
                    native_serve(
                        project,
                        state,
                        watch=command == "watch",
                        configuration=options.configuration,
                        credential_selection=options.credential_selection,
                    )
                )
            except KeyboardInterrupt:
                pass
            return
        chunks: list[Chunk] = []
        if command != "debug":
            from pydantic import JsonValue

            chunks = [
                Chunk(payload=TypeAdapter(dict[str, JsonValue]).validate_json(raw))
                for raw in options.input_chunk
            ]
            if options.input_media is not None:
                import base64

                from .inspection import read_public

                if len(chunks) != 1:
                    parser.error("--input-media requires exactly one --input-chunk")
                chunks[0] = chunks[0].model_copy(
                    update={
                        "media_base64": base64.b64encode(
                            read_public(options.input_media, 1024 * 1024)
                        ).decode(),
                        "media_type": options.media_type,
                    }
                )
        request = Request(
            kind="debug" if command == "debug" else "invoke",
            capability=None if command == "debug" else options.capability,
            payload="{}" if command == "debug" else options.input,
            chunks=tuple(chunks),
            seconds=None if command == "debug" else options.seconds,
        )

        async def exchange() -> None:
            media_file = None
            total = 0
            try:
                if command != "debug" and options.output_media is not None:
                    descriptor = os.open(
                        options.output_media,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                    )
                    media_file = os.fdopen(descriptor, "wb")
                async for reply in native_call(state, request):
                    if media_file is not None and reply["kind"] == "frame":
                        import base64

                        raw = base64.b64decode(
                            TypeAdapter(str).validate_python(reply["media_base64"]),
                            validate=True,
                        )
                        total += len(raw)
                        if total > 64 * 1024 * 1024:
                            raise Denied("Local output media exceeds its file budget")
                        media_file.write(raw)
                        reply = {
                            key: value
                            for key, value in reply.items()
                            if key != "media_base64"
                        } | {"media_bytes": len(raw)}
                    print(json.dumps(reply), flush=True)
                    if reply["kind"] == "error" or (
                        reply["kind"] == "result" and reply.get("error") is not None
                    ):
                        raise Denied(
                            "Local native request failed; no request was retried"
                        )
            finally:
                if media_file is not None:
                    media_file.close()

        try:
            asyncio.run(exchange())
        except KeyboardInterrupt:
            raise SystemExit(130) from None
        return
    if command in {"invoke", "test"} and (
        options.input_chunk
        or options.input_media
        or options.output_media
        or options.seconds is not None
    ):
        parser.error("Streaming/media/deadline options require --native")
    if command in {"run", "watch"} and (
        options.configuration is not None or options.credential_selection is not None
    ):
        parser.error("Configuration/credential options require --native")
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
                selected = argparse.ArgumentParser(add_help=False)
                selected.add_argument("--spec", type=Path)
                selected.add_argument("--node-project", type=Path)
                specification, _ = selected.parse_known_args(options.arguments)
                if (
                    "--help" in options.arguments
                    or "-h" in options.arguments
                    or specification.node_project is not None
                ):
                    build_release.main(options.arguments)
                else:
                    if specification.spec is None:
                        parser.error("Python signing requires --spec")
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
