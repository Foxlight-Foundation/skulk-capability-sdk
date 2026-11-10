"""Managed startup admission through an independently installed native wheel."""

import importlib
from typing import cast

import pytest

from tools.check_managed_profile import Native, fixture, python_case


def test_complete_independent_managed_vectors() -> None:
    """Installed native bytes and full identities match all independent witnesses."""
    native = cast(Native, importlib.import_module("_skulk_capability_kernel"))
    inputs = fixture()
    assert native.managed_profile_digest() == inputs.profile_digest
    for case in inputs.cases:
        assert python_case(native, case) == case.expected(), case.identity


def test_malformed_language_strings_refuse_without_echoing_secrets() -> None:
    """Python Unicode conversion must not substitute bytes before identity binding."""
    native = cast(Native, importlib.import_module("_skulk_capability_kernel"))
    with pytest.raises(ValueError, match="^portable_unicode:"):
        native.managed_contract_json("Startup", "\ud800")
