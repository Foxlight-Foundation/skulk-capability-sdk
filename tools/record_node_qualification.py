"""Bind selected Node process acceptance to actual CI artifacts and source."""

import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from pydantic import JsonValue, TypeAdapter
from skulk_capability_tools.node_application import NodeBuild
from skulk_capability_tools.runtime_release import RuntimeWheel

ROOT = Path(__file__).resolve().parents[1]
_OBJECT = TypeAdapter(dict[str, JsonValue])


def main() -> None:
    """Refuse incomplete process tests and record exact installed package bytes."""
    root = ROOT / ".runtime/selected-node"
    tests = ET.parse(root / "tests.xml").findall(".//testcase")
    if len(tests) < 6 or any(
        test.find(kind) is not None
        for test in tests
        for kind in ("failure", "error", "skipped")
    ):
        raise ValueError("Selected Node process qualification is incomplete")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip():
        raise ValueError("Node qualification source is not clean")
    selection = _OBJECT.validate_json((root / "selection.json").read_bytes())
    wheel = RuntimeWheel.model_validate(selection["wheel"])
    report = NodeBuild.model_validate_json(
        (root / "application/node-build.json").read_bytes()
    )
    identities = {
        "wheels/" + wheel.filename: wheel.sha256,
        "application/" + report.assets.filename: report.assets.sha256,
        "application/bundle.pyz": report.bundle_sha256,
    }
    for name, digest in identities.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Qualified Node artifact bytes differ")
    receipt = {
        "format": 1,
        "checkout": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "tree": subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True
        ).strip(),
        "artifacts": identities,
        "cases": sorted(test.attrib["name"] for test in tests),
        "installed_signed_admission": False,
        "provider_adoption": False,
        "complete_author_journey": False,
    }
    (root / "qualification.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    main()
