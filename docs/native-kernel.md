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
