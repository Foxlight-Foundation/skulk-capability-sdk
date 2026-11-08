"""Compare the candidate contract through Rust and installed Python/Node ABIs."""

import argparse
import hashlib
import importlib
import json
import subprocess
from pathlib import Path
from typing import Protocol, cast

from .portable_profile_cases import ROOT, Case, ContractName, Response, cases, fixture


class Native(Protocol):
    """The small ABI exercised through the independently installed wheel."""

    def portable_canonical_json(self, source: str) -> bytes: ...
    def portable_contract_json(self, name: ContractName, source: str) -> bytes: ...
    def portable_contract_digest(self, name: ContractName, source: str) -> str: ...
    def portable_schema_matches(self, definition: str, instance: str) -> bool: ...
    def portable_profile_digest(self) -> str: ...


def python_case(native: Native, case: Case) -> Response:
    """Read only fixed public native exports; never use a Python validator fallback."""
    try:
        if case.operation == "canonical":
            return Response(
                canonical=native.portable_canonical_json(case.source).decode()
            )
        if case.operation == "schema":
            return Response(
                valid=native.portable_schema_matches(case.definition, case.source)
            )
        return Response(
            canonical=native.portable_contract_json(case.name, case.source).decode(),
            digest=native.portable_contract_digest(case.name, case.source),
        )
    except ValueError as error:
        code = str(error).split(":", 1)[0]
        if not code.startswith("portable_"):
            raise
        return Response(error=code)


def external(args: list[str], inputs: list[Case]) -> list[Response]:
    """Bound a maintainer probe process; a failed probe is not an acceptance refusal."""
    source = "\n".join(json.dumps(case.request()) for case in inputs) + "\n"
    result = subprocess.run(
        args,
        input=source,
        text=True,
        capture_output=True,
        timeout=120,
        check=True,
        cwd=ROOT,
    )
    return [Response.model_validate_json(line) for line in result.stdout.splitlines()]


def main() -> None:
    """Compare every independent observation; report no payload or schema contents."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["rust", "python", "node"], default="rust")
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args()
    inputs = cases()
    if arguments.backend == "python":
        native = cast(Native, importlib.import_module("_skulk_capability_kernel"))
        if native.portable_profile_digest() != fixture().profile_digest:
            raise ValueError(
                "installed kernel profile differs from qualification input"
            )
        results = [python_case(native, case) for case in inputs]
    else:
        command = (
            [str(ROOT / "kernel/target/debug/examples/portable_probe")]
            if arguments.backend == "rust"
            else ["node", str(ROOT / "kernel/bindings/node/tools/portable_probe.mjs")]
        )
        results = external(command, inputs)
    if len(results) != len(inputs):
        raise ValueError("probe observation count differs")
    observations: list[dict[str, str]] = []
    for case, result in zip(inputs, results, strict=True):
        if result != case.expected:
            # The immutable case ID is enough to reproduce synthetic failures;
            # payload/schema contents and possible native exception text stay out.
            raise ValueError(f"portable profile disagrees at {case.identity}")
        observations.append(
            {
                "case": case.identity,
                "sha256": hashlib.sha256(result.model_dump_json().encode()).hexdigest(),
            }
        )
    encoded_inputs = "\n".join(
        json.dumps(case.request(), sort_keys=True) for case in inputs
    )
    report = {
        "backend": arguments.backend,
        "profile": fixture().profile,
        "profile_digest": fixture().profile_digest,
        "cases": len(inputs),
        "input_sha256": hashlib.sha256(encoded_inputs.encode()).hexdigest(),
        "observations": observations,
        "scope": (
            "portable contract candidate; "
            "not managed runtime adoption or SDK1.0 completion"
        ),
        "runtime_adoption": False,
    }
    if arguments.report:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "observations"}
        )
    )


if __name__ == "__main__":
    main()
