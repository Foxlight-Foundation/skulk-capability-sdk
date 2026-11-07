"""Public metadata projections for pre-publication size checks, never authority."""

from typing import Final, Literal

from pydantic import Field, JsonValue

from skulk_capability_sdk.contracts import Contract, Digest, Identifier

from .runtime_release import RuntimePlatform

SETTINGS_FILE_MAX_BYTES: Final = 16384
SETTINGS_VALUES_MAX_BYTES: Final = 8192


class Settings(Contract):
    """Desired local configuration; contains references, never secret values."""

    revision: int = Field(default=0, ge=0)
    enabled: bool = False
    schema_digest: Digest
    values: dict[str, JsonValue] = Field(default_factory=dict)


class ManagedSelection(Contract):
    """Core's atomic v1 selection contract; contains no provider credentials.

    Core measures its own build before selection and must remeasure it before
    launching the owner. The isolated SDK checks the retained attestation and
    its own runtime; it cannot import or measure the hosting core environment.
    """

    protocol: Literal[1] = 1
    revision: int = Field(ge=1, description="Monotonic owner selection revision.")
    operation_id: str = Field(
        pattern=r"^[a-f0-9]{32}$", description="Authorizing local operation ID."
    )
    runtime_digest: str = Field(
        pattern=r"^[a-f0-9]{64}$", description="Exact signed runtime generation."
    )
    enabled: bool = Field(description="Installation-level owner admission.")
    bundle_id: Identifier = Field(description="Stable logical plugin identity.")
    sequence: int = Field(ge=1, description="Selected release sequence.")
    highest_sequence: int = Field(
        ge=1, description="Retained highest selected sequence."
    )
    state_schema: Identifier = Field(description="Selected durable state schema.")
    permissions: tuple[str, ...] = Field(
        max_length=16, description="Owner-accepted release permissions."
    )
    configuration_schema: JsonValue = Field(
        description="Signed opaque node configuration schema."
    )
    platform: RuntimePlatform = Field(description="Qualified installation platform.")
    python_version: str = Field(
        max_length=128, description="Qualified base Python version."
    )
    skulk_version: str = Field(
        max_length=128, description="Core-attested Skulk version."
    )
    skulk_build_sha256: str = Field(
        pattern=r"^[a-f0-9]{64}$", description="Core-attested exact build digest."
    )
