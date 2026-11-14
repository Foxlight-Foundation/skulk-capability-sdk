# Skulk Capability SDK

Build capabilities for Skulk with Python contracts, local IPC, durable
operations, transactional storage, streaming media and declared credential reads.
The SDK is licensed under [MIT](LICENSE.md). Source and package downloads require
no Foxlight account. A publishing account is separate from runtime authority.

SDK0.5.8rc1 retains the qualified protocol-4 authoring interfaces.
Protocol-3 unary applications remain compatible during the documented reader
window. The shared native kernel, TypeScript facade and higher-level
`CapabilityApp` have separate qualification gates. The new explicit
[native Python application runtime](docs/native-application.md) uses the published
0.7.0rc3 execution candidate; installing this SDK does not switch an installed
runtime or migrate an application.

Python 3.13 is required. The SDK itself needs no compiler, GPU, model or fleet
lease. Capability execution also requires a compatible Skulk host; joining a
larger fabric is optional. Native Windows is not qualified.

The modules are:

| Module | Purpose |
| --- | --- |
| `contracts` | Strict manifests, descriptors, wire messages and canonical bytes |
| `wire` | Local framed IPC |
| `storage` | Owner-only files and exclusive locks |
| `operations` | Durable work with replay fences and explicit uncertainty |
| `database` | Transactional SQLite journals |
| `streams` | Unary and four-mode streaming lifecycle with bounded media |
| `credentials` | Read only the credential inputs declared by a capability |
| `serving` | Bind a capability's own server to the host-approved surface |
| `application` | Explicit native unary/stream handlers; prerelease and separately selected |

The package contains no provider account integration, gateway signing key,
installer or supervisor. Installation of this SDK does not authorize effects.
The host independently authenticates callers and admits each capability call.

`SOURCE-MANIFEST.json` records SHA-256 for the exact qualified Python export.
The initial export contains no repository history from the owner implementation.

## Author tools

[The Python author guide](https://github.com/Foxlight-Foundation/skulk-capability-sdk/blob/main/docs/python-authoring.md) covers anonymous wheel installation, a model-free source starter, local run/watch/invoke/debug, unsigned builds, exact runtime acquisition, signing/inspection and revision-fenced host lifecycle commands. The `skulk-capability-tools` package provides `skulk-cap` and compatible legacy publisher entry points.

The CLI and the runtime are separate packages. Runtime recipes retain owner 0.10.5/SDK 0.5.5 while the legacy author interfaces were qualified with SDK 0.5.7. Package installation grants no Fabric authority. The local development harness currently exercises unary calls; native signed packaging, TypeScript and real application adoption have separate qualification.

## Shared native kernel

The [MIT kernel source](kernel/README.md) includes native Rust, Python and Node
interfaces and the `skulk.portable/1` candidate. Its [profile reference](docs/native-kernel.md)
and independent conformance corpus describe the available boundary. Kernel
qualification builds remain separate from the released Python SDK contract and
managed-runtime adoption.
