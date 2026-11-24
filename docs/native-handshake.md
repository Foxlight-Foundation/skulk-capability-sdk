# Native author records and protocol-five handshakes

The native 0.3.0rc1 source candidate adds immutable manifest/startup handles and
`skulk.handshake/1` control/media hello admission. The released native 0.2.0rc1
record bytes and the `skulk.portable/1` and `skulk.managed/1` specifications remain
unchanged. These interfaces are candidate native APIs; normal signed owner/Core
adoption and full SDK 1.0 lifecycle interfaces remain separate qualifications.

Python's compiled `ManagedContract.from_json(name, source)` and Node's
`ManagedContract.fromJson(name, source)` admit `Manifest` or `Startup` using the
same Rust implementation. `name` and the full `digest` are immutable; `canonical()`
returns an owned copy of the normalized bytes. Startup bytes contain credentials.
Routine object representation/inspection omits contents; exporting bytes is an
explicit operation that belongs only on protected IPC. Do not log startup or
handshake bytes, or use a startup-derived digest as a public account identifier.

```python
from _skulk_capability_kernel import ManagedContract

# startup_json came from the host's protected inherited startup transport.
startup = ManagedContract.from_json("Startup", startup_json)
control_hello_bytes = startup.hello().canonical()
stream_hello_bytes = startup.hello(call_id="admitted-call").canonical()
```

```typescript
import {ManagedContract} from '@foxlight/skulk-capability-kernel-qualification/dist/portable.js';

const startup = ManagedContract.fromJson('Startup', startupJson);
const controlHelloBytes = startup.hello().canonical();
const streamHelloBytes = startup.hello('admitted-call').canonical();
```

The host derives its expected startup independently and verifies received JSON
with `expected_startup.admit_hello(source, call_id=None)` in Python or
`expectedStartup.admitHello(source, callId?)` in Node. Control hello binds the
host, transport, child, bundle/version, full normalized manifest SHA-256 and
host-issued token. Stream hello binds child/manifest/token plus the exact
separately admitted call ID. Secret-token comparison uses the shared `subtle`
primitive. Tokens must be 64 lowercase hexadecimal characters; the host must
issue a fresh cryptographically random 256-bit token for each child. The earlier
managed startup record's shape admission alone does not establish that entropy
or qualify its operational handshake.

`HandshakeContract.from_json` / `fromJson` perform structural admission only.
They require an explicit protocol five, handshake profile and exact fingerprint;
unknown fields/kinds, malformed scalar JSON, duplicate decoded keys and
oversized input refuse with bounded errors containing no supplied values. Both
raw and canonical handshake records are bounded to 16,384 encoded bytes. The
bound is for these small authentication records and does not cap inference or
media output. The independent normative specification is
[`handshake-v1.json`](../kernel/contracts/handshake-v1.json).

Parsing or matching a record does not install a capability, open a socket,
consume a nonce, authorize inference, establish an original caller, or select a
runtime. The host still owns current trust, signed selection, one-use channel
lifecycle, call admission, cancellation and replay/uncertainty state. A capability
must not supply its own expected startup to a host's authorization decision.

The existing native `open_stream` / `openStream` transport remains explicitly
protocol four. New hello records are not passed through it or silently converted.
Protocol-five framed I/O, startup production/consumption and normal signed
installation require their own host/runtime integration and installed acceptance.

Maintainer qualification compares all 74 independent handshake cases through
Rust and the installed Python/Node handles, alongside all frozen portable and
managed cases. It then repeats all three corpora after offline archive
installation with compilers absent from `PATH`. Source qualification is not a
claim of installed owner/Core adoption.
