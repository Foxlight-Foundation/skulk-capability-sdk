"""Refuse declarations that cannot traverse the existing configuration lifecycle.

Rich capability descriptors have a separate metadata budget. Ordinary settings
reads and retained managed selections still use their original envelopes. Both
the builder and independent signer must check these projections before publishing.
"""

import json
from typing import Final

from pydantic import JsonValue

from skulk_capability_sdk.contracts import Manifest

from ._host_metadata import (
    SETTINGS_FILE_MAX_BYTES,
    SETTINGS_VALUES_MAX_BYTES,
    ManagedSelection,
    Settings,
)
from .describe import CONTROL_RESPONSE_MAX_BYTES
from .releases import Release

MANAGED_SELECTION_MAX_BYTES: Final = 131072
"""Existing SDK/Core selection and lifecycle journal bound; not an invocation limit."""


def configuration_reply_ceiling(manifest: Manifest) -> int:
    """Project the schema read with independently bounded saved settings.

    Saved settings occupy at most their existing file bound. ASCII escaping
    and spacing of values can add no more than their separately checked JSON
    value budget. A real Settings skeleton accounts for fixed wrapper fields.
    This conservative ceiling does not change either settings limit.
    """
    if manifest.configuration_schema is None:
        return 0
    settings = Settings(schema_digest="0" * 64).model_dump(mode="json")
    frame = {"result": {"schema": manifest.configuration_schema, "settings": settings}}
    return (
        len(json.dumps(frame).encode())
        + SETTINGS_FILE_MAX_BYTES
        + SETTINGS_VALUES_MAX_BYTES
        + 1
    )


def selection_metadata_sizes(release: Release) -> tuple[int, ...]:
    """Project initial retained selection and lifecycle envelopes for this release.

    Immutable release fields are exact. Version facts use their full declared
    128-character bounds, and the longest platform name covers every release
    target. Core still enforces its own record bound on every later transition.
    This is a publication check, not permission to select or execute the release.
    """
    identity = "0" * 32
    digest = "0" * 64
    selection = ManagedSelection(
        revision=1,
        operation_id=identity,
        runtime_digest=digest,
        enabled=False,
        bundle_id=release.manifest.bundle_id,
        sequence=release.sequence,
        highest_sequence=release.sequence,
        state_schema=release.state_schema,
        permissions=release.permissions,
        configuration_schema=release.manifest.configuration_schema,
        platform="linux-glibc-aarch64",
        python_version="x" * 128,
        skulk_version="x" * 128,
        skulk_build_sha256=release.skulk_build_sha256,
    ).model_dump(mode="json")
    select: dict[str, JsonValue] = {
        "operation_id": identity,
        "state": "recovery_required",
        "expected_revision": 0,
        "selection": selection,
        "verify_runtime": True,
    }
    withdraw = {
        **select,
        "withdraws_operation_id": "1" * 32,
    }
    del withdraw["verify_runtime"]
    lifecycle: dict[str, JsonValue] = {
        "request": {
            "operation_id": identity,
            "action": "select",
            "expected_revision": 0,
            "runtime_digest": digest,
            "rollback": False,
            "accept_permissions": False,
        },
        "selection": selection,
        "state": "recovery_required",
        "error_code": "validation_failed",
        "withdraws_operation_id": None,
    }
    return tuple(
        len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
        for value in (selection, select, withdraw, lifecycle)
    )


def validate_publication_metadata(release: Release, *, managed: bool) -> None:
    """Refuse unusable configuration or managed selection metadata before output.

    Args:
        release: Validated immutable release the builder or signer will publish.
        managed: Whether Core's managed runtime selection contract will consume it.

    Raises:
        ValueError: The schema read or an initial selection envelope exceeds the
            corresponding existing reader. No files or signatures are written.
    """
    reply = configuration_reply_ceiling(release.manifest)
    if reply > CONTROL_RESPONSE_MAX_BYTES:
        raise ValueError(
            f"configuration schema and bounded settings require up to {reply} bytes; "
            f"ordinary configuration replies allow {CONTROL_RESPONSE_MAX_BYTES}. "
            "Reduce configuration-schema metadata."
        )
    if managed:
        largest = max(selection_metadata_sizes(release))
        if largest > MANAGED_SELECTION_MAX_BYTES:
            raise ValueError(
                f"managed selection metadata requires {largest} bytes; "
                f"retained selection readers allow {MANAGED_SELECTION_MAX_BYTES}. "
                "Reduce configuration-schema or permission metadata."
            )
