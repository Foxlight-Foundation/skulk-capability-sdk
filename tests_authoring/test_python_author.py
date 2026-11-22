"""Independent native Python CLI creation, I/O, watch and process cleanup."""

import asyncio
import json
import os
import py_compile
import sys
from pathlib import Path

import pytest
from skulk_capability_tools import cli

from tests_authoring.support import (
    OBJECT,
    debug,
    frames,
    gone,
    initialize,
    invoke,
    session,
    wait_file,
)


@pytest.mark.parametrize("equals", [True, False])
def test_native_sign_dispatch_never_requires_a_python_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, equals: bool
) -> None:
    """Both argparse spellings reach Node signing without legacy source imports."""
    arguments = (
        ["--node-project=" + str(tmp_path)]
        if equals
        else ["--node-project", str(tmp_path)]
    )
    dispatched: list[list[str]] = []

    def sign(selected: list[str]) -> None:
        dispatched.append(selected)

    monkeypatch.setattr(sys, "argv", ["skulk-cap", "sign", *arguments])
    monkeypatch.setattr(cli.build_release, "main", sign)
    cli.main()
    assert dispatched == [arguments]


async def test_native_python_cli_all_modes_media_and_reaping(tmp_path: Path) -> None:
    """Four native handlers, actual media and terminal/end precede CLI success."""
    project = tmp_path / "python source with spaces"
    await initialize(project)
    source_media, output_media = tmp_path / "in.pcm", tmp_path / "out.pcm"
    source_media.write_bytes(b"\x00\xff\x01")
    async with session(project) as (state, _):
        identity = await debug(project, state)
        for mode in ("echo", "stream", "collect", "duplex"):
            extra = (
                ()
                if mode in {"echo", "stream"}
                else (
                    "--input-chunk",
                    '{"text":"one"}',
                    "--input-chunk",
                    '{"text":"two"}',
                )
            )
            code, replies, error = await invoke(project, state, mode, *extra)
            assert code == 0 and replies[-1]["error"] is None, error
            values = frames(replies)
            if values:
                assert values[-1]["kind"] == "end" and values[-2]["kind"] == "completed"
                assert values[-2]["payload"] == {
                    "text": "Hello" if mode == "stream" else "onetwo"
                }
        code, replies, error = await invoke(
            project,
            state,
            "duplex",
            "--input-chunk",
            '{"text":"audio"}',
            "--input-media",
            str(source_media),
            "--media-type",
            "audio/pcm",
            "--output-media",
            str(output_media),
        )
        assert code == 0 and output_media.read_bytes() == source_media.read_bytes(), (
            error
        )
        assert any(one.get("media_bytes") == 3 for one in replies)
    gone(identity["pid"])


async def test_native_python_watch_uses_bytes_and_preserves_state(
    tmp_path: Path,
) -> None:
    """Equal-size/mtime source edits retire old code without deleting app state."""
    project = tmp_path / "watch"
    await initialize(project)
    source = project / "native_example/app.py"
    source.write_text(
        source.read_text().replace(
            'return {"text": call.payload["text"]}', 'return {"text": "first"}'
        )
    )
    py_compile.compile(str(source), doraise=True)
    async with session(project, watch=True) as (state, _):
        first = await debug(project, state)
        (state / "kept-state").write_bytes(b"preserve")
        code, replies, error = await invoke(project, state)
        assert code == 0 and replies[-1]["payload"] == {"text": "first"}, error
        info = source.stat()
        source.write_text(source.read_text().replace('"first"', '"other"'))
        os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns))
        code, replies, error = await invoke(project, state)
        assert code == 0 and replies[-1]["payload"] == {"text": "other"}, error
        second = await debug(project, state)
        assert second["generation"] == 2 and second["pid"] != first["pid"]
        gone(first["pid"])
        assert (state / "kept-state").read_bytes() == b"preserve"
        assert len(list(state.glob("g-*"))) == 1


async def test_native_local_invalid_payload_never_dispatches_and_session_reuses(
    tmp_path: Path,
) -> None:
    """Compiled input/media validation refuses before invoking an author handler."""
    project = tmp_path / "invalid"
    await initialize(project)
    async with session(project) as (state, _):
        code, replies, _ = await invoke(
            project, state, "echo", "--input", '{"wrong":"data"}'
        )
        assert code != 0 and replies[-1]["kind"] == "error"
        await debug(project, state)
        code, replies, error = await invoke(project, state)
        assert code == 0 and replies[-1]["payload"] == {"text": "Hello"}, error
        code, replies, _ = await invoke(
            project,
            state,
            "duplex",
            "--input-chunk",
            '{"wrong":"data"}',
        )
        assert code != 0 and replies[-1]["kind"] == "error"
        await debug(project, state)


async def test_native_source_deadline_drains_and_health_stays_live(
    tmp_path: Path,
) -> None:
    """Handler cancellation completes before a subsequent call can succeed."""
    project = tmp_path / "deadline"
    await initialize(project)
    source = project / "native_example/app.py"
    source.write_text(
        source.read_text().replace(
            '    return {"text": call.payload["text"]}',
            '    if call.payload["text"] == "wait":\n'
            "        from pathlib import Path\n"
            '        Path("entered").write_text("entered")\n'
            "        try:\n"
            "            await asyncio.Event().wait()\n"
            "        finally:\n"
            '            Path("cleaned").write_text("cleaned")\n'
            '    return {"text": call.payload["text"]}',
        )
    )
    async with session(project) as (state, _):
        waiting = asyncio.create_task(
            invoke(
                project, state, "echo", "--input", '{"text":"wait"}', "--seconds", "1"
            )
        )
        await wait_file(state / "entered")
        await debug(project, state)
        code, replies, _ = await waiting
        assert code != 0 and replies[-1]["error"] == "timeout"
        assert (state / "cleaned").read_text() == "cleaned"
        code, replies, error = await invoke(project, state)
        assert code == 0 and replies[-1]["error"] is None, error


async def test_native_settings_watch_changes_generation_without_disclosing_values(
    tmp_path: Path,
) -> None:
    """Ordinary settings restart a local generation; debug stays redacted."""
    project, configuration = tmp_path / "settings", tmp_path / "configuration.json"
    await initialize(project)
    configuration.write_text('{"label":"first"}')
    async with session(project, watch=True, configuration=configuration) as (state, _):
        first = await debug(project, state)
        configuration.write_text('{"label":"second"}')
        code, _, error = await invoke(project, state)
        assert code == 0, error
        second = await debug(project, state)
        assert second["generation"] == 2
        assert "second" not in json.dumps(second)
        gone(first["pid"])


async def test_native_local_requests_cannot_supply_a_fabric_context(
    tmp_path: Path,
) -> None:
    """Private author IPC accepts payloads, not a claimed production caller/issuer."""
    project = tmp_path / "identity"
    await initialize(project)
    async with session(project) as (state, _):
        reader, writer = await asyncio.open_unix_connection(str(state / "session.sock"))
        raw = json.dumps(
            {"kind": "invoke", "context": {"caller_id": "fabric-admin"}}
        ).encode()
        writer.write(len(raw).to_bytes(4, "big") + raw)
        await writer.drain()
        size = int.from_bytes(await reader.readexactly(4), "big")
        reply = OBJECT.validate_json(await reader.readexactly(size))
        assert reply["kind"] == "error"
        writer.close()
        await writer.wait_closed()
        await debug(project, state)


async def test_disconnected_native_client_drains_before_session_reuse(
    tmp_path: Path,
) -> None:
    """A lost source client cancels backend work without replay or dead session."""
    project = tmp_path / "disconnect"
    await initialize(project)
    source = project / "native_example/app.py"
    source.write_text(
        source.read_text().replace(
            '    return {"text": call.payload["text"]}',
            "    from pathlib import Path\n"
            '    with Path("calls").open("a") as log:\n'
            '        log.write(call.payload["text"] + "\\n")\n'
            '    if call.payload["text"] == "wait":\n'
            '        Path("entered").write_text("entered")\n'
            "        try:\n"
            "            await asyncio.Event().wait()\n"
            "        finally:\n"
            '            Path("cleaned").write_text("cleaned")\n'
            '    return {"text": call.payload["text"]}',
        )
    )
    async with session(project) as (state, _):
        _, writer = await asyncio.open_unix_connection(str(state / "session.sock"))
        raw = json.dumps({"kind": "invoke", "payload": '{"text":"wait"}'}).encode()
        writer.write(len(raw).to_bytes(4, "big") + raw)
        await writer.drain()
        await wait_file(state / "entered")
        _, departed = await asyncio.open_unix_connection(str(state / "session.sock"))
        queued = json.dumps(
            {"kind": "invoke", "payload": '{"text":"departed"}'}
        ).encode()
        departed.write(len(queued).to_bytes(4, "big") + queued)
        await departed.drain()
        await debug(project, state)
        departed.close()
        await departed.wait_closed()
        await debug(project, state)
        writer.close()
        await writer.wait_closed()
        await wait_file(state / "cleaned")
        code, replies, error = await invoke(project, state)
        assert code == 0 and replies[-1]["payload"] == {"text": "Hello"}, error
        assert (state / "calls").read_text().splitlines() == ["wait", "Hello"]
