# Selected Node application packaging candidate

This SDK1 source candidate packages native TypeScript/JavaScript applications
without changing managed/2, release/3 or runtime/5. It is not a stable SDK1
publication. Normal signed owner/Core installation and the complete native
`init/run/watch/invoke/debug` journey remain separate acceptance work.

The installed application has three measured parts:

| Artifact | Purpose |
| --- | --- |
| `bundle.pyz` | A real CPython 3.13 zipapp containing a public bootstrap and an exact application inventory. |
| Application asset wheel | Compiled JavaScript and production `node_modules`, including the public SDK and its issued native addons. |
| `skulk-node-runtime` wheel | The exact Node executable, runtime identity and complete upstream license notices for one platform. |

The bootstrap verifies the selected Node and every application file before
replacing itself with Node using `exec`. The PID, process group, inherited
configuration and owner-fence descriptors, and log pipes survive that replacement.
It does not read startup authority or manufacture caller context. Node receives
only private runtime-selection metadata and the original startup FD.

## Stage an application

Use [the native author CLI](native-authoring.md) for a generated TypeScript
starter and local run/watch/invoke/debug. The public
`verify_application_assets(application, root)` and
`launch_node_source(application, root, config_fd)` helpers verify an explicit
source snapshot without installing it into the interpreter. Installed launches
continue to require the selected application namespace under the runtime prefix.
Neither launcher authenticates a Fabric caller or supplies startup authority.

Compile TypeScript before packaging. Put only runtime modules and production
dependencies in an explicit directory, for example `stage/`. Packaging does not
run npm, a compiler, lifecycle scripts or author hooks.

The entry module exports a factory:

```ts
import {CapabilityApp, type ApplicationFactory} from '@foxlight/skulk-capability-sdk';

const createApplication: ApplicationFactory = runtime => {
  const app = new CapabilityApp({credentialHelper: runtime.credentialHelper});
  app.unary('example.echo', async call => ({text: call.payload.text ?? null}),
    {version: '1.0.0'});
  return app;
};
export default createApplication;
```

The runtime selects the matching public Python credential helper. Applications
cannot choose its program, backend or vault through ordinary request settings.
The helper preserves a selected virtualenv path while measuring its actual
interpreter target; Node and application assets refuse symlinks.

Add `node-capability.toml` beside the compiled managed/2 `manifest.json`:

```toml
format = 1
assets = "stage"
entry = "app.mjs"
license = "LICENSE"
```

Use the same candidate version of the Python SDK and the staged npm SDK. The
manifest must explicitly include the generated application dependency in
`runtime_dependencies`: `skulk-node-app-` followed by the first 24 lowercase
hex characters of SHA256 of the UTF-8 `bundle_id`. This stable namespace avoids
collisions between applications; it grants no runtime authority. The builder
refuses a missing dependency instead of adding one on the author's behalf.

```sh
skulk-cap build --native --language typescript --project ./example --output ./build
```

The empty output directory receives `bundle.pyz`, the application wheel,
`manifest.json` with the exact archive digest, and `node-build.json`. Repeating
the same inputs produces the same artifact bytes. Existing output is refused.
Packaging rejects links, missing or extra declared assets, known private
configuration files and oversized inventories. Use a clean production staging
tree; this guard is not a general secret scanner.

Regular helpers retain their executable status in the signed inventory and
installed wheel. Startup checks that status as well as every file's bytes;
permission changes cannot silently turn a staged helper into a different asset.

## Select Node and sign

The reviewed cohort in `skulk_capability_sdk/node_cohort.json` pins the official
Node **22.23.5** archive, extracted binary and full license by SHA256 and size
for macOS ARM64 and glibc Linux x86_64/ARM64. The platform floor is macOS 11 or
glibc 2.34. It carries no npm executable, compiler or source-build fallback.
The upstream release is described in the
[Node release notes](https://nodejs.org/en/blog/release/v22.23.5).

`skulk_capability_tools.node_runtime.build_node_runtime(archive, platform, output)`
builds that deterministic asset wheel from the exact pinned upstream archive.
It verifies all bytes and retains upstream notices without executing the archive.
The unchanged three selected companion wheels are published in
[v0.5.13-rc.1](https://github.com/Foxlight-Foundation/skulk-capability-sdk/releases/tag/v0.5.13-rc.1).
The [public package quickstart](native-public-quickstart.md) selects their exact
platform hashes. The separate [native runtime recipes](native-runtime-acquisition.md)
acquire the owner/Python closure; they do not include this Node wheel or your
application asset wheel.

Add the exact Node and application wheels to the complete reviewed Python
runtime wheelhouse, including the compatible separately licensed owner, public
SDK, native kernel and ordinary dependencies. The application wheel pins the
Node wheel through its `Requires-Dist` metadata. Then use the existing signer:

```sh
skulk-cap sign --inference-runtime --node-project ./example \
  --manifest ./build/manifest.json --wheelhouse ./wheelhouse \
  --private-key ./publisher.key --publisher example.publisher \
  --sequence 1 --expires-at "$EXPIRY_UNIX_SECONDS" --state-schema example-v1 \
  --permission "Declared example inference" --skulk-source ./Skulk/src \
  --output ./release
```

The signer requires the exact rebuilt application wheel and matching public
Python bootstrap bytes. Existing runtime dependency-closure, platform, native
cohort and signature checks still apply. The result uses the ordinary native
release and runtime signature domains; it is not an installation bypass.

For Python native projects, `skulk-cap build --native --project ... --output ...`
selects the actual compiled managed/2 declaration rather than the legacy reader.
Legacy build defaults remain unchanged.

## Qualification

The dedicated `tests_packaged` suite launches the actual Python zipapp and
independently installed npm assets on selected Node. It exercises same-PID exec,
caller preservation, all four I/O modes, binary media, protocol input completion,
cancellation, health, restart, shutdown/reaping and the selected Python credential
reader. Tests declare a 30-second startup budget for asset measurement; ordinary
short health and call deadlines remain enforced. Per-platform CI retains the
actual Node/application wheels, zipapp, test results and exact source receipts.

These results qualify source packaging and installed language-runtime behavior.
They do not qualify signed owner/Core admission, a provider account, a capability
migration, native OS credential sessions or the complete SDK1 author journey.
