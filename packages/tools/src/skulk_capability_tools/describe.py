"""What the owner tells its host about an installed node, as a pure function.

The describe reply is the one control reply whose size grows with what a
capability declares, so it is kept separate from the owner's I/O: the owner
sends it, and the publisher measures its largest possible form before
signing a release.
"""

import json
from typing import Final

from pydantic import JsonValue

from skulk_capability_sdk.contracts import (
    PROTOCOL,
    SURFACE_URL_MAX_LENGTH,
    Manifest,
    require_extended_contracts_reader,
)

CONTROL_RESPONSE_MAX_BYTES: Final = 131072
"""Largest ordinary control-socket reply; discovery has its own metadata bound."""

DESCRIBE_RESPONSE_MAX_BYTES: Final = 524288
"""Largest description reply, independently bounded by owner and Core reader.

The describe reply carries every descriptor, so it is the control reply
whose size grows with what a capability declares; the publisher measures its
worst case (``worst_case_describe_bytes``) before signing a release.
"""


def validate_discovery_compatibility(manifest: Manifest) -> int:
    """Measure the whole owner reply and refuse an incompatible declared range.

    A manifest can fit 128 KiB while its expanded owner projection does not.
    The publisher must account for both before signing. Returns the measured
    worst-case bytes; raises ValueError for overflow or a missing reader floor.
    """
    size = worst_case_describe_bytes(manifest)
    if size > DESCRIBE_RESPONSE_MAX_BYTES:
        raise ValueError(
            f"the owner's describe reply can reach {size} bytes; "
            f"the host reads at most {DESCRIBE_RESPONSE_MAX_BYTES}"
        )
    if size > 131072:
        require_extended_contracts_reader(manifest.skulk_requires)
    return size


def steward_projection(manifest: Manifest) -> list[JsonValue]:
    """Project steward exposures as read tools or inert proposals."""
    projected: list[JsonValue] = []
    for item in manifest.steward:
        entry: dict[str, JsonValue] = {
            "qualified_id": item.qualified_id,
            "mode": "read" if item.behavior == "read" else "proposal",
        }
        if item.summary is not None:
            entry["summary"] = item.summary
        projected.append(entry)
    return projected


def describe_projection(
    manifest: Manifest,
    *,
    node_id: str,
    status: str,
    surfaces: list[JsonValue],
    extended: bool,
    proposals_available: bool,
    proposal_actions_available: bool,
) -> dict[str, JsonValue]:
    """Project one installed node for describe; every value is plain JSON.

    A pure function of the manifest and the node's current state, so the
    publisher can measure the largest reply a manifest can produce.
    """
    lifecycle = any(item.behavior == "lifecycle" for item in manifest.steward)
    return {
        "node_id": node_id,
        "bundle_id": manifest.bundle_id,
        "version": manifest.bundle_version,
        "status": status,
        "configurable": manifest.configuration_schema is not None,
        # The contract promises the bundle id as the fallback title.
        **({"title": manifest.title or manifest.bundle_id} if extended else {}),
        **({"surfaces": surfaces} if extended and manifest.surfaces else {}),
        **(
            {"steward": steward_projection(manifest)}
            if extended and manifest.steward
            else {}
        ),
        **(
            {"operations_available": True}
            if extended and manifest.operations is not None
            else {}
        ),
        **({"proposals_available": True} if proposals_available and lifecycle else {}),
        **(
            {"proposal_actions_available": True}
            if proposal_actions_available and lifecycle
            else {}
        ),
        **(
            {"setup_available": True, "setup_actions_available": True}
            if manifest.preflight_profile == "runpod-native"
            else {}
        ),
        **(
            {"preflight_available": True}
            if manifest.preflight_profile is not None
            else {}
        ),
        **({"credentials_configurable": True} if manifest.credential_inputs else {}),
        "descriptors": [
            descriptor.model_dump(mode="json") for descriptor in manifest.descriptors
        ],
    }


def worst_case_describe_bytes(manifest: Manifest) -> int:
    """The largest describe reply this manifest can produce, as the owner writes it.

    One node with every optional projection present, identifiers at their
    longest, and every surface ready with a URL at its longest: nothing a
    running installation reports can make the reply larger.
    """
    longest = "x" * 128
    surfaces: list[JsonValue] = [
        {
            "surface_id": surface.surface_id,
            "title": surface.title,
            "kind": surface.kind,
            "ready": True,
            "url": "https://" + "x" * (SURFACE_URL_MAX_LENGTH - len("https://"))
            if surface.kind == "link"
            else None,
        }
        for surface in manifest.surfaces
    ]
    node = describe_projection(
        manifest,
        node_id=longest,
        status="configuration_invalid",
        surfaces=surfaces,
        extended=True,
        proposals_available=True,
        proposal_actions_available=True,
    )
    reply: dict[str, JsonValue] = {
        "transport_node_id": longest,
        "protocol": PROTOCOL,
        "host_callbacks_available": True,
        "nodes": [node],
    }
    return len(json.dumps({"result": reply}).encode()) + 1
