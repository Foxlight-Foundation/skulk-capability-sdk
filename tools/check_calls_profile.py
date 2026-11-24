"""Compare independent full-wire call observations through each native binding."""

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
    """Compiled framing API; no language-level validator fallback."""

    def calls_profile_digest(self) -> str: ...
    def calls_v2_profile_digest(self) -> str: ...
    def encode_call_v2_frame(self, source: str, payload: bytes) -> bytes: ...
    def decode_call_v2_frame(self, packet: bytes) -> tuple[bytes, bytes]: ...
    def encode_call_frame(self, source: str, payload: bytes) -> bytes: ...
    def decode_call_frame(self, packet: bytes) -> tuple[bytes, bytes]: ...


class Case(BaseModel):
    """Complete independent source/body/wire vector and expected observation."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    identity: str = Field(alias="id")
    profile: Literal["skulk.calls/2"] | None = None
    action: Literal["encode", "decode"]
    source: str
    payload_hex: str
    packet_hex: str
    canonical: str | None
    error: str | None

    def request(self) -> dict[str, str | None]:
        """Send exactly the same probe input to Rust, Python and Node."""
        request: dict[str, str | None] = {
            "action": self.action,
            "source": self.source,
            "payload_hex": self.payload_hex,
            "packet_hex": self.packet_hex,
        }
        if self.profile is not None:
            request["profile"] = self.profile
        return request

    def expected(self) -> Response:
        """Return the full manually framed bytes or bounded refusal."""
        return Response(canonical=self.canonical, error=self.error)


class Fixture(BaseModel):
    """Closed synthetic corpus; source checks do not establish host authority."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    profile: Literal["skulk.calls/1", "skulk.calls/2"]
    profile_digest: str
    scope: str
    cases: list[Case]


def fixture(inference: bool = False) -> Fixture:
    """Read maintained independent expectations; never regenerate using the kernel."""
    return Fixture.model_validate_json(
        (
            ROOT
            / "kernel/contracts"
            / ("calls-v2-cases.json" if inference else "calls-v1-cases.json")
        ).read_bytes()
    )


def python_case(native: Native, case: Case) -> Response:
    """Observe the installed real ABI without Python validation or serialization."""
    encode = native.encode_call_v2_frame if case.profile else native.encode_call_frame
    decode = native.decode_call_v2_frame if case.profile else native.decode_call_frame
    try:
        if case.action == "encode":
            wire = encode(case.source, bytes.fromhex(case.payload_hex))
        else:
            metadata, body = decode(bytes.fromhex(case.packet_hex))
            wire = encode(metadata.decode(), body)
        return Response(canonical=wire.hex(), error=None)
    except ValueError:
        return Response(canonical=None, error="calls_invalid")


def main() -> None:
    """Require every full expected vector before emitting bounded observations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["rust", "python", "node"], default="rust")
    parser.add_argument("--inference", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--node-facade", type=Path)
    args = parser.parse_args()
    inputs = fixture(args.inference)
    if args.backend == "python":
        native = cast(Native, importlib.import_module("_skulk_capability_kernel"))
        if (
            native.calls_v2_profile_digest()
            if args.inference
            else native.calls_profile_digest()
        ) != inputs.profile_digest:
            raise ValueError("Installed calls profile differs")
        results = [python_case(native, case) for case in inputs.cases]
    else:
        command = (
            [str(ROOT / "kernel/target/debug/examples/calls_probe")]
            if args.backend == "rust"
            else ["node", str(ROOT / "kernel/bindings/node/tools/calls_probe.mjs")]
        )
        if args.backend == "node" and args.node_facade:
            command.append(args.node_facade.resolve().as_uri())
        result = subprocess.run(
            command,
            input="\n".join(json.dumps(c.request()) for c in inputs.cases) + "\n",
            text=True,
            capture_output=True,
            check=True,
            timeout=120,
            cwd=ROOT,
        )
        results = [
            Response.model_validate_json(line) for line in result.stdout.splitlines()
        ]
    if len(results) != len(inputs.cases):
        raise ValueError("Calls observation count differs")
    observations: list[dict[str, str]] = []
    for case, result in zip(inputs.cases, results, strict=True):
        if result != case.expected():
            raise ValueError(f"calls profile disagrees at {case.identity}")
        observations.append(
            {
                "case": case.identity,
                "sha256": hashlib.sha256(result.model_dump_json().encode()).hexdigest(),
            }
        )
    report: dict[str, object] = {
        "backend": args.backend,
        "profile": inputs.profile,
        "profile_digest": inputs.profile_digest,
        "cases": len(inputs.cases),
        "input_sha256": hashlib.sha256(
            "\n".join(
                json.dumps(c.request(), sort_keys=True) for c in inputs.cases
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
