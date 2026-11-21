"""Installed public SDK child used only with synthetic test credentials."""

import asyncio
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from pydantic import JsonValue

from skulk_capability_sdk.application import (
    ApplicationStartup,
    CapabilityApp,
    Invocation,
)
from skulk_capability_sdk.credentials import CredentialReader, CredentialUnavailable


async def main() -> None:
    """Read current aliases in a separate process without any owner imports."""
    reader: CredentialReader | None = None

    @asynccontextmanager
    async def setup(startup: ApplicationStartup) -> AsyncGenerator[None]:
        nonlocal reader
        reader = startup.credentials
        yield

    async def read(_call: Invocation) -> dict[str, JsonValue]:
        assert reader is not None
        try:
            value = await asyncio.to_thread(reader.read, "api-token")
        except CredentialUnavailable:
            return {"unavailable": True}
        return {"synthetic_value": value.get_secret_value().decode() if value else None}

    app = CapabilityApp(setup=setup)
    app.unary("example", read)
    await app.serve(int(sys.argv[1]))


if __name__ == "__main__":
    asyncio.run(main())
