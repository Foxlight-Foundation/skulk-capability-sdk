"""Compare managed records through Rust and installed Python/Node artifacts."""

import argparse
import hashlib
import importlib
import json
from pathlib import Path
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from .portable_profile_cases import Response

ROOT = Path(__file__).resolve().parents[1]


class ManagedHandle(Protocol):
    """Actual immutable compiled author record, not a Python validator."""

    @property
    def digest(self) -> str: ...
    def canonical(self) -> bytes: ...


class ManagedFactory(Protocol):
    """Factory provided by the independently installed native extension."""

    def from_json(self, name: str, source: str) -> ManagedHandle: ...


class Native(Protocol):
    """The installed native managed ABI, including negative root-name tests."""

    ManagedContract: ManagedFactory

    def managed_profile_digest(self) -> str: ...
    def managed_contract_json(self, name: str, source: str) -> bytes: ...
    def managed_contract_digest(self, name: str, source: str) -> str: ...


class ManagedCase(BaseModel):
    """One independent synthetic record or bounded refusal witness."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    identity: str = Field(alias="id")
    name: str
    source: str
    canonical: str | None = None
    digest: str | None = None
    error: str | None = None

    def request(self) -> dict[str, str]:
        """Return only maintainer-probe input; no live startup records are used."""
        return {"operation": "managed", "name": self.name, "source": self.source}

    def expected(self) -> Response:
        """Independent expected bytes and identity, never a native-produced oracle."""
        return Response(canonical=self.canonical, digest=self.digest, error=self.error)


class Fixture(BaseModel):
    """Frozen complete observations for the managed profile and dependencies."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    profile: Literal["skulk.managed/1"]
    profile_digest: str
    portable_profile_digest: str
    scope: str
    cases: list[ManagedCase]


def fixture() -> Fixture:
    """Load synthetic inputs without importing a private owner or provider."""
    return Fixture.model_validate_json(
        (ROOT / "kernel/contracts/managed-v1-cases.json").read_bytes()
    )


def python_case(native: Native, case: ManagedCase) -> Response:
    """Exercise only the installed ABI; native refusals never echo input data."""
    try:
        record = native.ManagedContract.from_json(case.name, case.source)
        return Response(canonical=record.canonical().decode(), digest=record.digest)
    except ValueError as error:
        code = str(error).split(":", 1)[0]
        if not code.startswith("portable_") or "synthetic-do-not-echo-secret" in str(
            error
        ):
            raise
        return Response(error=code)


def main() -> None:
    """Compare all observations; record only synthetic IDs and observation hashes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["rust", "python", "node"], default="rust")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--node-facade", type=Path)
    args = parser.parse_args()
    inputs = fixture()
    if args.backend == "python":
        native = cast(Native, importlib.import_module("_skulk_capability_kernel"))
        if native.managed_profile_digest() != inputs.profile_digest:
            raise ValueError(
                "Installed managed profile differs from qualification input"
            )
        results = [python_case(native, case) for case in inputs.cases]
    else:
        command = (
            [str(ROOT / "kernel/target/debug/examples/portable_probe")]
            if args.backend == "rust"
            else ["node", str(ROOT / "kernel/bindings/node/tools/portable_probe.mjs")]
        )
        # Reuse the bounded probe transport, without retyping the legacy corpus.
        if args.backend == "node" and args.node_facade:
            command.append(args.node_facade.resolve().as_uri())
        results = external_requests(command, inputs.cases)
    if len(results) != len(inputs.cases):
        raise ValueError("Managed observation count differs")
    observations: list[dict[str, str]] = []
    for case, result in zip(inputs.cases, results, strict=True):
        if result != case.expected():
            raise ValueError(f"Managed profile disagrees at {case.identity}")
        observations.append(
            {
                "case": case.identity,
                "sha256": hashlib.sha256(result.model_dump_json().encode()).hexdigest(),
            }
        )
    encoded = "\n".join(
        json.dumps(case.request(), sort_keys=True) for case in inputs.cases
    )
    report = {
        "backend": args.backend,
        "profile": inputs.profile,
        "profile_digest": inputs.profile_digest,
        "cases": len(inputs.cases),
        "input_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        "observations": observations,
        "scope": "native managed record admission; not signed installation or adoption",
        "runtime_adoption": False,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "observations"}
        )
    )


def external_requests(args: list[str], inputs: list[ManagedCase]) -> list[Response]:
    """Bound the existing probe transport to the managed synthetic input shape."""
    import subprocess

    result = subprocess.run(
        args,
        input="\n".join(json.dumps(case.request()) for case in inputs) + "\n",
        text=True,
        capture_output=True,
        timeout=120,
        check=True,
        cwd=ROOT,
    )
    return [Response.model_validate_json(line) for line in result.stdout.splitlines()]


if __name__ == "__main__":
    main()
