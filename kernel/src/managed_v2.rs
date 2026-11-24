//! Distinct inference-capable installation/startup; declarations grant no authority.

use crate::managed::{self, ManagedProfile};
use crate::portable::PortableError;

/// Managed inference cohort identity, independent of issued managed/1 bytes.
pub const PROFILE: &str = "skulk.managed/2";
/// Old readers must refuse the new author/startup cohort before execution.
pub const PROTOCOL: u32 = 6;

/// Fingerprint the complete managed inference schemas, defaults and rules.
pub fn profile_digest() -> &'static str {
    managed::profile_digest_for(ManagedProfile::Inference)
}

/// Normalize a closed inference Manifest or private Startup without side effects.
pub fn validate_contract_json(name: &str, source: &str) -> Result<Vec<u8>, PortableError> {
    managed::validate_for(ManagedProfile::Inference, name, source)
}

/// Bind the normalized record to its full model/profile identity, never a grant.
pub fn contract_digest(name: &str, source: &str) -> Result<String, PortableError> {
    managed::digest_for(ManagedProfile::Inference, name, source)
}

/// Immutable admitted metadata; private Startup bytes may contain credentials.
///
/// No Debug/display implementation exposes those bytes. Hosts still own trust,
/// selection, protected one-use startup delivery and every caller authorization.
pub struct Record {
    pub(crate) inner: managed::Record,
}

impl Record {
    /// Parse only the explicit managed/2 cohort, without installing or executing.
    pub fn from_json(name: &str, source: &str) -> Result<Self, PortableError> {
        managed::Record::from_profile(ManagedProfile::Inference, name, source)
            .map(|inner| Self { inner })
    }

    /// Return the public record kind without sensitive inherited fields.
    pub fn name(&self) -> &'static str {
        self.inner.name()
    }

    /// Return the full normative identity; this is not execution permission.
    pub fn digest(&self) -> &str {
        self.inner.digest()
    }

    /// Explicitly expose immutable bytes for protected host-owned transport.
    pub fn canonical(&self) -> &[u8] {
        self.inner.canonical()
    }
}
