//! Explicit inference declarations, separate from all issued portable/1 identities.

use crate::portable::{self, AuthorProfile, PortableError};

/// Author identity with a distinct inference behavior; never caller authority.
pub const PROFILE: &str = "skulk.portable/2";
/// Older managed/install/startup readers must refuse this new author envelope.
pub const PROTOCOL: u32 = 6;

/// Bind the entire normative declaration and unchanged schema language.
pub fn profile_digest() -> &'static str {
    portable::profile_digest_for(AuthorProfile::Inference)
}

/// Validate a profile-two descriptor/envelope through the shared compiled rules.
pub fn validate_contract_json(name: &str, source: &str) -> Result<Vec<u8>, PortableError> {
    portable::validate_for(AuthorProfile::Inference, name, source)
}

/// Compute the full profile/model-separated contract identity, not a grant.
pub fn contract_digest(name: &str, source: &str) -> Result<String, PortableError> {
    portable::digest_for(AuthorProfile::Inference, name, source)
}
