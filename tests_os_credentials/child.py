"""Public-only Python application for actual native-session credential checks."""

import asyncio
import hashlib
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from pydantic import JsonValue, TypeAdapter

from skulk_capability_sdk.application import (
    ApplicationStartup,
    CapabilityApp,
    Invocation,
)
from skulk_capability_sdk.credentials import CredentialReader, CredentialUnavailable

_TEXT = TypeAdapter(str)


async def main() -> None:
    """Serve native startup and compare secrets in memory without returning them."""
    reader: CredentialReader | None = None

    @asynccontextmanager
    async def setup(startup: ApplicationStartup) -> AsyncGenerator[None]:
        nonlocal reader
        reader = startup.credentials
        yield

    async def read(call: Invocation) -> dict[str, JsonValue]:
        assert reader is not None
        reference = _TEXT.validate_python(call.payload.get("reference", "api-token"))
        try:
            value = await asyncio.to_thread(reader.read, reference)
        except CredentialUnavailable:
            return {"unavailable": True}
        except ValueError:
            return {"undeclared": True}
        if value is None:
            return {"present": False}
        raw = value.get_secret_value()
        return {
            "present": True,
            "matched": hashlib.sha256(raw).hexdigest() == call.payload.get("expected"),
            "safe": raw.decode() not in repr(value),
        }

    app = CapabilityApp(setup=setup)
    app.unary("example", read)
    await app.serve(int(sys.argv[1]))


if __name__ == "__main__":
    asyncio.run(main())
