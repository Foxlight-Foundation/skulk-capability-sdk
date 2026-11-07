"""Bounded read-only native helper; no backend discovery or credential mutations."""

import base64
import json
import resource
import sys
from typing import Literal, final

from keyring.backend import KeyringBackend
from pydantic import Field

from .contracts import Contract


@final
class NativeRead(Contract):
    """One exact owner namespace and immutable version to read."""

    backend: Literal["macos_keychain", "linux_secret_service"]
    service: str = Field(pattern=r"^ai\.foxlight\.skulk\.[a-f0-9]{32}$")
    entry: str = Field(pattern=r"^[a-f0-9]{32}$")


def _backend(request: NativeRead) -> KeyringBackend:
    if request.backend == "macos_keychain" and sys.platform == "darwin":
        from keyring.backends.macOS import Keyring

        return Keyring()
    if request.backend == "linux_secret_service" and sys.platform == "linux":
        from keyring.backends.SecretService import Keyring

        return Keyring()
    raise ValueError("backend unavailable")


def main() -> None:
    """Read at most one native version, with no diagnostic payloads on failure."""
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        raw = sys.stdin.buffer.read(8193)
        if len(raw) > 8192:
            raise ValueError("request bound")
        request = NativeRead.model_validate_json(raw)
        value = _backend(request).get_password(request.service, request.entry)
        if value is None or not 0 < len(value) <= 5500:
            raise ValueError("value bound")
        decoded = base64.b64decode(value, validate=True)
        if not 0 < len(decoded) <= 4096:
            raise ValueError("value bound")
        sys.stdout.write(json.dumps({"value": value}))
    except Exception:
        # Native libraries can include account and key information in exceptions.
        # The parent publishes one fixed unavailable error, never this payload.
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
