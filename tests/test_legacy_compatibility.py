"""Frozen protocol bytes and pre-migration journals survive SDK replacement.

These references come from exact released-source environments. Tests read them;
updating a reference is a separate reviewed compatibility decision.
"""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, JsonValue

from skulk_capability_sdk import contracts, operations, streams


def schemas() -> dict[str, object]:
    """Compare the public facade to independently retained released schemas."""
    models: tuple[type[BaseModel], ...] = (
        contracts.Descriptor,
        contracts.Bounds,
        contracts.Surface,
        contracts.SurfaceReport,
        contracts.OperationBounds,
        contracts.StewardExposure,
        contracts.CredentialInput,
        contracts.Manifest,
        contracts.Startup,
        contracts.ApprovedEffect,
        operations.OperationCall,
        operations.OperationStatus,
        operations.OperationPlan,
        operations.OperationPage,
        operations.OperationLog,
    )
    result: dict[str, object] = {
        model.__name__: model.model_json_schema() for model in models
    }
    result["WireMessage"] = contracts.WIRE.json_schema()
    result["StreamPacket"] = streams.STREAM_PACKET.json_schema()
    return result


FIXTURES = Path(__file__).parent / "fixtures" / "author-baseline"


class CanonicalCase(BaseModel):
    """One language-neutral message with its exact legacy encoding."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    id: str
    message: dict[str, JsonValue]
    canonical_utf8: str
    sha256: str


class InputCase(BaseModel):
    """One scoped operation input; numeric kinds must remain distinguishable."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    id: str
    scope: str
    input: dict[str, JsonValue]
    sha256: str


class Snapshot(BaseModel):
    """The complete retained author contract for one source revision."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    format: int
    source: dict[str, str]
    protocol: dict[str, JsonValue]
    schemas: dict[str, JsonValue]
    manifest_cases: tuple[CanonicalCase, ...]
    canonical_cases: tuple[CanonicalCase, ...]
    operation_input_cases: tuple[InputCase, ...]


def load_snapshot(version: str) -> Snapshot:
    """Load a committed reference without evaluating or rebuilding its source."""
    return Snapshot.model_validate_json((FIXTURES / f"sdk-{version}.json").read_bytes())


def test_current_author_schema_is_the_frozen_contract() -> None:
    expected = load_snapshot("0.5.5")
    assert expected.format == 1
    current = json.loads(json.dumps(schemas()))
    assert current == expected.schemas
    assert expected.protocol == {
        "current": contracts.PROTOCOL,
        "accepted": list(contracts.ACCEPTED_PROTOCOLS),
        "max_frame_bytes": contracts.MAX_FRAME_BYTES,
        "max_manifest_bytes": contracts.MAX_MANIFEST_BYTES,
        "max_startup_bytes": contracts.MAX_STARTUP_BYTES,
        "max_media_bytes": streams.MAX_MEDIA_BYTES,
    }


@pytest.mark.parametrize("version", ["0.4.0", "0.5.0", "0.5.5"])
def test_legacy_control_bytes_and_operation_hashes_remain_exact(version: str) -> None:
    expected = load_snapshot(version)
    for case in expected.manifest_cases:
        manifest = contracts.Manifest.model_validate_json(json.dumps(case.message))
        assert contracts.canonical(manifest).decode() == case.canonical_utf8
        assert contracts.manifest_digest(manifest) == case.sha256
    for case in expected.canonical_cases:
        message = contracts.WIRE.validate_json(json.dumps(case.message), strict=True)
        encoded = contracts.canonical(message)
        assert encoded.decode() == case.canonical_utf8, case.id
        assert hashlib.sha256(encoded).hexdigest() == case.sha256, case.id
    for case in expected.operation_input_cases:
        assert operations.input_digest(case.scope, case.input) == case.sha256, case.id
    values = {case.id: case.sha256 for case in expected.operation_input_cases}
    assert values["integer"] != values["integral-float"]


def test_real_legacy_journal_preserves_uncertainty_identity_and_replay_fences(
    tmp_path: Path,
) -> None:
    path = tmp_path / "operations.sqlite"
    connection = sqlite3.connect(path)
    try:
        connection.executescript((FIXTURES / "sdk-0.4.0-journal.sql").read_text())
    finally:
        connection.close()
    path.chmod(0o600)
    journal = operations.OperationJournal(
        path,
        contracts.OperationBounds(retain=4, max_queued=8, reservations=64),
        lambda: 2000,
    )
    scope = "legacy-scope"
    pending = journal.status("pending", scope)
    uncertain = journal.status("uncertain", scope)
    accepted = journal.status("accepted", scope)
    finished = journal.status("finished", scope)
    assert pending is not None and pending.state == "failed"
    assert pending.error == "interrupted before submission"
    assert uncertain is not None and uncertain.state == "interrupted"
    assert uncertain.submission == "uncertain" and uncertain.backend_reference is None
    assert accepted is not None and accepted.state == "interrupted"
    assert accepted.submission == "accepted"
    assert accepted.backend_reference == "synthetic-backend-job"
    assert journal.started_at("accepted", scope) == 1000
    assert journal.cancel_requested("accepted", scope)
    assert journal.interrupted_accepted(scope) == [
        ("accepted", "synthetic-backend-job")
    ]
    assert journal.next_queued(scope) is None
    assert finished is not None and finished.state == "succeeded"
    assert finished.result == {"artifact": "synthetic-local-result"}
    assert journal.status("pruned", scope) is None
    payload: dict[str, JsonValue] = {"label": "pruned"}
    with pytest.raises(ValueError, match="already used"):
        journal.reserve(
            "pruned", operations.input_digest(scope, payload), payload, scope
        )
    with pytest.raises(ValueError, match="different input"):
        changed: dict[str, JsonValue] = {"label": "different"}
        journal.reserve(
            "pruned", operations.input_digest(scope, changed), changed, scope
        )
