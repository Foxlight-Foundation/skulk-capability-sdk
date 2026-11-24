"""Bounded test-only mutations of explicitly owned synthetic native entries."""

import base64
import json
import resource
import sys
from typing import Literal, final

from keyring.backend import KeyringBackend
from keyring.core import set_keyring, set_password
from pydantic import BaseModel, ConfigDict, Field


@final
class Request(BaseModel):
    """One test namespace, immutable entry and explicit native implementation."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    backend: Literal["macos_keychain", "linux_secret_service"]
    operation: Literal["put", "delete"]
    service: str = Field(pattern=r"^ai\.foxlight\.skulk\.[a-f0-9]{32}$")
    entry: str = Field(pattern=r"^[a-f0-9]{32}$")
    value: str | None = Field(default=None, max_length=5500)


def selected(request: Request) -> KeyringBackend:
    """Use a concrete backend; test setup must never discover a file fallback."""
    if request.backend == "macos_keychain" and sys.platform == "darwin":
        from keyring.backends.macOS import Keyring

        return Keyring()
    if request.backend == "linux_secret_service" and sys.platform == "linux":
        from keyring.backends.SecretService import Keyring

        return Keyring()
    raise ValueError("test native backend unavailable")


def main() -> None:
    """Mutate only the supplied synthetic entry and return no credential bytes."""
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    request = Request.model_validate_json(sys.stdin.buffer.read(8193))
    backend = selected(request)
    if request.operation == "put":
        if (
            request.value is None
            or not 0 < len(base64.b64decode(request.value, validate=True)) <= 4096
        ):
            raise ValueError("test credential bound")
        set_keyring(backend)
        set_password(request.service, request.entry, request.value)
        if backend.get_password(request.service, request.entry) != request.value:
            raise ValueError("native round trip differs")
    else:
        if backend.get_password(request.service, request.entry) is not None:
            backend.delete_password(request.service, request.entry)
        if backend.get_password(request.service, request.entry) is not None:
            raise ValueError("test entry remains")
    print(json.dumps({"passed": True}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Native exceptions can retain session/account details. The test parent
        # gets only a failed status; all created entry IDs remain in its custody.
        raise SystemExit(1) from None
