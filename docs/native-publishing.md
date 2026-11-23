# Native candidate publishing

The experimental **SDK0.5.13rc1 / author tools0.2.0rc3** supplies an explicit
compiler-free native build path. The Python application interface is unchanged.
The public tools contain authoring, artifact and signature contracts; the owner,
supervisor, runtime installer and provider code are independently supplied.
The packages are publicly available in
[v0.5.13-rc.1](https://github.com/Foxlight-Foundation/skulk-capability-sdk/releases/tag/v0.5.13-rc.1).
They remain candidates; SDK1's complete installed and consumer gates are separate.

A native build selects `skulk.managed/2`, release protocol3, runtime protocol5
and `skulk.calls/2`. It uses the actual public compiled author reader, full native
identities and the six active profile fingerprints. All thirteen installed ABI
fingerprints are compared with the immutable public candidate receipt. The
selected runtime kernel wheel must match that receipt's exact filename, SHA-256,
size and platform. No legacy revision is truncated or padded.

## Inputs and commands

Use Python3.13 and install the public SDK, author tools and one exact public
native wheel for your host. The workspace's `native-runtime` group selects
[native-v0.7.0-rc.5](https://github.com/Foxlight-Foundation/skulk-capability-sdk/releases/tag/native-v0.7.0-rc.5).
The separately issued rc3 and rc4 cohorts remain readable and independently
selectable. TypeScript durable jobs use the distinct rc6 custody cohort; this
does not change the Python author reader selected by the workspace.
No compiler, Foxlight account or publisher registration is needed to acquire
these public source/packages or to build a disposable candidate.

The capability supplies its own spec and pure manifest factory. A native spec
uses the same package, dependency and data-file fields as a legacy spec. An
optional `validator` receives the public `NativeManifest` object from
`skulk_capability_tools.native_publishing`; its `.record` is the actual immutable
compiled record and `.canonical_json` holds that reader's canonical bytes.
The validator does not receive a legacy Python `Manifest`.

Build each supported platform from an exact runtime lock. That lock must include
the separately obtained owner package and all capability dependencies. Acquire
only an explicitly qualified candidate owner; ordinary runtime recipes still
name the existing legacy cohort and do not select native execution.

```sh
capability-wheelhouse --lock runtime.uv.lock --spec capability-native.toml \
  --native-candidate --output wheels

capability-build --spec capability-native.toml --manifest manifest.json \
  --inference-runtime --wheelhouse wheels --skulk-source skulk-source/src \
  --private-key publisher.key --publisher example --sequence 1 \
  --expires-at EXPIRY_UNIX_SECONDS --state-schema example-native-v1 \
  --permission 'Run this explicitly configured capability' --output release
```

`publisher.key` is a protected local32-byte Ed25519 seed. The publisher keeps it
outside the capability package and wheelhouse. Use disposable keys and explicit
test trust for qualification. An operator must independently approve a publisher
before any of its code can be installed.

`--native-candidate` permits locked prereleases and the published native Linux
floor (glibc2.34). It does not select a wire protocol or authorize installation.
Without it, the ordinary recipe retains its existing stable-version policy and
glibc2.28 floor. Hash, size, dependency, platform and source-build checks apply to
both paths; the flag never enables an unpinned dependency or a third-party
source build for a missing native binary.

Universal locks can contain the same name and version under different platform
URLs. The recipe follows their explicit resolution markers and source references;
it refuses an ambiguous source instead of choosing by lock order or wheel tags.

`--inference-runtime` separately selects the native author/release/runtime
contracts. A native manifest requires a complete wheelhouse. The builder checks
runtime launchers, native binary identity, dependency closure, source ownership,
archive self-containment, duplicate sources and host metadata limits before
writing final artifacts. The deterministic bundle's actual hash is bound into
a newly admitted native manifest. The runtime signature uses the exact
`skulk.runtime/5` domain with its NUL separator. Legacy signature bytes and
accepted release/runtime protocol windows are unchanged.

The public publisher can produce a signed candidate without Skulk installed by
measuring a separately acquired public Core source directory. This is provenance,
not host authority. Core still verifies current publisher trust, compatibility,
exact artifacts and runtime admission before startup.

## Separate delivery and authority gates

Use the explicit native [author CLI journey](native-authoring.md) for
create/run/watch/invoke/debug. Existing legacy flows retain their own contracts.
`capability-feed --native` assembles and verifies a native catalog from exact
release inputs and an independent rebuild reference; use `--help` for its
required identities and expiry bounds. Native catalog assembly and the existing
reviewed Store submission process are separate publication surfaces. Local
signing does not register a Foxlight
publisher, distribute a package, enable inference, approve a provider account or
grant caller permissions. Use the normal independently qualified Core/owner
installation path; the publisher contains no installer or host bypass.

Keep these results distinct: public source/package availability, deterministic
artifact integrity, publisher/catalog registration, signed installed startup,
application state migration, and real provider qualification. A successful
native package build alone proves none of the latter gates.

When editing the SDK workspace locally, force both workspace distributions to be
rebuilt during the frozen non-editable sync before testing. An existing wheel
with the same version can otherwise hide source edits:

```sh
uv sync --frozen --all-packages --no-editable --group native-runtime \
  --reinstall-package skulk-capability-sdk \
  --reinstall-package skulk-capability-tools
```
