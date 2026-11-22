"""Real TypeScript job applications share native contracts and Python custody."""

import asyncio
import json
import os
import shutil
import signal
import sqlite3
from pathlib import Path

import pytest
from pydantic import JsonValue, TypeAdapter

from skulk_capability_sdk.contracts import OperationBounds
from skulk_capability_sdk.operations import (
    OperationLog,
    OperationPage,
    OperationPlan,
    OperationStatus,
)
from skulk_capability_sdk.storage import HostLock
from tests_native.test_application import running
from tests_native.test_jobs import (
    Backend,
    application,
    definition,
    request,
    state,
)
from tests_typescript.test_node_application import NodeApplication

ROOT = Path(__file__).parents[1]
_OBJECT = TypeAdapter(dict[str, JsonValue])


def source(
    directory: Path,
    *,
    observe: str = "running",
    submit: str = "return id;",
    result: str = "{value:2}",
) -> str:
    """Build only a synthetic, account-free public SDK job adapter."""
    spec = definition()
    options: dict[str, JsonValue] = {
        "id": "fixture.jobs",
        "version": "1.0.0",
        "title": "Synthetic jobs",
        "description": "Account-free inference job fixture",
        "inputSchema": {
            "type": "object",
            "properties": {"value": {"type": "number"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        "resultSchema": spec.result_schema(),
    }
    return (
        "const spec = JobDefinition.create("
        + json.dumps(options)
        + ");\n"
        + """let cancelled = false;
const jobs = new DurableJobs(spec, {
  async submit(id, input, signal) { SUBMIT },
  async observe(reference, signal) {
    return {state: cancelled ? 'cancelled' : OBSERVED,
      result: OBSERVED === 'succeeded' ? RESULT : null};
  },
  async cancel(reference, signal) { cancelled = true; },
}, {pollSeconds:0.01, adapterSeconds:0.2,
  plan: async (input, signal) => ({plan_digest:'a'.repeat(64),summary:'Synthetic',
    expires_at:2147483648,input})});
const app = new CapabilityApp({setup:(startup, signal) =>
  jobs.serve(startup,{directory:ROOT,signal}),ready:()=>jobs.ready});
jobs.register(app);""".replace("OBSERVED", json.dumps(observe))
        .replace("RESULT", result)
        .replace("SUBMIT", submit)
        .replace("ROOT", json.dumps(str(directory)))
    )


def test_job_model_templates_match_python_models() -> None:
    """The JS envelopes must track actual Python models, including defaults."""
    templates = _OBJECT.validate_json(
        (ROOT / "packages/typescript/src/job-schema-models.json").read_bytes()
    )
    assert templates == {
        "status": OperationStatus.model_json_schema(),
        "page": OperationPage.model_json_schema(),
        "log": OperationLog.model_json_schema(),
        "plan": OperationPlan.model_json_schema(),
    }


async def test_node_job_definition_matches_python() -> None:
    """Public constructors in both languages produce the same signed descriptors."""
    node = shutil.which("node")
    assert node is not None
    package = Path(
        os.environ.get(
            "SKULK_TYPESCRIPT_ENTRY",
            str(ROOT / "packages/typescript/dist/index.js"),
        )
    ).resolve()
    code = source(Path("/unused"))
    code = code[: code.index("let cancelled")]
    process = await asyncio.create_subprocess_exec(
        node,
        "--input-type=module",
        "-e",
        f"import {{JobDefinition}} from {json.dumps(package.as_uri())};\n"
        + code
        + "process.stdout.write(JSON.stringify(spec.descriptors()));",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    output, error = await process.communicate()
    assert process.returncode == 0, error.decode()
    assert json.loads(output) == definition().descriptors()


async def test_node_durable_jobs_caller_isolation_replay_plan_and_cancel(
    tmp_path: Path,
) -> None:
    """Use real native calls, journal workers, private caller pages and cancellation."""
    app = NodeApplication(tmp_path, source(tmp_path / "jobs"))
    async with running(
        app,
        descriptors=definition().descriptors(),
        operations=OperationBounds().model_dump(mode="json"),
    ) as peer:
        start: dict[str, JsonValue] = {
            "op": "start",
            "operation_id": "kept",
            "input": {"value": 1},
        }
        frame, _ = await request(peer, "work", start)
        assert frame["error"] is None
        await state(peer, "kept", "running")
        frame, _ = await request(peer, "work", {**start, "input": {"value": 1.0}})
        assert frame["error"] is None
        frame, _ = await request(peer, "work", {**start, "input": {"value": 2}})
        assert frame["error"] is not None
        for op in ("status", "log"):
            frame, _ = await request(
                peer,
                "read",
                {"op": op, "operation_id": "kept"},
                caller="client:other",
            )
            assert frame["error"] is not None
        frame, page = await request(peer, "read", {"op": "list"}, caller="client:other")
        assert frame["error"] is None and page["operations"] == []
        frame, estimate = await request(
            peer, "work", {"op": "plan", "input": {"value": 1}}
        )
        assert frame["error"] is None and estimate["summary"] == "Synthetic"
        frame, page = await request(peer, "read", {"op": "list"})
        assert frame["error"] is None
        parsed = OperationPage.model_validate_json(json.dumps(page))
        assert parsed.active == 1 and parsed.operations[0].backend_reference is None
        assert parsed.operations[0].queue_position is None
        frame, log = await request(
            peer, "read", {"op": "log", "operation_id": "kept", "offset": 0}
        )
        assert frame["error"] is None and log["operation_id"] == "kept"
        frame, _ = await request(peer, "work", {"op": "cancel", "operation_id": "kept"})
        assert frame["error"] is None
        await state(peer, "kept", "cancelled")
        await peer.health()


async def test_python_node_python_restart_uses_same_state(tmp_path: Path) -> None:
    """Accepted backend references and replay reservations survive both languages."""
    directory, spec, backend = tmp_path / "jobs", definition(), Backend()
    bounds = OperationBounds().model_dump(mode="json")
    app, _ = application(directory, backend, spec)
    start: dict[str, JsonValue] = {
        "op": "start",
        "operation_id": "kept",
        "input": {"value": 1},
    }
    async with running(app, descriptors=spec.descriptors(), operations=bounds) as peer:
        frame, _ = await request(peer, "work", start)
        assert frame["error"] is None
        await state(peer, "kept", "running")
    node = NodeApplication(tmp_path, source(directory, observe="succeeded"))
    async with running(node, descriptors=spec.descriptors(), operations=bounds) as peer:
        completed = await state(peer, "kept", "succeeded")
        assert completed["result"] == {"value": 2}
        frame, _ = await request(peer, "work", start)
        assert frame["error"] is None
    app, _ = application(directory, backend, spec)
    async with running(app, descriptors=spec.descriptors(), operations=bounds) as peer:
        completed = await state(peer, "kept", "succeeded")
        assert completed["result"] == {"value": 2}
        frame, _ = await request(peer, "work", start)
        assert frame["error"] is None
    assert len(backend.submissions) == 1


async def wait_file(path: Path) -> str:
    """Wait for an actual adapter effect, with a bounded test deadline."""
    async with asyncio.timeout(3):
        while True:
            if path.exists():
                break
            await asyncio.sleep(0.01)
    return path.read_text()


async def direct_job_script(tmp_path: Path, body: str) -> bytes:
    """Own a separate public SDK job lifecycle through complete process exit."""
    node = shutil.which("node")
    assert node is not None
    package = Path(
        os.environ.get(
            "SKULK_TYPESCRIPT_ENTRY",
            str(ROOT / "packages/typescript/dist/index.js"),
        )
    ).resolve()
    script = tmp_path / "direct.mjs"
    script.write_text(
        "import {CapabilityApp,ApplicationStartup,JobDefinition,DurableJobs,"
        f"SubmissionRefused}} from {json.dumps(package.as_uri())};\n"
        + source(tmp_path / "jobs")
        + "\nconst startup = new ApplicationStartup({node_id:'node',host_id:'host',"
        + "configuration:null,serve_host:null,operations:"
        + json.dumps(OperationBounds().model_dump(mode="json"))
        + ",descriptors:spec.descriptors()});\n"
        + body
    )
    process = await asyncio.create_subprocess_exec(
        node,
        str(script),
        start_new_session=True,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    try:
        output, error = await asyncio.wait_for(process.communicate(), 6)
        assert process.returncode == 0, error.decode()
        return output
    finally:
        if process.returncode is None:
            os.killpg(process.pid, signal.SIGKILL)
            await process.wait()


async def test_cancelled_origin_allocation_is_drained_before_reservation(
    tmp_path: Path,
) -> None:
    """A real SQLite lock does not block health or permit a late accepted job."""
    directory = tmp_path / "jobs"
    app = NodeApplication(tmp_path, source(directory))
    async with running(
        app,
        descriptors=definition().descriptors(),
        operations=OperationBounds().model_dump(mode="json"),
    ) as peer:
        with sqlite3.connect(directory / "origins.sqlite") as database:
            database.execute("BEGIN IMMEDIATE")
            try:
                await peer.invoke(
                    "blocked-allocation",
                    capability_id="fixture.jobs.work",
                    payload=b'{"op":"start","operation_id":"cancelled","input":{"value":1}}',
                )
                await asyncio.sleep(0.1)
                await peer.health()
                await peer.send("cancel", call_id="blocked-allocation")
                await peer.send("cancel", call_id="blocked-allocation")
                await peer.health()
            finally:
                database.rollback()
        frame, _ = await asyncio.wait_for(peer.receive(), 3)
        assert frame["error"] == "cancelled"
        for file, table in (
            ("origins", "native_job_origins"),
            ("operations", "reservations"),
        ):
            with sqlite3.connect(directory / f"{file}.sqlite") as database:
                assert database.execute(f"SELECT count(*) FROM {table}").fetchone() == (
                    0,
                )


async def test_shutdown_holds_shared_custody_through_real_adapter_cleanup(
    tmp_path: Path,
) -> None:
    """Owner shutdown cannot expose state before a cancelled adapter drains."""
    directory = tmp_path / "jobs"
    entered, cleaning, release = (
        tmp_path / name for name in ("entered", "cleaning", "release")
    )
    submit = (
        "const fs = await import('node:fs/promises');"
        f"await fs.writeFile({json.dumps(str(entered))}, 'entered');"
        "try { await new Promise(resolve => {if(signal.aborted)resolve();"
        "else signal.addEventListener('abort',resolve,{once:true});}); }"
        f"finally {{ await fs.writeFile({json.dumps(str(cleaning))},'cleaning');"
        f"while (!(await fs.stat({json.dumps(str(release))}).catch(()=>null))) "
        "await new Promise(resolve=>setTimeout(resolve,10)); }"
        "return id;"
    )
    app = NodeApplication(tmp_path, source(directory, submit=submit))
    async with running(
        app,
        descriptors=definition().descriptors(),
        operations=OperationBounds().model_dump(mode="json"),
    ) as peer:
        await request(peer, "work", {"op": "start", "input": {"value": 1}})
        await wait_file(entered)
        await peer.send("shutdown")
        try:
            await wait_file(cleaning)
            with pytest.raises(BlockingIOError):
                HostLock(directory / "custody")
            assert not peer.app_task.done()
        finally:
            release.write_text("release")
        await asyncio.wait_for(asyncio.shield(peer.app_task), 3)
    lock = HostLock(directory / "custody")
    lock.close()


async def test_timeout_drains_adapter_before_delivering_replacement_callback(
    tmp_path: Path,
) -> None:
    """Expired queued callbacks cannot start late while earlier cleanup is blocked."""
    directory = tmp_path / "jobs"
    cleaning, release, replacement = (
        tmp_path / name for name in ("cleaning", "release", "replacement")
    )
    submit = (
        "const fs=await import('node:fs/promises');if(input.value===1){"
        "await new Promise(resolve=>signal.addEventListener('abort',resolve,"
        "{once:true}));"
        f"await fs.writeFile({json.dumps(str(cleaning))},'cleaning');"
        f"while(!(await fs.stat({json.dumps(str(release))}).catch(()=>null)))"
        "await new Promise(resolve=>setTimeout(resolve,10));}else{"
        f"await fs.writeFile({json.dumps(str(replacement))},String(input.value));"
        "}return id;"
    )
    app = NodeApplication(tmp_path, source(directory, submit=submit))
    async with running(
        app,
        descriptors=definition().descriptors(),
        operations=OperationBounds().model_dump(mode="json"),
    ) as peer:
        for identifier, value in (("abandoned", 1), ("expired", 2)):
            frame, _ = await request(
                peer,
                "work",
                {"op": "start", "operation_id": identifier, "input": {"value": value}},
            )
            assert frame["error"] is None
        try:
            await wait_file(cleaning)
            await asyncio.sleep(0.3)
            await peer.health()
            assert not replacement.exists()
        finally:
            release.write_text("release")
        await state(peer, "expired", "interrupted")
        frame, _ = await request(
            peer,
            "work",
            {"op": "start", "operation_id": "live", "input": {"value": 3}},
        )
        assert frame["error"] is None
        assert await wait_file(replacement) == "3"


async def test_saturated_reads_cannot_strand_cancelled_origin_cleanup(
    tmp_path: Path,
) -> None:
    """Bounded RPC refusal preserves health and a cleanup slot under real contention."""
    directory = tmp_path / "jobs"
    app = NodeApplication(tmp_path, source(directory))
    async with running(
        app,
        concurrency=16,
        descriptors=definition().descriptors(),
        operations=OperationBounds().model_dump(mode="json"),
    ) as peer:
        results: dict[str, JsonValue] = {}
        with sqlite3.connect(directory / "origins.sqlite") as database:
            database.execute("BEGIN IMMEDIATE")
            try:
                await peer.invoke(
                    "blocked",
                    capability_id="fixture.jobs.work",
                    payload=b'{"op":"start","input":{"value":1}}',
                )
                for index in range(15):
                    await peer.invoke(
                        f"read-{index}",
                        capability_id="fixture.jobs.read",
                        payload=b'{"op":"list"}',
                    )
                await peer.send("cancel", call_id="blocked")
                await peer.send("health", nonce="pressure", ready=False, surfaces=[])
                async with asyncio.timeout(1):
                    while True:
                        frame, _ = await peer.receive()
                        if frame["kind"] == "health":
                            assert frame["ready"] is True
                            break
                        results[str(frame["call_id"])] = frame["error"]
            finally:
                database.rollback()
        async with asyncio.timeout(3):
            while len(results) < 16:
                frame, _ = await peer.receive()
                results[str(frame["call_id"])] = frame["error"]
        assert results["blocked"] == "cancelled"
        await peer.health()
        for file, table in (
            ("origins", "native_job_origins"),
            ("operations", "reservations"),
        ):
            with sqlite3.connect(directory / f"{file}.sqlite") as database:
                assert database.execute(f"SELECT count(*) FROM {table}").fetchone() == (
                    0,
                )


@pytest.mark.parametrize("result", ["{value:'wrong'}", "{value:NaN}"])
async def test_invalid_backend_success_is_a_definite_failure(
    tmp_path: Path, result: str
) -> None:
    """Schema violations and nonportable values cannot remain successful or retry."""
    app = NodeApplication(
        tmp_path, source(tmp_path / "jobs", observe="succeeded", result=result)
    )
    async with running(
        app,
        descriptors=definition().descriptors(),
        operations=OperationBounds().model_dump(mode="json"),
    ) as peer:
        await request(
            peer,
            "work",
            {"op": "start", "operation_id": "invalid", "input": {"value": 1}},
        )
        value = await state(peer, "invalid", "failed")
        assert value["result"] is None
        assert value["error"] == "job result does not satisfy its native schema"


async def test_helper_process_death_never_replays_ambiguous_submission(
    tmp_path: Path,
) -> None:
    """Kill the actual native worker process after an effect, then reopen in Python."""
    directory, entered = tmp_path / "jobs", tmp_path / "entered"
    submit = (
        "const fs = await import('node:fs/promises');"
        f"await fs.writeFile({json.dumps(str(entered))},id);"
        "await new Promise(resolve=>signal.addEventListener('abort',resolve,"
        "{once:true}));return id;"
    )
    app = NodeApplication(tmp_path, source(directory, submit=submit))
    bounds = OperationBounds().model_dump(mode="json")
    async with running(
        app, descriptors=definition().descriptors(), operations=bounds
    ) as peer:
        await request(
            peer,
            "work",
            {"op": "start", "operation_id": "uncertain", "input": {"value": 1}},
        )
        await wait_file(entered)
        assert app.process is not None
        scan = await asyncio.create_subprocess_exec(
            "ps", "-A", "-o", "pid=,ppid=", stdout=asyncio.subprocess.PIPE
        )
        output, _ = await scan.communicate()
        children = [
            int(pid)
            for pid, parent in (line.split() for line in output.decode().splitlines())
            if int(parent) == app.process.pid
        ]
        assert len(children) == 1
        os.kill(children[0], signal.SIGKILL)
        async with asyncio.timeout(3):
            while True:
                await peer.send("health", nonce="failed", ready=False, surfaces=[])
                frame, _ = await peer.receive()
                if frame["ready"] is False:
                    break
                await asyncio.sleep(0.01)
    backend = Backend()
    replacement, _ = application(directory, backend, definition())
    async with running(
        replacement, descriptors=definition().descriptors(), operations=bounds
    ) as peer:
        value = await state(peer, "uncertain", "interrupted")
        assert value["submission"] == "uncertain"
        frame, _ = await request(
            peer,
            "work",
            {"op": "start", "operation_id": "uncertain", "input": {"value": 1}},
        )
        assert frame["error"] is None and backend.submissions == []


@pytest.mark.parametrize("store", ["operations", "origins"])
@pytest.mark.parametrize("damage", ["missing", "empty", "corrupt", "binding"])
async def test_damaged_state_refused_before_native_recovery(
    tmp_path: Path, store: str, damage: str
) -> None:
    """An initialized Node facade cannot silently recreate either durable file."""
    body = (
        "const resource=await jobs.serve(startup,{directory:ROOT});"
        "await resource.close();"
    )
    body = body.replace("ROOT", json.dumps(str(tmp_path / "jobs")))
    await direct_job_script(tmp_path, body)
    path = tmp_path / "jobs" / f"{store}.sqlite"
    if damage == "missing":
        path.unlink()
    elif damage in {"empty", "corrupt"}:
        path.write_bytes(b"" if damage == "empty" else b"not a database")
    else:
        with sqlite3.connect(path) as database:
            database.execute("UPDATE native_job_binding SET binding_digest='changed'")
    before = path.read_bytes() if path.exists() else None
    primary = tmp_path / "jobs/operations.sqlite"
    primary_before = primary.read_bytes() if primary.exists() else None
    refused = (
        "let refused=false;try {" + body + "}catch{refused=true;}"
        "if(!refused)throw Error('damaged state recreated');"
        "process.stdout.write('refused');"
    )
    assert await direct_job_script(tmp_path, refused) == b"refused"
    assert (path.read_bytes() if path.exists() else None) == before
    assert (primary.read_bytes() if primary.exists() else None) == primary_before


async def test_queue_refusal_reclaims_metadata_without_forgetting_reservations(
    tmp_path: Path,
) -> None:
    """More refused identities than the declared quota cannot exhaust caller state."""
    directory = tmp_path / "jobs"
    app = NodeApplication(tmp_path, source(directory))
    async with running(
        app,
        descriptors=definition().descriptors(),
        operations=OperationBounds(max_queued=1, reservations=64).model_dump(
            mode="json"
        ),
    ) as peer:
        for identifier, expected in (("active", "running"), ("queued", "queued")):
            frame, _ = await request(
                peer,
                "work",
                {"op": "start", "operation_id": identifier, "input": {"value": 1}},
            )
            assert frame["error"] is None
            await state(peer, identifier, expected)
        for index in range(70):
            frame, _ = await request(
                peer,
                "work",
                {
                    "op": "start",
                    "operation_id": f"refused-{index}",
                    "input": {"value": 1},
                },
            )
            assert frame["error"] is not None
        for file, table in (
            ("origins", "native_job_origins"),
            ("operations", "reservations"),
        ):
            with sqlite3.connect(directory / f"{file}.sqlite") as database:
                assert database.execute(f"SELECT count(*) FROM {table}").fetchone() == (
                    2,
                )
        await peer.health()


async def test_caller_pagination_and_pruned_reservation_survive_node_restart(
    tmp_path: Path,
) -> None:
    """Index batches skip retired rows while native reservations prevent replay."""
    directory = tmp_path / "jobs"
    spec, bounds = definition(), OperationBounds(retain=22).model_dump(mode="json")
    app = NodeApplication(tmp_path, source(directory, observe="succeeded"))
    async with running(app, descriptors=spec.descriptors(), operations=bounds) as peer:
        for caller, count in (("client:first", 24), ("client:other", 3)):
            for index in range(count):
                identifier = f"job-{index}"
                frame, _ = await request(
                    peer,
                    "work",
                    {
                        "op": "start",
                        "operation_id": identifier,
                        "input": {"value": 1},
                    },
                    caller=caller,
                )
                assert frame["error"] is None
                await state(peer, identifier, "succeeded", caller=caller)
        _, first = await request(peer, "read", {"op": "list"})
        page = OperationPage.model_validate_json(json.dumps(first))
        assert page.next_offset == 16 and len(page.operations) == 16
        _, second = await request(peer, "read", {"op": "list", "offset": 16})
        tail = OperationPage.model_validate_json(json.dumps(second))
        assert tail.next_offset is None and page.active == page.outstanding == 0
        assert [item.operation_id for item in (*page.operations, *tail.operations)] == [
            f"job-{index}" for index in range(23, 4, -1)
        ]
        _, other = await request(peer, "read", {"op": "list"}, caller="client:other")
        assert len(OperationPage.model_validate_json(json.dumps(other)).operations) == 3
    replacement = NodeApplication(tmp_path, source(directory, observe="succeeded"))
    async with running(
        replacement, descriptors=spec.descriptors(), operations=bounds
    ) as peer:
        frame, _ = await request(
            peer,
            "work",
            {"op": "start", "operation_id": "job-0", "input": {"value": 1}},
        )
        assert frame["error"] is not None
        with sqlite3.connect(directory / "operations.sqlite") as database:
            assert database.execute("SELECT count(*) FROM reservations").fetchone() == (
                27,
            )


@pytest.mark.parametrize("change", ["bounds", "node", "marker"])
async def test_job_binding_changes_fail_before_recovery(
    tmp_path: Path, change: str
) -> None:
    """An owner update cannot silently rebind initialized state or recreate a marker."""
    body = (
        "const resource=await jobs.serve(startup,{directory:ROOT});"
        "await resource.close();"
    ).replace("ROOT", json.dumps(str(tmp_path / "jobs")))
    await direct_job_script(tmp_path, body)
    primary = tmp_path / "jobs/operations.sqlite"
    before = primary.read_bytes()
    if change == "marker":
        (tmp_path / "jobs/native-jobs.json").unlink()
        update = ""
    else:
        update = (
            "const changed=new ApplicationStartup({node_id:"
            + ("'other'" if change == "node" else "'node'")
            + ",host_id:'host',configuration:null,serve_host:null,"
            + "descriptors:spec.descriptors(),operations:"
            + json.dumps(
                OperationBounds(retain=1 if change == "bounds" else 64).model_dump(
                    mode="json"
                )
            )
            + "});"
        )
        body = body.replace("serve(startup,", "serve(changed,")
    result = await direct_job_script(
        tmp_path,
        update
        + "let refused=false;try {"
        + body
        + "}catch{refused=true;}if(!refused)throw Error('state rebound');"
        + "process.stdout.write('refused');",
    )
    assert result == b"refused" and primary.read_bytes() == before


@pytest.mark.parametrize("kind", ["symlink", "fifo", "public"])
async def test_unsafe_job_marker_is_never_followed_or_blocking(
    tmp_path: Path, kind: str
) -> None:
    """Private metadata admission rejects special files before opening a journal."""
    directory = tmp_path / "jobs"
    directory.mkdir(mode=0o700)
    marker = directory / "native-jobs.json"
    if kind == "symlink":
        target = tmp_path / "target"
        target.write_text("must not be read or changed")
        marker.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(marker, mode=0o600)
    else:
        marker.write_text("{}")
        marker.chmod(0o644)
    assert (
        await direct_job_script(
            tmp_path,
            "let refused=false;try {await jobs.serve(startup,{directory:ROOT});}"
            "catch{refused=true;}if(!refused)throw Error('unsafe marker admitted');"
            "process.stdout.write('refused');".replace(
                "ROOT", json.dumps(str(directory))
            ),
        )
        == b"refused"
    )
    assert not (directory / "operations.sqlite").exists()


async def test_python_owner_fence_blocks_node_before_state_inspection(
    tmp_path: Path,
) -> None:
    """Node must share the real Python facade fence, not only the inner journal lock."""
    directory = tmp_path / "jobs"
    directory.mkdir(mode=0o700)
    fence = HostLock(directory / "custody")
    try:
        assert (
            await direct_job_script(
                tmp_path,
                "let refused=false;try {await jobs.serve(startup,{directory:ROOT});}"
                "catch{refused=true;}"
                "if(!refused)throw Error('concurrent open admitted');"
                "process.stdout.write('refused');".replace(
                    "ROOT", json.dumps(str(directory))
                ),
            )
            == b"refused"
        )
        assert not (directory / "native-jobs.json").exists()
        assert not (directory / "operations.sqlite").exists()
    finally:
        fence.close()


async def test_cancelled_late_acquisition_releases_fence_without_opening_state(
    tmp_path: Path,
) -> None:
    """A setup AbortSignal remains effective when actual lock acquisition is delayed."""
    directory = tmp_path / "jobs"
    directory.mkdir(mode=0o700)
    fence = HostLock(directory / "custody")
    task = asyncio.create_task(
        direct_job_script(
            tmp_path,
            "const controller=new AbortController();"
            "setTimeout(()=>controller.abort(),10);"
            "let refused=false;try {await jobs.serve(startup,{directory:ROOT,"
            "signal:controller.signal});}catch{refused=true;}"
            "if(!refused)throw Error('cancelled acquisition retained');"
            "process.stdout.write('cancelled');".replace(
                "ROOT", json.dumps(str(directory))
            ),
        )
    )
    try:
        await asyncio.sleep(0.5)
        fence.close()
        assert await task == b"cancelled"
    finally:
        fence.close()
        await asyncio.gather(task, return_exceptions=True)
    assert not (directory / "native-jobs.json").exists()
    fence = HostLock(directory / "custody")
    fence.close()
