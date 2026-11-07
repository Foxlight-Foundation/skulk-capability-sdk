"""The compatibility window: the current and previous protocol, both tested."""

import pytest
from pydantic import ValidationError

from skulk_capability_sdk import ACCEPTED_PROTOCOLS, PROTOCOL, contracts

_DESCRIPTOR = contracts.Descriptor(
    id="example.echo",
    version="1.0.0",
    title="Echo",
    description="Returns its input.",
    input_schema={"type": "object"},
    output_schema={"type": "object"},
)


def test_the_window_is_the_current_protocol_and_at_most_one_previous() -> None:
    assert PROTOCOL == max(ACCEPTED_PROTOCOLS)
    assert 1 <= len(ACCEPTED_PROTOCOLS) <= 2
    assert ACCEPTED_PROTOCOLS == tuple(sorted(ACCEPTED_PROTOCOLS))


@pytest.mark.parametrize("protocol", ACCEPTED_PROTOCOLS)
def test_every_accepted_protocol_validates_a_manifest_and_a_message(
    protocol: int,
) -> None:
    """The two-row matrix: each member of the window is exercised, not assumed."""
    manifest = contracts.Manifest(
        protocol=protocol,
        bundle_id="example.capability",
        bundle_version="0.1.0",
        executable="/opt/example/bundle.pyz",
        executable_sha256="0" * 64,
        arguments=(),
        descriptors=(_DESCRIPTOR,),
    )
    assert manifest.protocol == protocol
    assert contracts.Shutdown(protocol=protocol).protocol == protocol


def test_a_protocol_that_left_the_window_is_refused_by_name() -> None:
    """A capability older than the previous SDK is refused, not half-served."""
    retired = min(ACCEPTED_PROTOCOLS) - 1
    if retired < 1:
        pytest.skip("no protocol has left the window yet")
    with pytest.raises(ValidationError, match=f"protocol {retired} is not accepted"):
        contracts.Shutdown(protocol=retired)


def test_a_protocol_beyond_the_window_is_refused_by_name() -> None:
    beyond = max(ACCEPTED_PROTOCOLS) + 1
    with pytest.raises(ValidationError, match=f"protocol {beyond} is not accepted"):
        contracts.Shutdown(protocol=beyond)
    with pytest.raises(ValidationError, match="this SDK accepts"):
        contracts.Manifest(
            protocol=beyond,
            bundle_id="example.capability",
            bundle_version="0.1.0",
            executable="/opt/example/bundle.pyz",
            executable_sha256="0" * 64,
            arguments=(),
            descriptors=(_DESCRIPTOR,),
        )
