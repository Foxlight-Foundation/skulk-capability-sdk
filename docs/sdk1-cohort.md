# SDK1 package and host cohort

The SDK1 author contract is frozen. The final `1.0.0` Python/TypeScript packages
and `0.2.0` tools are being qualified with the matching managed runtime. Native
kernel `0.7.0` is already [published](https://github.com/Foxlight-Foundation/skulk-capability-sdk/releases/tag/native-v0.7.0).
Source checks, package publication and normal installed-host qualification have
separate receipts; select compatible inputs together.

| Component | SDK1 target | Current boundary |
| --- | --- | --- |
| Python SDK | `1.0.0` | Source/wheel qualification; canonical release pending |
| TypeScript SDK | `1.0.0` | Native addon and matching Python credential-helper cohort |
| Author tools | `0.2.0` | SDK1 dependency window and positive native0.7.0 receipt |
| Native Python/Node bindings | `0.7.0` | Published on macOS ARM64 and glibc Linux x86_64/ARM64 |
| Selected managed Node | `22.23.5` | Exact archive, executable and license identities |
| Owner runtime | `0.18.0` | Matching new runtime/package qualification pending |
| Skulk host | Compatible `2.0.3` cohort | Normal install, service and caller qualification required |
| Model-free reference | `0.2.0` | New SDK1 source; issued0.1.0 remains an immutable earlier input |

The earlier [public package quickstart](native-public-quickstart.md) and
[runtime acquisition recipe](native-runtime-acquisition.md) identify the issued
SDK16/native7/owner8 preview. Those receipts stay valid for their original bytes;
they are not a runtime for a new SDK1 application.

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
