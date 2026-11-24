"""Native catalog and public distribution use actual compiled metadata and domains."""

import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import JsonValue, TypeAdapter, ValidationError
from skulk_capability_tools import build_catalog, inspection
from skulk_capability_tools.assemble_feed import (
    ReferenceWheel,
    ReleaseExpectation,
    ReleaseReference,
)
from skulk_capability_tools.catalog import SignedCatalog, signed_record
from skulk_capability_tools.native_catalog import (
    NativeCatalog,
    NativeCatalogBinding,
    SignedNativeCatalog,
    native_catalog_message,
    native_entry_from_record,
    verify_native_catalog,
)
from skulk_capability_tools.native_feed import (
    NativeBuiltRelease,
    check_native_release_set,
    read_native_built,
)
from skulk_capability_tools.native_publishing import (
    NativeManifest,
    NativeRuntimeRelease,
    SignedNativeRuntimeRelease,
    native_runtime_message,
)
from skulk_capability_tools.native_runtime_recipe import NativeRuntimeRecipe
from skulk_capability_tools.publication_records import (
    publication_digest,
    publication_message,
    read_publication_record,
)
from skulk_capability_tools.releases import PublisherTrust
from skulk_capability_tools.wheelhouse import (
    LockedDependency,
    LockedFile,
    LockedPackage,
    Lockfile,
)
from test_native_publishing import runtime

from skulk_capability_sdk.contracts import Denied, canonical
from skulk_capability_sdk.storage import atomic_private

_OBJECT = TypeAdapter(dict[str, JsonValue])


def signed_native(key: Ed25519PrivateKey) -> SignedNativeRuntimeRelease:
    """Sign a synthetic runtime whose metadata uses the actual qualified native ABI."""
    selected = runtime()
    return SignedNativeRuntimeRelease(
        runtime=selected, signature=key.sign(native_runtime_message(selected)).hex()
    )


def native_policy_fixture() -> tuple[NativeBuiltRelease, ReleaseExpectation, Lockfile]:
    """Supply independent policy and locked inventory for signer refusal cases."""
    selected = signed_native(Ed25519PrivateKey.generate())
    release = selected.runtime.release
    reference = ReleaseReference(
        bundle_sha256=release.manifest.executable_sha256,
        bundle_size=release.artifact_size,
        manifest_sha256=hashlib.sha256(canonical(release.manifest)).hexdigest(),
        built_wheels={},
    )
    expected = ReleaseExpectation(
        publisher=release.publisher,
        sequence=release.sequence,
        expires_at=release.expires_at,
        bundle_id=release.manifest.bundle_id,
        state_schema=release.state_schema,
        compatible_state_schemas=release.compatible_state_schemas,
        permissions=release.permissions,
        platforms=(selected.runtime.platform,),
        reference=reference,
    )
    packages = tuple(
        LockedPackage(
            name=wheel.distribution[0],
            version=wheel.distribution[1],
            source={"registry": "https://example.test/simple"},
            wheels=(
                LockedFile(
                    url="https://example.test/" + wheel.filename,
                    hash="sha256:" + wheel.sha256,
                    size=wheel.size,
                ),
            ),
        )
        for wheel in selected.runtime.wheels
    )
    lock = Lockfile(
        version=1,
        package=tuple(
            package.model_copy(
                update={
                    "dependencies": tuple(
                        LockedDependency(name=item.name)
                        for item in packages
                        if item.name != package.name
                    )
                }
            )
            if package.name == "skulk-capability-sdk"
            else package
            for package in packages
        ),
    )
    return NativeBuiltRelease(selected, {}), expected, lock


@pytest.mark.parametrize(
    "fault", ["publisher", "sequence", "manifest", "wheel", "platform", "duplicate"]
)
def test_native_signer_compares_independent_policy_lock_and_rebuild(fault: str) -> None:
    """No candidate can nominate the policy or bytes the publisher signs against."""
    built, expected, lock = native_policy_fixture()
    check_native_release_set([built], expected, lock, "skulk-capability-sdk")
    values = expected.model_dump(mode="json")
    if fault in ("publisher", "sequence"):
        values[fault] = "different" if fault == "publisher" else 2
        expected = ReleaseExpectation.model_validate_json(json.dumps(values))
    elif fault == "manifest":
        expected = expected.model_copy(
            update={
                "reference": expected.reference.model_copy(
                    update={"manifest_sha256": "d" * 64}
                )
            }
        )
    elif fault == "wheel":
        package = lock.package[-1]
        wrong = package.wheels[0].model_copy(update={"hash": "sha256:" + "d" * 64})
        lock = lock.model_copy(
            update={
                "package": (
                    *lock.package[:-1],
                    package.model_copy(update={"wheels": (wrong,)}),
                )
            }
        )
    elif fault == "platform":
        expected = expected.model_copy(update={"platforms": ("unqualified-platform",)})
    with pytest.raises(Denied):
        check_native_release_set(
            [built, replace(built)] if fault == "duplicate" else [built],
            expected,
            lock,
            "skulk-capability-sdk",
        )


@pytest.mark.parametrize("fault", ["version", "hash", "duplicate", "format"])
def test_native_recipe_binds_exact_published_binary_and_independent_roots(
    fault: str,
) -> None:
    """Recipes cannot cross ABI assets or silently select another runtime."""
    built, _, _ = native_policy_fixture()
    selected = built.runtime
    wheels = [
        dict(
            url="https://example.test/" + wheel.filename,
            hash="sha256:" + wheel.sha256,
            size=wheel.size,
        )
        for wheel in selected.wheels
    ]
    owner = dict(
        url="https://example.test/skulk_capability_nodes-0.18.0rc2-py3-none-any.whl",
        hash="sha256:" + "e" * 64,
        size=1,
    )
    wheels.append(owner)
    values: dict[str, object] = dict(
        format=2,
        owner_version="0.18.0rc2",
        sdk_version="0.5.11rc1",
        platform=selected.platform,
        source_owner_commit="a" * 40,
        source_runtime_digest=selected.digest,
        kernel=selected.kernel.model_dump(mode="json"),
        wheels=wheels,
    )
    NativeRuntimeRecipe.model_validate_json(json.dumps(values))
    if fault == "version":
        values["owner_version"] = "0.18.0rc3"
    elif fault == "hash":
        wheels[0]["hash"] = "sha256:" + "d" * 64
    elif fault == "duplicate":
        wheels.append(owner)
    else:
        values["format"] = 2.0
    with pytest.raises(ValueError):
        NativeRuntimeRecipe.model_validate_json(json.dumps(values))


def test_native_signer_refuses_changed_artifact_before_any_wheel_loading(
    tmp_path: Path,
) -> None:
    """A built candidate's declared bundle is hashed before it can enter signing."""
    signed = signed_native(Ed25519PrivateKey.generate())
    atomic_private(tmp_path / "release.json", signed.model_dump_json().encode())
    atomic_private(tmp_path / "bundle.pyz", b"a")
    with pytest.raises(Denied, match="bytes differ"):
        read_native_built(tmp_path)


@pytest.mark.parametrize("fault", ["none", "missing", "hash", "registry"])
def test_native_signer_requires_independent_size_for_hash_locked_direct_urls(
    fault: str,
) -> None:
    """URL locks need matching independent sizes; registry rules stay unchanged."""
    built, expected, lock = native_policy_fixture()
    kernel = next(
        item for item in lock.package if item.name == "skulk-capability-kernel"
    )
    file = kernel.wheels[0]
    selected = kernel.model_copy(
        update={
            "source": {"registry": "https://example.test/simple"}
            if fault == "registry"
            else {"url": str(file.url)},
            "wheels": (file.model_copy(update={"size": None}),),
        }
    )
    lock = lock.model_copy(
        update={
            "package": tuple(
                selected if item.name == kernel.name else item for item in lock.package
            )
        }
    )
    reference = expected.reference.model_copy(
        update={
            "built_wheels": {}
            if fault == "missing"
            else {
                file.filename: ReferenceWheel(
                    sha256="d" * 64 if fault == "hash" else file.sha256,
                    size=file.size or 1,
                ),
            }
        }
    )
    expected = expected.model_copy(update={"reference": reference})
    if fault == "none":
        check_native_release_set([built], expected, lock, "skulk-capability-sdk")
    else:
        with pytest.raises(Denied):
            check_native_release_set([built], expected, lock, "skulk-capability-sdk")


def test_explicit_publication_reader_preserves_native_signature_and_identity() -> None:
    """The new reader supports native five; the frozen legacy reader refuses it."""
    key = Ed25519PrivateKey.generate()
    original = signed_native(key)
    observed = read_publication_record(original.model_dump_json().encode())
    assert isinstance(observed, SignedNativeRuntimeRelease)
    assert publication_digest(observed) == original.runtime.digest
    key.public_key().verify(
        bytes.fromhex(observed.signature), publication_message(observed)
    )
    with pytest.raises(ValidationError):
        signed_record(original.model_dump_json().encode())


@pytest.mark.parametrize("change", ["manifest", "default", "domain"])
def test_altered_native_claims_cannot_borrow_a_valid_signature(change: str) -> None:
    """Normalization and default insertion cannot restore an altered served payload."""
    key = Ed25519PrivateKey.generate()
    signed = signed_native(key)
    fields = _OBJECT.validate_json(signed.model_dump_json())
    claims = _OBJECT.validate_python(fields["runtime"], strict=True)
    if change == "manifest":
        release = _OBJECT.validate_python(claims["release"], strict=True)
        manifest = _OBJECT.validate_python(release["manifest"], strict=True)
        manifest["canonical_json"] = " " + str(manifest["canonical_json"])
        release["manifest"] = manifest
        claims["release"] = release
    elif change == "default":
        del claims["control_profile"]
    else:
        fields["signature"] = key.sign(canonical(signed.runtime)).hex()
    fields["runtime"] = claims
    if change == "domain":
        observed = read_publication_record(json.dumps(fields).encode())
        with pytest.raises(InvalidSignature):
            key.public_key().verify(
                bytes.fromhex(observed.signature), publication_message(observed)
            )
    else:
        with pytest.raises(ValueError, match="exact input"):
            read_publication_record(json.dumps(fields).encode())


def test_catalog_projection_binds_full_compiled_identity_and_processing_domains() -> (
    None
):
    """Native metadata keeps its full manifest identity and processing domains."""
    signed = signed_native(Ed25519PrivateKey.generate())
    raw = signed.model_dump_json().encode()
    entry = native_entry_from_record(raw, "https://example.test/native/1/")
    assert entry.release_sha256 == hashlib.sha256(raw).hexdigest()
    assert entry.native.manifest_revision == signed.runtime.release.manifest.digest
    assert len(entry.native.manifest_revision) == 64
    assert entry.native.kernel == signed.runtime.kernel
    assert entry.native.processing_domains == ("external",)
    assert entry.release_digest == signed.runtime.digest
    assert entry.transfer_bytes == signed.runtime.release.artifact_size + sum(
        wheel.size for wheel in signed.runtime.wheels
    )


def test_native_catalog_preserves_compiled_inference_steward_risk() -> None:
    """A legal native inference exposure is discoverable with its actual risk."""
    selected = runtime()
    value = _OBJECT.validate_json(selected.release.manifest.canonical_json)
    contracts = _OBJECT.validate_python(value["contracts"], strict=True)
    descriptors = contracts["descriptors"]
    assert isinstance(descriptors, list) and isinstance(descriptors[0], dict)
    descriptor = descriptors[0]
    assert descriptor["behavior"] == "inference"
    value["steward"] = [
        {
            "qualified_id": str(descriptor["id"]) + "@" + str(descriptor["version"]),
            "behavior": "inference",
            "risk": "inference",
        }
    ]
    manifest = NativeManifest(canonical_json=json.dumps(value))
    fields = _OBJECT.validate_json(selected.model_dump_json())
    release = _OBJECT.validate_python(fields["release"], strict=True)
    release["manifest"] = _OBJECT.validate_json(manifest.model_dump_json())
    fields["release"] = release
    selected = NativeRuntimeRelease.model_validate_json(json.dumps(fields))
    key = Ed25519PrivateKey.generate()
    signed = SignedNativeRuntimeRelease(
        runtime=selected, signature=key.sign(native_runtime_message(selected)).hex()
    )
    entry = native_entry_from_record(
        signed.model_dump_json().encode(), "https://example.test/native/1/"
    )
    assert entry.steward_risks == ("inference",)


@pytest.mark.parametrize("protocol", [True, 4.0, "4", 3, 5])
def test_native_catalog_version_has_no_aliases(protocol: JsonValue) -> None:
    """A new version is explicit and cannot enter through numeric coercion."""
    fields: dict[str, JsonValue] = {
        "protocol": protocol,
        "publisher": "synthetic",
        "revision": 1,
        "created_at": 100,
        "expires_at": 200,
        "entries": [],
    }
    with pytest.raises(ValidationError):
        NativeCatalog.model_validate_json(json.dumps(fields))


@pytest.mark.parametrize(
    "name,value", [("runtime_protocol", 5.0), ("release_protocol", 3.0)]
)
def test_native_catalog_binding_preserves_protocol_numeric_kinds(
    name: str, value: float
) -> None:
    """Discovery metadata cannot alias the signed native envelope versions."""
    signed = signed_native(Ed25519PrivateKey.generate())
    entry = native_entry_from_record(
        signed.model_dump_json().encode(), "https://example.test/native/1/"
    )
    fields = _OBJECT.validate_json(entry.native.model_dump_json())
    fields[name] = value
    with pytest.raises(ValidationError):
        NativeCatalogBinding.model_validate_json(json.dumps(fields))


def test_catalog_builder_requires_native_opt_in_and_uses_separate_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The command signs a native record without widening legacy catalogs."""
    monkeypatch.setattr(time, "time", lambda: 150)
    key = Ed25519PrivateKey.generate()
    signed = signed_native(key)
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    atomic_private(source / "release.json", signed.model_dump_json().encode())
    atomic_private(tmp_path / "publisher.key", key.private_bytes_raw())
    arguments = [
        "--publisher",
        "synthetic",
        "--private-key",
        str(tmp_path / "publisher.key"),
        "--revision",
        "1",
        "--expires-at",
        "200",
        "--output",
        str(tmp_path / "catalog.json"),
        "--release",
        str(source),
        "https://example.test/native/1/",
    ]
    with pytest.raises(SystemExit):
        build_catalog.main(arguments)
    assert not (tmp_path / "catalog.json").exists()
    build_catalog.main([*arguments, "--native"])
    raw = (tmp_path / "catalog.json").read_bytes()
    observed = SignedNativeCatalog.model_validate_json(raw)
    trust = PublisherTrust(
        revision=1,
        expires_at=200,
        publishers={"synthetic": key.public_key().public_bytes_raw().hex()},
    )
    assert verify_native_catalog(observed, trust).entries[0].bundle_id == (
        signed.runtime.release.manifest.bundle_id
    )
    key.public_key().verify(
        bytes.fromhex(observed.signature), native_catalog_message(observed.catalog)
    )
    with pytest.raises(InvalidSignature):
        key.public_key().verify(
            bytes.fromhex(observed.signature), canonical(observed.catalog)
        )
    with pytest.raises(ValidationError):
        SignedCatalog.model_validate_json(raw)


@pytest.mark.parametrize(
    "refusal",
    [
        "signature",
        "publisher",
        "expired",
        "revoked",
        "release-revoked",
        "record-revoked",
        "wheel-revoked",
    ],
)
def test_public_native_inspection_authenticates_actual_original_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refusal: str
) -> None:
    """Native inspection verifies trust and domain before reporting a valid release."""
    monkeypatch.setattr(time, "time", lambda: 150)
    key = Ed25519PrivateKey.generate()
    signed = signed_native(key)
    path, trust_path = tmp_path / "release.json", tmp_path / "trust.json"
    path.write_text(signed.model_dump_json())
    trust = PublisherTrust(
        revision=1,
        expires_at=200,
        publishers={"synthetic": key.public_key().public_bytes_raw().hex()},
    )
    trust_path.write_text(trust.model_dump_json())
    inspected = inspection.inspect_release(path, trust_path)
    assert inspected["signature_verified"] is True
    assert inspected["release_sha256"] == signed.runtime.digest
    if refusal == "signature":
        fields = _OBJECT.validate_json(signed.model_dump_json())
        fields["signature"] = key.sign(canonical(signed.runtime)).hex()
        path.write_text(json.dumps(fields))
    elif refusal == "publisher":
        trust = trust.model_copy(
            update={
                "publishers": {"unrelated": key.public_key().public_bytes_raw().hex()}
            }
        )
    elif refusal == "expired":
        trust = trust.model_copy(update={"expires_at": 149})
    else:
        identity = {
            "revoked": signed.runtime.digest,
            "release-revoked": signed.runtime.release.digest,
            "record-revoked": hashlib.sha256(path.read_bytes()).hexdigest(),
            "wheel-revoked": signed.runtime.wheels[0].sha256,
        }[refusal]
        trust = trust.model_copy(update={"revoked_artifacts": (identity,)})
    trust_path.write_text(trust.model_dump_json())
    with pytest.raises(Denied):
        inspection.inspect_release(path, trust_path)
