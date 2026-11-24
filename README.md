# Skulk capability SDK

Public MIT authoring contracts, Python and TypeScript application/client APIs,
shared native bindings and author tools for managed Skulk capabilities.

The SDK supplies application setup, unary/server/client/bidirectional I/O, declared
settings and credentials, health/readiness, invocation-scoped discovery and
composition, durable inference jobs, and cancellation-safe resource ownership.
The host authenticates the original caller and authorizes every effect.
Installing a package grants no Fabric authority or provider-account access.

## Start here

[SDK1 package and host cohort](docs/sdk1-cohort.md) distinguishes the new release
qualification from the separately issued preview.

- [Python and TypeScript native author guide](docs/native-authoring.md): create,
  run, debug, watch, invoke and build a model-free application.
- [Public package quickstart](docs/native-public-quickstart.md): exact downloads,
  package inspection and independent acquisition.
- [Runtime acquisition](docs/native-runtime-acquisition.md): select a compatible,
  separately licensed owner/runtime using its versioned release receipt.
- [Signing, feeds and Store submission](docs/native-publishing.md): publish an
  independently operated HTTPS feed; Store admission is a separate boundary.
- [Legacy Python author guide](docs/python-authoring.md): maintained governed
  protocol-four/runtime-two authoring and lifecycle commands.

SDK1 Python/TypeScript packages target version `1.0.1`, author tools `0.2.0`, native
kernel `0.7.0` and selected managed Node `22.23.5`. Package, owner/Core and
installed-application qualification are recorded separately. Consult the
acquisition guide for issued artifacts and the compatible host cohort.

## Interfaces

| Module / interface | Purpose |
| --- | --- |
| `CapabilityApp`, `ApplicationStartup` | Application setup, handlers, readiness, settings and owned resources |
| `Invocation.call`, `Invocation.stream` | Exact-target unary and four-mode clients; original-caller identity and bounded cleanup |
| `Invocation.discover`, `discoverCapabilities` | Caller-scoped active/external discovery; observations grant no execution rights |
| `CapabilityClient` | Exact descriptor observations, authorized reads and immutable inert proposals |
| `JobDefinition`, `DurableJobs` | Native inference jobs, caller-private observations, replay reservations and recovery |
| `credentials` | Declared credential reads with redaction, rotation and retirement |
| `contracts`, `wire`, `storage`, `operations`, `database`, `streams` | Maintained Python contracts, IPC, protected state and governed/durable operation primitives |
| `serving` | Bind an application server to its host-approved surface |

Native composition supports read/inference work. Proposals contain request data;
they confer no approval or execution authority. Existing governed Python
consumers retain their actual protocol-four/runtime-two interfaces. Updating
an SDK dependency does not change an application's runtime protocol.

Python3.13 is the qualified author interpreter. Native hosting is qualified on
macOS ARM64 and glibc2.34+ Linux x86_64/ARM64. TypeScript uses Node22.18 or later
in the Node22 line; managed packaging selects the exact Node22.23.5 assets.
Native Windows hosting is not qualified. Local development needs no model,
GPU, fleet lease or Foxlight account. Managed installation requires compatible
Skulk and an explicitly trusted runtime.

## Source and package boundaries

`SOURCE-MANIFEST.json` and the package export audits identify the reviewed public
source. The [shared kernel](kernel/README.md) supplies versioned canonical JSON,
contracts, journals and transport; its existing profile bytes remain frozen.
The public SDK contains no private owner/supervisor implementation, provider
integration, account session or publisher signing key. Package acquisition,
publisher registration and runtime authorization remain separate.
