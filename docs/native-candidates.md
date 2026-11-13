# Native prerelease candidates

The native kernel candidate is separate from the released Python author SDK.
It supplies compiled Python and Node primitives for native contract adoption.
It does not complete SDK 1.0, switch an installed capability, authorize a caller,
or establish normal signed owner/Core installation acceptance.

The candidate cohort targets macOS ARM64 and glibc Linux x86-64/ARM64. Python
wheels use the CPython 3.13 stable ABI; Node archives target Node 22. Build-time
qualification and compiler-free package installation run independently on each
of those artifact families. Other platforms need their own appropriate binary.

## Acquisition and trust

A published candidate release contains `native-candidates.json`, `SHA256SUMS`,
three wheels, three platform-specific Node archives and three evidence bundles.
The manifest binds exact filenames, sizes, SHA-256 hashes, source commit/tree,
profile fingerprints and the complete corpus input identities. Select the entry
for the actual host and verify its bytes before installing. Never select a wheel
by a release label alone or substitute a source build when the binary is missing.

SDK acquisition needs no Foxlight publishing account. GitHub release downloads
are public. The manifest describes the publisher's candidate artifacts; it is
not a grant or a substitute for a signed runtime inventory, current publisher
trust, original caller restrictions or host selection validation. The normal
capability installer must bind these exact bytes into its own qualified signed
runtime before using them for an installed capability.

Python installation from a verified local wheel uses `uv pip install --no-index
--no-deps /path/to/verified.whl` with the intended interpreter selected. Node
installation from a verified local archive uses `npm install --offline
--ignore-scripts /path/to/verified.tgz`. The Node package is currently named
`@foxlight/skulk-capability-kernel-qualification`; the prerelease is distributed
through GitHub rather than an npm registry publication. Its `private` metadata
prevents accidental registry publishing and does not prevent archive installation.
The complete packages include shared dependency notices and require no Rust or C
compiler at installation time.

## Maintainer pipeline

Run the frozen public-source and distribution checks, then native qualification:

```sh
uv sync --frozen --all-packages --no-editable
uv run --no-sync python -m tools.qualify_kernel
```

The qualifier verifies locked notices, builds and audits both native packages,
compares all portable and managed vectors, installs both archives offline with
compilers absent from `PATH`, and repeats the complete vectors against the
installed packages. Each job emits `native-candidate-<platform>.zip` containing
those exact binaries and evidence. A dirty local build may qualify functionality;
its bundle is explicitly ineligible for publication.

After exact-head review and all platform checks pass, download the three bundles
from that exact workflow source revision and verify their GitHub artifact
identities. Assemble a clean, complete, matching cohort:

```sh
uv run --no-sync python -m tools.native_candidates assemble \
  --source <qualified-commit> --destination <new-staging-directory> \
  <macos-bundle.zip> <linux-x86-64-bundle.zip> <linux-arm64-bundle.zip>
```

Assembly rejects dirty/foreign source, mixed trees or versions, wrong machine
wheels, substituted package bytes and incomplete/disagreeing observations. It
compares observations with the independent source corpus, not just with each
other. Existing staging output is never overwritten.

Publish through a draft **release** at a new `native-v<semver>` tag pointing at
the qualified source commit. Upload and verify every staged asset, then expose
it as a prerelease. Never overwrite an issued asset/tag or promote a candidate
to a stable SDK claim. Read back the public tag and manifest/assets anonymously,
verify every digest and exercise the downloaded packages on the actual host.
Normal signed installation and protected-state migration remain separate gates.

The native 0.6 source candidate adds the explicit
[composed-call profile](native-calls.md). Its candidate inventory requires six
complete independent corpora in Rust, Python and Node, including exact raw-body
binding and separate call metadata framing. The offline compiler-free package
check repeats all six profiles in both installed language bindings. This source
candidate does not change the published 0.4 cohort or select a runtime for an
existing application.

Native 0.7.0rc1 additionally defines [explicit inference contracts](native-inference.md).
Its inventory requires eight complete profile corpora across all three backends
and repeats them from offline installed artifacts. The portable/2 and
composition/2 reports partition 56 independently authored declaration and
authority witnesses. Issued 0.6 profiles and assets remain unchanged. A new
installation and call cohort is still required before hosts can execute these
contracts; package acquisition does not grant inference or external processing.
