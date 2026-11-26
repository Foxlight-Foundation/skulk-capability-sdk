# Native Python application runtime

SDK **1.0.1** provides `skulk_capability_sdk.application` and the matching
TypeScript application framework over published native kernel **0.7.0**.
Use the [public package quickstart](native-public-quickstart.md),
[local author guide](native-authoring.md) and [SDK1 cohort](sdk1-cohort.md).
The framework selects managed/2, composition/3, calls/2 and transport/2 rather
than asking applications to implement their own authority or framing.

Importing the Python SDK does not select a native runtime. Native application
execution requires the explicitly installed matching compiled kernel. Existing
governed Python applications retain their actual protocol-four/runtime-two
interfaces; package installation does not convert them to native managed/2.
Earlier prerelease receipts remain attached to their original bytes. Signed
installation, caller permission and provider behavior are separate from local
authoring and package publication.

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
frozen even when startup fails. When a signed ID has several versions, register
all of them explicitly with `version="1.0.0"` (and the other exact versions).
The version-less shorthand is accepted only for a unique signed ID; ambiguous
or undeclared registrations fail before setup or connection. Calls never fall
back to a different version or mode. `Invocation.version` exposes the admitted
executor version. Use the [explicit public native builder](native-publishing.md) for managed/2
release3/runtime5 cohorts. The legacy check/dev/inspect and catalog paths
retain their existing contracts. Building a package does not qualify signed
installed startup, caller authorization or application state adoption.

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
credential-reader configuration. `startup.credentials` exposes only a read-only
reader for the exact signed credential declarations; its backend selection is
never ordinary configuration. Configuration is an owned copy each time it is
read; absence remains `None`, rather than silently becoming an empty object.

`ready` and `surfaces` are synchronous cached callbacks. They must perform no
blocking work or I/O. The sole control reader answers health during an active
async handler or iterator. The protected channel's health/idle budget follows the
signed startup/health bounds, independently of model output and generation
lifetime. A quiet model is not a failed model. Handlers must yield control to the
event loop, using owned asynchronous resources for provider and media work.

An application admits up to its signed `bounds.concurrency` live calls (default
one). Excess admission and duplicate active IDs fault the channel; there is no
unbounded queue. Cancellation and cleanup belong to each call independently.
After behavior and cleanup finish, the runtime retains ownership of result
publication: a late cancel cannot interrupt a partially written result frame.
The host still decides whether a cancelled result may be delivered. The separate
[native durable job facade](native-jobs.md) owns inference jobs beyond one RPC.
Generic local preflight is available with a compatible owner and a declared
`local` profile. Provider-specific preflight, hot settings refresh and TypeScript
parity remain separately gated.

## Read declared credentials

Declare `credential_inputs` in the native manifest. During `setup`, retain the
reader from `startup.credentials`; it is projected only after the compiled
managed/2 validator has admitted the actual startup. No legacy manifest or
startup is constructed. Read off the event loop:

```python
value = await asyncio.to_thread(startup.credentials.read, "api-token")
```

The result is `SecretBytes`, or `None` for an unconfigured optional input.
Undeclared references raise `ValueError`. A missing required value, missing or
damaged selected storage, or unavailable native backend raises
`CredentialUnavailable` with sanitized messages. Never log the returned value.

The TypeScript runtime uses the same public selected-reader implementation in a
parent-bounded process; see [TypeScript credential access](typescript-authoring.md#read-declared-credentials).
Python applications keep using `startup.credentials.read` as above. The bridge's
contained-read entry point is for the SDK-owned helper whose parent enforces its
deadline and reaps it, rather than application handlers on the event loop.
Each read resolves the current alias, so rotation and retirement are visible on
the next read. The reader does not provision storage, select a fallback backend,
rotate credentials or create another vault. It grants no OS sandbox boundary.
See [the credential reader contract](python-authoring.md) for author tooling.

A compatible owner provisions the explicit selection and manages write-only
replacement/retirement with installed-node, schema and revision fences. Missing
required inputs suppress native discovery and new execution admission; they do
not make an otherwise healthy application process unhealthy. Keep management
initialization usable without provider credentials and handle
`CredentialUnavailable` there so an operator can repair the connection. The
cached `ready` callback describes process/control readiness, not account
availability. Credential changes do not cancel already dispatched work or
invalidate a value the application retained; read again before new provider work.
Settings changes continue to use owner-controlled withdraw, reap, persist and
restart. Do not use ordinary settings to select or overwrite credential storage.
This is source-level native credential support, not final SDK1 installed-cohort
qualification or provider-specific preflight support.

## Call an exact capability

An active invocation can call an explicitly selected unary destination. Build a
`CapabilityEndpoint` from that installed application's ID and its exact portable/2
descriptor. The descriptor may come from application configuration or a discovery
surface; constructing or parsing it establishes no signed provenance or permission.
The host rechecks the live signed destination, original caller and current policy
before dispatch. The client performs no discovery, model selection or routing.

```python
from pydantic import JsonValue

from skulk_capability_sdk.application import (
    CapabilityEndpoint, Invocation,
)

async def call_selected(
    invocation: Invocation,
    installed_id: str,
    descriptor: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    destination = CapabilityEndpoint.from_descriptor(installed_id, descriptor)
    reply = await invocation.call(destination, {"input": "application task"})
    return reply.payload
```

`Invocation.call` returns `CallReply`. `raw_payload` preserves the exact response
bytes and `payload` returns an owned object on each access. Pass raw UTF-8 JSON
object bytes when request whitespace/numeric spelling must be retained; an object
argument is encoded once. Input and successful output are checked against the
selected descriptor's portable schemas. A streaming descriptor is refused before
writing, rather than silently adapted to unary behavior.

The SDK binds the parent scope and original parent request from the admitted
invocation; an application cannot replace these fields through ordinary payload
data. The request names the exact installed destination, descriptor revision,
version, behavior and processing domain. These are claims for the host to verify,
never a bearer credential or a replacement for host authentication. Exporting a
`CallContext` does not create an active client, and a retained invocation cannot
issue calls after it finishes.

The default child deadline inherits the parent's nullable authority deadline.
An explicit `deadline_unix_ms` requests an absolute Unix-millisecond ceiling;
the host intersects it with the actual parent/caller lifetime and any applicable
owner ceiling. A later timestamp or null cannot extend parent authority. There is
no implicit overall generation timer, request allowance, retry or fallback. Health
and bounded partial I/O continue independently while a child is silent.

At most one delegation is pending per invocation; an overlapping call refuses
locally without a queue. Other admitted parent invocations remain independent.
Parent-scoped increasing u64 sequences use the frozen calls/2 encoding. Local
structural/schema refusals consume no sequence; a host-refused admitted request
consumes its sequence. The next explicit call uses the next identity without a
completed-request ledger.

`CapabilityCallError.code` is one of `denied`, `invalid_payload`, `provider_error`,
`timeout`, `cancelled` or `outcome_unknown`. No raw backend exception text is
exposed. An error never authorizes replay; the SDK does not retry or automatically
choose another destination. Uncaught errors propagate their bounded code to the
parent's result. Applications may handle known refusals explicitly within their
remaining host authority.
An `outcome_unknown` response permanently retires this invocation's client. The
application may report that uncertainty, but cannot issue a replacement effect
under the same parent.

Await and drain delegated calls before returning. Cancelling or abandoning a
pending child permanently retires the parent's client and prevents a successful
parent result, even if application code catches cancellation. Calls/2 has no
separate child-cancel frame: host-initiated cancellation drains its scoped child
before accepting parent completion; unilateral abandonment may require the host
to retire the application channel to fence uncertain work. Cleanup cannot issue
new delegations after cancellation. The SDK finishes an already-started frame
write before publishing parent completion, retains bounded correlation for an
already-crossing late response, and closes owned resources on channel loss. It
also releases a child wait already inside owned cleanup when the parent retires;
local cleanup does not depend on a response from a cancelled host scope. A
stream's buffered successful terminal is fenced before it can be flushed after
retirement. The SDK never reconnects an uncertain request.

A pending child retains its parent correlation even if local behavior has ended
and other invocations arrive. This state counts within signed concurrency, rather
than an unbounded completed-ID cache. A conforming host must finalize the admitted
child response before freeing that parent cleanup slot for further admission.
Host cancellation/finalization is part of the matching installed-host acceptance;
a host that abandons child finalization is refused rather than silently releasing
the SDK's pending slot.

The native tests compose two independently implemented public application handlers
through the released compiled codec and admission kernel. They qualify author-side
bytes, context, schemas, health, correlation and cancellation ownership. Their
synthetic host peers do not establish production authentication, signed installed
application composition or the complete clean public author journey.

## Call an exact streaming capability

Use `Invocation.stream` for an explicitly selected server-streaming,
client-streaming or bidirectional endpoint. `Invocation.call` remains unary.
The endpoint comes from exact host discovery; choosing it grants no permission.

```python
from skulk_capability_sdk.application import CapabilityEndpoint, Invocation


async def consume(call: Invocation, selected: CapabilityEndpoint) -> dict:
    async with call.stream(selected, {"input": "application task"}) as stream:
        if stream.mode != "server_streaming":
            await stream.send({"input": "additional input"})
            await stream.complete_input()
        async for frame in stream:
            # Both values are owned copies; media is separate from JSON.
            payload, media = frame.payload, frame.media
            if frame.kind == "completed":
                return payload or {}
    raise RuntimeError("provider did not complete successfully")
```

Supply the endpoint's declared chunk schema to `send`; invalid local chunks
consume no sequence or wire effect. Inline binary input additionally supplies
`media_metadata={"kind": "inline", "media_type": "audio/pcm"}` and `media=bytes`.
Input half-close leaves output open. One reader and one writer can run together;
overlapping operations in the same direction refuse without queuing.

`CallStreamFrame` exposes `kind`, an owned `projection`, owned `payload`, exact
`raw_projection` bytes and separate `media` bytes. A semantic `failed` or
`cancelled` terminal is not success. The client exposes any terminal only after
native end, native resource completion and the exact `delegate_finished` control
result. `stream.finished` reports that combined transport completion, including
valid semantic refusal. Continue iteration through its terminal before exiting.

One child, unary or streaming, may be pending per invocation. A known refusal
before start permits another admitted sequence. Early exit, cancellation or an
uncertain child outcome retires the parent client; calls/2 has no independent
child-cancel message. Teardown retains native socket/task ownership, while the
sole control reader remains available for health and other admitted roots.
Late final control remains correlated to the retired parent until consumed.

An optional `deadline_unix_ms` narrows the child. The client intersects it with
the captured parent budget and the host's actual child deadline/remaining time;
the owner and Core independently enforce current caller and signed target
ceilings. Null adds no generation deadline. Connection and partial-I/O bounds
remain independent resource protections. No call is retried or replayed.

These are source and account-free socket checks against rc5, with matching host
source qualification. They do not qualify arbitrary old owners or establish the
final SDK 1 signed installed package cohort.

## Lifetime, streaming and cancellation

The host supplies either an explicit nullable generation budget or a finite
budget bound to the context deadline. Finite requests honor the earlier budget
and wall-clock deadline. Null introduces no SDK generation timeout. Health,
connect/hello, bounded partial-frame I/O and write backpressure remain independent
resource fences. There is no aggregate output-token or media-byte allowance.

The compiled transport owns framing, separate per-frame media bounds,
per-direction sequences, half-close and terminal/end state. JSON input/output
chunks are checked against the exact declared portable schemas; completed payloads
use the declared output schema, including a missing or null completed payload.
Portable JSON values are bounded at 64 KiB by the frozen portable/2 profile; use
declared caller-streaming or bidirectional binary media for larger inputs. A binary-only chunk is not required to satisfy a
JSON schema. A blob reference remains metadata: the SDK never fetches caller
URLs or resolves paths supplied by a frame.

An output iterator supplies chunks followed by exactly one `completed`, `failed`
or `cancelled` terminal. Declared failure/cancellation codes carry an empty,
sanitized message; backend exception text is never included. After the first
terminal, the runtime closes the iterator without resuming
ordinary handler code. Put teardown in `finally` or register invocation resources.
The compiled channel holds the terminal until protected iterator/resource
cleanup finishes; then it sends one end marker. Control completion follows stream
cleanup. Client-streaming consumes caller input and returns its result in the
terminal; server-streaming has no caller media ingress. Bidirectional handlers may
consume/yield concurrently using application-owned tasks, which they must drain
before returning.

Use `await call.enter_resource(async_context_manager)` for invocation-owned
resources. The SDK closes these resources under cancellation protection before
result or stream terminal/end delivery. Register resources during the handler and
drain any application-owned tasks before returning. Arbitrary teardown hidden
inside a unary backend function follows ordinary Python cancellation semantics;
register its resources with the SDK when teardown must finish reliably.

Cancellation interrupts active behavior once and waits for SDK-owned cleanup.
Repeated cancellation cannot cut across resource teardown. Shutdown drains the
handler before closing control, then completes application teardown. Invalid frames, broken
channels and backend failures never reconnect or replay an uncertain request.
Backend exception text is not sent over IPC; callers receive a closed sanitized
error code. An app cannot manufacture a successful stream end after its channel
has failed.

The independent `tests_native` suite uses real released compiled socket codecs,
not a Python replacement validator. It covers exact bytes/original context,
refusal before behavior, all three streaming modes, separate media, schema and
terminal refusals, handler/application cleanup (including cancellation during natural terminal or
schema-refusal teardown), invocation resource custody, repeated cancellation, explicit
finite timeout, signed concurrent call isolation, backpressured result publication
with late cancellation, and over-thirty-second silent generation with live health. Real
signed installation and protected application-state migration remain separate
acceptance gates.
