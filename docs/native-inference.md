# Explicit inference authority contracts

Native 0.7.0rc1 defines two distinct candidate profiles:
[`skulk.portable/2`](../kernel/contracts/portable-v2.json) for signed author
declarations and [`skulk.composition/2`](../kernel/contracts/composition-v2.json)
for host-owned caller context and delegation attenuation. They preserve the
issued portable, composition and call profiles. Existing applications and owners
continue using their selected cohort; installing this package does not select
the new profiles or grant inference permission.

The new descriptor behavior `inference` covers generated outputs, including
text, embeddings and speech. It is separate from `read`, `proposal` and `execute`.
Every descriptor explicitly declares its own `processing_domain`: `local`,
`private_site` or `external`. No missing-field default grants a processing
boundary. A declaration describes the entry point; it is not caller consent.
Delegated work requires independently admitted destinations and boundaries.

The portable envelope uses protocol 6. Its closed schema language, JSON domain,
canonicalization and resource limits match portable/1; the new effect does not
make recursive schemas, Unicode regex shorthands or unsafe integers portable.
Descriptor identities include the required domain and profile fingerprint.

## Attenuation

Composition protocol 2 permits read and inference delegation. The exact signed
entry point is an effect ceiling:

| Current executor | Eligible child effects |
| --- | --- |
| Read | Read |
| Inference | Read, inference |
| Proposal or execute | None through this contract |

A read child loses inference targets even when its original caller has broader
permission. The inference parent retains its separately authorized sibling
targets. Governed effects remain denied even if policy lists them.

Each child is the intersection of its parent and current caller policy, further
bounded by its own effect ceiling. Original caller ID, kind, issuer and root
remain fixed. Scope and exact body binding, fresh child identity, shrinking
deadlines, current policy revision and cycle prevention remain mandatory.
Changing a descriptor version cannot disguise reentry into an ancestor.

Processing permission is independent: authorizing inference does not authorize
external disclosure. The selected destination domain must occur in both parent
and current policy. Hosts bind destination behavior and domain to the exact
signed descriptor. Task labels, model names and supplied target records cannot
establish those facts.

These rules constrain calls admitted by the host. They do not sandbox arbitrary
application code or enforce network egress. Untrusted code needs independent
host isolation; a signed `local` declaration alone cannot provide that isolation.

The permission snapshot is separate from current readiness. Unrelated capacity
changes do not rewrite grants. Immediately before dispatch, the host must
independently verify the selected installation, runtime, incarnation, current
policy and actual provider admission.

## Native interfaces and qualification boundary

Rust exposes `portable_v2` and `composition_v2`. Python exposes
`portable_v2_contract_json`, `portable_v2_contract_digest`, both profile digest
functions and `CompositionV2Contract`. Node exposes `PortableV2Contract`,
`CompositionV2Contract`, `portableV2ProfileDigest` and
`compositionV2ProfileDigest`. Composition handles hide sensitive caller metadata
from incidental inspection; exporting canonical bytes is explicit. No binding
implements a replacement language-level validator.

The packaged independent corpus contains 56 complete canonical, digest and
refusal witnesses. Qualification compares every outcome through Rust, Python
and Node, then repeats both new profiles from offline installed archives with
compilers absent. Candidate bundles retain separate complete reports for all
eight profiles. A matching observation count alone is insufficient.

These are structural/native ABI contracts. Parsing Context or Policy does not
authenticate a caller, consume a one-use grant, reserve an active scope, invoke a
provider or authorize replay. Sensitive context and request hashes must not enter
logs or replicated state. Host-owned cancellation, revocation, disconnect,
uncertain execution and protected storage remain necessary.

The issued calls/1 and managed/1 cohorts do not admit these records. A compatible
installation, startup and call cohort, host implementation and installed
application qualification are required before actual inference adoption.
Candidate manifests include both versions; consumers must select the full
versioned profile ID instead of collapsing entries to a family name.
This contract does not select a router, classifier, provider or model.
