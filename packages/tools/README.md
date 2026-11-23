# Skulk capability author tools

Generic author and publisher tooling for Skulk capabilities, licensed under MIT.
The package provides `skulk-cap` and the existing `capability-build`,
`capability-wheelhouse`, `capability-catalog` and `capability-feed` commands.
It depends on the public author SDK and packages from public package indexes.

Publisher signing keys stay on the publishing host. The independently installed
Skulk owner enforces trust, admission and lifecycle operations. Installing these
tools does not grant runtime authority or access to a provider account.

`EXTRACTION.json` records the reviewed generic source. The private owner,
supervisor, installer and provider implementations are excluded.

Use the [Python author guide](https://github.com/Foxlight-Foundation/skulk-capability-sdk/blob/main/docs/python-authoring.md) for legacy package installation and a source-to-host walkthrough. The explicit [native author guide](../../docs/native-authoring.md) covers Python/TypeScript creation, all four I/O modes, media, cancellation and watch. The private owner remains independently installed.

The pinned runtime recipes acquire existing public wheel artifacts by exact SHA-256 and size. They include no owner implementation and confer no trust. Independently operated HTTPS feeds need no Foxlight account; hosted catalog submission is a separate service.

The experimental native candidate builder and explicit wheelhouse opt-in are
documented in the [native publishing guide](../../docs/native-publishing.md).
They select a separately signed native cohort using the actual public compiled
reader. The legacy check/dev/inspect and catalog interfaces keep their existing
protocols. Candidate package creation, distribution and installed authorization
remain independently qualified.

## Native distribution candidates

Native source packaging also supports `skulk-cap build --native` for Python
and `--native --language typescript` for staged Node applications. The latter
uses `skulk-cap sign --inference-runtime --node-project ...` with the existing
runtime-five wheel closure. See the [Node packaging candidate](../../docs/node-packaging.md)
for exact runtime selection, source-only qualification and remaining admission.

Tools `0.2.0rc3` add explicit native publication paths. Install the qualified
public native kernel cohort before reading managed/2 declarations; no compiler
or private Git checkout is needed. These tools do not grant installation,
execution, account access or provider usage.

- `capability-catalog --native` authenticates native runtime-five records and
  signs a separate protocol-four catalog. Full manifest revision, exact native
  source/version/ABI profiles and processing domains remain explicit. Without
  this flag, a native record is refused. Legacy catalogs keep their schemas
  and signing bytes.
- `capability-feed --native` accepts runtime-five candidates only. It checks
  bounded artifact bytes, the exact independent publisher policy and platform
  set, the caller's frozen lock, and independently rebuilt bundle, native
  manifest wrapper and source-built wheels before signing. The independent
  reference can be produced with `native_feed.native_release_reference` in a
  separate rebuild job. The output is `catalog-v4.json` plus immutable
  `<bundle>/<sequence>/<platform>/` release directories. The signer does not
  install or execute capability or owner wheels. Each publication run needs
  a new sequence; never rerun a signing attempt under an issued sequence.
- `skulk-cap inspect` and `skulk-cap publish` authenticate native signature
  domains and hash the allowlisted artifacts. Local web-root publication
  does not establish HTTPS availability or host authorization.
- `skulk-cap runtime --native --recipe reviewed-native.json --platform
  linux-glibc-x86_64 --output wheelhouse` acquires an explicitly supplied format-two
  recipe. It binds owner and SDK versions, immutable owner source, qualified
  runtime provenance, exact native cohort and full bounded wheel inventory.
  Every download is anonymous HTTPS and size/hash checked. The recipe is an
  acquisition input, independently of release trust. It is not a claim that
  a native owner binary has already been published at a stable public URL.

Serve native catalogs separately from the legacy `catalog.json` and
`catalog-v3.json` projections. Deploy a compatible reader before publishing
native entries. Built-in readers may probe older versions only after a genuine
initial 404; signatures, accepted version and revision floors never downgrade.
Qualification must separately record the actual platform, normally installed
application, Core/owner identities and published input custody. Metadata tests
alone do not qualify an application installation or real-account use.

Native rebuild references include source-built wheels and directly acquired
URL wheels when the lock records a hash without a size. Each direct URL still
needs its exact locked SHA-256; the size comes from an independent acquisition,
never the candidate. Other-platform native binary sizes come from the immutable
qualified cohort receipt. Ordinary registry locks and legacy signing defaults retain
their original requirement for a recorded exact size.

Tools `0.2.0rc4` add the issued native7 acquisition receipt. New SDK15 author
environments select that cohort for Python and TypeScript. Publisher admission
checks all fourteen native fingerprints, including optional discovery1, while
keeping the issued six-field manifest projection unchanged. Earlier native3,
native4 and native5 receipts retain their exact bytes and artifact identities.
The matching owner/Core bridge independently controls original-caller discovery
and execution; package acquisition supplies neither permission.

## Legacy applications on the SDK1 runtime

Tools `0.2.0rc5` add `capability-feed --native-candidate`, matching the wheelhouse
option. It admits the frozen SDK1 prereleases and glibc2.34 dependency floor for
legacy runtime-two releases. The application retains its protocol-four manifest,
governed policy, descriptor identities and signature domain. Installation and
effect approval still belong to the host.

Use this opt-in in both wheel acquisition and feed assembly. The publisher still
requires the exact platform set, frozen wheel hashes and independent rebuild.
`--native` continues to select runtime-five and catalog-four declarations;
dependency policy does not perform a contract conversion. Existing legacy
publication defaults and the issued native cohorts remain unchanged.
