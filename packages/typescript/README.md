# Skulk capability SDK for TypeScript

The public MIT `@foxlight/skulk-capability-sdk` package provides SDK1 application
and client APIs over the exact prebuilt native0.7.0 addon cohort. The loader
checks every selected addon member before loading it and has no source-build
fallback. Package installation grants no Fabric or provider authority.

Use `CapabilityApp` for setup, readiness, settings, and unary/server/client/bidirectional I/O.
`Invocation.call` and `Invocation.stream` preserve exact destination and request
identities, original-caller scope, deadlines and cancellation ownership.
`Invocation.discover` uses the active parent's protected companion channel;
`discoverCapabilities` uses an explicitly enrolled client key and caller-filtered
HTTPS directory. Neither discovery route grants execution rights.

`CapabilityClient` exposes exact descriptors, authorized reads and immutable
inert proposals. A proposal is request data for review and cannot execute work.
Native composition is read/inference; existing governed Python applications
retain their own protocol-four/runtime-two interfaces.

`JobDefinition` and `DurableJobs` share Python's native declarations, caller-private
observations, replay reservations and state binding. Acquire `jobs.serve(startup,
{directory, signal})` during setup and declare both endpoints and operation bounds.
Journal work runs in an owned helper process. Keep the adapter's AbortSignal
through backend calls and retain ownership until actual cleanup finishes.

Credential reads use the explicitly selected matching public Python SDK helper.
Values are redacted, rotation/retirement remain visible, and cancellation reaps
owned helpers. No ambient interpreter or credential backend fallback is chosen.

Supported author runtimes are Node22.18 or later in the Node22 line on macOS ARM64
and glibc2.34+ Linux x86_64/ARM64. Managed packaging selects the exact measured
Node22.23.5 wheel; the Python bootstrap preserves its owning process identity.
The matching host/runtime authenticates startup and every call. Unsupported
platforms and older feature profiles refuse explicitly.

See the repository's [native author guide](https://github.com/Foxlight-Foundation/skulk-capability-sdk/blob/initiative/sdk1-completion/docs/native-authoring.md),
[TypeScript API guide](https://github.com/Foxlight-Foundation/skulk-capability-sdk/blob/initiative/sdk1-completion/docs/typescript-authoring.md),
[discovery guide](https://github.com/Foxlight-Foundation/skulk-capability-sdk/blob/initiative/sdk1-completion/docs/native-discovery.md)
and [versioned runtime acquisition](https://github.com/Foxlight-Foundation/skulk-capability-sdk/blob/initiative/sdk1-completion/docs/native-runtime-acquisition.md).
Source/package checks, real native credential sessions and signed installed
owner/Core journeys retain their own exact-version qualification receipts.

Repository TLS tests use the OpenSSL CLI for disposable certificates. The
runtime client uses Node's built-in HTTP(S)/TLS modules.
