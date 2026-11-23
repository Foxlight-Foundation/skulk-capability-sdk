# Describe capabilities, perform reads and prepare proposals

The SDK 0.5.16rc1 candidate provides the same common interfaces in Python and
TypeScript. It uses the issued native7 readers and the existing invocation
discovery/call channels. It adds no host, credential, native execution profile,
automatic model selection or routing.

Use `CapabilityClient` inside an admitted application invocation. `describe`
freshly resolves an exact descriptor through that original caller's scoped
directory. `read` accepts only a declared unary `read`; inference, proposal and
execute declarations refuse before transport. The host independently checks
current permission again when dispatching the actual read.
Configured reads use that existing call path directly and do not need the
optional discovery channel or an extra catalog round trip.

```python
from pydantic import JsonValue
from skulk_capability_sdk.application import CapabilityApp, Invocation
from skulk_capability_sdk.capabilities import CapabilityClient

app = CapabilityApp()

async def handle(call: Invocation) -> dict[str, JsonValue]:
    """Read a configured destination through this invocation's current caller."""
    client = CapabilityClient(call)
    directory = await call.discover()
    offer = directory.select("managed.example", "example.status", "1.0.0")
    description = await client.describe(offer.endpoint)
    result = await client.read(description.endpoint, {"item": "selected"})
    return result.payload

app.unary("example", handle)
```

```ts
import {CapabilityApp, CapabilityClient} from '@foxlight/skulk-capability-sdk';

const app = new CapabilityApp();

app.unary('example', async call => {
  const client = new CapabilityClient(call);
  const directory = await call.discover();
  const offer = directory.select('managed.example', 'example.status', '1.0.0');
  const description = await client.describe(offer.endpoint);
  return (await client.read(description.endpoint, {item:'selected'})).payload;
});
```

The caller cannot supply a replacement identity, bearer or policy. Descriptions
are observations. They are not cached permission, and retaining a client after
its parent finishes cannot activate another root. Calls retain the existing
current-policy, cancellation, revocation, deadline and uncertain-outcome rules.
No read or directory failure triggers a retry or another destination.

## Inert proposals

`await client.propose(endpoint, arguments)` makes a fresh caller-filtered
observation and constructs immutable request data. It invokes no handler and
consumes no child-effect sequence. A later action needs separate host admission
and any required approval.

```python
intent = await client.propose(offer.endpoint, {"item": "selected"})
review_identity = intent.digest
# Explicit export may contain private arguments. Do not log it.
review_bytes = intent.canonical()
```

```ts
const intent = await client.propose(offer.endpoint, {item:'selected'});
const reviewIdentity = intent.digest;
const reviewBytes = intent.canonical(); // Explicit private-data export.
```

`CapabilityDescription.from_endpoint` (`fromEndpoint`) and
`CapabilityProposal.prepare` also work locally with a configured native
`CapabilityEndpoint`. Those pure methods make no claim that the destination is
installed, visible or permitted. They can describe a governed declaration and
prepare review data without opening a governed execution path.

The current invocation directory admits read and inference destinations.
Governed destinations remain unavailable through the caller-bound client until
a separately qualified host authority admits them. The local constructor is
never a substitute for that admission or for approval.

A proposal binds the exact node, capability/version, complete native descriptor
identity, canonical JSON arguments and their request hash. Both languages use
the native readers/codec and produce identical bytes. The explicit SDK data
envelope has `format: 1` and `kind: "capability_request_proposal"`; it is not an
issued native approval or execution contract. A digest is a comparison identity.
No caller context, token, approval credential or replay permission is included.
No method approves, submits or executes the proposal.
`request_bytes` (`requestBytes`) explicitly exports the exact request bytes bound
by that hash. Re-encoding a payload copy can change its byte identity; any later
host admission must check the actual bytes it will dispatch.

The argument object follows the existing portable JSON/schema limits. Unary and
server-output request intents are supported. Client-input and bidirectional
media streams refuse because initial JSON alone cannot represent their input.
No media path, URL or blob reference is resolved. Normal invocation stream APIs
remain the interface for actual streamed work.

Descriptors, arguments and canonical exports are copied at public boundaries.
Incidental representation/JSON inspection hides proposal contents. Explicit
exports still require the application's own confidentiality and retention policy.

## Qualification scope

Maintained tests compare fixed Python/TypeScript intent vectors, Unicode/numeric
canonicalization, native schema refusals, mutable input isolation, effect
classification and invalid identifiers. Real native socket/application tests
check original parent correlation, multiple directory reads followed by
child-effect sequence one, no proposal dispatch, and refusal to revive a
completed parent. These synthetic peers do not establish production host
authorization; installed host qualification remains a separate acceptance gate.

This is one SDK1 component. The coordinated SDK1/capability/Skulk release,
native OS-session credentials, independent public author journeys and consumer
feedback retain their own acceptance gates.
