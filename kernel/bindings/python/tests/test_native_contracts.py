"""Installed Python delegates typed normalization to the protected shared core."""

import json
from pathlib import Path

import _skulk_capability_kernel as native  # pyright: ignore[reportMissingModuleSource]
import pytest

FIXTURES = Path(__file__).resolve().parents[4] / "tests/fixtures/author-baseline"


def test_frozen_wire_and_manifest_bytes_through_native_author_validation() -> None:
    reference = json.loads((FIXTURES / "sdk-0.5.0.json").read_text())
    for root, field in [
        ("WireMessage", "canonical_cases"),
        ("Manifest", "manifest_cases"),
    ]:
        for case in reference[field]:
            source = case["canonical_utf8"]
            assert native.validate_contract_json(root, source) == source.encode()


def test_shared_strict_types_defaults_and_semantic_refusals() -> None:
    projected = native.validate_contract_json(
        "Bounds", '{"startup_seconds":1,"concurrency":true}'
    )
    assert b'"startup_seconds":1.0' in projected
    assert b'"concurrency":1' in projected
    assert b"stream_seconds" not in projected
    for source in ['{"max_children":true}', '{"max_children":1.0}']:
        with pytest.raises(ValueError):
            native.validate_contract_json("Bounds", source)
    with pytest.raises(ValueError):
        native.validate_contract_json("OperationCall", '{"op":"status"}')
    with pytest.raises(ValueError):
        native.validate_contract_json(
            "OperationCall", '{"op":"list","offset":9007199254740993}'
        )
