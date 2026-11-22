"""Build a native Node capability's selected assets and Python bootstrap.

No author hooks or provider code execute while packaging. Existing release-three
and runtime-five signatures bind the resulting zipapp and complete wheel closure.
"""

import hashlib
import importlib.metadata
import io
import os
import stat
import tomllib
import zipfile
from pathlib import Path
from typing import Literal, Self, final

from packaging.version import Version
from pydantic import Field, JsonValue, TypeAdapter, field_validator, model_validator

from skulk_capability_sdk.contracts import Contract, Denied, canonical
from skulk_capability_sdk.node_application import (
    MAX_APPLICATION_BYTES,
    MAX_APPLICATION_FILES,
    MAX_APPLICATION_METADATA,
    MAX_ASSET_BYTES,
    NodeApplication,
    NodeAsset,
    asset_path,
    generated_asset,
)
from skulk_capability_sdk.node_runtime import NODE_VERSION, measured_file
from skulk_capability_sdk.storage import secure_directory

from .inspection import read_public
from .native_publishing import NativeManifest
from .node_runtime import asset_wheel_bytes
from .runtime_release import RuntimeWheel

_BOOTSTRAP = b"from skulk_capability_sdk.node_application import main\nmain()\n"


@final
class NodeBuild(Contract):
    """Unsigned output identities; these do not admit installation or execution."""

    bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    application: NodeApplication
    assets: RuntimeWheel
    manifest: NativeManifest


@final
class NodeProject(Contract):
    """Explicit staging inputs from node-capability.toml, not a host declaration."""

    format: Literal[1] = 1
    assets: str = Field(description="Project-relative compiled staging directory.")
    entry: str = Field(description="Staging-relative default factory module.")
    license: str = Field(default="LICENSE", description="Project-relative license.")

    @field_validator("format", mode="before")
    @classmethod
    def exact_format(cls, value: object) -> object:
        """Keep the project format separate from boolean or floating aliases."""
        if type(value) is not int or value != 1:
            raise ValueError("Node project format must be integer1")
        return value

    @model_validator(mode="after")
    def relative_inputs(self) -> Self:
        """Refuse absolute or aliased project inputs before source acquisition."""
        for value in (self.assets, self.entry, self.license):
            asset_path(value)
        return self


def node_project_artifacts(
    project: Path, manifest: NativeManifest | None = None
) -> tuple[NodeBuild, bytes, bytes]:
    """Read a Node project once and construct bytes without executing its hooks."""
    project = project.resolve(strict=True)
    spec = NodeProject.model_validate(
        tomllib.loads(read_public(project / "node-capability.toml", 65536).decode())
    )
    for relative in (spec.assets, spec.license):
        path = project
        for part in Path(relative).parts:
            path = path / part
            if path.is_symlink():
                raise Denied("Node project inputs must not traverse links")
    declared = manifest or NativeManifest(
        canonical_json=read_public(project / "manifest.json", 262144).decode()
    )
    return node_application_artifacts(
        project / spec.assets,
        declared,
        entry=spec.entry,
        license_text=read_public(project / spec.license, 1024 * 1024),
    )


def build_node_project(project: Path, output: Path) -> NodeBuild:
    """Build explicit native Node source without running scripts or provider hooks."""
    report, bundle, wheel = node_project_artifacts(project)
    _write_artifacts(report, bundle, wheel, output)
    return report


def _assets(directory: Path) -> dict[str, bytes]:
    content: dict[str, bytes] = {}
    total = folders = 0
    for parent, subdirectories, names in os.walk(directory, followlinks=False):
        folders += 1
        if folders > 2 * MAX_APPLICATION_FILES:
            raise Denied("Node application source directories exceed their bound")
        root = Path(parent)
        if root.is_symlink():
            raise Denied("Node application source contains a directory link")
        for name in subdirectories:
            if (root / name).is_symlink():
                raise Denied("Node application source contains a directory link")
        for name in sorted(names):
            path = root / name
            if generated_asset(name):
                if not stat.S_ISREG(path.lstat().st_mode):
                    raise Denied("Node application bookkeeping is not a regular file")
                continue
            if name in {
                ".env",
                ".npmrc",
                ".pypirc",
                "publisher.key",
            } or name.startswith(".env."):
                raise Denied("Node application source contains private configuration")
            if len(content) >= MAX_APPLICATION_FILES:
                raise Denied("Node application source files exceed their bound")
            digest = measured_file(path, maximum=MAX_ASSET_BYTES)
            raw = read_public(path, MAX_ASSET_BYTES)
            if hashlib.sha256(raw).hexdigest() != digest:
                raise Denied("Node application source changed during packaging")
            total += len(raw)
            if total > MAX_APPLICATION_BYTES:
                raise Denied("Node application source bytes exceed their bound")
            content[path.relative_to(directory).as_posix()] = raw
    return content


def node_application_artifacts(
    directory: Path,
    manifest: NativeManifest,
    *,
    entry: str,
    license_text: bytes,
) -> tuple[NodeBuild, bytes, bytes]:
    """Build a declared compiled tree, returning report, zipapp and asset wheel.

    The directory is an explicit staging tree, not the author's project root.
    Put compiled modules and production node_modules there. The signed manifest
    must already declare the generated asset dependency; packaging cannot expand
    permissions or add a dependency the author did not request.
    """
    if not license_text or len(license_text) > 1024 * 1024:
        raise Denied("Node application requires bounded license text")
    content = _assets(directory)
    package = TypeAdapter(dict[str, JsonValue]).validate_json(
        content.get("node_modules/@foxlight/skulk-capability-sdk/package.json", b"{}")
    )
    version_value = package.get("version")
    if (
        package.get("name") != "@foxlight/skulk-capability-sdk"
        or not isinstance(version_value, str)
        or Version(version_value)
        != Version(importlib.metadata.version("skulk-capability-sdk"))
    ):
        raise Denied("Node SDK and Python SDK must use the matching author cohort")
    application = NodeApplication(
        bundle_id=manifest.bundle_id,
        bundle_version=manifest.bundle_version,
        entry=entry,
        files=tuple(
            NodeAsset(path=name, size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            for name, raw in sorted(content.items())
        ),
    )
    if application.distribution not in manifest.runtime_dependencies:
        raise Denied("Node application asset dependency is not declared")
    raw_application = canonical(application)
    if len(raw_application) > MAX_APPLICATION_METADATA:
        raise Denied("Node application metadata exceeds its bound")
    version = str(Version(application.bundle_version))
    distribution = application.distribution.replace("-", "_")
    metadata = f"{distribution}-{version}.dist-info"
    members = {application.namespace + "/" + name: raw for name, raw in content.items()}
    members[metadata + "/METADATA"] = (
        f"Metadata-Version: 2.4\nName: {application.distribution}\nVersion: {version}\n"
        "Summary: Selected Node capability application assets\n"
        "Requires-Python: ==3.13.*\nLicense-File: LICENSE\n"
        f"Requires-Dist: skulk-node-runtime=={NODE_VERSION}\n\n"
    ).encode()
    members[metadata + "/WHEEL"] = (
        b"Wheel-Version: 1.0\nGenerator: skulk-capability-tools\n"
        b"Root-Is-Purelib: true\nTag: py3-none-any\n"
    )
    members[metadata + "/licenses/LICENSE"] = license_text
    wheel = asset_wheel_bytes(members, metadata, executable=set())
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, raw in (
            ("__main__.py", _BOOTSTRAP),
            ("node-application.json", raw_application),
        ):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, raw, compresslevel=9)
    bundle = buffer.getvalue()
    digest = hashlib.sha256(bundle).hexdigest()
    assets = RuntimeWheel(
        filename=f"{distribution}-{version}-py3-none-any.whl",
        size=len(wheel),
        sha256=hashlib.sha256(wheel).hexdigest(),
    )
    return (
        NodeBuild(
            bundle_sha256=digest,
            application=application,
            assets=assets,
            manifest=manifest.with_artifact(digest),
        ),
        bundle,
        wheel,
    )


def build_node_application(
    directory: Path,
    manifest: NativeManifest,
    output: Path,
    *,
    entry: str,
    license_text: bytes,
) -> NodeBuild:
    """Write unsigned artifacts once, refusing existing outputs without executing."""
    report, bundle, wheel = node_application_artifacts(
        directory, manifest, entry=entry, license_text=license_text
    )
    _write_artifacts(report, bundle, wheel, output)
    return report


def _write_artifacts(
    report: NodeBuild, bundle: bytes, wheel: bytes, output: Path
) -> None:
    secure_directory(output)
    if any(output.iterdir()):
        raise Denied("Node application output must be empty")
    for name, raw in (
        ("bundle.pyz", bundle),
        (report.assets.filename, wheel),
        ("manifest.json", report.manifest.canonical_json.encode()),
        ("node-build.json", report.model_dump_json(indent=2).encode()),
    ):
        descriptor = os.open(
            output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
