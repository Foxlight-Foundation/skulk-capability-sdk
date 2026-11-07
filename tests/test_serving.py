"""Where a capability's own server listens, and whom it admits."""

import pytest
from pydantic import ValidationError

from skulk_capability_sdk import contracts
from skulk_capability_sdk.serving import (
    LOOPBACK_HOST,
    advertised_host,
    bind_hosts,
    peer_allowed,
)


def _startup(
    serve_host: str | None, protocol: int = contracts.PROTOCOL
) -> contracts.Startup:
    """A startup record for a one-descriptor capability of ``protocol``."""
    manifest = contracts.Manifest(
        protocol=protocol,
        bundle_id="example.capability",
        bundle_version="0.1.0",
        executable="/opt/example/bundle.pyz",
        executable_sha256="0" * 64,
        arguments=(),
        descriptors=(
            contracts.Descriptor(
                id="example.echo",
                version="1.0.0",
                title="Echo",
                description="Returns its input.",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
            ),
        ),
    )
    return contracts.Startup(
        socket="/tmp/control.sock",
        token="0" * 64,
        node_id="node",
        host_id="host",
        transport_id="transport",
        manifest=manifest,
        serve_host=serve_host,
    )


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.8.9.1",
        "::1",
        "::ffff:127.0.0.1",
        "100.64.0.1",
        "100.101.2.3",
        "100.127.255.254",
        "::ffff:100.101.2.3",
        "fd7a:115c:a1e0::1",
        "fd7a:115c:a1e0:ab12::1%utun4",
    ],
)
def test_callers_on_the_node_or_its_tailnet_are_admitted(address: str) -> None:
    assert peer_allowed(address)


@pytest.mark.parametrize(
    "address",
    [
        "192.168.1.20",
        "10.0.0.5",
        "172.16.3.4",
        "::ffff:192.168.1.20",
        "100.63.255.255",
        "100.128.0.1",
        "8.8.8.8",
        "fe80::1%en0",
        "fd7a:115c:a1e1::1",
        "0.0.0.0",
        "::",
        "localhost",
        "",
        "not an address",
    ],
)
def test_every_other_caller_is_refused(address: str) -> None:
    """The local network is refused too: the host chose to serve its tailnet only."""
    assert not peer_allowed(address)


def test_without_a_serve_address_a_child_stays_on_loopback() -> None:
    record = _startup(None)
    assert bind_hosts(record) == (LOOPBACK_HOST,)
    assert advertised_host(record) == LOOPBACK_HOST


def test_a_child_serves_loopback_and_its_serve_address() -> None:
    four = _startup("100.70.1.2")
    assert bind_hosts(four) == (LOOPBACK_HOST, "100.70.1.2")
    assert advertised_host(four) == "100.70.1.2"
    six = _startup("fd7a:115c:a1e0::1:2")
    assert bind_hosts(six) == (LOOPBACK_HOST, "fd7a:115c:a1e0::1:2")
    assert advertised_host(six) == "[fd7a:115c:a1e0::1:2]"


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "::1", "0.0.0.0", "::", "224.0.0.1", "node.example"]
)
def test_a_serve_address_is_one_routable_unicast_address(address: str) -> None:
    with pytest.raises(ValidationError, match="serve address"):
        _startup(address)


def test_the_retired_protocol_is_refused_before_startup() -> None:
    """Protocol two is outside this release's current-plus-previous window."""
    with pytest.raises(ValidationError, match="protocol 2 is not accepted"):
        _startup(None, protocol=2)
