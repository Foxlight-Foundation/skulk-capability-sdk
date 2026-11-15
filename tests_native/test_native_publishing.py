"""Public native publisher declarations against the actual compiler-free ABI.

These tests validate metadata and signature domains. They do not stand in for
installed owner/Core qualification or binary artifact byte verification.
"""

import hashlib
import importlib.metadata
import json

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import JsonValue, ValidationError
from skulk_capability_tools.native_publishing import (
    NativeManifest,
    NativeProfiles,
    NativeRelease,
    NativeRuntimeRelease,
    native_cohort,
    native_runtime_message,
)
from skulk_capability_tools.release_runtime import ROOT_DEPENDENCIES
from skulk_capability_tools.releases import Release
from skulk_capability_tools.runtime_release import (
    RuntimeRelease,
    RuntimeWheel,
    current_platform,
)
from test_application import startup_value

from skulk_capability_sdk.contracts import canonical


def declaration() -> NativeManifest:
    """Reuse a frozen author fixture through the actual compiled managed/2 reader."""
    value = startup_value("/tmp/publisher-control", "/tmp/publisher-stream", "unary")
    return NativeManifest(canonical_json=json.dumps(value["manifest"]))


def runtime() -> NativeRuntimeRelease:
    """Construct metadata-only roots around the exact public native asset."""
    profiles = NativeProfiles.installed()
    receipt = native_cohort(profiles.version)
    platforms = receipt["platforms"]
    assert isinstance(platforms, list)
    item = next(
        value
        for value in platforms
        if isinstance(value, dict) and value["platform"] == current_platform()
    )
    assert isinstance(item, dict) and isinstance(item["wheel"], dict)
    kernel = item["wheel"]
    wheels = [
        RuntimeWheel.model_validate(
            {
                "filename": kernel["name"],
                "size": kernel["size"],
                "sha256": kernel["sha256"],
            }
        )
    ]
    for name in ROOT_DEPENDENCIES:
        version = "0.5.11rc1" if name == "skulk-capability-sdk" else "1.0.0"
        wheels.append(
            RuntimeWheel(
                filename=name.replace("-", "_") + "-" + version + "-py3-none-any.whl",
                size=1,
                sha256="a" * 64,
            )
        )
    inventory = dict(sorted(wheel.distribution for wheel in wheels))
    digest = hashlib.sha256(
        json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    release = NativeRelease(
        publisher="synthetic",
        sequence=1,
        created_at=100,
        expires_at=200,
        artifact_size=1,
        manifest=declaration().with_artifact("b" * 64),
        platforms=("darwin", "linux"),
        skulk_build_sha256="c" * 64,
        dependency_lock_sha256=digest,
        state_schema="synthetic-v1",
        compatible_state_schemas=(),
        permissions=("Account-free publisher fixture",),
    )
    return NativeRuntimeRelease(
        release=release,
        platform=current_platform(),
        kernel=profiles,
        wheels=tuple(wheels),
    )


def test_native_identity_and_artifact_binding_use_the_actual_compiled_reader() -> None:
    """Binding an archive changes the native identity without legacy revisions."""
    original = declaration()
    following = original.with_artifact("b" * 64)
    assert len(original.digest) == len(following.digest) == 64
    assert original.digest != following.digest
    assert following.executable == "bundle.pyz"
    assert following.executable_sha256 == "b" * 64
    assert following.bundle_id == original.bundle_id
    assert following.runtime_dependencies == original.runtime_dependencies


def test_exact_native_signature_domains_do_not_widen_legacy_readers() -> None:
    """Native signatures cannot be replayed under the legacy runtime domain."""
    selected = runtime()
    key = Ed25519PrivateKey.generate()
    signed = key.sign(native_runtime_message(selected))
    key.public_key().verify(signed, b"skulk.runtime/5\x00" + canonical(selected))
    with pytest.raises(InvalidSignature):
        key.public_key().verify(signed, canonical(selected))
    assert (
        selected.digest == hashlib.sha256(native_runtime_message(selected)).hexdigest()
    )
    assert (
        selected.release.digest
        == hashlib.sha256(
            b"skulk.release/3\x00" + canonical(selected.release)
        ).hexdigest()
    )
    with pytest.raises(ValidationError):
        RuntimeRelease.model_validate_json(selected.model_dump_json())
    with pytest.raises(ValidationError):
        Release.model_validate_json(selected.release.model_dump_json())


@pytest.mark.parametrize("value", [True, 3.0, "3", 2, 5])
def test_native_release_version_has_no_numeric_aliases(value: JsonValue) -> None:
    """An explicit native version cannot be selected through coercion."""
    fields = runtime().release.model_dump(mode="json")
    fields["protocol"] = value
    with pytest.raises(ValidationError):
        NativeRelease.model_validate_json(json.dumps(fields))


@pytest.mark.parametrize("value", [True, 5.0, "5", 3, 4])
def test_native_runtime_version_has_no_numeric_aliases(value: JsonValue) -> None:
    """The runtime-five envelope cannot be selected through JSON coercion."""
    fields = runtime().model_dump(mode="json")
    fields["protocol"] = value
    with pytest.raises(ValidationError):
        NativeRuntimeRelease.model_validate_json(json.dumps(fields))


@pytest.mark.parametrize(
    "fault",
    [
        "source",
        "profile",
        "wheel-hash",
        "wheel-size",
        "inventory",
        "duplicate",
        "sdk",
        "platform",
    ],
)
def test_native_publisher_refuses_crossed_cohorts_and_incomplete_metadata(
    fault: str,
) -> None:
    """Correctly shaped claims cannot rename a binary or cross a source/profile."""
    selected = runtime()
    value = selected.model_dump(mode="json")
    if fault in {"source", "profile"}:
        value["kernel"]["source_commit" if fault == "source" else "portable"] = "a" * (
            40 if fault == "source" else 64
        )
    elif fault in {"wheel-hash", "wheel-size"}:
        value["wheels"][0]["sha256" if fault == "wheel-hash" else "size"] = (
            "b" * 64 if fault == "wheel-hash" else 2
        )
    elif fault == "inventory":
        value["release"]["dependency_lock_sha256"] = "a" * 64
    elif fault == "duplicate":
        value["wheels"].append(value["wheels"][0])
    elif fault == "sdk":
        for wheel in value["wheels"]:
            if wheel["filename"].startswith("skulk_capability_sdk-"):
                wheel["filename"] = "skulk_capability_sdk-0.5.7-py3-none-any.whl"
        inventory = dict(
            sorted(
                RuntimeWheel.model_validate(wheel).distribution
                for wheel in value["wheels"]
            )
        )
        value["release"]["dependency_lock_sha256"] = hashlib.sha256(
            json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    else:
        value["platform"] = "macos-x86_64"
    with pytest.raises(ValidationError):
        NativeRuntimeRelease.model_validate_json(json.dumps(value))


def test_cohort_data_matches_the_real_installed_binary() -> None:
    """Receipt selection is explicit and verifies all actual immutable profiles."""
    selected = NativeProfiles.installed()
    assert selected.version == importlib.metadata.version("skulk-capability-kernel")
    assert selected.source_commit == native_cohort(selected.version)["source_commit"]
    with pytest.raises(ValueError, match="unavailable"):
        native_cohort("99.0.0")
