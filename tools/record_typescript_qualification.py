"""Retain exact public npm package and actual installed process-test identities."""

import hashlib
import json
import subprocess
import xml.etree.ElementTree as xml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*arguments: str) -> str:
    """Read the current repository identity without exposing environment state."""
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()


def main() -> None:
    """Record an exact clean source/package receipt, never host authorization."""
    package = ROOT / ".runtime/foxlight-skulk-capability-sdk-1.0.0.tgz"
    installed = (
        ROOT / ".runtime/node-installed/node_modules/@foxlight/skulk-capability-sdk"
    )
    source = ROOT / "packages/typescript"
    consumer = ROOT / ".runtime/node-installed/consumer.mts"
    if git("status", "--porcelain"):
        raise ValueError("TypeScript qualification source is not clean")
    for path in (source / "dist").iterdir():
        if path.read_bytes() != (installed / "dist" / path.name).read_bytes():
            raise ValueError("Installed TypeScript package differs")
    if consumer.read_bytes() != (source / "tests/consumer.ts").read_bytes():
        raise ValueError("Independent TypeScript consumer differs")
    tree = xml.parse(ROOT / ".runtime/typescript.xml")
    tests = tree.findall(".//testcase")
    if not tests or any(
        test.find(kind) is not None
        for test in tests
        for kind in ("failure", "error", "skipped")
    ):
        raise ValueError("TypeScript process qualification is incomplete")
    cases = sorted(
        test.attrib["classname"] + "." + test.attrib["name"] for test in tests
    )
    if len(cases) != len(set(cases)):
        raise ValueError("TypeScript process qualification has duplicate cases")
    receipt = {
        "format": 1,
        "checkout": git("rev-parse", "HEAD"),
        "parents": git("show", "-s", "--format=%P", "HEAD").split(),
        "tree": git("rev-parse", "HEAD^{tree}"),
        "source_clean": True,
        "package": package.name,
        "package_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        "node": subprocess.check_output(["node", "--version"], text=True).strip(),
        "declarations_consumer_sha256": hashlib.sha256(
            consumer.read_bytes()
        ).hexdigest(),
        "cases": cases,
        "installed_signed_admission": False,
        "provider_adoption": False,
        "durable_parity": False,
        "durable_source": True,
        "credential_parity": False,
        "credential_source": True,
        "complete_author_journey": False,
    }
    (ROOT / ".runtime/typescript-source.json").write_text(
        json.dumps(receipt, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
