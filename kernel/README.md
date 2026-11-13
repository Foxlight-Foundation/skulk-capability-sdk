# Skulk capability kernel

MIT shared Rust implementation with native Python and Node bindings. This source
includes the `skulk.portable/1` descriptor/envelope candidate, compiled schemas,
canonical JSON, stream primitives and durable-operation primitives.

The portable profile is defined in [portable-v1.json](contracts/portable-v1.json).
Its native authority and complete synthetic outcome vectors are shared across
Rust, Python and TypeScript. The [reference](../docs/native-kernel.md) documents
the profile and its limits.

Build from the repository root with Rust 1.94.1, Node 22 and Python 3.13:

```sh
uv sync --frozen --all-packages --no-editable
uv run --no-sync python tools/check_kernel_export.py
uv run --no-sync python -m tools.qualify_kernel
```

Qualification builds a wheel and Node archive, installs the wheel offline, runs
Rust and binding tests, checks a typed TypeScript consumer, audits both archive
contents and compares all 1,959 portable outcomes through three backends. These
are maintainer artifacts; building them does not select an installed runtime.
The report records `runtime_adoption: false`.

The existing Python SDK retains its released contract. The portable envelope is
not yet the complete managed manifest/startup/installation interface. Historical
fixtures protect known primitive behavior; they do not define acceptance for
the distinct portable profile.

Versioned prerelease package acquisition and its distinct installed-adoption
gates are described in [native candidates](../docs/native-candidates.md).

## Explicit protocol-five transport candidate

Native 0.4.0rc1 adds `skulk.transport/1`, separately fingerprinted from frozen
portable/managed/handshake contracts. The public Python compiled functions
`encode_portable_control`, `decode_portable_control`, `encode_portable_packet`
and `decode_portable_packet` have equivalent typed Node facade exports.
`transport_profile_digest` / `transportProfileDigest` returns the complete
normative wire fingerprint. Framing is a four-byte big-endian JSON byte length,
canonical JSON, then raw media only for stream packets. Control/header bytes
are capped at 65,536, hello at 16,384, binary media at 1,048,576. These are
per-record transport resource bounds; they are not inference output allowances.
Larger media must use multiple bounded chunks or a separately qualified media
reference path. Header admission precedes binary allocation. All invocation
records carry the full 64-hex portable Descriptor identity; host admission still
compares it to the selected descriptor and validates payload and current authority.

`open_portable_stream(startup, call_id, mode, seconds)` and Node
`openPortableStream` derive the endpoint and hello from the admitted Startup.
The host separately authenticates that startup and admits the exact call/mode.
The shared native lifecycle retains eight-packet ingress, real backpressure,
contiguous call/direction sequences, independent input half-close, terminal
holding through iterator cleanup, one terminal/end acknowledgment and cancellation
fencing. The invocation deadline includes connect, hello and all reads/writes.
Close the returned stream in a language-owned `finally`; `finish` acknowledges
completed handler/iterator cleanup. Neither language runs a replacement validator.

`open_portable_control(startup, seconds)` / `openPortableControl` opens only the
startup's control endpoint. The channel exposes `send`, `receive` and `close`.
Seconds bounds connect/hello, each operation including lock admission, and idle
time; complete records renew idle time so healthy long-running children remain
connected. Partial/invalid I/O, cancellation, EOF and expiry fence the entire
channel. Close it from `finally`; reconnection and outstanding-call uncertainty
belong to the host, and no call is automatically replayed. Rust also provides
`Channel::accept` and bounded readers to bind the host's expected startup before
control admission. Host-side nonce consumption remains a separate atomic duty.

Protocol four remains explicitly selected for existing pinned generations.
Protocol-five codecs refuse old/missing/unknown profiles and never downgrade.
The new pure/native interfaces do not install a capability, grant inference
permissions, identify the original caller or qualify normal signed owner/Core
startup. Signed acquisition, protected inherited startup, coordinated first-party
state preservation and installed qualification remain separate gates.

## Host-owned composition candidate

Native 0.5.0rc1 adds independent `skulk.composition/1` records and pure
delegation attenuation through Rust and both compiled language bindings. Read
the [composition contract](../docs/native-composition.md) for authority ownership,
exact request binding, privacy, deadline and replay obligations. This does not
extend frozen native transport records or qualify runtime delegation/migration.

The composed-call candidate is documented in [native calls](../docs/native-calls.md).
Its explicit `skulk.calls/1` framing preserves raw body bytes separately from
canonical metadata, binds composition request hashes, and shares the protected
native channel's lifecycle. It does not extend the issued transport profile or
establish authenticated host authority by parsing a context.

Native 0.7.0rc1 adds explicit [inference authority contracts](../docs/native-inference.md):
portable/2 declarations with a required processing boundary and composition/2
attenuation with separate read/inference ceilings. The 56 independent witnesses
run through all three native backends and offline archive consumers. Issued
profiles are unchanged; compatible installed readers and actual inference
adoption remain separate requirements.
