"""Real native job process, synthetic backend acceptance and crash uncertainty."""

import asyncio
import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from skulk_capability_sdk.application import ApplicationStartup, CapabilityApp
from skulk_capability_sdk.jobs import DurableJobs, JobDefinition
from skulk_capability_sdk.operations import OperationPlan, OperationState

_TEXT = TypeAdapter(str)


async def main() -> None:
    """Exercise only isolated synthetic disk effects using the installed SDK."""
    directory: Path | None = None

    class Backend:
        async def submit(self, operation_id: str, payload: dict[str, JsonValue]) -> str:
            assert directory is not None
            descriptor = os.open(
                directory / "backend-effects",
                os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as target:
                target.write(operation_id.encode() + b"\n")
                target.flush()
                os.fsync(target.fileno())
            print("synthetic effect accepted", flush=True)
            await asyncio.Event().wait()
            return "unreachable"

        async def observe(
            self, reference: str
        ) -> tuple[
            OperationState,
            float | None,
            str | None,
            dict[str, JsonValue] | None,
            str | None,
        ]:
            raise AssertionError("no backend reference was returned")

        async def cancel(self, reference: str) -> None:
            raise AssertionError("no accepted backend reference exists")

    async def plan(value: dict[str, JsonValue]) -> OperationPlan:
        return OperationPlan(
            plan_digest="a" * 64, summary="Synthetic", expires_at=2**31, input=value
        )

    spec = JobDefinition.create(
        id="fixture.jobs",
        version="1.0.0",
        title="Synthetic jobs",
        description="Account-free inference job fixture",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "number"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        result_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )
    jobs = DurableJobs(
        spec, Backend(), plan=plan, poll_seconds=0.01, adapter_seconds=30
    )

    @asynccontextmanager
    async def setup(startup: ApplicationStartup) -> AsyncGenerator[None]:
        nonlocal directory
        settings = startup.configuration
        assert settings is not None
        directory = Path(_TEXT.validate_python(settings["directory"], strict=True))
        async with jobs.serve(startup, directory=directory):
            yield

    app = CapabilityApp(setup=setup)
    jobs.register(app)
    await app.serve(int(sys.argv[1]))


if __name__ == "__main__":
    asyncio.run(main())
