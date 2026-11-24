"""Versioned read-only bridge for selected public SDK runtimes, never a broker."""

import base64
import hashlib
import importlib.metadata
import json
import resource
import sys
from pathlib import Path
from typing import Annotated, Literal, Self, final

from pydantic import Field, field_validator, model_validator

from .contracts import Contract
from .credentials import CredentialAccess, read_contained_credential

MAX_REQUEST_BYTES = 32768
IDENTITY_FILES = (
    "_credential_bridge.py",
    "_credential_reader.py",
    "credentials.py",
    "storage.py",
    "contracts.py",
)
CredentialReference = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$")]


@final
class Declaration(Contract):
    """Only the credential reference and required bit from admitted startup."""

    credential_id: CredentialReference
    required: bool


@final
class ReadRequest(Contract):
    """One declared read over inherited private pipes; no mutating operations."""

    protocol: Literal[1]
    configuration: str | None = Field(max_length=4096)
    declarations: tuple[Declaration, ...] = Field(max_length=16)
    reference: CredentialReference

    @field_validator("protocol", mode="before")
    @classmethod
    def exact_protocol(cls, value: object) -> object:
        """Reject boolean and floating aliases of the private helper version."""
        if type(value) is not int:
            raise ValueError("unsupported helper protocol")
        return value

    @model_validator(mode="after")
    def unique_declarations(self) -> Self:
        """Never let duplicate declarations weaken a required credential."""
        if len({item.credential_id for item in self.declarations}) != len(
            self.declarations
        ):
            raise ValueError("duplicate credential declaration")
        return self


def reader_identity() -> str:
    """Fingerprint the public reader implementation for cohort compatibility.

    This is not authentication of an arbitrary interpreter. The managed runtime
    separately selects and attests its executable and complete dependency closure.
    """
    directory = Path(__file__).parent
    digests = {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for name in IDENTITY_FILES
    }
    return hashlib.sha256(
        json.dumps(digests, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def main() -> None:
    """Read one value, with sanitized failures and no descendant processes.

    The parent verifies the hello before sending private selection metadata,
    bounds both startup and the complete read, and reaps the exact process.
    Native access uses the same public reader but runs in this containment
    boundary, so killing it cannot orphan a nested credential helper.
    """
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        print(
            json.dumps(
                {
                    "protocol": 1,
                    "sdk_version": importlib.metadata.version("skulk-capability-sdk"),
                    "reader_sha256": reader_identity(),
                }
            ),
            flush=True,
        )
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("request exceeds bound")
        request = ReadRequest.model_validate_json(raw)
        value = read_contained_credential(
            CredentialAccess(
                configuration=request.configuration,
                declarations=tuple(
                    (item.credential_id, item.required) for item in request.declarations
                ),
            ),
            request.reference,
        )
        print(
            json.dumps(
                {
                    "protocol": 1,
                    "value": None
                    if value is None
                    else base64.b64encode(value.get_secret_value()).decode(),
                }
            ),
            flush=True,
        )
    except Exception:
        # Backend errors may contain account names, paths or secret values.
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
