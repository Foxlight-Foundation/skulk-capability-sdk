# Explicit inference authority contracts

Native 0.7.0rc3 retains the earlier candidate profiles and adds an explicitly
selected execution cohort:

| Profile | Purpose |
| --- | --- |
| [`skulk.portable/2`](../kernel/contracts/portable-v2.json) | Signed author declarations with explicit inference and processing domain |
| [`skulk.composition/2`](../kernel/contracts/composition-v2.json) | Host-owned caller context and delegation attenuation |
| [`skulk.managed/2`](../kernel/contracts/managed-v2.json) | Installation manifest and private protocol-six startup |
| [`skulk.handshake/2`](../kernel/contracts/handshake-v2.json) | Exact matching of that startup and an optional host-admitted stream call |
| [`skulk.composition/3`](../kernel/contracts/composition-v3.json) | Read/inference attenuation with explicit nullable authority expiry |
| [`skulk.calls/2`](../kernel/contracts/calls-v2.json) | Composed unary and streaming calls with exact raw-body binding |
| [`skulk.transport/2`](../kernel/contracts/transport-v2.json) | Protocol-six health/shutdown and separate binary media |

They preserve the issued profiles and the earlier rc1 portable/2 and
composition/2 contracts. Existing applications and owners continue using their
selected cohort. Installing the package does not select these profiles or grant
inference permission. These candidate packages are not a released runtime.

The new descriptor behavior `inference` covers generated outputs, including
text, embeddings and speech. It is separate from `read`, `proposal` and `execute`.
Every descriptor explicitly declares its own `processing_domain`: `local`,
`private_site` or `external`. No missing-field default grants a processing
boundary. A declaration describes the entry point; it is not caller consent.
Delegated work requires independently admitted destinations and boundaries.

The portable envelope uses protocol 6. Its closed schema language, JSON domain,
canonicalization and resource limits match portable/1; the new effect does not
make recursive schemas, Unicode regex shorthands or unsafe integers portable.
Descriptor identities include the required domain and profile fingerprint.

## Attenuation

Composition protocol 2 permits read and inference delegation. The exact signed
entry point is an effect ceiling:

| Current executor | Eligible child effects |
| --- | --- |
| Read | Read |
| Inference | Read, inference |
| Proposal or execute | None through this contract |

A read child loses inference targets even when its original caller has broader
permission. The inference parent retains its separately authorized sibling
targets. Governed effects remain denied even if policy lists them.

Each child is the intersection of its parent and current caller policy, further
bounded by its own effect ceiling. Original caller ID, kind, issuer and root
remain fixed. Scope and exact body binding, fresh child identity, shrinking
deadlines when present, current policy revision and cycle prevention remain
mandatory.
Changing a descriptor version cannot disguise reentry into an ancestor.

Processing permission is independent: authorizing inference does not authorize
external disclosure. The selected destination domain must occur in both parent
and current policy. Hosts bind destination behavior and domain to the exact
signed descriptor. Task labels, model names and supplied target records cannot
establish those facts.

These rules constrain calls admitted by the host. They do not sandbox arbitrary
application code or enforce network egress. Untrusted code needs independent
host isolation; a signed `local` declaration alone cannot provide that isolation.

The permission snapshot is separate from current readiness. Unrelated capacity
changes do not rewrite grants. Immediately before dispatch, the host must
independently verify the selected installation, runtime, incarnation, current
policy and actual provider admission.

## Installation and protected startup

A managed/2 manifest can declare `effect: inference` with read and inference
entry points. A read manifest cannot contain inference. Proposal and execute
entry points require a governed manifest. Nested declarations must be
portable/2, with protocol 6 and their exact normative fingerprints.

`bounds.inference_seconds` defaults to `null`. It adds no overall inference
execution ceiling or output/request-count allowance. An owner can opt into a
finite ceiling, which intersects independently admitted caller/parent deadlines.
Existing read, control and stream watchdog fields retain their meanings; they
must not silently become generation limits. `bounds.concurrency` is an admission
resource from 1 to 32, default 1, and never widens caller or processing permission.
These are declaration rules; enforcing inference execution needs a compatible
host and call/media cohort.

A Steward exposure can declare inference with `risk: inference`. It must match
the exact descriptor and an inference or governed manifest. It cannot disguise
inference as an observation. Exposure metadata neither extends Steward nor grants
it execution authority.

Startup binds the complete normalized manifest identity, executable, protected
endpoints, settings and host/application identities. Tokens are exactly 64
lowercase hexadecimal digits; the host must generate 32 cryptographically random
bytes and own delivery, lifetime and one-use admission. Structural parsing does
not establish randomness, trust or one-use state. Credentials and startup bytes
must stay on protected inherited IPC and out of logs or published manifests.

Handshake/2 derives and matches the exact protocol-six hello. Public identities
must match and token comparison is constant-time. A stream hello also binds the
host-selected call. Successful structural matching can be repeated: only the
host can atomically consume its live grant. Supplied original-caller claims are
not handshake fields and are refused.

## Execution lifetime and transport

The execution cohort selects portable/2, managed/2, handshake/2, composition/3,
calls/2 and transport/2 together. It does not reinterpret any older profile.
Composition/2 remains available with its original finite-deadline semantics.

Composition/3 requires `deadline_unix_ms` in Context, Policy and DelegateRequest.
An explicit `null` adds no overall authority deadline. Missing fields are invalid.
The child receives the earliest non-null parent, current-policy and requested
expiry. A later request is clamped to existing authority; null never erases a
finite ancestor or credential expiry. Every supplied finite deadline must still
be in the future, and the host must provide a valid current clock.

Calls/2 binds the exact raw request SHA-256 to composition/3. Invoke
`remaining_seconds` is null exactly when the admitted context expiry is null.
A streaming `delegate_started` likewise carries either two null lifetime fields
or a finite timestamp and remaining budget. The host intersects any opted-in
owner ceiling and actual monotonic scope deadline before writing; the receiver
must clamp finite budgets against its parent and the absolute expiry. Delayed
messages cannot mint a fresh budget. Parsing is not active-call authorization.

There is no implicit 30-second unary or 300-second stream generation limit, no
maximum-integer stand-in for infinity, and no aggregate output/request allowance.
Finite numeric bounds protect portable representation. They are not defaults.
Disconnect, authority withdrawal, cancellation and bounded active-call admission
still apply to an uncapped call.

Transport/2 accepts health and shutdown controls only. Context-free invoke,
stream-invoke and result records are refused: execution uses calls/2. All four
I/O modes retain separate raw bodies/media, sequence validation, half-close,
held terminal output, handler cleanup and end acknowledgment. A streaming
success must agree with its corresponding call completion. Unknown outcomes
never authorize replay.

Connect, hello, channel health/idle, partial-frame reads and writes have separate
resource watchdogs. A 30-second partial-I/O clock starts after the first prefix
byte and covers the rest of that bounded frame. Waiting for the first byte uses
the real active-call lifetime and cancellation; model silence alone is not a
partial frame. Writes are bounded. Long-lived control channels require health
traffic, independently of whether a generation has a deadline.

## Native interfaces and qualification boundary

Rust exposes `portable_v2`, `composition_v2`, `managed_v2` and `handshake_v2`.
Python adds `ManagedV2Contract` and `HandshakeV2Contract`, with
`managed_v2_profile_digest` and `handshake_v2_profile_digest`. Node exposes the
same separate factories with `managedV2ProfileDigest` and
`handshakeV2ProfileDigest`. Managed startup handles derive `hello` and match
`admit_hello` in Python, or `admitHello` in Node. Exporting canonical bytes is
explicit; routine handle inspection hides credentials and caller metadata.
No binding implements a replacement language-level validator.

Rust additionally exposes `composition_v3`, `calls_v2` and `transport_v2`.
Python exposes `CompositionV3Contract`, `open_call_v2_control` and
`open_transport_v2_stream`; Node exposes `CompositionV3Contract`,
`openCallV2Control` and `openTransportV2Stream`. Their framing/fingerprint
functions have explicit V2 names. Stream opening accepts `None` in Python or
`null` in Node for no additional generation ceiling; omitting that optional
argument has the same meaning. Existing stream entry points retain their limits.
Only managed/2 startup handles can open the new channels.

The independent corpora contain 56 author/composition and 89 managed/handshake
witnesses, plus 94 composition/3, 110 calls/2 and 64 transport/2 witnesses.
Qualification compares every complete canonical record, identity, full-wire
byte vector and refusal through Rust, Python and Node. Offline installation
repeats every versioned profile from the actual archives with compilers absent.
Candidate bundles require separate complete reports for all thirteen profiles.
Actual Unix-socket and virtual-clock tests cover nullable/finite stream lifetime,
partial-I/O limits, half-close, cancellation and cleanup. A matching observation
count alone is insufficient.

These are structural/native ABI contracts. Parsing Context or Policy does not
authenticate a caller, consume a one-use grant, reserve an active scope, invoke a
provider or authorize replay. Sensitive context and request hashes must not enter
logs or replicated state. Host-owned cancellation, revocation, disconnect,
uncertain execution and protected storage remain necessary.

Issued calls/1 and transport/1 channels remain bound to managed/1 and
handshake/1. The new startup handles deliberately cannot open those channels.
Installed host readers, original-caller admission and real application/state
migration remain required before inference adoption. Package/ABI qualification
does not establish any of those installed behaviors.
Candidate manifests include both versions; consumers must select the full
versioned profile ID instead of collapsing entries to a family name.
This contract does not select a router, classifier, provider or model.


## Native host observer API (0.7.0rc4)

Python `TransportV2Observer` and the equivalent Node wrapper observe exact call
identity, direction, mode, sequence, independent half-close, and terminal/end
through the shared native lifecycle. `accept_input` / `accept_output` (Node
`acceptInput` / `acceptOutput`) admit complete protocol-six packets and return
canonical projections plus separate raw media. Invalid observations permanently
fence the observer. Inspecting or serializing it reveals no call or content.

`transport_v2_media_size` (Node `transportV2MediaSize`) validates a bounded header
before a host allocates the media body. The complete packet must still be admitted;
a valid allocation hint does not prove framing, sequencing or authority.

`input_completed`, `output_terminated`, `finished` and `failed` are independent
structural observations. A terminal alone does not prove cleanup: `finished`
requires its matching end. Even that pair does not authenticate a caller, consume
a one-use startup/stream grant, prove an application control result or authorize
delivery. The host must enforce these separately, along with current policy,
signed schemas, bounded queues, process custody and cancellation cleanup. No
profile fingerprint, issued contract, output allowance or generation lifetime is
changed by this additive API.
