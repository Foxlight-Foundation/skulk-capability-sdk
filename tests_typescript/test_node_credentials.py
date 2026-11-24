"""Real Node processes use the installed, explicitly selected public SDK reader."""

import asyncio
import hashlib
import json
import os
import shlex
import sys
from pathlib import Path

import pytest
from pydantic import JsonValue

from skulk_capability_sdk._credential_bridge import reader_identity
from skulk_capability_sdk.storage import atomic_private
from tests.test_credential_reader import installation, replace
from tests_native.test_application import running
from tests_typescript.test_node_application import NodeApplication


def helper(executable: Path | None = None) -> dict[str, str]:
    """Select an explicit interpreter/program and its actual measured bytes."""
    path = executable or Path(sys.executable)
    info = path.stat()
    assert path.is_file() and info.st_uid in {0, os.getuid()}, (
        "Fixture requires a regular interpreter owned by root or this user",
        info.st_uid,
        os.getuid(),
    )
    assert not info.st_mode & 0o022, (
        "Fixture interpreter must not be group/world writable",
        oct(info.st_mode),
    )
    return {
        "executable": str(path),
        "executableSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def declarations(required: bool) -> list[dict[str, JsonValue]]:
    """Use actual native managed declarations with no credential values."""
    return [
        {
            "credential_id": "api-token",
            "required": required,
            "title": "Token",
            "description": "Synthetic credential input",
        }
    ]


def source(selected: dict[str, str] | None) -> str:
    """The app never exposes secret values or backend paths in its reply."""
    options = (
        "" if selected is None else "credentialHelper:" + json.dumps(selected) + ","
    )
    return """import {createHash} from 'node:crypto';
import {inspect} from 'node:util';
let reader;
const app = new CapabilityApp({OPTIONS
  async setup(startup) {reader=startup.credentials;}
});
app.unary('example', async call => {
  try {
    const value=await reader.read(call.payload.reference ?? 'api-token',
      {signal:call.signal});
    if (value===null) return {present:false};
    const raw=value.getSecretValue();
    const matched=createHash('sha256').update(raw).digest('hex')
      ===call.payload.expected;
    const label=new TextDecoder().decode(raw);
    const safe=![String(value),JSON.stringify(value),inspect(value)]
      .some(v=>v.includes(label));
    raw.fill(0);
    return {present:true,matched,safe};
  } catch(error) {
    if (error instanceof CredentialUnavailable) return {unavailable:true};
    if (error instanceof TypeError) return {undeclared:true};
    throw error;
  }
});""".replace("OPTIONS", options)


@pytest.mark.parametrize("required", [False, True])
async def test_node_reader_rotates_retires_and_restarts(
    tmp_path: Path, required: bool
) -> None:
    """Fresh reads use current aliases while private metadata stays off the API."""
    _, selected = installation(tmp_path / "store", required=required)
    configuration = tmp_path / "store/selection.json"
    script = source(helper())
    app = NodeApplication(tmp_path, script)
    async with running(
        app,
        credential_inputs=declarations(required),
        credential_config=str(configuration),
    ) as peer:
        await peer.invoke()
        frame, body = await peer.receive()
        assert frame["error"] is None
        assert json.loads(body) == (
            {"unavailable": True} if required else {"present": False}
        )
        for sequence, value in enumerate(
            (b"first-synthetic-token", b"replacement-token")
        ):
            replace(selected, value)
            expected = hashlib.sha256(value).hexdigest()
            await peer.invoke(
                f"rotated-{sequence}",
                payload=json.dumps({"expected": expected}).encode(),
            )
            frame, body = await peer.receive()
            assert frame["error"] is None and json.loads(body) == {
                "present": True,
                "matched": True,
                "safe": True,
            }
            assert str(configuration).encode() not in body and value not in body
            await peer.health()
        import sqlite3

        with sqlite3.connect(Path(selected.root) / "credentials.db") as connection:
            connection.execute("DELETE FROM aliases")
        await peer.invoke("retired")
        frame, body = await peer.receive()
        assert frame["error"] is None
        assert json.loads(body) == (
            {"unavailable": True} if required else {"present": False}
        )
        replace(selected, b"survived-restart")
    async with running(
        NodeApplication(tmp_path, script),
        credential_inputs=declarations(required),
        credential_config=str(configuration),
    ) as peer:
        await peer.invoke(
            payload=json.dumps(
                {"expected": hashlib.sha256(b"survived-restart").hexdigest()}
            ).encode()
        )
        frame, body = await peer.receive()
        assert frame["error"] is None and json.loads(body) == {
            "present": True,
            "matched": True,
            "safe": True,
        }


@pytest.mark.parametrize(
    "fault",
    [
        "no-helper",
        "wrong-program",
        "missing",
        "damaged",
        "native-unavailable",
        "fifo-program",
        "oversized-program",
    ],
)
async def test_node_reader_refuses_without_fallback(tmp_path: Path, fault: str) -> None:
    """Optional absence is distinct from a missing/mismatched selected backend."""
    _, selected = installation(tmp_path / "store", required=False)
    replace(selected, b"do-not-fallback-to-this-file")
    configuration = tmp_path / "store/selection.json"
    selected_helper = helper()
    if fault == "wrong-program":
        selected_helper["executableSha256"] = "0" * 64
    elif fault in {"fifo-program", "oversized-program"}:
        program = tmp_path / "unusable-program"
        if fault == "fifo-program":
            os.mkfifo(program, 0o600)
        else:
            with program.open("wb") as output:
                output.truncate(129 * 1024 * 1024)
        selected_helper = {"executable": str(program), "executableSha256": "0" * 64}
    elif fault == "missing":
        configuration.unlink()
    elif fault == "damaged":
        atomic_private(configuration, b'{"backend":"unsupported-secret-backend"}')
    elif fault == "native-unavailable":
        # Retain a plausible file value; selecting another backend must never read it.
        native = selected.model_copy(
            update={
                "backend": "linux_secret_service"
                if sys.platform == "darwin"
                else "macos_keychain"
            }
        )
        atomic_private(configuration, native.model_dump_json().encode())
    app = NodeApplication(
        tmp_path, source(None if fault == "no-helper" else selected_helper)
    )
    async with running(
        app, credential_inputs=declarations(False), credential_config=str(configuration)
    ) as peer:
        await peer.invoke()
        frame, body = await peer.receive()
        assert frame["error"] is None and json.loads(body) == {"unavailable": True}
        await peer.invoke("unknown", payload=b'{"reference":"another-account"}')
        frame, body = await peer.receive()
        assert frame["error"] is None and json.loads(body) == {"undeclared": True}
        await peer.health()
    if fault == "missing":
        assert not configuration.exists()


def fake_helper(path: Path, body: str) -> Path:
    """An explicitly measured synthetic helper for transport/custody faults only."""
    script = path.with_suffix(".py")
    script.write_text("import sys,json,os,time\n" + body)
    # Shebangs cannot represent an interpreter path containing spaces. exec
    # preserves this exact process/PID without creating a helper descendant.
    path.write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(sys.executable)
        + " "
        + shlex.quote(str(script))
        + "\n"
    )
    path.chmod(0o700)
    return path


def hello() -> str:
    """Use the actual source cohort, not a bypass in the Node reader."""
    value = {
        "protocol": 1,
        "sdk_version": "1.0.0",
        "reader_sha256": reader_identity(),
    }
    return "print(" + repr(json.dumps(value)) + ",flush=True)\n"


async def test_node_refuses_wrong_helper_before_sending_selection(
    tmp_path: Path,
) -> None:
    """A mismatching helper receives no private startup path or references."""
    received = tmp_path / "received"
    program = fake_helper(
        tmp_path / "wrong-reader",
        'print(json.dumps({"protocol":1,"sdk_version":"wrong",'
        '"reader_sha256":"0"*64}),flush=True)\n'
        "raw=sys.stdin.buffer.read()\nif raw:open("
        + repr(str(received))
        + ',"wb").write(raw)\n',
    )
    app = NodeApplication(tmp_path, source(helper(program)))
    async with running(
        app,
        credential_inputs=declarations(False),
        credential_config=str(tmp_path / "private-selection.json"),
    ) as peer:
        await peer.invoke()
        frame, body = await peer.receive()
        assert frame["error"] is None and json.loads(body) == {"unavailable": True}
    assert not received.exists()


async def test_node_cancel_reaps_reader_and_preserves_health(tmp_path: Path) -> None:
    """Repeated root cancellation owns actual process exit before the result."""
    pidfile = tmp_path / "reader-pid"
    program = fake_helper(
        tmp_path / "blocked-reader",
        hello()
        + "sys.stdin.buffer.read()\nopen("
        + repr(str(pidfile))
        + ",'w').write(str(os.getpid()))\ntime.sleep(60)\n",
    )
    app = NodeApplication(tmp_path, source(helper(program)))
    async with running(
        app,
        credential_inputs=declarations(False),
        credential_config=str(tmp_path / "private-selection.json"),
    ) as peer:
        await peer.invoke()
        for _ in range(300):
            if pidfile.exists():
                break
            await asyncio.sleep(0.01)
        assert pidfile.exists()
        pid = int(pidfile.read_text())
        assert app.process is not None
        assert os.getpgid(pid) == os.getpgid(app.process.pid)
        await peer.health()
        await peer.send("cancel", call_id="call-1")
        await peer.send("cancel", call_id="call-1")
        frame, body = await peer.receive()
        assert frame["error"] == "cancelled" and body == b""
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        await peer.health()


async def test_node_shutdown_owns_an_unawaited_read(tmp_path: Path) -> None:
    """Dropping the read Promise never detaches its helper from app cleanup."""
    pidfile = tmp_path / "dropped-pid"
    program = fake_helper(
        tmp_path / "dropped-reader",
        hello()
        + "sys.stdin.buffer.read()\nopen("
        + repr(str(pidfile))
        + ",'w').write(str(os.getpid()))\ntime.sleep(60)\n",
    )
    app = NodeApplication(
        tmp_path,
        """import {access} from 'node:fs/promises';
import {setTimeout as delay} from 'node:timers/promises';
let reader;
const app=new CapabilityApp({credentialHelper:HELPER,
async setup(startup){reader=startup.credentials;}});
app.unary('example',async ()=>{
  void reader.read('api-token');
  for(let i=0;i<300;i++){try{await access(PIDFILE);return {};}catch{await delay(10);}}
  throw Error('helper did not start');
});""".replace("HELPER", json.dumps(helper(program))).replace(
            "PIDFILE", json.dumps(str(pidfile))
        ),
    )
    async with running(
        app,
        credential_inputs=declarations(False),
        credential_config=str(tmp_path / "private-selection.json"),
    ) as peer:
        await peer.invoke()
        frame, _ = await peer.receive()
        assert frame["error"] is None
        pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize(
    "fault", ["required-null", "base64", "oversized", "extra", "flood"]
)
async def test_node_bad_helper_reply_is_sanitized_and_reaped(
    tmp_path: Path, fault: str
) -> None:
    """Even a selected synthetic peer cannot weaken required reads or frame bounds."""
    pidfile = tmp_path / "bad-reader-pid"
    payload = (
        None
        if fault in {"required-null", "extra"}
        else "!!!!"
        if fault == "base64"
        else "A" * 6000
    )
    output = (
        "print("
        + repr(json.dumps({"protocol": 1, "value": payload}))
        + ",flush=True)\n"
    )
    if fault == "extra":
        output += output
    elif fault == "flood":
        output = "sys.stdout.write('x'*17000)\nsys.stdout.flush()\ntime.sleep(60)\n"
    program = fake_helper(
        tmp_path / "bad-reader",
        hello()
        + "sys.stdin.buffer.read()\nopen("
        + repr(str(pidfile))
        + ",'w').write(str(os.getpid()))\n"
        + output,
    )
    app = NodeApplication(tmp_path, source(helper(program)))
    async with running(
        app,
        credential_inputs=declarations(fault == "required-null"),
        credential_config=str(tmp_path / "private-selection.json"),
    ) as peer:
        await peer.invoke()
        frame, body = await peer.receive()
        assert frame["error"] is None and json.loads(body) == {"unavailable": True}
        assert pidfile.exists()
        with pytest.raises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)
        await peer.health()


@pytest.mark.parametrize("phase", ["startup", "read"])
async def test_node_helper_timeout_reaps_while_control_health_continues(
    tmp_path: Path, phase: str
) -> None:
    """The real helper watchdog bounds stalled startup and stalled backend reads."""
    pidfile = tmp_path / "timed-out-pid"
    body = "open(" + repr(str(pidfile)) + ",'w').write(str(os.getpid()))\n"
    if phase == "read":
        body += hello() + "sys.stdin.buffer.read()\n"
    program = fake_helper(tmp_path / "timed-out-reader", body + "time.sleep(60)\n")
    app = NodeApplication(tmp_path, source(helper(program)))
    async with running(
        app,
        credential_inputs=declarations(False),
        credential_config=str(tmp_path / "private-selection.json"),
    ) as peer:
        stop = asyncio.Event()

        async def heartbeat() -> None:
            while not stop.is_set():
                await peer.send(
                    "health", nonce="credential-watchdog", ready=False, surfaces=[]
                )
                try:
                    await asyncio.wait_for(stop.wait(), 0.25)
                except TimeoutError:
                    pass

        await peer.invoke()
        task = asyncio.create_task(heartbeat())
        health = 0
        try:
            async with asyncio.timeout(20):
                while True:
                    frame, payload = await peer.receive()
                    if frame["kind"] == "result":
                        assert frame["error"] is None and json.loads(payload) == {
                            "unavailable": True
                        }
                        break
                    assert frame["kind"] == "health"
                    health += 1
        finally:
            stop.set()
            await task
        assert health >= 2 and pidfile.exists()
        with pytest.raises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)
