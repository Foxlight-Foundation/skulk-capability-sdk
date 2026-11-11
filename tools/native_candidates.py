"""Stage immutable native candidate assets from complete qualified platform bundles."""

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tools.native_packages import (
    MAX_ASSET_BYTES,
    PLATFORMS,
    Platform,
    asset_digest,
    inspect_npm,
    inspect_wheel,
    platform_name,
    require_wheel_platform,
    versions,
)

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "Foxlight-Foundation/skulk-capability-sdk"


class Frozen(BaseModel):
    """Closed maintainer metadata with no implicit execution authority."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class Asset(Frozen):
    """One exact binary asset measured before it enters a release manifest."""

    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(gt=0, le=MAX_ASSET_BYTES)


class Observation(Frozen):
    """A synthetic case ID and the hash of its complete native observation."""

    case: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Corpus(Frozen):
    """Complete per-backend evidence; counts alone never qualify an artifact."""

    backend: Literal["rust", "python", "node"]
    profile: str
    profile_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    cases: int = Field(gt=0)
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observations: tuple[Observation, ...]
    scope: str
    runtime_adoption: Literal[False]


class Bundle(Frozen):
    """Qualified bytes at a clean source identity, before runtime integration."""

    format: Literal[1] = 1
    platform: Platform
    version: str
    node_version: str
    source_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    source_tree: str = Field(pattern=r"^[a-f0-9]{40}$")
    source_clean: bool
    wheel: Asset
    npm: Asset
    compiler_free_install_verified: Literal[True]
    runtime_adoption: Literal[False] = False


def _identity(*args: str) -> str:
    """Read a local Git identity without assuming a branch name or publishing it."""
    return subprocess.check_output(
        ["git", "rev-parse", *args], cwd=ROOT, text=True
    ).strip()


def validate_corpora(files: dict[str, bytes]) -> tuple[Corpus, ...]:
    """Require complete, identical observations across all three native backends."""
    from tools.check_handshake_profile import fixture as handshake_fixture
    from tools.check_managed_profile import fixture as managed_fixture
    from tools.check_transport_profile import fixture as transport_fixture
    from tools.portable_profile_cases import cases
    from tools.portable_profile_cases import fixture as portable_fixture

    qualified: list[Corpus] = []
    for kind, source_name in [
        ("portable", "portable-v1-cases.json"),
        ("managed", "managed-v1-cases.json"),
        ("handshake", "handshake-v1-cases.json"),
        ("transport", "transport-v1-cases.json"),
    ]:
        fixture = json.loads((ROOT / "kernel/contracts" / source_name).read_bytes())
        if kind == "portable":
            inputs = cases()
            expected_input = hashlib.sha256(
                "\n".join(
                    json.dumps(c.request(), sort_keys=True) for c in inputs
                ).encode()
            ).hexdigest()
            expected = [
                Observation(
                    case=c.identity,
                    sha256=hashlib.sha256(
                        c.expected.model_dump_json().encode()
                    ).hexdigest(),
                )
                for c in inputs
            ]
            if fixture["profile_digest"] != portable_fixture().profile_digest:
                raise ValueError("Portable fixture identity differs")
        else:
            managed = (
                managed_fixture()
                if kind == "managed"
                else handshake_fixture()
                if kind == "handshake"
                else transport_fixture()
            )
            expected_input = hashlib.sha256(
                "\n".join(
                    json.dumps(c.request(), sort_keys=True) for c in managed.cases
                ).encode()
            ).hexdigest()
            expected = [
                Observation(
                    case=c.identity,
                    sha256=hashlib.sha256(
                        c.expected().model_dump_json().encode()
                    ).hexdigest(),
                )
                for c in managed.cases
            ]
        reports = [
            Corpus.model_validate_json(files[f"{kind}-{backend}.json"])
            for backend in ["rust", "python", "node"]
        ]
        for report, backend in zip(reports, ["rust", "python", "node"], strict=True):
            if (
                report.backend != backend
                or report.profile_digest != fixture["profile_digest"]
                or report.profile != fixture["profile"]
            ):
                raise ValueError("Candidate corpus identity differs")
            if (
                report.input_sha256 != expected_input
                or tuple(expected) != report.observations
            ):
                raise ValueError(
                    "Candidate observations differ from independent source corpus"
                )
            if (
                report.cases != len(report.observations)
                or len({o.case for o in report.observations}) != report.cases
            ):
                raise ValueError("Candidate evidence is incomplete or duplicates cases")
            if report.model_dump(exclude={"backend"}) != reports[0].model_dump(
                exclude={"backend"}
            ):
                raise ValueError("Candidate backend observations differ")
        qualified.append(reports[0])
    return tuple(qualified)


def create_bundle(output: Path) -> Path:
    """Collect tested artifacts without equating a local dirty build with a release."""
    report = json.loads((output / "report.json").read_bytes())
    python_version, node_version = versions()
    family = platform_name(report["platform"], report["architecture"])
    paths = {
        "wheel": output / "wheels" / report["wheel"]["name"],
        "npm": output / "npm" / report["npm"]["name"],
    }
    assets: dict[str, Asset] = {}
    for kind, path in paths.items():
        digest = asset_digest(path)
        if digest != report[kind]["sha256"]:
            raise ValueError("Candidate package differs from tested bytes")
        assets[kind] = Asset(name=path.name, sha256=digest, size=path.stat().st_size)
    install = json.loads((output / "compiler-free-install.json").read_bytes())
    if install != {
        "format": 1,
        "wheel_sha256": assets["wheel"].sha256,
        "npm_sha256": assets["npm"].sha256,
        "compilers_available": False,
        "network_required": False,
        "runtime_adoption": False,
    }:
        raise ValueError("Native compiler-free installation proof differs")
    inspect_wheel(paths["wheel"])
    inspect_npm(paths["npm"])
    metadata = Bundle(
        platform=family,
        version=python_version,
        node_version=node_version,
        source_commit=_identity("HEAD"),
        source_tree=_identity("HEAD^{tree}"),
        source_clean=not subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT
        ),
        wheel=assets["wheel"],
        npm=assets["npm"],
        compiler_free_install_verified=True,
    )
    evidence = {
        path.name: path.read_bytes()
        for path in output.glob("*-*.json")
        if path.name.startswith(("portable-", "managed-", "handshake-", "transport-"))
    }
    validate_corpora(evidence)
    archive = output / f"native-candidate-{family}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr("candidate.json", metadata.model_dump_json(indent=2) + "\n")
        zipped.write(output / "report.json", "report.json")
        zipped.write(
            output / "compiler-free-install.json", "compiler-free-install.json"
        )
        for name, data in sorted(evidence.items()):
            zipped.writestr(name, data)
        for path in paths.values():
            zipped.write(path, path.name)
    return archive


def read_bundle(
    path: Path, source: str
) -> tuple[Bundle, dict[str, bytes], tuple[Corpus, ...]]:
    """Refuse foreign, dirty, incomplete, oversized or substituted platform assets."""
    asset_digest(path)
    with zipfile.ZipFile(path) as zipped:
        entries = zipped.infolist()
        if (
            len(entries) != 17
            or sum(e.file_size for e in entries) > 2 * MAX_ASSET_BYTES
        ):
            raise ValueError("Native candidate bundle inventory differs")
        names = [e.filename for e in entries]
        if len(names) != len(set(names)) or any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,191}", name)
            for name in names
        ):
            raise ValueError("Invalid or duplicate native candidate member")
        if any(
            e.is_dir()
            or (e.external_attr >> 16) & 0o170000 not in (0, 0o100000)
            or e.file_size > MAX_ASSET_BYTES
            for e in entries
        ):
            raise ValueError("Native candidate member must be a bounded regular file")
        files = {e.filename: zipped.read(e) for e in entries}
    bundle = Bundle.model_validate_json(files["candidate.json"])
    if (
        bundle.source_commit != source
        or not bundle.source_clean
        or (bundle.version, bundle.node_version) != versions()
    ):
        raise ValueError(
            "Native candidate source/version is not the approved clean revision"
        )
    expected = {
        "candidate.json",
        "report.json",
        "compiler-free-install.json",
        bundle.wheel.name,
        bundle.npm.name,
        *[
            f"{kind}-{backend}.json"
            for kind in ["portable", "managed", "handshake", "transport"]
            for backend in ["rust", "python", "node"]
        ],
    }
    if set(files) != expected:
        raise ValueError("Native candidate bundle inventory differs")
    require_wheel_platform(bundle.wheel.name, bundle.platform)
    report = json.loads(files["report.json"])
    if (
        platform_name(report["platform"], report["architecture"]) != bundle.platform
        or report["runtime_adoption"] is not False
    ):
        raise ValueError("Candidate platform evidence differs")
    for kind, asset in [("wheel", bundle.wheel), ("npm", bundle.npm)]:
        data = files[asset.name]
        if (
            len(data) != asset.size
            or hashlib.sha256(data).hexdigest() != asset.sha256
            or report[kind] != {"name": asset.name, "sha256": asset.sha256}
        ):
            raise ValueError("Candidate package differs from tested bytes")
    install = json.loads(files["compiler-free-install.json"])
    if install != {
        "format": 1,
        "wheel_sha256": bundle.wheel.sha256,
        "npm_sha256": bundle.npm.sha256,
        "compilers_available": False,
        "network_required": False,
        "runtime_adoption": False,
    }:
        raise ValueError("Native compiler-free installation proof differs")
    with tempfile.TemporaryDirectory(prefix="native-inspect-") as temporary:
        for asset, inspect in [
            (bundle.wheel, inspect_wheel),
            (bundle.npm, inspect_npm),
        ]:
            selected = Path(temporary) / asset.name
            selected.write_bytes(files[asset.name])
            inspect(selected)
    return bundle, files, validate_corpora(files)


def assemble(paths: list[Path], destination: Path, source: str) -> Path:
    """Validate the whole cohort before writing immutable publication staging assets."""
    if len(paths) != 3 or not re.fullmatch(r"[a-f0-9]{40}", source):
        raise ValueError("Exactly three approved native platform bundles required")
    checked = [(path, *read_bundle(path, source)) for path in paths]
    if {b.platform for _, b, _, _ in checked} != set(PLATFORMS) or len(
        {b.source_tree for _, b, _, _ in checked}
    ) != 1:
        raise ValueError("Candidate platform cohort is incomplete or mixed")
    approved_tree = _identity(source + "^{tree}")
    if any(b.source_tree != approved_tree for _, b, _, _ in checked):
        raise ValueError("Candidate tree differs from approved source")
    first = checked[0][3]
    for _, _, _, corpus in checked[1:]:
        if corpus != first:
            raise ValueError("Candidate platform observations differ")
    python_version, node_version = versions()
    tag = f"native-v{node_version}"
    base = f"https://github.com/{REPOSITORY}/releases/download/{tag}"
    platforms: list[dict[str, object]] = []
    pending: dict[str, bytes] = {}
    for archive_path, bundle, files, _ in sorted(
        checked, key=lambda item: item[1].platform
    ):
        wheel_name = bundle.wheel.name
        npm_name = f"skulk-capability-kernel-{python_version}-{bundle.platform}.tgz"
        if wheel_name in pending:
            raise ValueError("Native candidate asset names collide")
        pending[wheel_name] = files[bundle.wheel.name]
        pending[npm_name] = files[bundle.npm.name]
        evidence_name = f"qualification-{bundle.platform}.zip"
        pending[evidence_name] = archive_path.read_bytes()

        def asset(name: str) -> dict[str, object]:
            data = pending[name]
            return {
                "name": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "url": f"{base}/{name}",
            }

        platforms.append(
            {
                "platform": bundle.platform,
                "wheel": asset(wheel_name),
                "npm": asset(npm_name),
                "evidence": asset(evidence_name),
            }
        )
    manifest = {
        "format": 1,
        "version": python_version,
        "node_version": node_version,
        "repository": REPOSITORY,
        "tag": tag,
        "source_commit": source,
        "source_tree": checked[0][1].source_tree,
        "scope": (
            "Native ABI prerelease candidate; "
            "not SDK 1.0 or installed owner/Core acceptance"
        ),
        "runtime_adoption": False,
        "profiles": [
            {
                "profile": c.profile,
                "profile_digest": c.profile_digest,
                "cases": c.cases,
                "input_sha256": c.input_sha256,
            }
            for c in first
        ],
        "platforms": platforms,
    }
    pending["native-candidates.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    pending["SHA256SUMS"] = (
        "\n".join(
            f"{hashlib.sha256(data).hexdigest()}  {name}"
            for name, data in sorted(pending.items())
        )
        + "\n"
    ).encode()
    destination.mkdir(parents=True, exist_ok=False)
    for name, data in pending.items():
        (destination / name).write_bytes(data)
    return destination / "native-candidates.json"


def main() -> None:
    """Create build bundles or assemble a reviewed three-platform release cohort."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    bundle = sub.add_parser("bundle")
    bundle.add_argument("output", type=Path)
    release = sub.add_parser("assemble")
    release.add_argument("--source", required=True)
    release.add_argument("--destination", type=Path, required=True)
    release.add_argument("bundles", type=Path, nargs=3)
    args = parser.parse_args()
    if args.command == "bundle":
        print(create_bundle(args.output))
    else:
        print(assemble(args.bundles, args.destination, args.source))


if __name__ == "__main__":
    main()
