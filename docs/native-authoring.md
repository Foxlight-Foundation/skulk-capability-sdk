# Develop a native capability locally

The explicit native author CLI creates model-free Python and TypeScript projects
and runs the same compiled managed/2, calls/2 and transport/2 interfaces used by
the application runtime. It supports unary, server-streaming, client-streaming
and bidirectional handlers. No provider account, model, GPU or fleet is needed.

This is an SDK1 candidate source journey. SDK **0.5.13rc1** and tools
**0.2.0rc3** are not stable SDK1 releases. The normal signed owner/Core
installation, update and rollback journey has a separate acceptance gate.

## Prepare an author environment

Use a reviewed checkout of this public repository on macOS ARM64 or glibc 2.34+
Linux x86_64/ARM64 with Python 3.13. From the repository root:

```sh
uv sync --frozen --all-packages --group native-runtime --no-editable \
  --reinstall-package skulk-capability-sdk --reinstall-package skulk-capability-tools
uv build --all-packages
uv run --no-sync python tools/check_export.py
uv run --no-sync python tools/check_tools_export.py
AUTHOR="$PWD/.venv/bin/skulk-cap"
```

The frozen native group selects an issued, hash-pinned Python kernel wheel.
It does not compile Rust. Set `AUTHOR` to the absolute path of
`.venv/bin/skulk-cap` in this checkout, so subsequent commands use that isolated
installed environment even when you change directories. Candidate source
acquisition requires no Foxlight account. Installing tools grants no Fabric
permissions or provider access.

For TypeScript, prepare the public npm SDK and explicitly selected Node runtime:

```sh
npm ci --ignore-scripts --prefix packages/typescript
npm run build --prefix packages/typescript
mkdir -p .runtime
npm pack ./packages/typescript --pack-destination .runtime
SDK_TARBALL="$PWD/.runtime/foxlight-skulk-capability-sdk-0.5.13-rc.1.tgz"
uv run --no-sync python tools/check_typescript_export.py \
  .runtime/foxlight-skulk-capability-sdk-0.5.13-rc.1.tgz
uv run --no-sync python tools/prepare_node_qualification.py \
  --output .runtime/selected-node
```

The last command downloads the exact pinned official Node archive, verifies its
hash and size, builds the asset wheel without executing downloaded code, and
installs it into this disposable author environment. It refuses an existing
output directory. It uses the same compiler-free Node selection as packaging;
Node is not selected from your shell's `PATH` when launching an application.
The companion release assets may still be unpublished candidates; this source
recipe does not depend on a draft release being anonymously downloadable.

Use Node 22.23.5 and npm for the explicit TypeScript build. A compiler is needed
only to compile TypeScript source. The packaged Node runtime contains neither
npm nor TypeScript. A subsequent frozen sync removes the extra Node wheel;
prepare it again with a new output directory if needed.

## Create a project

Python:

```sh
"$AUTHOR" init --native --language python --name hello_capability ./hello
```

TypeScript, using the absolute path of the audited npm tarball:

```sh
"$AUTHOR" init --native --language typescript --name hello_capability \
  --sdk-package "$SDK_TARBALL" ./hello
cd hello
npm install --ignore-scripts
npm run build
```

`init` refuses an existing destination and never runs an installer, compiler or
project hook. Python also accepts `--sdk-package` with an audited SDK wheel to
record its explicit acquisition source in the generated project. Without that
option, project metadata pins the installed SDK version; prerelease versions
may require source acquisition until published.

Each starter declares four versioned capabilities in `manifest.json`:

| Capability | I/O mode | Example behavior |
| --- | --- | --- |
| `hello.capability.echo@1.0.0` | Unary | Return the supplied text |
| `hello.capability.stream@1.0.0` | Server streaming | Emit text and one terminal |
| `hello.capability.collect@1.0.0` | Client streaming | Join input chunks |
| `hello.capability.duplex@1.0.0` | Bidirectional | Echo text/media and complete |

`capability.toml` selects Python source. `node-capability.toml` selects staged
JavaScript and production dependencies. A project must select exactly one.
TypeScript `npm run build` compiles and stages dependencies without npm lifecycle
scripts. Development compilers and local acquisition paths stay out of runtime
assets. The author session itself never runs npm or a compiler.
Python launchers receive declared manifest arguments. Node factories take their
settings through startup configuration and refuse arbitrary CLI arguments.

## Run and invoke

From the generated project:

```sh
"$AUTHOR" run --native
```

In another terminal in that same project, using the same `AUTHOR` path:

```sh
"$AUTHOR" debug --native
"$AUTHOR" invoke --native --capability hello.capability.echo@1.0.0 \
  --input '{"text":"Hello"}'
"$AUTHOR" invoke --native --capability hello.capability.collect@1.0.0 \
  --input-chunk '{"text":"one"}' --input-chunk '{"text":"two"}'
"$AUTHOR" invoke --native --capability hello.capability.stream@1.0.0 \
  --input '{"text":"Hello"}'
```

Results are progressive JSON lines. Streaming success requires the real native
terminal and end frames followed by the correlated control result. `test`
performs the same explicit request as `invoke`. It does not start another
session or invent a retry. Nonzero exit status reports a refusal or failed call.
`debug` challenges live readiness and reports the generation, language, PID and
manifest digest, without settings, credential paths or startup tokens.
Generation-owned health probes also keep the native control channel live during
idle time and long streams; media traffic alone does not renew that channel.

To exercise binary media with the duplex starter:

```sh
"$AUTHOR" invoke --native --capability hello.capability.duplex@1.0.0 \
  --input-chunk '{"text":"audio"}' --input-media ./input.pcm \
  --media-type audio/pcm --output-media ./output.pcm
```

`--input-media` requires exactly one input chunk. Media travels through the native
binary channel; base64 is confined to private CLI envelopes. `--output-media`
creates an owner-only file and refuses replacement. Without it, output frames
include `media_base64`. An interrupted output file can be partial; inspect the
exit status before using it. Input is limited to 64 chunks with 1 MiB media per
chunk, the local IPC envelope to 4 MiB, and output files to 64 MiB. The declared
native manifest and schemas impose their own bounds as well.

`--seconds` requests a shorter deadline; it cannot extend a finite manifest
ceiling. Read handlers use the declared call or stream watchdog. Inference
handlers use `bounds.inference_seconds` separately; `null` adds no overall
inference ceiling, and the local client adds none of its own. Connection,
health and partial-frame watchdogs still apply. A disconnect or timeout cancels
work and drains cleanup before reuse, including output backpressure. The CLI
never replays a call after a lost connection. Uncertain native transport or
cleanup failure stops the session.
Unexpected application loss or failed health makes `run`/`watch` exit nonzero;
Ctrl-C remains an intentional, clean stop.

## Watch and settings

Stop `run` with Ctrl-C, then use:

```sh
"$AUTHOR" watch --native
```

Before each new invocation, watch hashes declared source/assets and executable
status. It detects byte changes even when file size and modification time stay
the same. Changes restart between calls; an active request is not replayed.
For TypeScript, compile edits explicitly, for example `npm run compile -- --watch`
in another terminal. Watch observes compiled output rather than invoking tsc.

Each generation has a disposable, verified code snapshot. The stable session
working directory preserves application data while obsolete snapshots, sockets
and owned process groups are removed. Use a private `--state` directory to keep
a particular session location; pass it to both server and client commands.
Native and legacy default state locations are distinct. This is a development
working directory, not installed-state migration or a sandbox for untrusted code.

`run` and `watch` accept `--configuration settings.json` for declared ordinary
JSON settings and `--credential-selection selection.json` for an owner-only
selection supported by the SDK credential reader. The selection is not a
credential value. It is supplied only at startup; invocation clients cannot
choose it. Ordinary settings changes trigger a watch restart. Applications use
the public credential reader for declared aliases and current secret values.
Do not place secrets in ordinary settings or project assets.

## Build and move to an installed host

```sh
"$AUTHOR" build --native --project ./hello --output ./unsigned-python
"$AUTHOR" build --native --language typescript --project ./hello \
  --output ./unsigned-node
```

Use only the command matching the project language. Outputs are unsigned.
See [native publishing](native-publishing.md) and
[Node packaging](node-packaging.md) for exact runtime acquisition and signing.

The local session supplies synthetic `local-author` identity for its one exact
selected descriptor. Clients cannot submit a Fabric context or authority. It
refuses delegated production calls: installed application-to-application work
requires a normally authorized owner/Core host. This source journey proves
native author behavior, not signed installation, a provider connection or SDK1
release readiness.
