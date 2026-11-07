# Build a Python capability

The public SDK and author tools use MIT and require no Foxlight account.
Installing them grants no Skulk permissions. Catalog publishing registration,
provider accounts and host authorization are separate concerns.

Use Python 3.13 or later on macOS arm64 or glibc Linux x86_64/aarch64. The SDK
and tools install as portable Python wheels; their dependencies have binary
wheels at the supported platform floors. Native Windows is unsupported.

## Install immutable packages

With [uv](https://docs.astral.sh/uv/), install the two release wheels together:

```sh
uv tool install --python 3.13 \
  --with https://github.com/Foxlight-Foundation/skulk-capability-sdk/releases/download/v0.5.7/skulk_capability_sdk-0.5.7-py3-none-any.whl \
  https://github.com/Foxlight-Foundation/skulk-capability-sdk/releases/download/v0.5.7/skulk_capability_tools-0.1.0-py3-none-any.whl
skulk-cap --version
```

Each release also includes source archives and SHA256SUMS. Check those sums when
mirroring or provisioning offline. The tools do not log in, obtain Fabric keys,
approve provider spending or start a fleet node during installation.

## Create and run the model-free starter

```sh
skulk-cap init hello --name hello_capability
cd hello
skulk-cap run
```

In a second terminal in the same project:

```sh
skulk-cap debug
skulk-cap invoke --input '{"text":"Hello from my capability"}'
skulk-cap test --input '{"text":"A second independent call"}'
```

`debug` challenges the child for readiness. `invoke` sends one bounded unary
request to the exact descriptor revision in `manifest.json`; `test` performs the
same explicit smoke request. Neither command invents a Fabric identity. A lost
reply is never retried. The session stops if the wire or a result becomes
uncertain, preventing a late reply from being used for another call.

Stop the first terminal with Ctrl-C. Use `skulk-cap watch` to check source changes
before each new request and restart between requests. It does not replay an
interrupted invocation. One child and one request are admitted at a time. Socket
and startup credentials live in owner-only local storage; startup credentials
travel through an inherited descriptor rather than the command line.

The starter deliberately needs no model, GPU, provider account or fleet lease.
This local harness currently exercises unary IPC. Real installation, distributed
authorization, durable work and media streams have separate qualification gates.
The low-level public SDK also exposes operations, streams, settings/credential
declarations and surface contracts; the harness is not a claim of their complete
application-framework or TypeScript parity.

## Build source without a signing key

```sh
skulk-cap build --output dist/unsigned
```

The deterministic zipapp contains only the declared project source. The build
checks imports against declared dependencies, verifies package origins and
imports the archive without the source checkout. Core and private owner imports
are refused. Add runtime requirements to `capability.toml` and matching canonical
root names to the manifest rather than relying on packages in your environment.

The output is **unsigned**. It is not an installable release and grants no host
trust. `dist/unsigned/manifest.json` records the exact bundle hash.

## Obtain the runtime and sign a release

The private owner is separately distributed by the capability store. The tools
carry reviewed, hash-pinned recipes for owner 0.10.5/SDK 0.5.5 on each supported
platform. These recipes point to existing public immutable wheel artifacts;
they contain no owner implementation. The author-tool interpreter uses SDK
0.5.7, while this runtime deliberately retains its independently qualified SDK.

```sh
skulk-cap runtime --platform macos-arm64 --output dist/runtime
skulk-cap keygen --private-key .keys/publisher.key
```

Choose `linux-glibc-x86_64` or `linux-glibc-aarch64` when applicable. Every wheel
is checked against its exact size and SHA-256 before output is written. No
private repository access, compiler or source build is needed for this runtime.
Its own license remains separate from the MIT author tools.

Key generation refuses replacement and prints only the public Ed25519 key.
Keep the private key outside release directories. To sign, supply your publisher
ID, a monotonically increasing sequence, a future Unix expiry and explicit
human-readable permissions:

```sh
skulk-cap sign \
  --spec capability.toml --manifest dist/unsigned/manifest.json \
  --wheelhouse dist/runtime --skulk-source ../Skulk/src \
  --private-key .keys/publisher.key --publisher example-author \
  --sequence 1 --expires-at FUTURE_UNIX_TIME \
  --state-schema hello-v1 --permission 'Return the caller supplied text' \
  --output dist/release-1
```

`--skulk-source` measures the public Core checkout used for qualification. For
the current development baseline, obtain Skulk from its public `dev` branch and
pin `864f72c76a95e7352f8beddae587ee16ab89e93d`. The source digest is provenance;
the manifest's `skulk_requires` range and host protocol remain independent
compatibility checks. Signing checks the complete dependency inventory, launchers,
metadata envelopes and wheel/bundle agreement. Published directories must be new
and empty; never overwrite release bytes.

Create an explicit **public** trust file for inspection and host provisioning:

```json
{
  "revision": 1,
  "expires_at": 1900000000,
  "publishers": {"example-author": "YOUR_64_HEX_PUBLIC_KEY"},
  "revoked_publishers": [],
  "revoked_artifacts": []
}
```

```sh
skulk-cap inspect --release dist/release-1/release.json \
  --trust publisher-trust.json --artifacts dist/release-1 \
  --declared-host-version 2.0.3
```

Signature, artifact bytes and declared version compatibility are reported
separately. This does not prove that a live host admits or can execute the app.

## Install through a real Skulk host

Use a compatible public development host at the qualified immutable revision. Skulk's public `install.sh --ref IMMUTABLE_DEV_SHA --dir DIRECTORY`
accepts a pinned development ref. Installing Core has its own compiler, dashboard
and platform prerequisites; compiler-free SDK installation does not remove them.

A model-free host can run with `--no-worker --no-downloads`. To keep its network
separate, select a unique `SKULK_LIBP2P_NAMESPACE` before starting it and use
`--libp2p-port 0`. A fresh local host is enough; joining a larger fabric is optional.
Use a separate OS account or ephemeral machine when qualifying alongside an
existing installation: macOS's plugin service is per-user, and a separate network
namespace does not give it a separate service root. Set up the plugin service
through Skulk's Plugins screen or its normal `skulk-plugin-service setup` command.

Use `skulk-cap publish --release dist/release-1 --trust publisher-trust.json --web-root HTTPS_DOCUMENT_DIRECTORY` to place only the verified release, bundle and wheels into a new server directory. Key files and unrelated source are excluded, and an existing destination is refused. This verifies local publication bytes; it does not configure DNS/TLS or claim remote reachability.

Serve that directory and every exact wheel over your own HTTPS
origin. The host verifies TLS, forbids redirects and checks signatures and hashes.
Do not serve `.keys/`, project settings, credential files or unrelated source.
An independently signed feed needs no Foxlight publishing account.

Choose a new managed installation ID and a private journal directory. Register it:

```sh
skulk-cap register --host http://127.0.0.1:52415 \
  --installation managed.hello-example --journal .skulk-cap/register.json
```

Create `source.json` with `expected_revision: 0`, your HTTPS `base_url` ending
in `/`, `metadata_filename: "release.json"` and the public `trust` object above.
The source command accepts public feed configuration only; secrets cannot enter
its journal. For private feed credentials, use the host's existing write-only
configuration interface.

```sh
skulk-cap source --host http://127.0.0.1:52415 \
  --installation managed.hello-example --definition source.json \
  --journal .skulk-cap/source-1.json
skulk-cap review --host http://127.0.0.1:52415 --installation managed.hello-example
```

Review the returned runtime digest, source revision, permissions and compatibility.
Use them in an explicit install request with one newly generated 32-character
lowercase hexadecimal operation ID:

```sh
skulk-cap install --host http://127.0.0.1:52415 \
  --installation managed.hello-example --runtime-digest REVIEWED_DIGEST \
  --source-revision REVIEWED_REVISION --operation-id ORIGINAL_OPERATION_ID \
  --journal .skulk-cap/install-1.json
skulk-cap status --host http://127.0.0.1:52415 --journal .skulk-cap/install-1.json
```

Wait until the original operation reports `staged`. Activate with a different
operation ID, the staged digest, the current selection revision (zero before a
first selection), and explicit permission acceptance:

```sh
skulk-cap activate --host http://127.0.0.1:52415 \
  --installation managed.hello-example --runtime-digest REVIEWED_DIGEST \
  --selection-revision CURRENT_SELECTION_REVISION --accept-permissions \
  --operation-id ACTIVATION_OPERATION_ID --journal .skulk-cap/activate-1.json
```

Activation completion is distinct from capability readiness. Inspect the host's
Plugins/topology and call the declared contract through the unified capability API.
For managed children, discover contracts on the serving host with `GET /v1/capabilities`. Pin its returned descriptor revision and use that response's transport `node_id` as the call target. The child's application node ID shown in topology is a different identity; it is not a peer address for the capability HTTP API. Readiness is reported separately in `GET /v1/plugins/managed/capabilities`.

Remote administration requires an existing scoped bearer supplied through an
owner-only `--token-file`; HTTP is accepted only for literal loopback addresses.
The CLI does not generate grants or send a bearer to another origin.

## Update, rollback and uninstall

Publish a new immutable sequence, update its revision-fenced source configuration,
review, then `update` using the new exact digest/source revision. `update` stages
bytes; activation remains explicit. `rollback` selects a previously staged digest
using the current selection revision and an explicit rollback operation. It does
not lower the accepted sequence floor or replay provider work.

`uninstall` takes the installation ID, current selection revision, operation ID
and journal. It uses normal host cleanup and retains the host's recovery records.
Purging those records is a separate explicit owner action in Skulk.

Every mutation writes private intent before transmission. Reusing a journal reads
the original operation instead of sending again. Different intent under the same
journal is refused. After a timeout or lost acknowledgement, use `status`; do not
delete the journal or generate another operation to discover what happened.

The legacy `capability-build`, `capability-wheelhouse`, `capability-catalog` and
`capability-feed` entry points remain available for existing publisher projects.
`skulk-cap catalog` and `skulk-cap feed` expose their same explicit signing and
assembly options. Hosted catalog submission is a separate publishing-service
milestone; these tools do not claim registration or server acceptance.
