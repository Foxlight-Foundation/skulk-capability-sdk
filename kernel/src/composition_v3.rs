//! Host-owned inference attenuation with explicit nullable authority deadlines.

use crate::composition::{self, CompositionProfile};

/// Bounded public refusal vocabulary shared by both composition profiles.
pub use crate::composition::CompositionError;

/// Explicit execution composition; null deadlines add no hidden generation cap.
pub const PROFILE: &str = "skulk.composition/3";

/// Fingerprint all normative schemas and rules; no request content is hashed.
pub fn profile_digest() -> &'static str {
    composition::profile_digest_for(CompositionProfile::Execution)
}

/// Immutable sensitive metadata; parsing never establishes active host authority.
pub struct Record {
    inner: composition::Record,
}

impl Record {
    /// Admit a new Context, Policy or DelegateRequest without dispatching work.
    pub fn from_json(name: &str, source: &str) -> Result<Self, CompositionError> {
        composition::Record::from_profile(CompositionProfile::Execution, name, source)
            .map(|inner| Self { inner })
    }

    /// Return only the public record kind, without caller/scope metadata.
    pub fn name(&self) -> &'static str {
        self.inner.name()
    }

    /// Explicitly expose canonical bytes for protected host-owned transport.
    pub fn canonical(&self) -> &[u8] {
        self.inner.canonical()
    }
}

/// Narrow authenticated parent/current policy and the destination effect ceiling.
///
/// The host supplies actual active-call/caller state, current permission and
/// exact request/destination observations. This pure operation never authenticates
/// JSON, dispatches work, classifies a task or supplies provider credentials.
pub fn admit(
    parent: &Record,
    request: &Record,
    policy: &Record,
    executor: &str,
    request_bytes: &[u8],
    now_unix_ms: u64,
    scope_id: &str,
) -> Result<Record, CompositionError> {
    composition::admit_for(
        CompositionProfile::Execution,
        &parent.inner,
        &request.inner,
        &policy.inner,
        executor,
        request_bytes,
        now_unix_ms,
        scope_id,
    )
    .map(|inner| Record { inner })
}
