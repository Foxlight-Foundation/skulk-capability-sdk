"""Compare independently framed synthetic discovery goldens through native bindings."""

import argparse
import hashlib
import importlib
import json
import subprocess
from pathlib import Path
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from .portable_profile_cases import Response

ROOT = Path(__file__).resolve().parents[1]


class Native(Protocol):
    """Actual compiled codec, without a language-level validation fallback."""

    def discovery_profile_digest(self) -> str: ...
    def discovery_request(
        self, startup: str, context: str, parent: str, sequence: int
    ) -> bytes: ...
    def admit_discovery_request(self, startup: str, packet: bytes) -> bytes: ...
    def discovery_reply(
        self, startup: str, request: str, payload: bytes, error: str | None
    ) -> bytes: ...
    def discovery_response(
        self, startup: str, request: str, packet: bytes
    ) -> tuple[str | None, bytes]: ...
    def discovery_payload_size(self, source: str) -> int: ...


class Probe(BaseModel):
    """Closed synthetic input, including full exact raw request/reply bytes."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    action: Literal["request", "admit", "reply", "response", "hint"]
    startup: str
    context: str
    parent: str
    sequence: str
    source: str
    packet_hex: str
    payload_hex: str
    code: str | None


class Case(BaseModel):
    """One independently authored expected frame, sanitized query or refusal."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    identity: str = Field(alias="id")
    request: Probe
    canonical: str | None
    error: str | None

    def expected(self) -> Response:
        """Return the independent complete expected observation."""
        return Response(canonical=self.canonical, error=self.error)


class Fixture(BaseModel):
    """Public synthetic evidence; it is never active caller or installation trust."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    profile: Literal["skulk.discovery/1"]
    profile_digest: str
    scope: str
    cases: tuple[Case, ...]


def fixture() -> Fixture:
    """Read maintained golden bytes, without opening runtime or credential state."""
    return Fixture.model_validate_json(
        (ROOT / "kernel/contracts/discovery-v1-cases.json").read_bytes()
    )


def python_case(native: Native, case: Case) -> Response:
    """Observe only actual compiled framing and startup matching."""
    p = case.request
    try:
        match p.action:
            case "request":
                value = native.discovery_request(
                    p.startup, p.context, p.parent, int(p.sequence)
                ).hex()
            case "admit":
                value = native.admit_discovery_request(
                    p.startup, bytes.fromhex(p.packet_hex)
                ).decode()
            case "reply":
                value = native.discovery_reply(
                    p.startup, p.source, bytes.fromhex(p.payload_hex), p.code
                ).hex()
            case "response":
                code, body = native.discovery_response(
                    p.startup, p.source, bytes.fromhex(p.packet_hex)
                )
                value = json.dumps(
                    {"code": code, "payload_hex": body.hex()},
                    sort_keys=True,
                    separators=(",", ":"),
                )
            case "hint":
                value = str(native.discovery_payload_size(p.source))
        return Response(canonical=value, error=None)
    except (ValueError, OverflowError):
        return Response(canonical=None, error="discovery_invalid")


def main() -> None:
    """Require every full expected byte observation before recording profile hashes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["rust", "python", "node"], default="rust")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--node-facade", type=Path)
    args = parser.parse_args()
    inputs = fixture()
    if args.backend == "python":
        native = cast(Native, importlib.import_module("_skulk_capability_kernel"))
        if native.discovery_profile_digest() != inputs.profile_digest:
            raise ValueError("Discovery profile differs")
        values = [python_case(native, c) for c in inputs.cases]
    else:
        command = (
            [str(ROOT / "kernel/target/debug/examples/discovery_probe")]
            if args.backend == "rust"
            else ["node", str(ROOT / "kernel/bindings/node/tools/discovery_probe.mjs")]
        )
        if args.backend == "node" and args.node_facade:
            command.append(args.node_facade.resolve().as_uri())
        result = subprocess.run(
            command,
            input="\n".join(c.request.model_dump_json() for c in inputs.cases) + "\n",
            text=True,
            capture_output=True,
            check=True,
            timeout=120,
            cwd=ROOT,
        )
        values = [
            Response.model_validate_json(line) for line in result.stdout.splitlines()
        ]
    if len(values) != len(inputs.cases):
        raise ValueError("Discovery observations are incomplete")
    observations: list[dict[str, str]] = []
    for case, result in zip(inputs.cases, values, strict=True):
        if result != case.expected():
            raise ValueError("Discovery disagrees at " + case.identity)
        observations.append(
            {
                "case": case.identity,
                "sha256": hashlib.sha256(result.model_dump_json().encode()).hexdigest(),
            }
        )
    report = {
        "backend": args.backend,
        "profile": inputs.profile,
        "profile_digest": inputs.profile_digest,
        "cases": len(values),
        "input_sha256": hashlib.sha256(
            "\n".join(
                json.dumps(c.request.model_dump(), sort_keys=True) for c in inputs.cases
            ).encode()
        ).hexdigest(),
        "observations": observations,
        "scope": inputs.scope,
        "runtime_adoption": False,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "observations"}))


if __name__ == "__main__":
    main()
