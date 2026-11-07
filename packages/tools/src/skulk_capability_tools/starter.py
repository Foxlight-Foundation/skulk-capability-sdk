"""Create a model-free capability using only public, installed author interfaces."""

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from pydantic import JsonValue

from skulk_capability_sdk.contracts import Denied, Descriptor, Manifest

CHILD = '''"""A model-free echo capability using the public managed-child protocol."""

import argparse
import asyncio

from skulk_capability_sdk.contracts import (
    Health, Hello, Invoke, Result, Shutdown, manifest_digest,
)
from skulk_capability_sdk.storage import read_startup
from skulk_capability_sdk.wire import read_message, write_message


async def serve(startup_fd: int) -> None:
    """Answer the host's authenticated startup, readiness and echo requests."""
    startup = read_startup(startup_fd)
    reader, writer = await asyncio.open_unix_connection(startup.socket)
    try:
        await write_message(writer, Hello(
            protocol=startup.manifest.protocol,
            host_id=startup.host_id, transport_node_id=startup.transport_id,
            capability_node_id=startup.node_id, bundle_id=startup.manifest.bundle_id,
            bundle_version=startup.manifest.bundle_version, token=startup.token,
            manifest_sha256=manifest_digest(startup.manifest),
        ))
        while True:
            message = await read_message(reader)
            if isinstance(message, Shutdown):
                return
            if isinstance(message, Health):
                await write_message(writer, message.model_copy(update={"ready": True}))
            elif isinstance(message, Invoke):
                descriptor = startup.manifest.descriptors[0]
                valid = (
                    message.protocol == startup.manifest.protocol
                    and message.capability_id == descriptor.id
                    and message.version == descriptor.version
                    and message.descriptor_revision == descriptor.revision
                    and set(message.payload) == {"text"}
                    and isinstance(message.payload["text"], str)
                    and len(message.payload["text"]) <= 4096
                )
                await write_message(writer, Result(
                    protocol=startup.manifest.protocol, call_id=message.call_id,
                    payload={"text": message.payload["text"]} if valid else None,
                    error=None if valid else "invalid_payload",
                ))
    finally:
        writer.close()
        await writer.wait_closed()


def main() -> None:
    """Read startup authority only from the inherited descriptor."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-fd", type=int, required=True)
    arguments = parser.parse_args()
    asyncio.run(serve(arguments.config_fd))


if __name__ == "__main__":
    main()
'''


def initialize(destination: Path, name: str) -> None:
    """Create a new project, refusing replacement of an existing destination."""
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", name):
        raise Denied("Project name must be a lowercase Python package name")
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise Denied("Project destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".skulk-cap-init-", dir=destination.parent))
    try:
        package = stage / name
        package.mkdir()
        (package / "__init__.py").write_text('"""Model-free capability example."""\n')
        (package / "app.py").write_text(CHILD)
        schema: dict[str, JsonValue] = {
            "type": "object",
            "properties": {"text": {"type": "string", "maxLength": 4096}},
            "required": ["text"],
            "additionalProperties": False,
        }
        descriptor = Descriptor(
            id=name.replace("_", ".") + ".echo",
            version="1.0.0",
            title="Echo",
            description="Return supplied text without a model or external request.",
            input_schema=schema,
            output_schema=schema,
        )
        manifest = Manifest(
            bundle_id=name.replace("_", "."),
            bundle_version="0.1.0",
            executable="bundle.pyz",
            executable_sha256="0" * 64,
            arguments=(),
            descriptors=(descriptor,),
            runtime_dependencies=("skulk-capability-sdk",),
        )
        (stage / "manifest.json").write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n"
        )
        (stage / "capability.toml").write_text(
            f'package = "{name}"\npackage_root = "{name}"\nentry = "{name}.app"\n'
            'dependencies = ["skulk-capability-sdk>=0.5.5,<0.6"]\n'
        )
        (stage / "pyproject.toml").write_text(
            f'[project]\nname = "{name.replace("_", "-")}"\nversion = "0.1.0"\n'
            'requires-python = ">=3.13"\n'
            'dependencies = ["skulk-capability-sdk>=0.5.5,<0.6"]\n\n'
            "[tool.uv.sources]\nskulk-capability-sdk = { url = "
            '"https://github.com/Foxlight-Foundation/skulk-capability-sdk/releases/'
            'download/v0.5.6/skulk_capability_sdk-0.5.6-py3-none-any.whl" }\n'
        )
        (stage / ".gitignore").write_text(".venv/\n__pycache__/\n.skulk-cap/\ndist/\n")
        (stage / "README.md").write_text(
            f"# {name}\n\nA model/GPU-free managed capability.\n\n"
            "From this directory, run `skulk-cap run`. In another terminal run "
            '`skulk-cap invoke --input \'{"text":"Hello"}\'` and `skulk-cap debug`.\n\n'
            "`skulk-cap watch` restarts source only between requests. "
            "Stop with Ctrl-C. "
            "Local development has no Fabric identity or grants. "
            "Use the public SDK installation guide for signing "
            "and real host admission.\n"
        )
        # Reserve the destination exclusively. Interrupted initialization leaves
        # its partial project visible rather than deleting unowned new files.
        destination.mkdir()
        for path in stage.iterdir():
            os.rename(path, destination / path.name)
    finally:
        shutil.rmtree(stage)
