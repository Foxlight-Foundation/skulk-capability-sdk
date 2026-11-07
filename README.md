# Skulk Capability SDK

Build capabilities for Skulk with Python contracts, local IPC, durable
operations, transactional storage, streaming media and declared credential reads.
The SDK is licensed under [MIT](LICENSE.md). Source and package downloads require
no Foxlight account. A publishing account is separate from runtime authority.

This Python release retains the qualified protocol-4 authoring interfaces.
Protocol-3 unary applications remain compatible during the documented reader
window. The shared native kernel, TypeScript facade and higher-level
`CapabilityApp` are separate release gates; this package does not switch the
installed runtime to unfinished native code.

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

The package contains no provider account integration, gateway signing key,
installer or supervisor. Installation of this SDK does not authorize effects.
The host independently authenticates callers and admits each capability call.

`SOURCE-MANIFEST.json` records SHA-256 for the exact qualified Python export.
The initial export contains no repository history from the owner implementation.

## Author tools

[The Python author guide](https://github.com/Foxlight-Foundation/skulk-capability-sdk/blob/main/docs/python-authoring.md) covers anonymous wheel installation, a model-free source starter, local run/watch/invoke/debug, unsigned builds, exact runtime acquisition, signing/inspection and revision-fenced host lifecycle commands. The `skulk-capability-tools` package provides `skulk-cap` and compatible legacy publisher entry points.

The CLI and the runtime are separate packages. Runtime recipes retain owner 0.10.5/SDK 0.5.5 while these author interfaces use SDK 0.5.7. Package installation grants no Fabric authority. The local development harness currently exercises unary calls; application-framework, TypeScript and native adoption have separate qualification.
