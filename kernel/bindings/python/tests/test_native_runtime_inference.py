"""Real compiled inference startup handles; no owner, account or execution."""

import importlib
import json
from typing import cast

import pytest

from tools.check_handshake_profile import Native as HandshakeNative
from tools.check_handshake_profile import fixture as handshake_fixture
from tools.check_handshake_profile import python_case as handshake_case
from tools.check_managed_profile import Native as ManagedNative
from tools.check_managed_profile import fixture as managed_fixture
from tools.check_managed_profile import python_case as managed_case


def test_independent_inference_managed_and_handshake_observations() -> None:
    """Check every full canonical record, identity and bounded refusal."""
    native = importlib.import_module("_skulk_capability_kernel")
    managed = managed_fixture(True)
    handshake = handshake_fixture(True)
    assert native.managed_v2_profile_digest() == managed.profile_digest
    assert native.handshake_v2_profile_digest() == handshake.profile_digest
    for case in managed.cases:
        assert managed_case(cast(ManagedNative, native), case) == case.expected(), (
            case.identity
        )
    for case in handshake.cases:
        assert handshake_case(cast(HandshakeNative, native), case) == case.expected(), (
            case.identity
        )


def test_sensitive_inference_startup_handles_are_frozen_and_explicit() -> None:
    """Old readers refuse; incidental inspection and mutation cannot expose secrets."""
    native = importlib.import_module("_skulk_capability_kernel")
    source = handshake_fixture(True).cases[0].startup
    startup = native.ManagedV2Contract.from_json("Startup", source)
    hello = startup.hello()
    assert repr(startup) == "ManagedV2Contract(Startup)"
    assert repr(hello) == "HandshakeV2Contract(Hello)"
    assert json.loads(source)["token"] not in repr(startup)
    assert (
        startup.admit_hello(hello.canonical().decode()).canonical() == hello.canonical()
    )
    assert startup.canonical() is not startup.canonical()
    for factory, name in [
        (native.ManagedContract, "Startup"),
        (native.HandshakeContract, "Hello"),
    ]:
        with pytest.raises(ValueError, match="^portable_profile:"):
            factory.from_json(
                name, source if name == "Startup" else hello.canonical().decode()
            )
    for record in [startup, hello]:
        with pytest.raises(AttributeError):
            record.name = "other"
        with pytest.raises(TypeError):
            json.dumps(record)
    for factory, name in [
        (native.ManagedV2Contract, "Startup"),
        (native.HandshakeV2Contract, "Hello"),
    ]:
        with pytest.raises(TypeError):
            factory()
        with pytest.raises(ValueError, match="^portable_unicode:"):
            factory.from_json(name, "\ud800")
