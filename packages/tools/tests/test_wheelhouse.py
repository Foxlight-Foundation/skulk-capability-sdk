"""The wheelhouse recipe takes exactly the locked wheels, at the platform floor."""

import hashlib
import json
import stat
import time
from pathlib import Path

import pytest
from packaging.tags import Tag
from skulk_capability_tools.wheelhouse import (
    BUILD_BACKEND_LOCK,
    LockedFile,
    LockedPackage,
    Lockfile,
    build_from_source,
    build_wheelhouse,
    git_source,
    load_lock,
    manylinux_platforms,
    platform_environment,
    platform_policy,
    runtime_closure,
    select_wheel,
    supported_tags,
)

from skulk_capability_sdk.contracts import Denied

FAMILIES = ("macos-arm64", "linux-glibc-x86_64", "linux-glibc-aarch64")
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def environment(family: str) -> dict[str, str]:
    """Marker values for a CPython 3.13 host of one release family."""
    return platform_environment(family)


def locked(filename: str) -> LockedFile:
    """A locked wheel whose served bytes are its own filename."""
    content = filename.encode()
    return LockedFile(
        url=f"https://files.example/{filename}",
        hash=f"sha256:{hashlib.sha256(content).hexdigest()}",
        size=len(content),
    )


def package(
    name: str,
    version: str,
    *wheels: str,
    dependencies: tuple[dict[str, object], ...] = (),
    source: dict[str, str] | None = None,
) -> LockedPackage:
    """One locked package from the registry unless a source is given."""
    return LockedPackage.model_validate(
        {
            "name": name,
            "version": version,
            "source": source or {"registry": "https://pypi.org/simple"},
            "dependencies": dependencies,
            "wheels": [locked(filename).model_dump() for filename in wheels],
        }
    )


def tags_for(family: str) -> tuple[Tag, ...]:
    return supported_tags(platform_policy(family))


def test_linux_floor_is_glibc_2_28_and_never_the_bare_linux_tag() -> None:
    """The newest accepted tag is the floor; legacy aliases follow their version."""
    x86 = manylinux_platforms("x86_64")
    assert x86[0] == "manylinux_2_28_x86_64"
    assert x86.index("manylinux2014_x86_64") == x86.index("manylinux_2_17_x86_64") + 1
    assert x86[-1] == "manylinux1_x86_64"
    assert not [tag for tag in x86 if tag.startswith("linux_")]
    arm = manylinux_platforms("aarch64")
    assert arm[0] == "manylinux_2_28_aarch64"
    assert arm[-2:] == ("manylinux_2_17_aarch64", "manylinux2014_aarch64")
    assert platform_policy("macos-arm64")[0] == "macosx_14_0_arm64"


def test_an_unknown_platform_has_no_policy() -> None:
    with pytest.raises(Denied, match="no wheelhouse policy"):
        platform_policy("windows-amd64")
    with pytest.raises(Denied, match="no wheelhouse policy"):
        platform_environment("windows-amd64")


def test_the_build_backend_is_locked_by_version_and_hash() -> None:
    """Every build dependency is an exact version with its wheel's SHA-256."""
    names: set[str] = set()
    for line in BUILD_BACKEND_LOCK:
        requirement, _, digest = line.partition(" --hash=sha256:")
        name, _, version = requirement.partition("==")
        assert name and version and len(digest) == 64
        names.add(name)
    # hatchling's own requirements on CPython 3.13 (tomli is for older Pythons).
    assert names == {
        "hatchling",
        "packaging",
        "pathspec",
        "pluggy",
        "tomlkit",
        "trove-classifiers",
    }


@pytest.mark.parametrize(
    ("family", "wheels", "chosen"),
    [
        (
            "linux-glibc-x86_64",
            (
                "native-1.0-cp313-cp313-manylinux_2_34_x86_64.whl",
                "native-1.0-cp313-cp313-manylinux_2_28_x86_64.whl",
                "native-1.0-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
            ),
            "native-1.0-cp313-cp313-manylinux_2_28_x86_64.whl",
        ),
        (
            "linux-glibc-aarch64",
            (
                "native-1.0-py3-none-any.whl",
                "native-1.0-cp311-abi3-manylinux_2_17_aarch64.whl",
                "native-1.0-cp313-cp313-manylinux_2_17_aarch64.whl",
            ),
            "native-1.0-cp313-cp313-manylinux_2_17_aarch64.whl",
        ),
        (
            "macos-arm64",
            (
                "native-1.0-cp313-cp313-macosx_15_0_arm64.whl",
                "native-1.0-cp313-cp313-macosx_14_0_arm64.whl",
                "native-1.0-cp313-cp313-macosx_11_0_arm64.whl",
            ),
            "native-1.0-cp313-cp313-macosx_14_0_arm64.whl",
        ),
        (
            "macos-arm64",
            (
                "native-1.0-cp311-abi3-macosx_10_9_universal2.whl",
                "native-1.0-cp313-cp313-manylinux_2_28_x86_64.whl",
            ),
            "native-1.0-cp311-abi3-macosx_10_9_universal2.whl",
        ),
    ],
)
def test_the_floor_host_preference_chooses_the_wheel(
    family: str, wheels: tuple[str, ...], chosen: str
) -> None:
    """Above-floor wheels are never chosen; the exact ABI beats the stable ABI."""
    assert select_wheel(package("native", "1.0", *wheels), tags_for(family)) == (
        locked(chosen)
    )


@pytest.mark.parametrize(
    ("version", "wheels", "refusal"),
    [
        ("1.0", ("native-1.0-cp313-cp313-manylinux_2_34_x86_64.whl",), "no binary"),
        ("1.0", ("native-1.0-cp313-cp313-linux_x86_64.whl",), "no binary"),
        ("1.0", (), "no binary"),
        ("1.0rc1", ("native-1.0rc1-py3-none-any.whl",), "prerelease"),
        ("1.0", ("native-2.0-py3-none-any.whl",), "does not belong"),
        ("1.0", ("other-1.0-py3-none-any.whl",), "does not belong"),
    ],
)
def test_a_package_without_a_released_floor_wheel_is_refused(
    version: str, wheels: tuple[str, ...], refusal: str
) -> None:
    """Sdist-only, above-floor, non-portable and prerelease packages never enter."""
    with pytest.raises(Denied, match=refusal):
        select_wheel(
            package("native", version, *wheels), tags_for("linux-glibc-x86_64")
        )


def lock_of(*packages: LockedPackage) -> Lockfile:
    return Lockfile(version=1, package=packages)


def sdk(*dependencies: dict[str, object]) -> LockedPackage:
    return package(
        "skulk-capability-nodes",
        "0.8.0",
        dependencies=dependencies,
        source={"git": f"https://github.com/example/sdk.git?rev=main#{COMMIT}"},
    )


def test_the_closure_follows_only_edges_whose_marker_holds() -> None:
    """A Linux-only dependency joins a Linux runtime and stays out of a Mac one."""
    lock = lock_of(
        sdk(
            {"name": "native"}, {"name": "secrets", "marker": "sys_platform == 'linux'"}
        ),
        package("native", "1.0", "native-1.0-py3-none-any.whl"),
        package("secrets", "2.0", "secrets-2.0-py3-none-any.whl"),
        package("unrelated", "3.0", "unrelated-3.0-py3-none-any.whl"),
    )
    linux = runtime_closure(lock, "skulk-capability-nodes", environment(FAMILIES[1]))
    mac = runtime_closure(lock, "skulk-capability-nodes", environment(FAMILIES[0]))
    assert [item.name for item in linux] == [
        "native",
        "secrets",
        "skulk-capability-nodes",
    ]
    assert [item.name for item in mac] == ["native", "skulk-capability-nodes"]


def test_the_closure_disambiguates_versions_and_refuses_extras() -> None:
    """A fork of versions resolves by the edge's version; extras are never installed."""
    first = package("native", "1.0", "native-1.0-py3-none-any.whl")
    second = package("native", "2.0", "native-2.0-py3-none-any.whl")
    pinned = lock_of(sdk({"name": "native", "version": "2.0"}), first, second)
    closure = runtime_closure(
        pinned, "skulk-capability-nodes", environment(FAMILIES[1])
    )
    assert second in closure and first not in closure
    with pytest.raises(Denied, match="exactly one native"):
        runtime_closure(
            lock_of(sdk({"name": "native"}), first, second),
            "skulk-capability-nodes",
            environment(FAMILIES[1]),
        )
    with pytest.raises(Denied, match="installs no extras"):
        runtime_closure(
            lock_of(sdk({"name": "native", "extra": ["fast"]}), first),
            "skulk-capability-nodes",
            environment(FAMILIES[1]),
        )


def fetch_by_name(file: LockedFile, limit: int) -> bytes:
    return file.filename.encode()[:limit]


def build_sdk(item: LockedPackage) -> tuple[str, bytes]:
    filename = f"{item.name.replace('-', '_')}-{item.version}-py3-none-any.whl"
    return filename, f"built {item.name}".encode()


def runtime_lock() -> Lockfile:
    return lock_of(
        sdk({"name": "native"}),
        package(
            "native",
            "1.0",
            "native-1.0-cp313-cp313-manylinux_2_28_x86_64.whl",
            "native-1.0-cp313-cp313-macosx_14_0_arm64.whl",
        ),
    )


def test_the_wheelhouse_holds_exactly_the_closure_owner_only(tmp_path: Path) -> None:
    """Locked wheels are fetched, source packages built, and the report says which."""
    output = tmp_path / "wheelhouse"
    report = build_wheelhouse(
        runtime_lock(),
        output,
        root="skulk-capability-nodes",
        family="linux-glibc-x86_64",
        environment=environment("linux-glibc-x86_64"),
        fetch=fetch_by_name,
        build=build_sdk,
    )
    assert report.floor == "manylinux_2_28_x86_64"
    assert [(entry.filename, entry.origin) for entry in report.wheels] == [
        ("native-1.0-cp313-cp313-manylinux_2_28_x86_64.whl", "locked"),
        ("skulk_capability_nodes-0.8.0-py3-none-any.whl", "built"),
    ]
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    for entry in report.wheels:
        path = output / entry.filename
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry.sha256


@pytest.mark.parametrize("fault", ["bytes", "occupied", "platform"])
def test_a_refused_wheelhouse_writes_nothing(tmp_path: Path, fault: str) -> None:
    """Changed bytes, a used directory or a platform gap leave no partial output."""
    output = tmp_path / "wheelhouse"
    if fault == "occupied":
        output.mkdir(mode=0o700)
        (output / "stale.whl").write_bytes(b"stale")

    def fetch(file: LockedFile, limit: int) -> bytes:
        return b"tampered" if fault == "bytes" else fetch_by_name(file, limit)

    with pytest.raises(Denied):
        build_wheelhouse(
            runtime_lock(),
            output,
            root="skulk-capability-nodes",
            family="linux-glibc-aarch64"
            if fault == "platform"
            else "linux-glibc-x86_64",
            environment=environment("linux-glibc-x86_64"),
            fetch=fetch,
            build=build_sdk,
        )
    assert not output.exists() or [p.name for p in output.iterdir()] == ["stale.whl"]


def test_git_sources_pin_a_full_commit_inside_the_repository() -> None:
    """Only the fragment's commit pins bytes; a subdirectory cannot escape."""
    assert git_source(
        f"https://github.com/example/sdk.git?subdirectory=sdk-public&rev=main#{COMMIT}"
    ) == ("https://github.com/example/sdk.git", "sdk-public", COMMIT)
    assert git_source(f"https://github.com/example/sdk.git?rev=main#{COMMIT}") == (
        "https://github.com/example/sdk.git",
        None,
        COMMIT,
    )
    with pytest.raises(Denied, match="full commit"):
        git_source("https://github.com/example/sdk.git?rev=main")
    with pytest.raises(Denied, match="outside"):
        git_source(f"https://github.com/example/sdk.git?subdirectory=../x#{COMMIT}")


def test_only_git_and_workspace_sources_are_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A direct URL or virtual package cannot be turned into runtime bytes."""

    def which(name: str) -> str:
        return "/usr/bin/true"

    monkeypatch.setattr("shutil.which", which)
    build = build_from_source(tmp_path, tmp_path)
    for source in ({"url": "https://example.invalid/x.tar.gz"}, {"virtual": "."}):
        with pytest.raises(Denied, match="registry wheel"):
            build(package("x", "1.0", source=source))


def test_an_unknown_lock_format_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "uv.lock"
    path.write_text("version = 2\n")
    with pytest.raises(Denied, match="uv lock"):
        load_lock(path)


@pytest.mark.parametrize("machine", ["x86_64", "aarch64"])
def test_native_candidate_floor_is_explicit_and_bounded(machine: str) -> None:
    """The candidate path admits2.34 native wheels without widening ordinary tags."""
    family = "linux-glibc-" + machine
    selected = platform_policy(family, native_candidate=True)
    assert selected[0] == "manylinux_2_34_" + machine
    assert platform_policy(family)[0] == "manylinux_2_28_" + machine
    assert "manylinux_2_35_" + machine not in selected
    binary = package(
        "native",
        "0.7.0rc4",
        "native-0.7.0rc4-cp313-abi3-manylinux_2_34_" + machine + ".whl",
    )
    with pytest.raises(Denied, match="prerelease"):
        select_wheel(binary, supported_tags(selected))
    chosen = select_wheel(binary, supported_tags(selected), native_candidate=True)
    assert chosen.filename == binary.wheels[0].filename
    with pytest.raises(Denied, match="no binary wheel"):
        select_wheel(
            binary, supported_tags(platform_policy(family)), native_candidate=True
        )


def test_native_candidate_closure_does_not_ignore_dependency_constraints() -> None:
    """Opting into prereleases does not authorize an incompatible locked version."""
    lock = lock_of(
        sdk(), package("native", "0.7.0rc4", "native-0.7.0rc4-py3-none-any.whl")
    )
    with pytest.raises(Denied, match="prerelease"):
        runtime_closure(
            lock,
            "skulk-capability-nodes",
            environment(FAMILIES[1]),
            ("native==0.7.0rc4",),
        )
    selected = runtime_closure(
        lock,
        "skulk-capability-nodes",
        environment(FAMILIES[1]),
        ("native==0.7.0rc4",),
        native_candidate=True,
    )
    assert {item.name for item in selected} == {"skulk-capability-nodes", "native"}
    with pytest.raises(Denied, match="incompatible"):
        runtime_closure(
            lock,
            "skulk-capability-nodes",
            environment(FAMILIES[1]),
            ("native>=1",),
            native_candidate=True,
        )


@pytest.mark.parametrize("family", FAMILIES)
def test_same_version_direct_sources_follow_exact_platform_resolution(
    family: str,
) -> None:
    """A universal native lock selects one platform URL without guessing by name."""
    candidates: list[LockedPackage] = []
    dependencies: list[dict[str, object]] = []
    for _platform, marker, filename in [
        (
            FAMILIES[0],
            "sys_platform == 'darwin' and platform_machine == 'arm64'",
            "native-0.7.0rc4-cp313-abi3-macosx_11_0_arm64.whl",
        ),
        (
            FAMILIES[1],
            "sys_platform == 'linux' and platform_machine == 'x86_64'",
            "native-0.7.0rc4-cp313-abi3-manylinux_2_34_x86_64.whl",
        ),
        (
            FAMILIES[2],
            "sys_platform == 'linux' and platform_machine == 'aarch64'",
            "native-0.7.0rc4-cp313-abi3-manylinux_2_34_aarch64.whl",
        ),
    ]:
        file = locked(filename)
        dependencies.append(
            {
                "name": "native",
                "version": "0.7.0rc4",
                "source": {"url": file.url},
                "marker": marker,
            }
        )
        candidates.append(
            LockedPackage.model_validate(
                {
                    "name": "native",
                    "version": "0.7.0rc4",
                    "source": {"url": file.url},
                    "resolution-markers": [marker],
                    "wheels": [file.model_dump()],
                }
            )
        )
    selected = runtime_closure(
        lock_of(sdk(*dependencies), *candidates),
        "skulk-capability-nodes",
        environment(family),
        ("native==0.7.0rc4",),
        native_candidate=True,
    )
    native_package = next(item for item in selected if item.name == "native")
    chosen = select_wheel(
        native_package,
        supported_tags(platform_policy(family, native_candidate=True)),
        native_candidate=True,
    )
    assert {file.filename for file in native_package.wheels} == {chosen.filename}
    assert ("macosx" in chosen.filename) == (family == FAMILIES[0])
    assert ("aarch64" in chosen.filename) == (family == FAMILIES[2])


def test_a_locked_source_reference_is_not_ignored() -> None:
    """Same-name/version sources remain distinct even when both have valid tags."""
    first = package(
        "native",
        "1.0",
        "native-1.0-py3-none-any.whl",
        source={"registry": "https://first.example/simple"},
    )
    second = first.model_copy(
        update={"source": {"registry": "https://second.example/simple"}}
    )
    root = sdk({"name": "native", "version": "1.0", "source": second.source})
    selected = runtime_closure(
        lock_of(root, first, second), "skulk-capability-nodes", environment(FAMILIES[0])
    )
    assert (
        next(item for item in selected if item.name == "native").source == second.source
    )
    with pytest.raises(Denied, match="exactly one"):
        runtime_closure(
            lock_of(sdk({"name": "native"}), first, second),
            "skulk-capability-nodes",
            environment(FAMILIES[0]),
        )


def _sdk1_legacy_feed(tmp_path: Path, architecture: str) -> tuple[list[str], Path]:
    """Prepare synthetic locked RC wheels at the SDK1 floor, without execution."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from skulk_capability_tools.assemble_feed import (
        ReleaseExpectation,
        manifest_digest,
        release_reference,
    )
    from skulk_capability_tools.release_runtime import ROOT_DEPENDENCIES
    from skulk_capability_tools.releases import Release
    from skulk_capability_tools.runtime_release import (
        RuntimeRelease,
        RuntimeWheel,
        SignedRuntimeRelease,
    )

    from skulk_capability_sdk.contracts import Descriptor, Manifest
    from skulk_capability_sdk.storage import atomic_private

    built = tmp_path / "candidate"
    built.mkdir(mode=0o700)
    dependencies = (*ROOT_DEPENDENCIES, "skulk-capability-kernel")
    names = ("skulk-capability-nodes", *dependencies)
    packages: list[LockedPackage] = []
    wheels: list[RuntimeWheel] = []
    for name in names:
        version = "0.18.0rc8" if name == "skulk-capability-nodes" else "1.0"
        tag = "py3-none-any"
        if name == "skulk-capability-kernel":
            version = "0.7.0rc7"
            tag = f"cp313-abi3-manylinux_2_34_{architecture}"
        filename = f"{name.replace('-', '_')}-{version}-{tag}.whl"
        item = package(
            name,
            version,
            filename,
            dependencies=tuple({"name": value} for value in dependencies)
            if name == "skulk-capability-nodes"
            else (),
        )
        packages.append(item)
        content = filename.encode()
        atomic_private(built / filename, content)
        wheels.append(
            RuntimeWheel(
                filename=filename,
                sha256=hashlib.sha256(content).hexdigest(),
                size=len(content),
            )
        )
    lock = lock_of(*packages)
    lines = ["version = 1"]
    for item in packages:
        lines.extend(
            [
                "[[package]]",
                "name = " + json.dumps(item.name),
                "version = " + json.dumps(item.version),
                'source = { registry = "https://pypi.org/simple" }',
                "dependencies = ["
                + ",".join(
                    "{ name = " + json.dumps(d.name) + " }" for d in item.dependencies
                )
                + "]",
                "wheels = [",
            ]
        )
        for file in item.wheels:
            lines.append(
                "{ url = "
                + json.dumps(file.url)
                + ", hash = "
                + json.dumps(file.hash)
                + ", size = "
                + str(file.size)
                + " },"
            )
        lines.append("]")
    lock_path = tmp_path / "uv.lock"
    lock_path.write_text("\n".join(lines) + "\n")
    content = b"synthetic capability; never executed"
    atomic_private(built / "bundle.pyz", content)
    manifest = Manifest(
        bundle_id="fixture.sdk1-legacy",
        bundle_version="1.0.0",
        executable="bundle.pyz",
        arguments=(),
        executable_sha256=hashlib.sha256(content).hexdigest(),
        descriptors=(
            Descriptor(
                id="fixture.observe",
                version="1.0.0",
                title="Observe",
                description="Synthetic declaration",
                input_schema={},
                output_schema={},
            ),
        ),
        effect="governed",
        skulk_requires=">=2.0.3,<3",
    )
    now = int(time.time())
    inventory = dict(sorted(wheel.distribution for wheel in wheels))
    release = Release(
        manifest=manifest,
        publisher="fixture",
        sequence=1,
        created_at=now - 10,
        expires_at=now + 3600,
        artifact_size=len(content),
        skulk_build_sha256="a" * 64,
        dependency_lock_sha256=hashlib.sha256(
            json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        platforms=("linux",),
        python_requires="==3.13.*",
        state_schema="fixture-v1",
        compatible_state_schemas=(),
        permissions=("Read synthetic input",),
    )
    runtime = RuntimeRelease(
        release=release, platform=f"linux-glibc-{architecture}", wheels=tuple(wheels)
    )
    record = SignedRuntimeRelease(runtime=runtime, signature="0" * 128)
    atomic_private(built / "release.json", record.model_dump_json().encode())
    reference = release_reference(built, lock)
    assert reference.manifest_sha256 == manifest_digest(manifest)
    # Exercise the actual CLI and its protocol-independent policy forwarding.
    expectation = ReleaseExpectation(
        publisher="fixture",
        sequence=1,
        expires_at=now + 3600,
        bundle_id=manifest.bundle_id,
        state_schema="fixture-v1",
        compatible_state_schemas=(),
        permissions=release.permissions,
        platforms=(runtime.platform,),
        reference=reference,
    )
    reference_path = tmp_path / "reference.json"
    reference_path.write_text(expectation.reference.model_dump_json())
    key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "publisher.key"
    atomic_private(key_path, key.private_bytes_raw())
    output = tmp_path / "feed"
    return [
        "--private-key",
        str(key_path),
        "--public-key",
        key.public_key().public_bytes_raw().hex(),
        "--publisher",
        "fixture",
        "--feed-url",
        "https://feed.example.test/",
        "--catalog-revision",
        "1",
        "--catalog-expires-at",
        str(now + 3600),
        "--sequence",
        "1",
        "--release-expires-at",
        str(now + 3600),
        "--bundle-id",
        manifest.bundle_id,
        "--state-schema",
        "fixture-v1",
        "--permission",
        "Read synthetic input",
        "--platform",
        runtime.platform,
        "--lock",
        str(lock_path),
        "--reference",
        str(reference_path),
        "--output",
        str(output),
        "--trust-output",
        str(tmp_path / "trust.json"),
        str(built),
    ], output


@pytest.mark.parametrize("architecture", ["x86_64", "aarch64"])
@pytest.mark.parametrize("candidate", [False, True])
def test_sdk1_dependency_policy_keeps_legacy_governed_protocol(
    tmp_path: Path, architecture: str, candidate: bool
) -> None:
    """RC/floor opt-in changes wheel admission while retaining runtime2/protocol4."""
    from skulk_capability_tools.assemble_feed import main, verify_feed
    from skulk_capability_tools.releases import PublisherTrust
    from skulk_capability_tools.runtime_release import SignedRuntimeRelease

    arguments, output = _sdk1_legacy_feed(tmp_path, architecture)
    try:
        if not candidate:
            with pytest.raises(SystemExit) as refused:
                main(arguments)
            assert refused.value.code == 2
            assert not output.exists()
            return
        main(["--native-candidate", *arguments])
    finally:
        (tmp_path / "publisher.key").unlink()
    trust = PublisherTrust.model_validate_json((tmp_path / "trust.json").read_bytes())
    catalog = verify_feed(
        output, "https://feed.example.test/", trust, now=int(time.time())
    )
    assert catalog.protocol == 2
    path = (
        output
        / "fixture.sdk1-legacy/1"
        / f"linux-glibc-{architecture}"
        / "release.json"
    )
    signed = SignedRuntimeRelease.model_validate_json(path.read_bytes())
    assert signed.runtime.protocol == 2
    assert signed.runtime.release.manifest.protocol == 4
    assert signed.runtime.release.manifest.effect == "governed"


def test_sdk1_feed_policy_still_rejects_a_wrong_locked_hash(tmp_path: Path) -> None:
    """The RC opt-in never substitutes a candidate's wheel for its locked bytes."""
    from skulk_capability_tools.assemble_feed import main

    arguments, output = _sdk1_legacy_feed(tmp_path, "x86_64")
    lock_path = tmp_path / "uv.lock"
    source = lock_path.read_text()
    original = locked("skulk_capability_nodes-0.18.0rc8-py3-none-any.whl").hash
    lock_path.write_text(source.replace(original, "sha256:" + "0" * 64))
    try:
        with pytest.raises(SystemExit) as refused:
            main(["--native-candidate", *arguments])
        assert refused.value.code == 2
        assert not output.exists()
    finally:
        (tmp_path / "publisher.key").unlink()
