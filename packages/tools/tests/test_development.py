"""Run a genuinely independent source child; local smoke is not host admission."""

import asyncio
import json
import os
import py_compile
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from skulk_capability_tools.development import call, project_manifest, serve
from skulk_capability_tools.starter import initialize

from skulk_capability_sdk.contracts import Denied, Health, Result
from skulk_capability_sdk.wire import read_message, write_message


@pytest.fixture
def state() -> Iterator[Path]:
    """Keep Unix socket names within the macOS limit and remove owned scratch."""
    root = Path(tempfile.mkdtemp(prefix="cap-test-", dir="/tmp"))
    try:
        yield root
    finally:
        shutil.rmtree(root)


async def ready(state: Path, session: asyncio.Task[None]) -> None:
    """Wait for the session socket or surface its actual startup failure."""
    async with asyncio.timeout(10):
        while not (state / "session.sock").exists():
            if session.done():
                await session
                raise AssertionError("Session exited before readiness")
            await asyncio.sleep(0.01)


async def stop(session: asyncio.Task[None], state: Path) -> None:
    """Cancel the parent and require its socket/child cleanup to finish."""
    session.cancel()
    with pytest.raises(asyncio.CancelledError):
        await session
    assert not (state / "session.sock").exists()


async def test_independent_starter_handshake_readiness_invoke_and_watch(
    tmp_path: Path, state: Path
) -> None:
    project = tmp_path / "project"
    initialize(project, "hello_capability")
    assert project_manifest(project).runtime_dependencies == ("skulk-capability-sdk",)
    session = asyncio.create_task(serve(project, state, watch=True))
    try:
        await ready(state, session)
        health = await call(project, state, capability=None, payload=None)
        assert isinstance(health, Health) and health.ready
        reply = await call(project, state, capability=None, payload={"text": "Hello"})
        assert isinstance(reply, Result) and reply.payload == {"text": "Hello"}
        source = project / "hello_capability" / "app.py"
        source.write_text(
            source.read_text().replace(
                'payload={"text": message.payload["text"]} if valid else None,',
                'payload={"text": "changed"} if valid else None,',
            )
        )
        reply = await call(project, state, capability=None, payload={"text": "Hello"})
        assert isinstance(reply, Result) and reply.payload == {"text": "changed"}
    finally:
        await stop(session, state)


async def test_invalid_input_refuses_without_replay_and_closes_poisoned_session(
    tmp_path: Path, state: Path
) -> None:
    project = tmp_path / "project"
    initialize(project, "hello_capability")
    session = asyncio.create_task(serve(project, state))
    await ready(state, session)
    with pytest.raises(asyncio.IncompleteReadError):
        await call(project, state, capability=None, payload={"unexpected": "input"})
    await asyncio.wait_for(session, 5)
    assert not (state / "session.sock").exists()


def test_init_refuses_existing_destination_and_invalid_import_names(
    tmp_path: Path,
) -> None:
    initialize(tmp_path / "project", "hello_capability")
    with pytest.raises(Denied, match="already exists"):
        initialize(tmp_path / "project", "hello_capability")
    with pytest.raises(Denied, match="package name"):
        initialize(tmp_path / "other", "../escape")
    assert not (tmp_path / "other").exists()


@pytest.mark.parametrize("fault", ["identity", "readiness"])
async def test_failed_startup_is_refused_and_reaped(
    tmp_path: Path,
    state: Path,
    fault: str,
) -> None:
    project = tmp_path / "project"
    initialize(project, "failed_capability")
    source = project / "failed_capability" / "app.py"
    text = source.read_text()
    text = (
        text.replace("token=startup.token", 'token="0" * 64')
        if fault == "identity"
        else text.replace('update={"ready": True}', 'update={"ready": False}')
    )
    source.write_text(text)
    with pytest.raises(Denied, match="handshake|not ready"):
        await asyncio.wait_for(serve(project, state), 5)
    assert not (state / "session.sock").exists()
    assert not (state / "child.sock").exists()


async def test_uncertain_execution_is_not_replayed_or_reused(
    tmp_path: Path,
    state: Path,
) -> None:
    project = tmp_path / "project"
    initialize(project, "timeout_capability")
    manifest = json.loads((project / "manifest.json").read_text())
    manifest["bounds"]["call_seconds"] = 1.0
    (project / "manifest.json").write_text(json.dumps(manifest))
    source = project / "timeout_capability" / "app.py"
    source.write_text(
        source.read_text().replace(
            "descriptor = startup.manifest.descriptors[0]",
            "\n".join(
                [
                    "from pathlib import Path",
                    '                Path("calls.txt").open("a").write("called\\n")',
                    "                await asyncio.sleep(10)",
                    "                descriptor = startup.manifest.descriptors[0]",
                ]
            ),
        )
    )
    session = asyncio.create_task(serve(project, state, watch=True))
    await ready(state, session)
    with pytest.raises(asyncio.IncompleteReadError):
        await call(project, state, capability=None, payload={"text": "Hello"})
    await asyncio.wait_for(session, 5)
    assert (project / "calls.txt").read_text() == "called\n"
    assert not (state / "session.sock").exists()


async def test_child_does_not_inherit_ambient_secrets(
    tmp_path: Path,
    state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SKULK_DEVELOPMENT_TEST_SECRET", "synthetic-not-a-credential")
    project = tmp_path / "project"
    initialize(project, "environment_capability")
    source = project / "environment_capability" / "app.py"
    source.write_text(
        source.read_text().replace(
            "startup = read_startup(startup_fd)",
            "import os\n"
            '    assert "SKULK_DEVELOPMENT_TEST_SECRET" not in os.environ\n'
            "    startup = read_startup(startup_fd)",
        )
    )
    session = asyncio.create_task(serve(project, state))
    try:
        await ready(state, session)
        assert isinstance(
            await call(project, state, capability=None, payload=None), Health
        )
    finally:
        await stop(session, state)


async def test_src_project_validator_runs_before_source_child_startup(
    tmp_path: Path, state: Path
) -> None:
    project = tmp_path / "project"
    name = "validated_source_capability"
    initialize(project, name)
    (project / "src").mkdir()
    shutil.move(str(project / name), project / "src" / name)
    (project / "src" / name / "validation.py").write_text(
        "def check(manifest):\n"
        '    assert manifest.bundle_id == "validated.source.capability"\n'
    )
    specification = project / "capability.toml"
    specification.write_text(
        specification.read_text().replace(
            f'package_root = "{name}"', f'package_root = "src/{name}"'
        )
        + f'validator = "{name}.validation:check"\n'
    )
    session = asyncio.create_task(serve(project, state, watch=True))
    try:
        await ready(state, session)
        health = await call(project, state, capability=None, payload=None)
        assert isinstance(health, Health) and health.ready
        result = await call(
            project, state, capability=None, payload={"text": "validated"}
        )
        assert isinstance(result, Result) and result.payload == {"text": "validated"}
    finally:
        if not session.done():
            await stop(session, state)


async def test_source_entry_calls_main_without_a_script_guard(
    tmp_path: Path, state: Path
) -> None:
    project = tmp_path / "project"
    initialize(project, "function_entry_capability")
    source = project / "function_entry_capability/app.py"
    source.write_text(source.read_text().split('\nif __name__ == "__main__":')[0])
    session = asyncio.create_task(serve(project, state))
    try:
        await ready(state, session)
        result = await call(project, state, capability=None, payload={"text": "entry"})
        assert isinstance(result, Result) and result.payload == {"text": "entry"}
    finally:
        if not session.done():
            await stop(session, state)


async def test_watched_validator_is_fresh_and_receives_no_ambient_secrets(
    tmp_path: Path, state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKULK_AUTHOR_VALIDATOR_SECRET", "synthetic-not-a-credential")
    project = tmp_path / "project"
    initialize(project, "watched_validator_capability")
    validator = project / "watched_validator_capability/validation.py"
    validator.write_text(
        "import os\ndef check(manifest):\n"
        '    assert "SKULK_AUTHOR_VALIDATOR_SECRET" not in os.environ\n'
        "    assert 1\n"
    )
    py_compile.compile(str(validator), doraise=True)
    original_stat = validator.stat()
    specification = project / "capability.toml"
    specification.write_text(
        specification.read_text()
        + 'validator = "watched_validator_capability.validation:check"\n'
    )
    session = asyncio.create_task(serve(project, state, watch=True))
    try:
        await ready(state, session)
        assert isinstance(
            await call(project, state, capability=None, payload=None), Health
        )
        validator.write_text(validator.read_text().replace("assert 1", "assert 0"))
        os.utime(validator, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        # Bypass the CLI's own refreshed validation to exercise the running
        # watch server's independent restart/admission check.
        reader, writer = await asyncio.open_unix_connection(str(state / "session.sock"))
        try:
            await write_message(writer, Health(protocol=4, nonce="a" * 32, ready=False))
            with pytest.raises(asyncio.IncompleteReadError):
                await read_message(reader)
        finally:
            writer.close()
            await writer.wait_closed()
        await asyncio.wait_for(session, 5)
        assert not (state / "session.sock").exists()
    finally:
        if not session.done():
            await stop(session, state)


async def test_watched_source_ignores_same_size_same_mtime_bytecode(
    tmp_path: Path, state: Path
) -> None:
    project = tmp_path / "project"
    initialize(project, "bytecode_watch_capability")
    source = project / "bytecode_watch_capability/app.py"
    source.write_text(
        source.read_text().replace(
            'payload={"text": message.payload["text"]} if valid else None,',
            'payload={"text": "alpha"} if valid else None,',
        )
    )
    py_compile.compile(str(source), doraise=True)
    original_stat = source.stat()
    session = asyncio.create_task(serve(project, state, watch=True))
    try:
        await ready(state, session)
        first = await call(project, state, capability=None, payload={"text": "input"})
        assert isinstance(first, Result) and first.payload == {"text": "alpha"}
        source.write_text(source.read_text().replace('"alpha"', '"bravo"'))
        os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        assert source.stat().st_size == original_stat.st_size
        second = await call(project, state, capability=None, payload={"text": "input"})
        assert isinstance(second, Result) and second.payload == {"text": "bravo"}
    finally:
        if not session.done():
            await stop(session, state)


async def test_relative_state_is_normalized_before_child_changes_directory(
    tmp_path: Path, state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    initialize(project, "relative_state_capability")
    monkeypatch.chdir(state.parent)
    relative = Path(state.name)
    session = asyncio.create_task(serve(project, relative))
    try:
        await ready(state, session)
        result = await call(
            project, relative, capability=None, payload={"text": "relative"}
        )
        assert isinstance(result, Result) and result.payload == {"text": "relative"}
    finally:
        if not session.done():
            await stop(session, state)


async def test_source_launch_forwards_declared_manifest_arguments(
    tmp_path: Path, state: Path
) -> None:
    project = tmp_path / "project"
    initialize(project, "declared_arguments_capability")
    manifest = project / "manifest.json"
    value = json.loads(manifest.read_text())
    value["arguments"] = ["--mode", "declared"]
    manifest.write_text(json.dumps(value))
    source = project / "declared_arguments_capability/app.py"
    source.write_text(
        source.read_text().replace(
            'parser.add_argument("--config-fd", type=int, required=True)',
            'parser.add_argument("--config-fd", type=int, required=True)\n'
            '    parser.add_argument("--mode", choices=["declared"], required=True)',
        )
    )
    session = asyncio.create_task(serve(project, state))
    try:
        await ready(state, session)
        result = await call(
            project, state, capability=None, payload={"text": "arguments"}
        )
        assert isinstance(result, Result) and result.payload == {"text": "arguments"}
    finally:
        if not session.done():
            await stop(session, state)


async def test_watch_restart_preserves_the_subsequent_call_time_budget(
    tmp_path: Path, state: Path
) -> None:
    """A real slow restart plus call can exceed the old shared 35-second ceiling."""
    project = tmp_path / "project"
    initialize(project, "restart_budget_capability")
    manifest = project / "manifest.json"
    value = json.loads(manifest.read_text())
    value["bounds"].update(startup_seconds=30.0, call_seconds=30.0)
    manifest.write_text(json.dumps(value))
    session = asyncio.create_task(serve(project, state, watch=True))
    try:
        await ready(state, session)
        source = project / "restart_budget_capability/app.py"
        source.write_text(
            source.read_text()
            .replace(
                "startup = read_startup(startup_fd)",
                "startup = read_startup(startup_fd)\n    await asyncio.sleep(20)",
            )
            .replace(
                "descriptor = startup.manifest.descriptors[0]",
                "await asyncio.sleep(20)\n"
                "                descriptor = startup.manifest.descriptors[0]",
            )
        )
        reply = await asyncio.wait_for(
            call(project, state, capability=None, payload={"text": "after restart"}),
            timeout=55,
        )
        assert isinstance(reply, Result) and reply.payload == {"text": "after restart"}
    finally:
        if not session.done():
            await stop(session, state)
