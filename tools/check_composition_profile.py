"""Compare complete independent composition observations in all three bindings."""

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
    """Native metadata handle; no Python validator or host authority implementation."""

    def canonical(self) -> bytes: ...
    def admit(
        self,
        request: "Handle",
        policy: "Handle",
        executor: str,
        request_bytes: bytes,
        now_unix_ms: int,
        scope_id: str,
    ) -> "Handle": ...


class Factory(Protocol):
    """Factory supplied by the installed compiled extension."""

    def from_json(self, name: str, source: str) -> Handle: ...


class Native(Protocol):
    """Minimal compiled ABI for structurally admitted caller metadata."""

    CompositionContract: Factory

    def composition_profile_digest(self) -> str: ...


class Case(BaseModel):
    """Synthetic host observations and an independently authored expected outcome."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    identity: str = Field(alias="id")
    action: Literal["parse", "admit"]
    name: str
    source: str
    parent: str
    policy: str
    executor: str
    request_hex: str
    now_unix_ms: int
    scope_id: str
    canonical: str | None
    error: str | None

    def request(self) -> dict[str, str | int]:
        """Return identical complete probe input for Rust, Python and Node."""
        return {
            "action": self.action,
            "name": self.name,
            "source": self.source,
            "parent": self.parent,
            "policy": self.policy,
            "executor": self.executor,
            "request_hex": self.request_hex,
            "now_unix_ms": self.now_unix_ms,
            "scope_id": self.scope_id,
        }

    def expected(self) -> Response:
        """Complete canonical bytes or bounded refusal from the independent oracle."""
        return Response(canonical=self.canonical, error=self.error)


class Fixture(BaseModel):
    """Closed corpus identity; no caller credentials or actual host observations."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    profile: Literal["skulk.composition/1"]
    profile_digest: str
    scope: str
    cases: list[Case]


def fixture() -> Fixture:
    """Read the public independent source corpus, without host/account access."""
    return Fixture.model_validate_json(
        (ROOT / "kernel/contracts/composition-v1-cases.json").read_bytes()
    )


def python_case(native: Native, case: Case) -> Response:
    """Call the installed compiled handles, never shadowing native admission."""
    try:
        record = native.CompositionContract.from_json(case.name, case.source)
        if case.action == "admit":
            parent = native.CompositionContract.from_json("Context", case.parent)
            policy = native.CompositionContract.from_json("Policy", case.policy)
            record = parent.admit(
                record,
                policy,
                case.executor,
                bytes.fromhex(case.request_hex),
                case.now_unix_ms,
                case.scope_id,
            )
        return Response(canonical=record.canonical().decode())
    except ValueError as error:
        code = str(error).split(":", 1)[0]
        if not code.startswith("composition_") or "synthetic-do-not-echo-secret" in str(
            error
        ):
            raise
        return Response(error=code)


def main() -> None:
    """Compare every complete witness and emit only synthetic observation hashes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["rust", "python", "node"], default="rust")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--node-facade", type=Path)
    args = parser.parse_args()
    inputs = fixture()
    if args.backend == "python":
        native = cast(Native, importlib.import_module("_skulk_capability_kernel"))
        if native.composition_profile_digest() != inputs.profile_digest:
            raise ValueError("Installed composition profile differs")
        results = [python_case(native, case) for case in inputs.cases]
    else:
        command = (
            [str(ROOT / "kernel/target/debug/examples/composition_probe")]
            if args.backend == "rust"
            else [
                "node",
                str(ROOT / "kernel/bindings/node/tools/composition_probe.mjs"),
            ]
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
        raise ValueError("Composition observation count differs")
    observations: list[dict[str, str]] = []
    for case, result in zip(inputs.cases, results, strict=True):
        if result != case.expected():
            raise ValueError(f"composition profile disagrees at {case.identity}")
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
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "observations"},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
