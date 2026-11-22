# Skulk capability SDK for TypeScript

This is the SDK1 source candidate. The public package uses the issued native
0.7.0rc6 addons without a source-build fallback. Package installation grants no
Fabric authority. Managed installation and complete author journeys require
their own matching host/runtime qualification.

Supported author runtimes are Node 22.18 or later in the Node 22 line, on
macOS ARM64 and glibc 2.34+ Linux x86_64/ARM64. The exact supported addon is
selected and verified before it loads. Unsupported platforms refuse explicitly.

The source packaging candidate additionally pins managed execution to Node
22.23.5. A module's default `ApplicationFactory` receives selected runtime
metadata and returns `CapabilityApp`; its Python zipapp bootstrap preserves the
owning process PID while executing the measured Node wheel. See
[`docs/node-packaging.md`](https://github.com/Foxlight-Foundation/skulk-capability-sdk/blob/initiative/sdk1-completion/docs/node-packaging.md)
for staging, signing, limits and the remaining signed-installation gate.

Declared credential reads require an explicitly selected matching public Python
SDK helper. Values are redacted, reads observe rotation/retirement, and owned
helpers are reaped on cancellation. No ambient interpreter or backend fallback
is selected. Signed Node/runtime and native OS-session qualification remain
separate from source and clean-package checks.

`JobDefinition` and `DurableJobs` provide native durable inference with the same
descriptors, caller partitions, replay reservations and state binding as Python.
Acquire `jobs.serve(startup, {directory, signal})` from application setup and
register both read/work descriptors plus operation bounds in the signed manifest.
Journal/SQLite work runs in an owned helper process. Shared process custody lasts
until that process exits and actual adapter cleanup finishes. Pass the adapter's
AbortSignal through backend operations; the managed owner supplies final process
containment for noncooperative code. See the public repository's
[`docs/typescript-authoring.md`](https://github.com/Foxlight-Foundation/skulk-capability-sdk/blob/initiative/sdk1-completion/docs/typescript-authoring.md)
for the API, uncertainty semantics and remaining signed runtime qualification.
