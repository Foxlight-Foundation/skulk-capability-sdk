"""Compare complete independent transport/factory/binding observations."""

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
    """Installed compiled wire ABI; no Python validation or framing fallback."""

    def transport_profile_digest(self) -> str: ...
    def transport_v2_profile_digest(self) -> str: ...
    def encode_transport_v2_control(self, source: str) -> bytes: ...
    def encode_transport_v2_packet(self, projection: str, media: bytes) -> bytes: ...
    def encode_portable_control(self, source: str) -> bytes: ...
    def encode_portable_packet(self, projection: str, media: bytes) -> bytes: ...


class Case(BaseModel):
    """An independent synthetic observation; never a live credential fixture."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    identity: str = Field(alias="id")
    profile: Literal["skulk.transport/2"] | None = None
    action: Literal["control", "packet"]
    source: str
    media_hex: str
    valid: bool
    wire_hex: str | None

    def request(self) -> dict[str, str | None]:
        """Return the same probe request for all three native backends."""
        request: dict[str, str | None] = {
            "action": self.action,
            "source": self.source,
            "media_hex": self.media_hex,
        }
        if self.profile is not None:
            request["profile"] = self.profile
        return request

    def expected(self) -> Response:
        """Expected full wire bytes come from the independent frozen oracle."""
        return Response(
            canonical=self.wire_hex, error=None if self.valid else "transport_invalid"
        )


class Fixture(BaseModel):
    """Complete corpus identity and scope for the separate transport profile."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    profile: Literal["skulk.transport/1", "skulk.transport/2"]
    profile_digest: str
    scope: str
    cases: list[Case]


def fixture(inference: bool = False) -> Fixture:
    """Read synthetic source fixtures without a private owner or account."""
    return Fixture.model_validate_json(
        (
            ROOT
            / "kernel/contracts"
            / ("transport-v2-cases.json" if inference else "transport-v1-cases.json")
        ).read_bytes()
    )


def python_case(native: Native, case: Case) -> Response:
    """Exercise the public compiled handle API and retain only bounded outcomes."""
    control = (
        native.encode_transport_v2_control
        if case.profile
        else native.encode_portable_control
    )
    packet = (
        native.encode_transport_v2_packet
        if case.profile
        else native.encode_portable_packet
    )
    try:
        wire = (
            control(case.source)
            if case.action == "control"
            else packet(case.source, bytes.fromhex(case.media_hex))
        )
        return Response(canonical=wire.hex())
    except ValueError as error:
        if "synthetic-do-not-echo-secret" in str(error):
            raise
        return Response(error="transport_invalid")


def main() -> None:
    """Compare every case and report hashes of complete observations only."""
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
            native.transport_v2_profile_digest()
            if args.inference
            else native.transport_profile_digest()
        ) != inputs.profile_digest:
            raise ValueError("Installed transport profile differs")
        results = [python_case(native, case) for case in inputs.cases]
    else:
        command = (
            [str(ROOT / "kernel/target/debug/examples/transport_probe")]
            if args.backend == "rust"
            else ["node", str(ROOT / "kernel/bindings/node/tools/transport_probe.mjs")]
        )
        if args.backend == "node" and args.node_facade:
            command.append(args.node_facade.resolve().as_uri())
        # The shared bounded external probe accepts the same request protocol.
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
        raise ValueError("Transport observation count differs")
    observations: list[dict[str, str]] = []
    for case, result in zip(inputs.cases, results, strict=True):
        if result != case.expected():
            raise ValueError(f"transport profile disagrees at {case.identity}")
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
                json.dumps(case.request(), sort_keys=True) for case in inputs.cases
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
