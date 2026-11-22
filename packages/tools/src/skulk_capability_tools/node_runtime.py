"""Build a deterministic runtime wheel from reviewed official Node assets."""

import base64
import csv
import hashlib
import io
import json
import os
import stat
import tarfile
import zipfile
from pathlib import Path

from skulk_capability_sdk.contracts import Denied
from skulk_capability_sdk.node_runtime import (
    NODE_VERSION,
    NodePlatform,
    NodeSource,
    measured_file,
    node_metadata,
    node_source,
)
from skulk_capability_sdk.storage import secure_directory

from .runtime_release import RuntimeWheel

_TAGS = {
    "macos-arm64": "macosx_11_0_arm64",
    "linux-glibc-x86_64": "manylinux_2_34_x86_64",
    "linux-glibc-aarch64": "manylinux_2_34_aarch64",
}


def _payload(archive: Path, source: NodeSource) -> tuple[bytes, bytes]:
    if (
        archive.stat().st_size != source.archive_size
        or measured_file(archive, maximum=64 * 1024 * 1024) != source.archive_sha256
    ):
        raise Denied("Node archive differs from reviewed upstream bytes")
    prefix = source.archive_name.removesuffix(".tar.xz")
    targets = {
        prefix + "/bin/node": source.binary_size,
        prefix + "/LICENSE": source.license_size,
    }
    result: dict[str, bytes] = {}
    # Select two exact regular members; never extract an upstream tree, follow a
    # link, execute npm, or retain bundled tooling that the managed app does not use.
    with archive.open("rb") as stream, tarfile.open(fileobj=stream, mode="r|xz") as tar:
        count = total = 0
        for member in tar:
            count += 1
            total += member.size
            if count > 30000 or total > 512 * 1024 * 1024 or member.size < 0:
                raise Denied("Node archive inventory exceeds its bound")
            if member.name not in targets:
                continue
            if (
                member.name in result
                or not member.isfile()
                or member.size != targets[member.name]
            ):
                raise Denied("Node archive contains an invalid selected member")
            content = tar.extractfile(member)
            if content is None:
                raise Denied("Node archive selected member is missing")
            result[member.name] = content.read(member.size + 1)
    if set(result) != set(targets):
        raise Denied("Node archive omits a selected member")
    binary, license_text = result[prefix + "/bin/node"], result[prefix + "/LICENSE"]
    if (
        len(binary) != source.binary_size
        or len(license_text) != source.license_size
        or hashlib.sha256(binary).hexdigest() != source.binary_sha256
        or hashlib.sha256(license_text).hexdigest() != source.license_sha256
    ):
        raise Denied("Node archive selected content differs from its cohort")
    return binary, license_text


def node_wheel(archive: Path, family: NodePlatform) -> tuple[str, bytes]:
    """Repackage exact upstream binary/license bytes without compiling or running.

    The tag states this SDK's qualified platform baseline. All wheel members
    and RECORD hashes are deterministic; no interpreter or installer is embedded.
    """
    source = node_source(family)
    binary, license_text = _payload(archive, source)
    tag = "py3-none-" + _TAGS[family]
    metadata = f"skulk_node_runtime-{NODE_VERSION}.dist-info"
    members = {
        "_skulk_node_runtime/__init__.py": b'"""Pinned Node runtime assets."""\n',
        "_skulk_node_runtime/bin/node": binary,
        "_skulk_node_runtime/LICENSE": license_text,
        "_skulk_node_runtime/runtime.json": node_metadata(source),
        metadata + "/licenses/LICENSE": license_text,
        metadata + "/METADATA": (
            "Metadata-Version: 2.4\nName: skulk-node-runtime\n"
            f"Version: {NODE_VERSION}\n"
            "Summary: Measured Node runtime assets for Skulk capabilities\n"
            "Requires-Python: ==3.13.*\nLicense-Expression: MIT\n"
            "License-File: LICENSE\n"
            f"Project-URL: Upstream, {source.url}\n\n"
            "Exact upstream binary and full upstream third-party notices.\n"
        ).encode(),
        metadata + "/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: skulk-capability-tools\n"
            f"Root-Is-Purelib: false\nTag: {tag}\n"
        ).encode(),
    }
    return (
        f"skulk_node_runtime-{NODE_VERSION}-{tag}.whl",
        asset_wheel_bytes(
            members, metadata, executable={"_skulk_node_runtime/bin/node"}
        ),
    )


def asset_wheel_bytes(
    content: dict[str, bytes], metadata: str, *, executable: set[str]
) -> bytes:
    """Write deterministic ordinary wheel members and their complete RECORD."""
    members = dict(content)
    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    for name, raw in sorted(members.items()):
        digest = (
            base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
        )
        writer.writerow((name, "sha256=" + digest, str(len(raw))))
    writer.writerow((metadata + "/RECORD", "", ""))
    members[metadata + "/RECORD"] = record.getvalue().encode()
    output = io.BytesIO()
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as wheel:
        for name, raw in sorted(members.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (
                stat.S_IFREG | (0o755 if name in executable else 0o644)
            ) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            wheel.writestr(info, raw, compresslevel=9)
    value = output.getvalue()
    if len(value) > 64 * 1024 * 1024:
        raise Denied("Node wheel exceeds the runtime artifact bound")
    return value


def build_node_runtime(
    archive: Path, family: NodePlatform, output: Path
) -> RuntimeWheel:
    """Build and record one immutable wheel in an empty private output directory."""
    name, raw = node_wheel(archive, family)
    record = RuntimeWheel(
        filename=name, size=len(raw), sha256=hashlib.sha256(raw).hexdigest()
    )
    secure_directory(output)
    if any(output.iterdir()):
        raise Denied("Node runtime output must be empty")
    for filename, content in (
        (name, raw),
        (
            "node-runtime.json",
            (json.dumps(record.model_dump(mode="json"), indent=2) + "\n").encode(),
        ),
    ):
        descriptor = os.open(
            output / filename,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    return record
