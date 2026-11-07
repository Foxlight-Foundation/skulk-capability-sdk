"""Publisher inspection verifies bytes without claiming host execution authority."""

import hashlib
import json
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from skulk_capability_tools.cli import keygen
from skulk_capability_tools.inspection import inspect_release
from skulk_capability_tools.publish import publish
from skulk_capability_tools.releases import PublisherTrust, Release, SignedRelease

from skulk_capability_sdk.contracts import Denied, Descriptor, Manifest, canonical


def release(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build public synthetic metadata; every private signing key stays in memory."""
    now = int(time.time())
    key = Ed25519PrivateKey.generate()
    content = b"synthetic bundle"
    artifact = tmp_path / "bundle.pyz"
    artifact.write_bytes(content)
    manifest = Manifest(
        bundle_id="fixture.echo",
        bundle_version="1.0.0",
        executable="bundle.pyz",
        arguments=(),
        executable_sha256=hashlib.sha256(content).hexdigest(),
        descriptors=(
            Descriptor(
                id="fixture.echo",
                version="1.0.0",
                title="Echo",
                description="Synthetic echo",
                input_schema={},
                output_schema={},
            ),
        ),
    )
    claims = Release(
        manifest=manifest,
        publisher="fixture",
        sequence=1,
        created_at=now - 10,
        expires_at=now + 3600,
        artifact_size=len(content),
        skulk_build_sha256="a" * 64,
        dependency_lock_sha256="b" * 64,
        platforms=("darwin",),
        python_requires="==3.13.*",
        state_schema="fixture-v1",
        compatible_state_schemas=(),
        permissions=("Read synthetic input",),
    )
    signed = SignedRelease(release=claims, signature=key.sign(canonical(claims)).hex())
    metadata = tmp_path / "release.json"
    metadata.write_text(signed.model_dump_json())
    trust = tmp_path / "trust.json"
    trust.write_text(
        PublisherTrust(
            revision=1,
            expires_at=now + 3600,
            publishers={"fixture": key.public_key().public_bytes_raw().hex()},
        ).model_dump_json()
    )
    return metadata, trust, artifact


def test_signature_and_bytes_are_independent_of_host_admission(tmp_path: Path) -> None:
    metadata, trust, _ = release(tmp_path)
    result = inspect_release(metadata, trust, artifacts=tmp_path)
    assert result["signature_verified"] and result["artifact_bytes_verified"]
    assert result["declared_host_version_compatible"] is None
    assert (
        inspect_release(metadata, trust, declared_host_version="2.0.3")[
            "declared_host_version_compatible"
        ]
        is True
    )
    assert (
        inspect_release(metadata, trust, declared_host_version="1.0.0")[
            "declared_host_version_compatible"
        ]
        is False
    )


def test_publication_excludes_keys_and_refuses_tampering_and_replacement(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _, trust, artifact = release(source)
    (source / "publisher.key").write_bytes(b"synthetic secret")
    destination = tmp_path / "public"
    report = publish(source, trust, destination)
    assert report["files"] == 2 and report["network_verified"] is False
    assert {p.name for p in destination.iterdir()} == {"release.json", "bundle.pyz"}
    assert (destination / "bundle.pyz").stat().st_mode & 0o777 == 0o644
    with pytest.raises(Denied, match="immutable"):
        publish(source, trust, destination)
    artifact.write_bytes(b"altered bytes")
    with pytest.raises(Denied):
        publish(source, trust, tmp_path / "tampered")
    assert not (tmp_path / "tampered").exists()


@pytest.mark.parametrize("change", ["signature", "artifact", "revocation", "expiry"])
def test_inspection_refuses_corruption_and_stale_authority(
    tmp_path: Path, change: str
) -> None:
    metadata, trust, artifact = release(tmp_path)
    if change == "signature":
        raw = json.loads(metadata.read_text())
        raw["signature"] = "0" * 128
        metadata.write_text(json.dumps(raw))
    elif change == "artifact":
        artifact.write_bytes(b"altered")
    else:
        raw = json.loads(trust.read_text())
        raw["revoked_publishers"] = ["fixture"] if change == "revocation" else []
        if change == "expiry":
            raw["expires_at"] = int(time.time()) - 10
        trust.write_text(json.dumps(raw))
    with pytest.raises(Denied):
        inspect_release(metadata, trust, artifacts=tmp_path)


def test_key_creation_refuses_replacement_and_returns_only_public_material(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "keys" / "publisher.key"
    keygen(["--private-key", str(path)])
    original = path.read_bytes()
    public = json.loads(capsys.readouterr().out)
    assert set(public) == {"public_key"} and len(public["public_key"]) == 64
    assert path.stat().st_mode & 0o077 == 0 and len(original) == 32
    with pytest.raises(FileExistsError):
        keygen(["--private-key", str(path)])
    assert path.read_bytes() == original


def test_catalog_refuses_a_valid_but_oversized_signed_envelope(tmp_path: Path) -> None:
    """Model field limits do not establish the whole reader envelope's byte limit."""
    from skulk_capability_tools.build_catalog import main as catalog_main
    from skulk_capability_tools.catalog import CATALOG_MAX_BYTES

    from skulk_capability_sdk.storage import atomic_private

    template = tmp_path / "template"
    template.mkdir(mode=0o700)
    metadata, _, _ = release(template)
    original = SignedRelease.model_validate_json(metadata.read_bytes()).release
    key = Ed25519PrivateKey.generate()
    private = tmp_path / "publisher.key"
    atomic_private(private, key.private_bytes_raw())
    output = tmp_path / "catalog.json"
    arguments = [
        "--publisher",
        "fixture",
        "--private-key",
        str(private),
        "--revision",
        "1",
        "--expires-at",
        str(int(time.time()) + 3600),
        "--output",
        str(output),
    ]
    for index in range(32):
        directory = tmp_path / f"release-{index}"
        directory.mkdir(mode=0o700)
        claims = original.model_copy(
            update={
                "sequence": index + 1,
                "manifest": original.manifest.model_copy(
                    update={"bundle_id": f"fixture.echo{index}"}
                ),
                "permissions": tuple(f"{item:02d}" + "x" * 510 for item in range(16)),
            }
        )
        record = SignedRelease(
            release=claims, signature=key.sign(canonical(claims)).hex()
        )
        atomic_private(directory / "release.json", record.model_dump_json().encode())
        arguments += ["--release", str(directory), f"https://example.test/{index}/"]
        if index == 0:
            catalog_main(arguments)
            assert output.stat().st_size < CATALOG_MAX_BYTES
            output.unlink()
    with pytest.raises(SystemExit) as raised:
        catalog_main(arguments)
    assert raised.value.code == 2
    assert not output.exists()
