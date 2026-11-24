"""Exercise the maintained public reference through installed native CLI calls."""

import shutil
from pathlib import Path

from tests_authoring.support import command, debug, frames, session


async def test_public_reference_modes_media_and_early_aggregate_refusal(
    tmp_path: Path,
) -> None:
    """Keep the source example executable and refuse before echoing excess input."""
    source = Path(__file__).resolve().parents[1] / "examples/native_reference"
    project = tmp_path / "public-reference"
    shutil.copytree(source, project, ignore=shutil.ignore_patterns("__pycache__"))
    media = tmp_path / "input.pcm"
    media.write_bytes(b"\x00\xff\x01")
    output = tmp_path / "output.pcm"
    async with session(project) as (state, _):
        await debug(project, state)

        async def invoke(mode: str, *arguments: str):
            return await command(
                "invoke",
                "--native",
                "--project",
                str(project),
                "--state",
                str(state),
                "--capability",
                "foxlight.sdk-reference." + mode + "@1.0.0",
                *arguments,
            )

        for mode in ("echo", "stream", "collect", "duplex"):
            extra = (
                ()
                if mode in {"echo", "stream"}
                else (
                    "--input-chunk",
                    '{"text":"one"}',
                    "--input-chunk",
                    '{"text":""}',
                    "--input-chunk",
                    '{"text":"two"}',
                )
            )
            code, replies, error = await invoke(mode, *extra)
            assert code == 0 and replies[-1]["error"] is None, error
            if mode == "echo":
                assert replies[-1]["payload"] == {"text": "Hello"}
            else:
                values = frames(replies)
                assert values[-1]["kind"] == "end"
                assert values[-2]["kind"] == "completed"
                assert values[-2]["payload"] == {
                    "text": "Hello" if mode == "stream" else "onetwo"
                }
        code, _, error = await invoke(
            "duplex",
            "--input-chunk",
            '{"text":"audio"}',
            "--input-media",
            str(media),
            "--media-type",
            "audio/pcm",
            "--output-media",
            str(output),
        )
        assert code == 0 and output.read_bytes() == media.read_bytes(), error
        code, replies, _ = await invoke(
            "duplex",
            "--input-chunk",
            '{"text":"' + "a" * 4096 + '"}',
            "--input-chunk",
            '{"text":"b"}',
        )
        assert code != 0
        values = frames(replies)
        assert sum(frame["kind"] == "chunk" for frame in values) == 1
        assert any(frame["kind"] == "failed" for frame in values)
        await debug(project, state)
        code, replies, error = await invoke("echo")
        assert code == 0 and replies[-1]["payload"] == {"text": "Hello"}, error
