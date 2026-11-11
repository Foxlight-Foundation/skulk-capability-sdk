# Native portable contract candidate

The shared [MIT kernel](../kernel/README.md) exposes the same native contract
authority to Rust, Python and TypeScript. The packaged
[specification](../kernel/contracts/portable-v1.json) defines the current
`skulk.portable/1` profile. Each descriptor and protocol-five envelope includes
the exact specification fingerprint. Unknown profiles, fingerprints and
protocols refuse before execution.

The kernel canonicalizes bounded scalar JSON, inserts contract defaults and
computes full, domain-separated SHA-256 identities. It refuses duplicate decoded
keys, malformed Unicode, non-finite numbers and values outside the safe binary64
domain. Integral floats and signed zero normalize to integers. Keys sort by
Unicode scalar value, with compact ASCII JSON output. This is a named Skulk
profile rather than RFC 8785.

Schemas use a closed 2020-12 vocabulary, permanently offline reference
resolution and a bounded common regex syntax. Only root-local named definitions
are supported; all references resolve eagerly and cycles refuse. Formats are
annotations. Unknown keywords, external resources, dynamic references, regex
shorthands, flags, backreferences and lookaround refuse. The pinned regex backend
uses actual end-of-input for `$`, without a final-newline exception.

The specification lists exact resource limits: 65,536 bytes per schema/payload,
262,144 per author contract, JSON depth 64, 16,384 JSON nodes, 1,024 schema nodes
and 8,192 expanded schema nodes. These are encoded-record limits, separate from
model output-token limits or provider allowances. Stream/media transport has its
own contract.

Python exports `portable_canonical_json`, `portable_contract_json`,
`portable_contract_digest`, `portable_profile_digest` and the reusable immutable
`PortableSchema`. The TypeScript facade exposes `PortableContract`,
`PortableSchema`, `canonicalPortableJson` and `portableProfileDigest` over the
Node addon. Malformed language strings refuse before UTF-8 substitution. Native
validation has no Python/JavaScript validator or helper-process fallback.

The independent portable corpus contains 1,959 cases. Qualification compares
complete observations and input hashes through Rust and separately installed
Python/Node artifacts, plus archive contents and a strict TypeScript consumer.
Source provenance is recorded in
[`KERNEL-SOURCE-MANIFEST.json`](../KERNEL-SOURCE-MANIFEST.json).

Descriptor behavior labels grant no caller authority. The candidate envelope
covers descriptors and optional configuration schema; installed host/startup
negotiation, author/client interfaces and protected-state migration require
their own acceptance. The existing Python SDK does not switch profiles when
these sources or qualification artifacts are built.

## Managed installation and inherited startup candidate

The separate [`skulk.managed/1` specification](../kernel/contracts/managed-v1.json)
adds native `Manifest` and `Startup` admission. It embeds a portable/1 contract
envelope instead of redefining descriptor/schema semantics. The original
portable fingerprint, canonical bytes and 1,959-case corpus remain unchanged.
Every managed record declares protocol five and the exact managed specification
fingerprint. Older or mismatched readers must refuse it before child execution.

Manifests bind bundle/version, relative artifact entry, exact executable digest
and arguments, compatibility
requirements, lifecycle/operation bounds, operator surfaces, credential *names*,
runtime dependency roots and Steward exposure classifications. Native admission
rejects duplicate identities, unsupported nested schemas, mismatched behavior,
undeclared operation bounds and unsafe paths. Classification labels describe the
contract; they do not grant permission to execute it. The host must additionally
validate its own supported profile/version, publisher signatures, current trust,
selected runtime and original caller authority before effects.

Startup binds the **full** normalized manifest digest and the resolved absolute
entry path, private sockets, runtime
identities and schema-validated settings. Streaming requires its declared media
socket. Raw input and escaped canonical output each have a 262,144-byte record
bound; settings retain the portable 65,536-byte bound. These are metadata bounds,
not request-count or model output-token ceilings.

**Startup contains secrets.** Keep it on protected inherited IPC; do not publish,
log or include it in status/diagnostics. Native refusal messages never echo its
values. The manifest may be signed/public; startup is not public release metadata.
Its digest binds bytes, and is not a credential or execution authorization.

Python exposes `managed_profile_digest`, `managed_contract_json` and
`managed_contract_digest`. TypeScript exposes `ManagedContract` and
`managedProfileDigest` from the native portable facade. Both delegate to the same
Rust implementation. Independent synthetic managed vectors are exercised through
Rust and the separately installed Python/Node artifacts on each CI platform.
There is no language-level fallback validator.

The immutable original export receipt is now
[`KERNEL-INITIAL-EXPORT.json`](../KERNEL-INITIAL-EXPORT.json).
[`KERNEL-SOURCE-MANIFEST.json`](../KERNEL-SOURCE-MANIFEST.json) separately pins the
explicitly maintained public files. Its audit preserves the original receipt and
refuses undeclared source or changed current hashes; source review establishes
the public changes. This allows public development without relabelling new code
as byte-identical to its initial private export.

These exports establish native **record admission**. Normal signed owner/Core
installation, startup negotiation, application migration and SDK 1.0 installed
acceptance remain separate gates. Building these artifacts does not change any
installed capability or select this profile for the existing Python SDK.

The [native candidate pipeline](native-candidates.md) stages versioned, audited
prebuilt packages and verifies offline installation without a compiler. These
prereleases remain separate from normal signed owner/Core adoption.
