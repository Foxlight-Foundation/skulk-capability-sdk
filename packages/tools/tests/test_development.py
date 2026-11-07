"""Run a genuinely independent source child; local smoke is not host admission."""

import asyncio
import json
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from skulk_capability_tools.development import call, project_manifest, serve
from skulk_capability_tools.starter import initialize

from skulk_capability_sdk.contracts import Denied, Health, Result


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
