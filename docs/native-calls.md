# Native composed-call transport candidate

`skulk.calls/1` is an explicit candidate control profile for carrying host-owned
`skulk.composition/1` contexts and delegation requests. It is not selected by the
published native 0.4 runtime. Source presence, parsing a context, and exchanging a
synthetic frame do not establish authenticated Core admission or native Fabric
routing. Those require a compatible signed runtime cohort and installed tests.

The shared Rust implementation admits the complete frame in Python and Node.
`calls_profile_digest()` / `callsProfileDigest()` identify its complete normative
schemas and semantics. The existing managed, protected hello, control and media
profiles retain their original bytes and fingerprints. Hosts select the explicit
call profile from admitted runtime metadata before execution; readers must never
guess a profile or downgrade after partial I/O.

A frame contains a four-byte big-endian metadata length, canonical UTF-8 metadata,
and exactly `payload_size` raw bytes. Metadata has its own 262,144-byte bound;
request/result bodies have a separate 16 MiB bound. These are resource bounds,
not request-count allowances or inference-output ceilings. A large inference
response uses the declared streaming interface and its separate media lifecycle.
Both lengths are checked before a network reader allocates their respective
buffers. Bodies must be portable JSON objects on successful invocation/results.
Raw body bytes are never silently reserialized: whitespace, Unicode and numeric
spelling remain part of the request hash.

`invoke` carries the host's admitted context, host-local call identity, interface
mode and remaining budget. The codec binds the exact raw body SHA-256 to that
context. Only the host may establish its original caller, connection custody,
current policy, exact signed destination, processing domains, deadline and
cancellation tree. A caller claim parsed from capability JSON is not a root grant.
The declared unary/streaming resource budgets remain separate; a codec cannot
extend the owner's remaining monotonic deadline.

`delegate` names the parent's host-local call and carries a composition delegation
request with its exact raw body. The host must resolve that active parent on the
same authenticated connection, recheck the original caller's current policy,
attenuate authority/deadlines, and reject cycles before dispatch. Request IDs are
parent-scoped increasing unsigned 64-bit sequence numbers starting at one,
encoded as 64 lowercase hexadecimal digits. At most one delegation is pending
per parent. This bounds replay memory without a cumulative request allowance;
completed request IDs and request content need not be retained. Parsing or
framing a delegation does not reserve or execute it.

`result` and `delegate_result` carry either a JSON object or a bounded error code
with no body. An unknown execution outcome never authorizes a retry. Health,
shutdown and cancellation carry no body. The native channel shares the existing
protected startup-derived hello, bounded I/O, backpressure, idle timer and
interrupted-I/O fencing. Close it from the language owner's `finally` path. The
Python API is `open_call_control(startup, seconds)` with `receive()`,
`send(metadata_json, raw_body)` and `close()`; Node exposes `openCallControl` with
the corresponding methods. `encode_call_frame` / `decode_call_frame` and Node's
`encodeCallFrame` / `decodeCallFrame` expose the same compiled codec without I/O.

A declared stream uses the separate, call-bound `skulk.transport/1` media channel.
This candidate does not itself connect that media path to a live Core invocation.
Actual host authority, installed application dispatch, multi-capability
composition, cancellation/revocation across those paths, and protected application
state migration remain distinct acceptance gates.


Hosts that own the outer socket may call `call_payload_size(metadata_json)` /
`callPayloadSize(metadataJson)` before allocating or reading a body. That native
metadata check returns only a bounded size hint. It does not authenticate the
caller or admit the as-yet unread request bytes; full `decode_call_frame` /
`decodeCallFrame` admission remains mandatory before dispatch.

The control schema also makes stream delegation negotiation explicit. The exact
signed destination descriptor fixes its `io_mode`. A unary call returns one
`delegate_result`. A stream either fails before starting or receives one
`delegate_started` with a host-assigned media call ID and clamped budget, followed
by one `delegate_finished`. The host must bind that one-use media ID to the actual
parent connection/call, request and active child scope. It must enforce the
pending-call phases; parsing a frame does not create the media grant. The caller
must wait for both media completion and control finalization before exposing
success. Implementing and qualifying that caller/host bridge remains separate
from this wire-codec candidate.
