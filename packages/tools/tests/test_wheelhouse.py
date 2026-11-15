"""The wheelhouse recipe takes exactly the locked wheels, at the platform floor."""

import hashlib
import stat
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
