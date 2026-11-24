"""Real compiled inference authority; parsing records never identifies a caller."""

import importlib
import json
from typing import cast

import pytest

from tools.check_composition_profile import fixture as legacy_fixture
from tools.check_inference_profiles import Native, fixture, python_case


def test_complete_independent_inference_vectors() -> None:
    """Compare full canonical bytes, digests and refusals for both new profiles."""
    native = cast(Native, importlib.import_module("_skulk_capability_kernel"))
    inputs = fixture()
    assert native.composition_v2_profile_digest() == inputs.profile_digest
    assert native.portable_v2_profile_digest() == inputs.author_profile_digest
    for case in inputs.cases:
        assert python_case(native, case) == case.expected(), case.identity


def test_sensitive_inference_context_requires_explicit_export() -> None:
    """A frozen opaque handle never echoes caller metadata through inspection."""
    native = importlib.import_module("_skulk_capability_kernel")
    case = fixture().cases[0]
    record = native.CompositionV2Contract.from_json("Context", case.source)
    assert repr(record) == "CompositionV2Contract(Context)"
    assert json.loads(case.source)["caller_id"] not in repr(record)
    original = record.canonical()
    copied = bytearray(original)
    copied[:] = b"changed"
    assert record.canonical() == original
    with pytest.raises(AttributeError):
        record.name = "Policy"
    with pytest.raises(TypeError):
        native.CompositionV2Contract()


def test_compiled_profiles_cannot_mix_at_attenuation_boundary() -> None:
    """Legacy handles cannot acquire inference rights through a new entry point."""
    native = importlib.import_module("_skulk_capability_kernel")
    case = next(c for c in fixture().cases if c.identity == "exact-delegation")
    parent = native.CompositionV2Contract.from_json("Context", case.parent)
    request = native.CompositionV2Contract.from_json("DelegateRequest", case.source)
    policy = native.CompositionV2Contract.from_json("Policy", case.policy)
    with pytest.raises(ValueError, match="^composition_invalid:"):
        native.CompositionContract.from_json("Context", case.parent)
    old = native.CompositionContract.from_json(
        "Context", legacy_fixture().cases[0].source
    )
    with pytest.raises(TypeError):
        parent.admit(request, old, case.executor, b"", case.now_unix_ms, case.scope_id)
    assert policy.name == "Policy"
