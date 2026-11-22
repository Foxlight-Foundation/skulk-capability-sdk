"""Shared explicit native acquisition and asynchronous cleanup ownership."""

import asyncio
import importlib
import importlib.metadata


def native_module() -> object:
    """Load only the admitted native execution cohort, without a fallback."""
    if importlib.metadata.version("skulk-capability-kernel") not in {
        "0.7.0rc3",
        "0.7.0rc4",
        "0.7.0rc5",
        "0.7.0rc6",
    }:
        raise ValueError("native application requires the explicit execution cohort")
    return importlib.import_module("_skulk_capability_kernel")


async def finish_owned[Value](task: asyncio.Task[Value]) -> Value:
    """Keep ownership through repeated caller cancellation until cleanup finishes."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result
