# Native Python application runtime

SDK **0.5.8rc1** introduces `skulk_capability_sdk.application` as an explicit
prerelease author runtime. The existing protocol-4 modules retain their frozen
schemas and bytes. Importing the SDK does not import a native extension or select
a runtime. This module requires the separately installed, immutable native
**0.7.0rc3** execution candidate; another version fails explicitly.

Use the wheels from [native-v0.7.0-rc.3](https://github.com/Foxlight-Foundation/skulk-capability-sdk/releases/tag/native-v0.7.0-rc.3),
verify their published SHA256SUMS and install the matching platform wheel.
Python 3.13+ on macOS ARM64 or glibc Linux x86_64/aarch64 is qualified; a Rust
compiler and Foxlight account are unnecessary. This source checkout additionally
locks all three public wheel URLs/hashes in the `native-runtime` dependency group.
Its explicit suite fails on a missing/wrong native package, rather than skipping.

```sh
uv sync --frozen --all-packages --no-editable --group native-runtime
uv run --no-sync pytest tests_native
```

A compatible host must separately select the signed runtime5/release3 managed2
closure. Installing a Python package grants no caller/effect authority and does
not convert an existing installed application. Current normal owner/Core
admission is qualified for exact same-host unary calls. The public app's native
stream socket tests qualify its author side; they do not claim that owner/Core
stream admission or a real provider application's migration is complete.

## Register explicit handlers

```python
from collections.abc import AsyncGenerator, AsyncIterator

from pydantic import JsonValue

from skulk_capability_sdk.application import (
    CapabilityApp, InputFrame, Invocation, OutputFrame,
)

app = CapabilityApp()

async def summarize(call: Invocation) -> dict[str, JsonValue]:
    return {"summary": call.payload["text"]}

async def speak(
    call: Invocation, inputs: AsyncIterator[InputFrame]
) -> AsyncGenerator[OutputFrame]:
    # Supply application behavior here. The host owns admission and destination.
    yield OutputFrame("chunk", {"text": call.payload["text"]})
    yield OutputFrame("completed")

app.unary("example.summary", summarize)
app.stream("example.speech", speak)
# The entrypoint awaits app.serve(the_host_inherited_configuration_descriptor).
```

The signed manifest must declare exactly these IDs, their actual behavior,
processing domain, version, schema and I/O modes. A unary registration cannot
stand in for a stream registration. Register before `serve`; registration is
frozen even when startup fails. Do not point the legacy source-starter/build
commands at a native app and assume they generate the new signed cohort. Native
author packaging/onboarding is a separate delivery step.

`Invocation` supplies the exact raw request bytes, an owned JSON payload and a
read-only `CallContext` projection. The compiled validator checks composition3;
the app additionally binds the exact signed descriptor, destination, mode and raw
SHA256 before entering the handler. `context.export()` returns a new copy. It is
metadata, not a bearer or permission to delegate. The compatible host separately
authenticates the original caller and rechecks current authority at dispatch.
The runtime does not resolve destinations, classify tasks or call a provider.

## Application resources and health

Use the `setup` async-context-manager callback for application initialization and
cleanup. It receives `ApplicationStartup`, which exposes installed application
and owner IDs, nullable configuration and an optional host-selected serving
address. Its projection excludes handshake tokens, protected socket paths and
credential-reader configuration. Configuration is an owned copy each time it is
read; absence remains `None`, rather than silently becoming an empty object.

`ready` and `surfaces` are synchronous cached callbacks. They must perform no
blocking work or I/O. The sole control reader answers health during an active
async handler or iterator. The protected channel's health/idle budget follows the
signed startup/health bounds, independently of model output and generation
lifetime. A quiet model is not a failed model. Handlers must yield control to the
event loop, using owned asynchronous resources for provider and media work.

One application currently runs one active call. A second concurrent invoke is a
channel fault, not queued work or permission to create another effect. Durable
operations, author-side delegated calls, declared credentials/preflight, settings
refresh and TypeScript parity are not implemented by this module.

## Lifetime, streaming and cancellation

The host supplies either an explicit nullable generation budget or a finite
budget bound to the context deadline. Finite requests honor the earlier budget
and wall-clock deadline. Null introduces no SDK generation timeout. Health,
connect/hello, bounded partial-frame I/O and write backpressure remain independent
resource fences. There is no aggregate output-token or media-byte allowance.

The compiled transport owns framing, separate per-frame media bounds,
per-direction sequences, half-close and terminal/end state. JSON input/output
chunks are checked against the exact declared portable schemas; completed payloads
use the declared output schema. A binary-only chunk is not required to satisfy a
JSON schema. A blob reference remains metadata: the SDK never fetches caller
URLs or resolves paths supplied by a frame.

An output iterator supplies chunks followed by exactly one `completed`, `failed`
or `cancelled` terminal. The compiled channel holds the terminal until iterator
cleanup finishes; then it sends one end marker. Control completion follows stream
cleanup. Client-streaming consumes caller input and returns its result in the
terminal; server-streaming has no caller media ingress. Bidirectional handlers may
consume/yield concurrently using application-owned tasks, which they must drain
before returning.

Cancellation interrupts the active handler once and waits for its cleanup.
Repeated cancellation cannot cut across resource teardown. Shutdown drains the
handler before closing control, then completes application teardown. Invalid frames, broken
channels and backend failures never reconnect or replay an uncertain request.
Backend exception text is not sent over IPC; callers receive a closed sanitized
error code. An app cannot manufacture a successful stream end after its channel
has failed.

The independent `tests_native` suite uses real released compiled socket codecs,
not a Python replacement validator. It covers exact bytes/original context,
refusal before behavior, all three streaming modes, separate media, schema and
terminal refusals, handler/application cleanup, repeated cancellation, explicit
finite timeout and over-thirty-second silent generation with live health. Real
signed installation and protected application-state migration remain separate
acceptance gates.
