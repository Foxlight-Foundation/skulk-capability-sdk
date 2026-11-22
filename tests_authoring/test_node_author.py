"""Actual generated TypeScript compilation, staging and selected runtime CLI."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from skulk_capability_sdk.node_runtime import select_node
from tests_authoring.support import debug, frames, gone, initialize, invoke, session


@pytest.fixture(scope="module")
async def node_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Compile and install through the generated build scripts, without hooks."""
    project = tmp_path_factory.mktemp("author project with spaces") / "node"
    package = Path(os.environ["SKULK_TYPESCRIPT_PACKAGE"]).absolute()
    await initialize(project, "typescript", package)
    node = select_node(Path(sys.prefix)).executable
    environment = dict(
        os.environ, PATH=str(node.parent) + os.pathsep + os.environ["PATH"]
    )

    def build() -> None:
        subprocess.run(
            ["npm", "install", "--ignore-scripts"],
            cwd=project,
            env=environment,
            check=True,
            capture_output=True,
            timeout=90,
        )
        subprocess.run(
            ["npm", "run", "build"],
            cwd=project,
            env=environment,
            check=True,
            capture_output=True,
            timeout=90,
        )

    await asyncio.to_thread(build)
    assert json.loads((project / "stage/package.json").read_text()) == {
        "private": True,
        "type": "module",
    }
    assert not (project / "stage/package-lock.json").exists()
    assert "typescript" not in {
        p.name for p in (project / "stage/node_modules").iterdir()
    }
    return project


async def test_native_typescript_cli_four_modes_and_media(
    node_project: Path, tmp_path: Path
) -> None:
    """The real compiled factory runs on selected Node and keeps binary bytes exact."""
    source_media, output_media = tmp_path / "input", tmp_path / "output"
    source_media.write_bytes(b"\x00\xff\x01")
    async with session(node_project) as (state, _):
        identity = await debug(node_project, state)
        assert identity["language"] == "typescript"
        for mode in ("echo", "stream", "collect", "duplex"):
            extra = (
                ()
                if mode in {"echo", "stream"}
                else ("--input-chunk", '{"text":"one"}')
            )
            code, replies, error = await invoke(node_project, state, mode, *extra)
            assert code == 0 and replies[-1]["error"] is None, error
            expected = {"text": "Hello" if mode in {"echo", "stream"} else "one"}
            if mode == "echo":
                assert replies[-1]["payload"] == expected
            else:
                assert frames(replies)[-1]["kind"] == "end"
                assert frames(replies)[-2]["payload"] == expected
        code, replies, error = await invoke(
            node_project,
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
    gone(identity["pid"])


async def test_native_typescript_watch_replaces_only_owned_assets(
    node_project: Path,
) -> None:
    """Compiled edits restart, retain app state and reclaim obsolete snapshots."""
    source = node_project / "stage/app.js"
    original = source.read_text()
    try:
        async with session(node_project, watch=True) as (state, _):
            first = await debug(node_project, state)
            (state / "kept-state").write_text("preserve")
            source.write_text(original.replace("call.payload.text ?? ''", "'changed'"))
            code, replies, error = await invoke(node_project, state)
            assert code == 0 and replies[-1]["payload"] == {"text": "changed"}, error
            second = await debug(node_project, state)
            assert second["generation"] == 2
            gone(first["pid"])
            assert (state / "kept-state").read_text() == "preserve"
            assert len(list(state.glob("g-*"))) == 1
    finally:
        source.write_text(original)
