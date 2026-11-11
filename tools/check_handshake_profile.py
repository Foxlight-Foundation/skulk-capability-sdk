"""Compare complete independent handshake/factory/binding observations."""

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


class Handle(Protocol):
    """Only explicit credential-bearing wire export is needed by this probe."""

    def canonical(self) -> bytes: ...


class Startup(Handle, Protocol):
    """The actual immutable native startup handle, without a Python validator."""

    def hello(self, call_id: str | None = None) -> Handle: ...
    def admit_hello(self, source: str, call_id: str | None = None) -> Handle: ...


class ManagedFactory(Protocol):
    """Factory supplied by the installed compiled extension."""

    def from_json(self, name: str, source: str) -> Startup: ...


class HandshakeFactory(Protocol):
    """Factory supplied by the installed compiled extension."""

    def from_json(self, name: str, source: str) -> Handle: ...


class Native(Protocol):
    """Minimum installed ABI needed to exercise real author record handles."""

    ManagedContract: ManagedFactory
    HandshakeContract: HandshakeFactory

    def handshake_profile_digest(self) -> str: ...


class Case(BaseModel):
    """An independent synthetic observation; never a live credential fixture."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    identity: str = Field(alias="id")
    action: Literal["parse", "create", "admit"]
    name: str
    source: str
    startup: str
    call_id: str | None
    canonical: str | None
    error: str | None

    def request(self) -> dict[str, str | None]:
        """Return the same probe request for all three native backends."""
        return {
            "operation": "handshake",
            "action": self.action,
            "name": self.name,
            "source": self.source,
            "startup": self.startup,
            "call_id": self.call_id,
        }

    def expected(self) -> Response:
        """Use the frozen independent oracle, never native-produced expectations."""
        return Response(canonical=self.canonical, error=self.error)


class Fixture(BaseModel):
    """Complete corpus identity and scope for the separate handshake profile."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    profile: Literal["skulk.handshake/1"]
    profile_digest: str
    scope: str
    cases: list[Case]


def fixture() -> Fixture:
    """Read synthetic source fixtures without a private owner or account."""
    return Fixture.model_validate_json(
        (ROOT / "kernel/contracts/handshake-v1-cases.json").read_bytes()
    )


def python_case(native: Native, case: Case) -> Response:
    """Exercise the public compiled handle API and retain only bounded outcomes."""
    try:
        if case.action == "parse":
            record = native.HandshakeContract.from_json(case.name, case.source)
        else:
            startup = native.ManagedContract.from_json("Startup", case.startup)
            record = (
                startup.hello(case.call_id)
                if case.action == "create"
                else startup.admit_hello(case.source, case.call_id)
            )
        return Response(canonical=record.canonical().decode())
    except ValueError as error:
        code = str(error).split(":", 1)[0]
        if not code.startswith("portable_") or "synthetic-do-not-echo-secret" in str(
            error
        ):
            raise
        return Response(error=code)


def main() -> None:
    """Compare every case and report hashes of complete observations only."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["rust", "python", "node"], default="rust")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--node-facade", type=Path)
    args = parser.parse_args()
    inputs = fixture()
    if args.backend == "python":
        native = cast(Native, importlib.import_module("_skulk_capability_kernel"))
        if native.handshake_profile_digest() != inputs.profile_digest:
            raise ValueError("Installed handshake profile differs")
        results = [python_case(native, case) for case in inputs.cases]
    else:
        command = (
            [str(ROOT / "kernel/target/debug/examples/portable_probe")]
            if args.backend == "rust"
            else ["node", str(ROOT / "kernel/bindings/node/tools/portable_probe.mjs")]
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
        raise ValueError("Handshake observation count differs")
    observations: list[dict[str, str]] = []
    for case, result in zip(inputs.cases, results, strict=True):
        if result != case.expected():
            raise ValueError(f"handshake profile disagrees at {case.identity}")
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
