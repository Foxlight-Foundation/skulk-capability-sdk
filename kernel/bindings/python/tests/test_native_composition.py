"""Installed immutable composition handles; no host/account authority."""

import hashlib
import importlib
import json
from typing import cast

import pytest

from tools.check_composition_profile import Native, fixture, python_case


def test_complete_independent_composition_vectors() -> None:
    """All full canonical/refusal outcomes match the independent source corpus."""
    native = cast(Native, importlib.import_module("_skulk_capability_kernel"))
    inputs = fixture()
    assert native.composition_profile_digest() == inputs.profile_digest
    for case in inputs.cases:
        assert python_case(native, case) == case.expected(), case.identity


def test_sensitive_metadata_requires_explicit_export() -> None:
    """No incidental caller disclosure, public constructor or mutable identity."""
    native = importlib.import_module("_skulk_capability_kernel")
    case = fixture().cases[0]
    record = native.CompositionContract.from_json("Context", case.source)
    assert repr(record) == "CompositionContract(Context)"
    assert json.loads(case.source)["caller_id"] not in repr(record)
    original = record.canonical()
    mutable = bytearray(original)
    mutable[:] = b"changed"
    assert record.canonical() == original
    with pytest.raises(AttributeError):
        record.name = "Policy"
    with pytest.raises(TypeError):
        native.CompositionContract()


def test_actual_request_byte_bound() -> None:
    """Exact maximum bytes qualify; the next byte refuses before request hashing."""
    native = cast(Native, importlib.import_module("_skulk_capability_kernel"))
    case = next(c for c in fixture().cases if c.identity == "exact-delegation")
    parent = native.CompositionContract.from_json("Context", case.parent)
    policy = native.CompositionContract.from_json("Policy", case.policy)
    payload = b"x" * 16_777_216
    fields = json.loads(case.source)
    fields["request_sha256"] = hashlib.sha256(payload).hexdigest()
    request = native.CompositionContract.from_json(
        "DelegateRequest", json.dumps(fields)
    )
    result = parent.admit(
        request, policy, case.executor, payload, case.now_unix_ms, case.scope_id
    )
    assert json.loads(result.canonical())["request_sha256"] == fields["request_sha256"]
    with pytest.raises(ValueError, match="^composition_limit:"):
        parent.admit(
            request,
            policy,
            case.executor,
            payload + b"x",
            case.now_unix_ms,
            case.scope_id,
        )
