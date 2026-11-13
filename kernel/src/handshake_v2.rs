//! Inference-cohort startup matching; structural records never authenticate callers.

use crate::handshake::{self, HandshakeProfile};
use crate::managed_v2;
use crate::portable::PortableError;

/// Separate handshake identity for managed/2 protocol-six applications.
pub const PROFILE: &str = "skulk.handshake/2";

/// Identify all normative hello schemas and protected-startup matching rules.
pub fn profile_digest() -> &'static str {
    handshake::profile_digest_for(HandshakeProfile::Inference)
}

/// Immutable hello metadata, intentionally without credential-bearing Debug output.
pub struct Record {
    pub(crate) inner: handshake::Record,
}

impl Record {
    /// Validate only the new hello profile, without trusting or consuming a token.
    pub fn from_json(name: &str, source: &str) -> Result<Self, PortableError> {
        handshake::Record::from_profile(HandshakeProfile::Inference, name, source)
            .map(|inner| Self { inner })
    }

    /// Return the public record kind without private fields.
    pub fn name(&self) -> &'static str {
        self.inner.name()
    }

    /// Explicitly export immutable bytes for protected host-owned transport.
    pub fn canonical(&self) -> &[u8] {
        self.inner.canonical()
    }
}

/// Derive a hello for the exact admitted managed/2 startup and optional live call.
pub fn from_startup(
    startup: &managed_v2::Record,
    call_id: Option<&str>,
) -> Result<Record, PortableError> {
    handshake::from_startup_for(HandshakeProfile::Inference, &startup.inner, call_id)
        .map(|inner| Record { inner })
}

/// Match private startup fields and constant-time token bytes, without authorization.
///
/// The host separately owns channel lifetime, one-use tokens, active calls,
/// original callers and current policy. A matching hello grants no inference.
pub fn admit(
    startup: &managed_v2::Record,
    source: &str,
    call_id: Option<&str>,
) -> Result<Record, PortableError> {
    handshake::admit_for(HandshakeProfile::Inference, &startup.inner, source, call_id)
        .map(|inner| Record { inner })
}
