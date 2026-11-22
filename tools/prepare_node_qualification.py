"""Acquire pinned Node bytes for an explicit disposable qualification runtime."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen

from skulk_capability_tools.node_runtime import build_node_runtime

from skulk_capability_sdk.node_runtime import node_platform, node_source, select_node


def main() -> None:
    """Install only the measured Node asset wheel into this test interpreter."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    options = parser.parse_args()
    output: Path = options.output
    output.mkdir(parents=True, exist_ok=False)
    source = node_source(node_platform())
    archive: Path = options.archive or output / source.archive_name
    if options.archive is None:
        with urlopen(source.url, timeout=60) as response:
            raw = response.read(source.archive_size + 1)
        if (
            len(raw) != source.archive_size
            or hashlib.sha256(raw).hexdigest() != source.archive_sha256
        ):
            raise ValueError("Official Node download differs from the pinned cohort")
        archive.write_bytes(raw)
    wheel = build_node_runtime(archive, node_platform(), output / "wheels")
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "--no-deps",
            "--no-index",
            "--reinstall",
            str(output / "wheels" / wheel.filename),
        ],
        check=True,
    )
    selected = select_node(Path(sys.prefix))
    actual = subprocess.check_output([str(selected.executable), "--version"], text=True)
    if actual.strip() != "v" + source.version:
        raise ValueError("Selected Node version differs")
    (output / "selection.json").write_text(
        json.dumps(
            {
                "source": source.model_dump(mode="json"),
                "wheel": wheel.model_dump(),
                "installed_signed_admission": False,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
