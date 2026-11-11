# Host-owned caller composition candidate

Native 0.5.0rc1 adds the independent [`skulk.composition/1`
specification](../kernel/contracts/composition-v1.json). It provides structural
record admission and pure delegation attenuation in the same Rust kernel for
Python and TypeScript. It does not activate a caller scope, establish a trusted
caller, dispatch a request or change any installed application.

A host admits the original caller through its ordinary authentication boundary.
It retains the active `Context` in protected process-local state, bound to the
exact authenticated capability connection. The context records an opaque caller
identity and kind, issuer, root/parent scope, executor, exact request-byte hash,
policy revision, allowed concrete targets/processing domains, ancestry and a
shared absolute deadline. Scope IDs must be fresh random 256-bit host identities;
they are sensitive metadata, never bearer credentials. Never log or replicate
contexts, caller IDs, request hashes or scope-derived identifiers. Host signing
keys, gateway/client keys and provider credentials never enter the record.

`CompositionContract.from_json` in Python and `fromJson` in TypeScript validate
`Context`, `DelegateRequest` and `Policy`. A structurally valid claim of being an
operator remains an untrusted claim. The runtime must retrieve its own live
parent, current caller policy and authenticated executor rather than using
records supplied by a capability as those observations.

The parent's `admit` method checks an untrusted delegation request against those
host observations and the actual destination request bytes. It preserves the
original caller and root identity, binds parent scope and request, rechecks the
exact destination/revision/version/domain, intersects allowed targets and
processing domains, requires the live policy to name that same caller/kind/issuer,
appends ancestry and only shortens the deadline. Changing
version or descriptor revision cannot bypass a cycle check. A changed policy
revision requires fresh host admission. Only delegated `read` behavior is
supported; governed effects require separately qualified approval authority.

```python
from _skulk_capability_kernel import CompositionContract

# These observations come from authenticated host state, not capability input.
parent = CompositionContract.from_json("Context", active_parent_json)
policy = CompositionContract.from_json("Policy", current_caller_policy_json)
request = CompositionContract.from_json("DelegateRequest", incoming_request_json)
child = parent.admit(
    request, policy, authenticated_executor_json,
    actual_destination_request_bytes, host_now_unix_ms, fresh_host_scope_id,
)
```

The TypeScript facade has the same immutable `CompositionContract` and `admit`
interface. Both return defensive byte copies from explicit `canonical()`;
incidental representation/JSON inspection contains only the record kind. No
Python or JavaScript authorization/validation fallback is used.

The host must atomically find the still-active parent under the current
connection, reserve a parent-scoped one-use request ID, and fence expiry,
revocation, cancellation and disconnect before dispatch. It must cancel/drain
children when a parent ends, invalidate scopes on restart, bound its registry,
and never replay uncertain execution. Pure attenuation deliberately performs
none of these effects. Repeating this function is not permission to repeat a
request. A normal runtime adapter must qualify those obligations separately.

Records are bounded to 262,144 encoded bytes, concrete target lists to 64,
ancestry to eight hops and supplied request bytes to 16 MiB. These are resource
bounds for this metadata/admission API, not model output allowances. Hashes bind
exact request bytes, including their serialization; callers must not hash one
representation and dispatch another. The host must enforce its monotonic
remaining-time/cancellation fence as well as the absolute deadline. Clock change
must never extend an already active operation.

The independent synthetic corpus covers full canonical results, two-hop original
caller preservation and refusals for changed parent/executor/request/target,
policy change/revocation, disallowed processing, governed effects, cycles,
expired/extended deadlines and reused scope identities. Qualification compares
all complete observations in Rust and installed Python/Node artifacts, then
repeats both bindings after compiler-free offline archive installation.

Composition protocol 1 is a record-profile version. It does not extend the
closed native transport 1 / protocol-five invoke record, its 30-second maximum,
or any issued startup/handshake/descriptor profile. Sending composition through
a native connection requires explicit compatible transport negotiation and
normal owner/Core qualification. Active caller authentication, native delegated
calls and real protected application-state migration are separate acceptance
gates; this candidate alone qualifies none of them.
