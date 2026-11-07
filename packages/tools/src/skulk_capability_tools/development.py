"""Private, machine-local author sessions using the public child protocol.

This harness has no Fabric identity or grants. Real host admission, installation
and cross-node tests remain separate; passing a local invocation proves neither.
"""

import asyncio
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from pydantic import JsonValue

from skulk_capability_sdk.contracts import (
    MAX_MANIFEST_BYTES,
    Denied,
    Health,
    Hello,
    Invoke,
    Manifest,
    Result,
    Shutdown,
    Startup,
    canonical,
    manifest_digest,
)
from skulk_capability_sdk.storage import HostLock, secure_directory
from skulk_capability_sdk.wire import ProtocolError, read_message, write_message

from .inspection import read_public
from .packaging import load_spec, validate_manifest

LOCAL_EXCHANGE_SECONDS = 90.0
"""Cover read(5), old shutdown(12), validation(3), startup(30), call(30), reply.

These maxima come from the current SDK Bounds contract. A watched restart is
preparation for a new invocation, and must not consume its declared call window.
"""


async def validate_payload(schema: dict[str, JsonValue], payload: JsonValue) -> None:
    """Bound local schema evaluation independently of the author's event loop."""
    raw = json.dumps([schema, payload], allow_nan=False).encode()
    if len(raw) > 524288:
        raise Denied("Local schema request exceeds its input budget")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "skulk_capability_tools._schema_worker",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        env={"PATH": os.defpath, "PYTHONUTF8": "1"},
    )
    try:
        async with asyncio.timeout(3):
            await process.communicate(raw)
        if process.returncode != 0:
            raise Denied("Payload violates its offline local schema")
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def project_manifest(project: Path) -> Manifest:
    """Read a project's public manifest and verify its packaging specification."""
    spec, _ = load_spec(project / "capability.toml")
    manifest = Manifest.model_validate_json(
        read_public(project / "manifest.json", MAX_MANIFEST_BYTES)
    )
    # Project validators execute in a fresh bounded process. A watched restart
    # cannot reuse cached author modules or inherit ambient provider credentials.
    validate_manifest(spec.model_copy(update={"validator": None}), manifest)
    if spec.validator is not None:
        raw = json.dumps([str(project), manifest.model_dump_json()]).encode()
        if len(raw) > 524288:
            raise Denied("Project validation exceeds its metadata budget")
        try:
            with tempfile.TemporaryDirectory(
                prefix="skulk-validator-bytecode-"
            ) as bytecode:
                subprocess.run(
                    [sys.executable, "-m", "skulk_capability_tools._manifest_worker"],
                    input=raw,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env={
                        "PATH": os.defpath,
                        "PYTHONUTF8": "1",
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTHONPYCACHEPREFIX": bytecode,
                    },
                    timeout=3,
                    check=True,
                )
        except (subprocess.SubprocessError, OSError) as error:
            raise Denied("Project manifest validation refused or timed out") from error
    return manifest


def _sources(project: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(project.rglob("*.py")):
        if any(part.startswith(".") for part in path.relative_to(project).parts):
            continue
        digest.update(str(path.relative_to(project)).encode())
        digest.update(path.read_bytes())
    for name in ("manifest.json", "capability.toml"):
        digest.update((project / name).read_bytes())
    return digest.hexdigest()


class DevelopmentChild:
    """Own one source child, authenticated handshake and bounded cleanup."""

    def __init__(self, project: Path, state: Path) -> None:
        self.project = project
        self.state = state.absolute()
        self.manifest = project_manifest(project)
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.server: asyncio.Server | None = None

    async def start(self) -> None:
        """Launch approved source with private inherited startup, then check hello."""
        spec, _ = load_spec(self.project / "capability.toml")
        connected: asyncio.Future[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = (
            asyncio.get_running_loop().create_future()
        )

        def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            if connected.done():
                writer.close()
            else:
                connected.set_result((reader, writer))

        path = self.state / "child.sock"
        path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(accept, path=str(path))
        self.server = server
        os.chmod(path, 0o600)
        token = secrets.token_hex(32)
        startup = Startup(
            socket=str(path),
            token=token,
            node_id="local-author",
            host_id="local-author",
            transport_id="local-author",
            manifest=self.manifest,
        )
        try:
            with tempfile.TemporaryFile() as inherited:
                inherited.write(canonical(startup))
                inherited.seek(0)
                # Startup secrets travel only through a descriptor. The child
                # inherits no management token, provider account or Fabric key.
                self.process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-c",
                    (
                        "import importlib,sys\n"
                        "importlib.import_module(sys.argv.pop(1)).main()"
                    ),
                    spec.entry,
                    *self.manifest.arguments,
                    "--config-fd",
                    str(inherited.fileno()),
                    pass_fds=(inherited.fileno(),),
                    cwd=self.project,
                    stdin=asyncio.subprocess.DEVNULL,
                    env={
                        "PATH": os.defpath,
                        "PYTHONUTF8": "1",
                        # A fresh nonexistent prefix plus disabled writes ignores
                        # timestamp-valid project caches without deleting files.
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTHONPYCACHEPREFIX": str(
                            self.state / ("bytecode-" + secrets.token_hex(16))
                        ),
                        "PYTHONPATH": os.pathsep.join(
                            str(path.resolve())
                            for path in (
                                self.project,
                                (self.project / spec.package_root).parent,
                                *(
                                    (self.project / item.package_root).parent
                                    for item in spec.source_packages
                                ),
                            )
                        ),
                    },
                )
            async with asyncio.timeout(self.manifest.bounds.startup_seconds):
                self.reader, self.writer = await connected
                hello = await read_message(self.reader)
                if hello != Hello(
                    protocol=self.manifest.protocol,
                    host_id=startup.host_id,
                    transport_node_id=startup.transport_id,
                    capability_node_id=startup.node_id,
                    bundle_id=self.manifest.bundle_id,
                    bundle_version=self.manifest.bundle_version,
                    token=token,
                    manifest_sha256=manifest_digest(self.manifest),
                ):
                    raise Denied("Local child handshake differs from the project")
                if not (await self.health()).ready:
                    raise Denied("Local child reports not ready")
        finally:
            server.close()
            # Python 3.13 also waits for accepted connections here. The
            # authenticated child keeps its connection until owned cleanup.
            path.unlink(missing_ok=True)

    async def health(self) -> Health:
        """Challenge actual readiness; a running process alone is insufficient."""
        if self.reader is None or self.writer is None:
            raise Denied("Local child is not connected")
        challenge = Health(
            protocol=self.manifest.protocol, nonce=secrets.token_hex(16), ready=False
        )
        async with asyncio.timeout(self.manifest.bounds.health_seconds):
            await write_message(self.writer, challenge)
            reply = await read_message(self.reader)
        if (
            not isinstance(reply, Health)
            or reply.nonce != challenge.nonce
            or reply.protocol != self.manifest.protocol
        ):
            raise Denied("Local readiness reply is uncorrelated")
        return reply

    async def invoke(self, request: Invoke) -> Result:
        """Validate exact descriptor and payload before one non-replayed invocation."""
        deadline = asyncio.get_running_loop().time() + request.remaining_seconds
        async with asyncio.timeout_at(deadline):
            return await self._invoke(request, deadline)

    async def _invoke(self, request: Invoke, deadline: float) -> Result:
        descriptor = next(
            (
                item
                for item in self.manifest.descriptors
                if item.id == request.capability_id and item.version == request.version
            ),
            None,
        )
        if (
            descriptor is None
            or descriptor.io_mode != "unary"
            or descriptor.revision != request.descriptor_revision
            or request.protocol != self.manifest.protocol
            or request.remaining_seconds > self.manifest.bounds.call_seconds
        ):
            raise Denied("Invocation differs from the exact local unary contract")
        await validate_payload(descriptor.input_schema, request.payload)
        if self.reader is None or self.writer is None:
            raise Denied("Local child is not connected")
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("Local invocation deadline expired before dispatch")
        await write_message(
            self.writer, request.model_copy(update={"remaining_seconds": remaining})
        )
        reply = await read_message(self.reader)
        if (
            not isinstance(reply, Result)
            or reply.call_id != request.call_id
            or reply.protocol != self.manifest.protocol
        ):
            raise Denied("Local invocation reply is uncorrelated")
        if reply.payload is not None and descriptor.output_schema is not None:
            await validate_payload(descriptor.output_schema, reply.payload)
        return reply

    async def close(self) -> None:
        """Stop and reap this child before its source can be restarted."""
        try:
            if self.writer is not None:
                try:
                    async with asyncio.timeout(self.manifest.bounds.shutdown_seconds):
                        await write_message(
                            self.writer, Shutdown(protocol=self.manifest.protocol)
                        )
                except (OSError, TimeoutError):
                    pass
                finally:
                    self.writer.close()
        finally:
            if self.process is not None:
                try:
                    async with asyncio.timeout(self.manifest.bounds.shutdown_seconds):
                        await self.process.wait()
                except TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            if self.server is not None:
                self.server.close_clients()
                async with asyncio.timeout(2):
                    await self.server.wait_closed()


@asynccontextmanager
async def local_child(project: Path, state: Path) -> AsyncGenerator[DevelopmentChild]:
    """Always reap the owned child, including a failed handshake or invocation."""
    child = DevelopmentChild(project, state)
    try:
        await child.start()
        yield child
    finally:
        await child.close()


async def serve(project: Path, state: Path, *, watch: bool = False) -> None:
    """Run a private local IPC session; watch restarts only between invocations."""
    project = project.resolve(strict=True)
    state = state.absolute()
    secure_directory(state)
    lock = HostLock(state)
    path = state / "session.sock"
    gate = asyncio.Lock()
    stop = asyncio.Event()
    try:
        # Record before imports/readiness: edits during warm-up must remain
        # pending for the next request, even when the child loaded older bytes.
        fingerprint = _sources(project)
        async with local_child(project, state) as initial:
            current = initial

            async def accept(
                reader: asyncio.StreamReader, writer: asyncio.StreamWriter
            ) -> None:
                nonlocal current, fingerprint
                if gate.locked():
                    writer.close()
                    return
                try:
                    async with asyncio.timeout(LOCAL_EXCHANGE_SECONDS), gate:
                        async with asyncio.timeout(5):
                            message = await read_message(reader)
                        source_fingerprint = _sources(project) if watch else fingerprint
                        if fingerprint != source_fingerprint:
                            # No invocation is retried; old-child cleanup must
                            # finish before a replacement receives new work.
                            await current.close()
                            current = DevelopmentChild(project, state)
                            await current.start()
                            fingerprint = source_fingerprint
                        if isinstance(message, Invoke):
                            response = await current.invoke(message)
                        elif isinstance(message, Health):
                            health = await current.health()
                            response = health.model_copy(
                                update={"nonce": message.nonce}
                            )
                        else:
                            raise Denied(
                                "Only local invocation and health are supported"
                            )
                        await write_message(writer, response)
                except (
                    Denied,
                    ProtocolError,
                    ValueError,
                    OSError,
                    TimeoutError,
                    asyncio.IncompleteReadError,
                ):
                    # An uncertain call poisons the session; closing it prevents
                    # a late reply from being mistaken for a subsequent result.
                    stop.set()
                finally:
                    writer.close()

            path.unlink(missing_ok=True)
            server = await asyncio.start_unix_server(accept, path=str(path))
            os.chmod(path, 0o600)
            print(json.dumps({"ready": True, "scope": "local-development"}), flush=True)
            try:
                await stop.wait()
            finally:
                server.close()
                server.close_clients()
                await server.wait_closed()
                async with gate:
                    await current.close()
    finally:
        path.unlink(missing_ok=True)
        lock.close()


async def call(
    project: Path,
    state: Path,
    *,
    capability: str | None,
    payload: dict[str, JsonValue] | None,
) -> Result | Health:
    """Make one local request; a lost response is reported, never repeated."""
    manifest = project_manifest(project)
    if payload is None:
        request = Health(
            protocol=manifest.protocol, nonce=secrets.token_hex(16), ready=False
        )
    else:
        descriptors = [
            item
            for item in manifest.descriptors
            if item.io_mode == "unary"
            and (capability is None or item.qualified_id == capability)
        ]
        if len(descriptors) != 1:
            raise Denied("Choose exactly one unary --capability ID@VERSION")
        descriptor = descriptors[0]
        request = Invoke(
            protocol=manifest.protocol,
            call_id=secrets.token_hex(16),
            capability_id=descriptor.id,
            version=descriptor.version,
            descriptor_revision=descriptor.revision,
            remaining_seconds=manifest.bounds.call_seconds,
            payload=payload,
        )
    async with asyncio.timeout(LOCAL_EXCHANGE_SECONDS):
        reader, writer = await asyncio.open_unix_connection(str(state / "session.sock"))
        try:
            await write_message(writer, request)
            response = await read_message(reader)
            if isinstance(response, Result) and isinstance(request, Invoke):
                if response.call_id == request.call_id:
                    return response
            elif isinstance(response, Health) and isinstance(request, Health):
                if response.nonce == request.nonce:
                    return response
            raise Denied("Uncorrelated local session reply")
        finally:
            writer.close()
