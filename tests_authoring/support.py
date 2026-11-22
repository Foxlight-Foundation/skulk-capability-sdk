"""Actual installed CLI processes and private, short-path author session custody."""

import asyncio
import os
import signal
import sys
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from pydantic import JsonValue, TypeAdapter

OBJECT = TypeAdapter(dict[str, JsonValue])
CLI = str(Path(sys.prefix) / "bin/skulk-cap")


async def command(*arguments: str) -> tuple[int, list[dict[str, JsonValue]], str]:
    """Run the installed console entry point outside the SDK source directory."""
    process = await asyncio.create_subprocess_exec(
        CLI,
        *arguments,
        cwd="/tmp",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(70):
            output, error = await process.communicate()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    return (
        process.returncode or 0,
        [OBJECT.validate_json(line) for line in output.splitlines() if line],
        error.decode(),
    )


async def initialize(
    path: Path, language: str = "python", package: Path | None = None
) -> None:
    """Use native init without any account, project code or build hook execution."""
    arguments = [
        "init",
        "--native",
        "--language",
        language,
        "--name",
        "native_example",
        str(path),
    ]
    if package is not None:
        arguments += ["--sdk-package", str(package)]
    code, _, error = await command(*arguments)
    assert code == 0, error


@asynccontextmanager
async def session(
    project: Path, *, watch: bool = False, configuration: Path | None = None
) -> AsyncGenerator[tuple[Path, asyncio.subprocess.Process]]:
    """Start actual source CLI and require owned shutdown before dropping its state."""
    with tempfile.TemporaryDirectory(prefix="native-cli-", dir="/tmp") as directory:
        state = Path(directory)
        arguments = [
            CLI,
            "watch" if watch else "run",
            "--native",
            "--project",
            str(project),
            "--state",
            str(state),
        ]
        if configuration is not None:
            arguments += ["--configuration", str(configuration)]
        process = await asyncio.create_subprocess_exec(
            *arguments,
            cwd="/tmp",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        try:
            async with asyncio.timeout(70):
                line = await process.stdout.readline()
            assert line and OBJECT.validate_json(line) == {
                "ready": True,
                "scope": "local-development",
                "native": True,
            }
            yield state, process
        finally:
            if process.returncode is None:
                process.send_signal(signal.SIGINT)
            try:
                async with asyncio.timeout(15):
                    _, error = await process.communicate()
            except TimeoutError:
                process.kill()
                await process.wait()
                raise AssertionError("Native author session failed to reap") from None
            assert process.returncode == 0, error.decode()
            assert not (state / "session.sock").exists()
            assert not list(state.glob("g-*"))


async def invoke(
    project: Path,
    state: Path,
    suffix: str = "echo",
    *extra: str,
    operation: Literal["invoke", "test"] = "invoke",
) -> tuple[int, list[dict[str, JsonValue]], str]:
    """Exercise the actual client CLI against its private native source session."""
    return await command(
        operation,
        "--native",
        "--project",
        str(project),
        "--state",
        str(state),
        "--capability",
        "native.example." + suffix + "@1.0.0",
        *extra,
    )


async def debug(project: Path, state: Path) -> dict[str, JsonValue]:
    """Read only public development status, never the protected startup projection."""
    code, replies, error = await command(
        "debug", "--native", "--project", str(project), "--state", str(state)
    )
    assert code == 0 and len(replies) == 1, error
    value = replies[0]
    assert value["scope"] == "local-development" and value["ready"] is True
    assert not {
        "token",
        "credential_config",
        "configuration",
        "socket",
        "stream_socket",
    } & set(value)
    return value


def gone(pid: JsonValue) -> None:
    """The process inspected in this owned session must no longer exist."""
    assert isinstance(pid, int) and not isinstance(pid, bool)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    raise AssertionError("Native author process survived its generation")


def frames(replies: list[dict[str, JsonValue]]) -> list[dict[str, JsonValue]]:
    """Extract actual native projections from the progressive CLI output."""
    return [
        OBJECT.validate_python(one["frame"])
        for one in replies
        if one["kind"] == "frame"
    ]


async def wait_file(path: Path) -> None:
    """Boundedly observe a marker in another process without shared async events."""
    for _ in range(1000):
        if path.exists():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Native source marker did not appear")
