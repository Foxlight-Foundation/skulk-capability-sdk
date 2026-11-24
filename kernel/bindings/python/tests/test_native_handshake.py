"""Native author handles and protocol-five handshakes; no account or owner."""

import importlib
import json
from typing import cast

import pytest

from tools.check_handshake_profile import Native, fixture, python_case


def test_complete_independent_handshake_vectors() -> None:
    """Installed author handles match every independent canonical/refusal case."""
    native = cast(Native, importlib.import_module("_skulk_capability_kernel"))
    inputs = fixture()
    assert native.handshake_profile_digest() == inputs.profile_digest
    for case in inputs.cases:
        assert python_case(native, case) == case.expected(), case.identity


def test_immutable_secret_handles_require_explicit_export() -> None:
    """Incidental repr and mutation cannot disclose or replace startup bytes."""
    native = importlib.import_module("_skulk_capability_kernel")
    case = next(c for c in fixture().cases if c.action == "create" and c.error is None)
    startup = native.ManagedContract.from_json("Startup", case.startup)
    hello = startup.hello()
    assert startup.name == "Startup" and len(startup.digest) == 64
    assert startup.digest == native.managed_contract_digest("Startup", case.startup)
    assert repr(startup) == "ManagedContract(Startup)"
    assert repr(hello) == "HandshakeContract(Hello)"
    assert json.loads(case.startup)["token"] not in repr(startup)
    for record in [startup, hello]:
        with pytest.raises(AttributeError):
            record.name = "other"
    with pytest.raises(TypeError):
        native.ManagedContract()
    with pytest.raises(TypeError):
        native.HandshakeContract()
    with pytest.raises(ValueError, match="^portable_unicode:"):
        native.ManagedContract.from_json("Startup", "\ud800")
    with pytest.raises(ValueError, match="^portable_unicode:"):
        native.HandshakeContract.from_json("Hello", "\ud800")
    assert case.canonical is not None
    assert hello.canonical() == case.canonical.encode()
