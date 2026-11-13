"""Retain bounded source/binary/case identities after actual app qualification."""

import hashlib
import importlib
import importlib.metadata
import json
import subprocess
import tomllib
import xml.etree.ElementTree as xml
from collections.abc import Callable
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]


def _git(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()


def _cases(name: str) -> list[str]:
    tree = xml.parse(ROOT / ".runtime" / name)
    tests = tree.findall(".//testcase")
    if (
        not tests
        or len(tests) > 512
        or any(
            test.find(kind) is not None
            for test in tests
            for kind in ("failure", "error", "skipped")
        )
    ):
        raise ValueError("application qualification is incomplete")
    cases = sorted(
        test.attrib["classname"] + "." + test.attrib["name"] for test in tests
    )
    if len(cases) != len(set(cases)):
        raise ValueError("application qualification has duplicate cases")
    return cases


def main() -> None:
    """Record the actual installed ABI, complete test names and clean source pins.

    The report contains no payloads, settings, credentials, filesystem paths or
    provider responses. It does not grant authority or claim installed adoption.
    """
    with (ROOT / "pyproject.toml").open("rb") as source:
        expected = tomllib.load(source)["project"]["version"]
    if (
        importlib.metadata.version("skulk-capability-sdk") != expected
        or importlib.metadata.version("skulk-capability-kernel") != "0.7.0rc3"
        or _git("status", "--porcelain")
    ):
        raise ValueError("application qualification source or binary differs")
    module = importlib.import_module("_skulk_capability_kernel")
    profiles: dict[str, str] = {}
    for profile, getter in (
        ("skulk.portable/1", "portable_profile_digest"),
        ("skulk.portable/2", "portable_v2_profile_digest"),
        ("skulk.managed/1", "managed_profile_digest"),
        ("skulk.managed/2", "managed_v2_profile_digest"),
        ("skulk.handshake/1", "handshake_profile_digest"),
        ("skulk.handshake/2", "handshake_v2_profile_digest"),
        ("skulk.transport/1", "transport_profile_digest"),
        ("skulk.transport/2", "transport_v2_profile_digest"),
        ("skulk.composition/1", "composition_profile_digest"),
        ("skulk.composition/2", "composition_v2_profile_digest"),
        ("skulk.composition/3", "composition_v3_profile_digest"),
        ("skulk.calls/1", "calls_profile_digest"),
        ("skulk.calls/2", "calls_v2_profile_digest"),
    ):
        profiles[profile] = cast(Callable[[], str], getattr(module, getter))()
    report = {
        "format": 1,
        "checkout": _git("rev-parse", "HEAD"),
        "parents": _git("show", "-s", "--format=%P", "HEAD").split(),
        "tree": _git("rev-parse", "HEAD^{tree}"),
        "source_clean": True,
        "sdk_version": expected,
        "kernel_version": importlib.metadata.version("skulk-capability-kernel"),
        "application_sha256": hashlib.sha256(
            (ROOT / "skulk_capability_sdk/application.py").read_bytes()
        ).hexdigest(),
        "profiles": profiles,
        "root_cases": _cases("app-root.xml"),
        "native_cases": _cases("app-native.xml"),
        "installed_signed_admission": False,
        "provider_adoption": False,
        "protected_state_migration": False,
    }
    (ROOT / ".runtime/native-app-source.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
