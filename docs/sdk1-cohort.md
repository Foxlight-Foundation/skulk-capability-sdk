# SDK1 package and host cohort

The SDK1 author contract is frozen. Python/TypeScript **1.0.1** and author tools
**0.2.0** are [published](https://github.com/Foxlight-Foundation/skulk-capability-sdk/releases/tag/v1.0.1).
Native kernel **0.7.0** is [published separately](https://github.com/Foxlight-Foundation/skulk-capability-sdk/releases/tag/native-v0.7.0).
The matched owner **0.18.1** / model-free reference **0.2.1** packages are signed
and published through the Store. This does not complete the coordinated Skulk
release: final consumer packages, OS-service setup and host release qualification
remain separate gates.

| Component | SDK1 target | Current boundary |
| --- | --- | --- |
| Python SDK | `1.0.1` | Published terminal-publication fix |
| TypeScript SDK | `1.0.1` | Native addon and matching Python credential-helper cohort |
| Author tools | `0.2.0` | SDK1 dependency window and positive native0.7.0 receipt |
| Native Python/Node bindings | `0.7.0` | Published on macOS ARM64 and glibc Linux x86_64/ARM64 |
| Selected managed Node | `22.23.5` | Exact archive, executable and license identities |
| Owner runtime | `0.18.1` | Signed Store closure; public versioned acquisition recipes |
| Skulk host | Compatible `2.0.3` cohort | Normal install, service and caller qualification required |
| Model-free reference | `0.2.1` | Published signed model-free reference; earlier inputs preserved |

Start with the [public SDK1 package quickstart](native-public-quickstart.md) and
[matched runtime acquisition recipes](native-runtime-acquisition.md). The earlier
[preview quickstart](native-preview-quickstart.md) and
[preview recipes](native-preview-runtime-acquisition.md) retain their original
SDK16/native7/owner8 package identities. Do not mix those inputs with SDK1 apps.

## Work from source without a host

From a reviewed checkout of this public repository:

```sh
uv sync --frozen --all-packages --no-editable --group native-runtime
AUTHOR="$PWD/.venv/bin/skulk-cap"
"$AUTHOR" init --native --language python --name example ./example
cd example
"$AUTHOR" run --native
```

Use the author environment's absolute `skulk-cap` executable when changing
directories, as shown in the [native author guide](native-authoring.md).
Local creation, run, debug, watch, all four I/O modes and media need no model,
GPU, fleet, provider account or Foxlight account. TypeScript additionally needs
its explicitly selected Node runtime and package build; see the
[TypeScript guide](typescript-authoring.md).

Managed installation requires an explicitly trusted matching owner/runtime and
compatible Core. Package installation does not create a caller key, authorize
external processing, supply credentials or grant execution authority.

## Supported contract boundaries

Python and TypeScript share native setup, readiness, settings, declared credential
reads, unary/server/client/bidirectional I/O, invocation-scoped clients/discovery,
durable inference jobs and cancellation ownership. `CapabilityClient` supplies
exact descriptors, authorized reads and inert proposals. Preparing a proposal
does not approve or execute it.

Existing governed Python consumers retain protocol-four/runtime-two interfaces.
Native managed/2 composition admits read/inference work. An SDK dependency update
does not convert a legacy application to managed/2 or enable native provisioning.
Routing and application-specific policy remain separate from these SDK APIs.

Python3.13 and the three named native artifact families are qualified. Node
authoring supports22.18+ within the Node22 line; managed packages select22.23.5.
Native Windows hosting is not qualified. Keep historical descriptors, journals,
signed records and release assets attached to their actual versions.

## Terminal-publication patch

With concurrency one, a host can receive a completed result and immediately send
the next request while the native send is still settling locally. SDK `1.0.0`
could count that cleaned invocation as busy and close the control connection.
The `1.0.1` patch joins only terminal publication before admitting the next call.
Generation and resource cleanup still hold capacity, late delegated replies keep
their correlation, and shutdown retains ownership of each send. Real native IPC
regressions force this interleave in Python and TypeScript without sleeps.
Published `1.0.0` packages, native `0.7.0` artifacts and prior signed inputs remain
unchanged. This patch does not change contract profiles or authorize execution.

## Current limitations

The TypeScript setup bridge still has a 64 KiB JSON/copy bound where the native
startup contract permits 256 KiB. Keep startup records within the smaller bound
until [SDK issue 52](https://github.com/Foxlight-Foundation/skulk-capability-sdk/issues/52)
is resolved; unrestricted large-startup parity is not qualified. Native Windows
hosting and task routing are outside this release's qualified contract.
