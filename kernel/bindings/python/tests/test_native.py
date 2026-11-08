"""Real installed-extension qualification, independent of the public facade."""

import fcntl
import hashlib
import json
import math
import sqlite3
from pathlib import Path

# The installed module is native-only; its checked .pyi supplies all types.
import _skulk_capability_kernel as native  # pyright: ignore[reportMissingModuleSource]
import pytest
from pydantic import JsonValue

from skulk_capability_sdk.contracts import OperationBounds
from skulk_capability_sdk.operations import OperationPage, OperationStatus

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "tests/fixtures/author-baseline"


@pytest.mark.parametrize("version", ["0.4.0", "0.5.0"])
def test_original_python_contract_bytes_and_hashes(version: str) -> None:
    reference = json.loads((FIXTURES / f"sdk-{version}.json").read_text())
    for group in ["canonical_cases", "manifest_cases"]:
        for case in reference[group]:
            encoded = native.canonical(case["message"])
            assert encoded == case["canonical_utf8"].encode()
            assert hashlib.sha256(encoded).hexdigest() == case["sha256"]
    for case in reference["operation_input_cases"]:
        assert native.input_digest(case["scope"], case["input"]) == case["sha256"]


def test_python_values_surrogates_kinds_cycles_and_safe_errors() -> None:
    payload = {"number": 1.0, "zero": -0.0, "big": 2**128, "text": "\ud800"}
    expected = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    assert native.canonical(payload) == expected.encode()
    assert (
        native.input_digest("\ud800", payload)
        == hashlib.sha256(
            json.dumps(
                {"scope": "\ud800", "input": payload},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    for value in [math.nan, math.inf, -math.inf]:
        with pytest.raises(ValueError, match="non-finite"):
            native.canonical(value)
    with pytest.raises(TypeError, match="keys must be strings"):
        native.canonical({1: "secret-must-not-appear"})
    cyclic: list[object] = []
    cyclic.append(cyclic)
    with pytest.raises(ValueError, match="nesting"):
        native.canonical(cyclic)
    with pytest.raises(ValueError, match="duplicate"):
        native.canonical_json('{"credential":1,"credential":2}')


def test_builtin_subclass_values_keep_legacy_encoding() -> None:
    class Integer(int):
        def __str__(self) -> str:
            raise AssertionError("overridden method must not run")

    class String(str):
        def encode(self, *args: object, **kwargs: object) -> bytes:
            raise AssertionError("overridden method must not run")

    assert native.canonical({String("key"): Integer(1)}) == b'{"key":1}'


def test_legacy_journal_recovery_fences_rollback_and_lock(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    path = tmp_path / "operations.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript((FIXTURES / "sdk-0.4.0-journal.sql").read_text())
    path.chmod(0o600)
    journal = native.NativeJournal(
        str(path), '{"max_queued":8,"retain":4,"reservations":64}', 2000
    )

    def call(method: str, **fields: object) -> object:
        return json.loads(
            journal.transact(
                json.dumps(
                    {"method": method, "scope": "legacy-scope", "now": 2000, **fields}
                )
            )
        )

    def status(operation_id: str) -> OperationStatus:
        return OperationStatus.model_validate(call("status", operation_id=operation_id))

    accepted = status("accepted")
    assert accepted.state == "interrupted"
    assert accepted.backend_reference == "synthetic-backend-job"
    assert call("started_at", operation_id="accepted") == 1000
    assert call("cancel_requested", operation_id="accepted") is True
    assert status("pending").state == "failed"
    assert status("uncertain").submission == "uncertain"
    assert status("finished").result == {"artifact": "synthetic-local-result"}
    assert call("interrupted_accepted") == [["accepted", "synthetic-backend-job"]]
    assert call("next_queued") is None
    with pytest.raises(ValueError, match="retired"):
        call(
            "reserve",
            operation_id="pruned",
            input_digest=native.input_digest("legacy-scope", {"label": "pruned"}),
            input={"label": "pruned"},
        )
    before = call("status", operation_id="accepted")
    with pytest.raises(ValueError, match="invalid"):
        call("update", operation_id="accepted", state="running", progress=1.5)
    assert call("status", operation_id="accepted") == before
    with pytest.raises(ValueError, match="invalid"):
        call("request_cancel", operation_id="accepted", now=-1)
    assert call("status", operation_id="accepted") == before
    page = OperationPage.model_validate_json(json.dumps(call("page")))
    assert page.active == 0 and page.outstanding == 2
    assert all(row.input is None and row.result is None for row in page.operations)
    journal.acquire_ownership()
    with (tmp_path / "supervisor.lock").open("r+") as lock:
        with pytest.raises(BlockingIOError):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal.close()
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with pytest.raises(ValueError, match="invalid"):
        journal.transact('{"method":"page"}')
    # The original Python implementation can still open all native observations.
    from skulk_capability_sdk.operations import OperationJournal

    legacy = OperationJournal(
        path,
        OperationBounds(max_queued=8, retain=4, reservations=64),
        clock=lambda: 2000,
    )
    accepted_legacy = legacy.status("accepted", "legacy-scope")
    uncertain_legacy = legacy.status("uncertain", "legacy-scope")
    assert accepted_legacy is not None and uncertain_legacy is not None
    assert accepted_legacy.backend_reference == "synthetic-backend-job"
    assert uncertain_legacy.submission == "uncertain"
    assert legacy.cancel_requested("accepted", "legacy-scope")


def test_refuses_shared_paths_and_invalid_bounds(tmp_path: Path) -> None:
    tmp_path.chmod(0o755)
    with pytest.raises(ValueError, match="private_storage"):
        native.NativeJournal(str(tmp_path / "operations.sqlite"), "{}", 1)
    tmp_path.chmod(0o700)
    with pytest.raises(ValueError, match="invalid"):
        native.NativeJournal(str(tmp_path / "operations.sqlite"), '{"unknown":1}', 1)


def test_native_structured_contract_values_keep_constructor_and_wire_distinct() -> None:
    """Tuple strictness and raw JSON values survive the shared native boundary."""
    from skulk_capability_sdk.contracts import canonical
    from skulk_capability_sdk.operations import OperationCall

    page = native.validate_contract_value(
        "OperationPage", {"active": 0, "operations": ()}
    )
    assert page.canonical() == canonical(OperationPage(active=0, operations=()))
    with pytest.raises(ValueError):
        native.validate_contract_value("OperationPage", {"active": 0, "operations": []})
    payload: dict[str, JsonValue] = {
        "\ud800": "\ud800",
        "integer": 2**128,
        "float": 1.0,
    }
    record = native.validate_contract_value(
        "OperationCall", {"op": "start", "input": payload}
    )
    assert record.canonical() == canonical(OperationCall(op="start", input=payload))
    invalid_wire = native.validate_contract_value(
        "OperationCall", {"op": "start", "input": {"x": math.nan}}
    )
    with pytest.raises(ValueError):
        invalid_wire.canonical()


def test_native_structured_stream_frame_preserves_arbitrary_binary() -> None:
    """A validated inline-media model nests in a frame without a UTF-8 roundtrip."""
    media = native.validate_contract_value(
        "InlineMedia", {"data": b"\xff\x00\x80", "media_type": "audio/test"}
    )
    with pytest.raises(ValueError):
        media.canonical()
    frame = native.validate_contract_value(
        "StreamPacket",
        {
            "call_id": "c",
            "direction": "provider_to_caller",
            "sequence": 1,
            "kind": "chunk",
            "media": media,
        },
    )
    projection, raw = native.decode_stream_packet(frame.stream_packet())
    assert raw == b"\xff\x00\x80"
    assert "data" not in json.loads(projection)["media"]


def test_constructor_conversion_refuses_oversized_strings_before_copying() -> None:
    """The bound applies before the binding creates its UTF-32 transport copy."""
    with pytest.raises(ValueError, match="constructor exceeds bound"):
        native.validate_contract_value(
            "OperationCall", {"op": "start", "input": {"x": "x" * 4_194_305}}
        )
