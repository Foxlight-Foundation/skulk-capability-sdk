"""Real native directory framing with valid aggregate metadata above 16K nodes."""

import json
from pathlib import Path
from typing import cast

import _skulk_capability_kernel as native  # pyright: ignore[reportMissingModuleSource]
import pytest

from skulk_capability_sdk.discovery import CapabilityDirectory
from tools.check_discovery_profile import Native, fixture


@pytest.mark.parametrize(
    "count,rich,depth", [(64, True, 0), (1000, False, 0), (1, False, 62)]
)
def test_valid_directory_has_a_separate_outer_document_budget(
    count: int, rich: bool, depth: int
) -> None:
    """Actual typed public admission and native proofs preserve every offer and byte."""
    root = Path(__file__).resolve().parents[4]
    offer = json.loads((root / "kernel/contracts/discovery-offer.json").read_text())
    if rich:
        offer["descriptor"]["input_schema"] = {
            "type": "object",
            "properties": {"selection": {"enum": list(range(512))}},
        }
    if depth:
        nested: object = 0
        for _ in range(depth):
            nested = [nested]
        offer["descriptor"]["input_schema"]["default"] = nested
    if rich or depth:
        offer["target"]["descriptor_revision"] = native.portable_v2_contract_digest(
            "Descriptor", json.dumps(offer["descriptor"])
        )
    body = json.dumps(
        [{**offer, "plugin_id": f"managed.example{i}"} for i in range(count)]
    ).encode()
    assert len(CapabilityDirectory.from_json(body).offers) == count
    probe = fixture().cases[0].request
    codec = cast(Native, native)
    query = codec.discovery_request(probe.startup, probe.context, probe.parent, 1)
    metadata, _ = native.decode_discovery_frame(query)
    reply = codec.discovery_reply(probe.startup, metadata.decode(), body, None)
    assert codec.discovery_response(probe.startup, metadata.decode(), reply) == (
        None,
        body,
    )
