"""Compare independent synthetic inference-authority goldens through real bindings."""

import argparse
import hashlib
import importlib
import json
import subprocess
from pathlib import Path
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from .check_composition_profile import Factory

ROOT = Path(__file__).resolve().parents[1]
Kind = Literal["inference-author", "inference-composition", "inference-execution"]


class Observation(BaseModel):
    """Complete native result, containing only packaged synthetic fixture data."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    canonical: str | None = None
    error: str | None = None
    digest: str | None = None


class Case(BaseModel):
    """One independent declaration, binding, effect or processing witness."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    identity: str = Field(alias="id")
    profile: Literal["skulk.composition/3"] | None = None
    action: Literal["parse", "admit", "author"]
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
    digest: str | None

    def request(self) -> dict[str, str | int]:
        """Supply identical complete probe input without expected outcomes."""
        request: dict[str, str | int] = {
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
        if self.profile is not None:
            request["profile"] = self.profile
        return request

    def expected(self) -> Observation:
        """Read an independently authored full canonical/digest/refusal golden."""
        return Observation(
            canonical=self.canonical, error=self.error, digest=self.digest
        )


class Fixture(BaseModel):
    """Both exact semantic profile identities and their complete synthetic corpus."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    profile: Literal["skulk.composition/2", "skulk.composition/3"]
    profile_digest: str
    author_profile: Literal["skulk.portable/2"]
    author_profile_digest: str
    scope: str
    cases: tuple[Case, ...]


class Native(Protocol):
    """Compiled profile-two ABI, never a Python permission implementation."""

    CompositionV2Contract: Factory
    CompositionV3Contract: Factory

    def composition_v3_profile_digest(self) -> str: ...

    def composition_v2_profile_digest(self) -> str: ...
    def portable_v2_profile_digest(self) -> str: ...
    def portable_v2_contract_json(self, name: str, source: str) -> bytes: ...
    def portable_v2_contract_digest(self, name: str, source: str) -> str: ...


def fixture(execution: bool = False) -> Fixture:
    """Read only packaged synthetic witnesses; never open account or runtime state."""
    return Fixture.model_validate_json(
        (
            ROOT
            / "kernel/contracts"
            / ("inference-v3-cases.json" if execution else "inference-v2-cases.json")
        ).read_bytes()
    )


def selected_cases(kind: Kind) -> tuple[Case, ...]:
    """Partition independent goldens by the exact profile they exercise."""
    return tuple(
        case
        for case in fixture(kind == "inference-execution").cases
        if (case.action == "author") == (kind == "inference-author")
    )


def profile_identity(kind: Kind) -> tuple[str, str]:
    """Return the declared profile and frozen semantic fingerprint for a partition."""
    inputs = fixture(kind == "inference-execution")
    return (
        (inputs.author_profile, inputs.author_profile_digest)
        if kind == "inference-author"
        else (inputs.profile, inputs.profile_digest)
    )


def python_case(native: Native, case: Case) -> Observation:
    """Invoke actual native declaration/attenuation functions, without projection."""
    try:
        if case.action == "author":
            return Observation(
                canonical=native.portable_v2_contract_json(
                    case.name, case.source
                ).decode(),
                digest=native.portable_v2_contract_digest(case.name, case.source),
            )
        factory = (
            native.CompositionV3Contract
            if case.profile
            else native.CompositionV2Contract
        )
        record = factory.from_json(case.name, case.source)
        if case.action == "admit":
            parent = factory.from_json("Context", case.parent)
            policy = factory.from_json("Policy", case.policy)
            record = parent.admit(
                record,
                policy,
                case.executor,
                bytes.fromhex(case.request_hex),
                case.now_unix_ms,
                case.scope_id,
            )
        return Observation(canonical=record.canonical().decode())
    except ValueError as error:
        code = str(error).split(":", 1)[0]
        if not code.startswith(("composition_", "portable_")) or (
            "synthetic-do-not-echo-secret" in str(error)
        ):
            raise
        return Observation(error=code)


def main() -> None:
    """Check complete Rust/Python/Node observations, not live caller admission."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["rust", "python", "node"], default="rust")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--node-facade", type=Path)
    parser.add_argument(
        "--kind",
        choices=["inference-author", "inference-composition", "inference-execution"],
        required=True,
    )
    args = parser.parse_args()
    kind = cast(Kind, args.kind)
    inputs = fixture(kind == "inference-execution")
    cases = selected_cases(kind)
    profile, digest = profile_identity(kind)
    encoded = "\n".join(json.dumps(c.request(), sort_keys=True) for c in cases)
    if args.backend == "python":
        native = cast(Native, importlib.import_module("_skulk_capability_kernel"))
        if (
            (
                native.composition_v3_profile_digest()
                if kind == "inference-execution"
                else native.composition_v2_profile_digest()
            )
            != inputs.profile_digest
            or native.portable_v2_profile_digest() != inputs.author_profile_digest
        ):
            raise ValueError("Installed inference profiles differ")
        results = [python_case(native, case) for case in cases]
    else:
        command = (
            [str(ROOT / "kernel/target/debug/examples/inference_probe")]
            if args.backend == "rust"
            else ["node", str(ROOT / "kernel/bindings/node/tools/inference_probe.mjs")]
        )
        if args.backend == "node" and args.node_facade:
            command.append(args.node_facade.resolve().as_uri())
        result = subprocess.run(
            command,
            input=encoded + "\n",
            text=True,
            capture_output=True,
            check=True,
            timeout=120,
            cwd=ROOT,
        )
        results = [
            Observation.model_validate_json(line) for line in result.stdout.splitlines()
        ]
    if len(results) != len(cases):
        raise ValueError("Inference observation count differs")
    for case, result in zip(cases, results, strict=True):
        if result != case.expected():
            raise ValueError(f"Inference profile disagrees at {case.identity}")
    report = {
        "backend": args.backend,
        "profile": profile,
        "profile_digest": digest,
        "cases": len(cases),
        "input_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        "observations": [
            {
                "case": case.identity,
                "sha256": hashlib.sha256(result.model_dump_json().encode()).hexdigest(),
            }
            for case, result in zip(cases, results, strict=True)
        ],
        "scope": inputs.scope,
        "runtime_adoption": False,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "backend": args.backend,
                "profile": profile,
                "cases": len(cases),
                "passed": True,
            }
        )
    )


if __name__ == "__main__":
    main()
