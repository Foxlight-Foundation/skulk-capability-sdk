//! Pure attenuation of host-owned caller scopes; records are never bearer grants.

use std::collections::BTreeSet;
use std::sync::OnceLock;

use serde_json::{Value, json};
use thiserror::Error;

use crate::{codec, portable_json};

/// Composition has its own identity; no frozen transport profile is extended.
pub const PROFILE: &str = "skulk.composition/1";
const MAX_RECORD_BYTES: usize = 262_144;
const MAX_REQUEST_BYTES: usize = 16_777_216;
const MAX_ANCESTRY: usize = 8;
type Result<T> = std::result::Result<T, CompositionError>;

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum CompositionProfile {
    Original,
    Inference,
}

impl CompositionProfile {
    fn identity(self) -> &'static str {
        match self {
            Self::Original => PROFILE,
            Self::Inference => crate::composition_v2::PROFILE,
        }
    }

    fn source(self) -> &'static str {
        match self {
            Self::Original => include_str!("../contracts/composition-v1.json"),
            Self::Inference => include_str!("../contracts/composition-v2.json"),
        }
    }

    fn protocol(self) -> u64 {
        match self {
            Self::Original => 1,
            Self::Inference => 2,
        }
    }
}

/// Bounded failures deliberately omit callers, request content and scope IDs.
#[derive(Debug, Error, PartialEq, Eq)]
pub enum CompositionError {
    #[error("composition_invalid: unsupported or malformed record")]
    Invalid,
    #[error("composition_binding: active caller scope or request differs")]
    Binding,
    #[error("composition_denied: destination or policy is not eligible")]
    Denied,
    #[error("composition_expired: deadline is unavailable")]
    Expired,
    #[error("composition_limit: resource bound exceeded")]
    Limit,
}

fn canonical(source: &str) -> Result<Vec<u8>> {
    let value =
        portable_json::parse(source, MAX_RECORD_BYTES).map_err(|_| CompositionError::Invalid)?;
    let bytes = portable_json::encode(&value).map_err(|_| CompositionError::Invalid)?;
    if bytes.len() > MAX_RECORD_BYTES {
        return Err(CompositionError::Limit);
    }
    Ok(bytes)
}

fn specification(profile: CompositionProfile) -> &'static Value {
    static ORIGINAL: OnceLock<Value> = OnceLock::new();
    static INFERENCE: OnceLock<Value> = OnceLock::new();
    let specification = match profile {
        CompositionProfile::Original => &ORIGINAL,
        CompositionProfile::Inference => &INFERENCE,
    };
    specification.get_or_init(|| {
        serde_json::from_str(profile.source()).expect("packaged composition specification")
    })
}

/// Fingerprint the complete normative schema and semantics, never a request.
pub fn profile_digest() -> &'static str {
    profile_digest_for(CompositionProfile::Original)
}

pub(crate) fn profile_digest_for(profile: CompositionProfile) -> &'static str {
    static ORIGINAL: OnceLock<String> = OnceLock::new();
    static INFERENCE: OnceLock<String> = OnceLock::new();
    let digest = match profile {
        CompositionProfile::Original => &ORIGINAL,
        CompositionProfile::Inference => &INFERENCE,
    };
    digest.get_or_init(|| {
        let mut domain = b"skulk.composition.profile\0".to_vec();
        domain.extend(canonical(profile.source()).expect("bounded composition specification"));
        codec::sha256(&domain)
    })
}

fn validator(profile: CompositionProfile, name: &str) -> Result<&'static jsonschema::Validator> {
    static ORIGINAL: [OnceLock<jsonschema::Validator>; 4] = [const { OnceLock::new() }; 4];
    static INFERENCE: [OnceLock<jsonschema::Validator>; 4] = [const { OnceLock::new() }; 4];
    let index = match name {
        "Context" => 0,
        "DelegateRequest" => 1,
        "Policy" => 2,
        "Target" => 3,
        _ => return Err(CompositionError::Invalid),
    };
    let cell = match profile {
        CompositionProfile::Original => &ORIGINAL[index],
        CompositionProfile::Inference => &INFERENCE[index],
    };
    Ok(cell.get_or_init(|| {
        let spec = specification(profile);
        let schema = if name == "Target" {
            spec["$defs"]["Target"].clone()
        } else {
            let mut schema = spec["models"][name].clone();
            schema["$defs"] = spec["$defs"].clone();
            schema
        };
        jsonschema::options()
            .with_draft(jsonschema::Draft::Draft202012)
            .offline()
            .should_validate_formats(false)
            .with_pattern_options(jsonschema::PatternOptions::regex())
            .build(&schema)
            .expect("closed composition schema")
    }))
}

// Descriptor changes and version aliases cannot bypass a call-cycle boundary.
fn identity(target: &Value) -> (&str, &str) {
    (
        target["node_id"].as_str().expect("validated target"),
        target["capability_id"].as_str().expect("validated target"),
    )
}

fn allows(profile: CompositionProfile, executor: &Value, behavior: &Value) -> bool {
    match profile {
        CompositionProfile::Original => behavior == "read",
        CompositionProfile::Inference => {
            (executor == "read" && behavior == "read")
                || (executor == "inference" && (behavior == "read" || behavior == "inference"))
        }
    }
}

fn context_semantics(profile: CompositionProfile, value: &Value) -> Result<()> {
    let ancestors = value["ancestry"].as_array().expect("validated context");
    let mut identities = BTreeSet::new();
    for target in ancestors.iter().chain(std::iter::once(&value["executor"])) {
        if !identities.insert(identity(target)) {
            return Err(CompositionError::Denied);
        }
    }
    let root = value["parent_scope_id"].is_null();
    if root != ancestors.is_empty()
        || root != (value["scope_id"] == value["root_scope_id"])
        || value["scope_id"] == value["parent_scope_id"]
        || !value["allowed_processing_domains"]
            .as_array()
            .expect("validated domains")
            .contains(&value["executor"]["processing_domain"])
    {
        return Err(CompositionError::Binding);
    }
    if profile == CompositionProfile::Inference {
        let executor = &value["executor"]["behavior"];
        if !allows(profile, executor, executor)
            || value["allowed_targets"]
                .as_array()
                .expect("validated targets")
                .iter()
                .any(|target| !allows(profile, executor, &target["behavior"]))
        {
            return Err(CompositionError::Denied);
        }
    }
    Ok(())
}

/// Immutable structural admission, intentionally without Debug/display/digest.
///
/// Only the host's authenticated active-call registry can establish provenance.
pub struct Record {
    profile: CompositionProfile,
    name: &'static str,
    value: Value,
    canonical: Vec<u8>,
}

impl Record {
    /// Admit Context, DelegateRequest or Policy; this grants no execution rights.
    pub fn from_json(name: &str, source: &str) -> Result<Self> {
        Self::from_profile(CompositionProfile::Original, name, source)
    }

    pub(crate) fn from_profile(
        profile: CompositionProfile,
        name: &str,
        source: &str,
    ) -> Result<Self> {
        let name = match name {
            "Context" => "Context",
            "DelegateRequest" => "DelegateRequest",
            "Policy" => "Policy",
            _ => return Err(CompositionError::Invalid),
        };
        let canonical = canonical(source)?;
        let value: Value =
            serde_json::from_slice(&canonical).map_err(|_| CompositionError::Invalid)?;
        if value["contract_profile"] != profile.identity()
            || value["profile_digest"] != profile_digest_for(profile)
            || value["protocol"].as_u64() != Some(profile.protocol())
            || !validator(profile, name)?.is_valid(&value)
        {
            return Err(CompositionError::Invalid);
        }
        if name == "Context" {
            context_semantics(profile, &value)?;
        }
        Ok(Self {
            profile,
            name,
            value,
            canonical,
        })
    }

    /// Public record kind only; metadata is never included in representation.
    pub fn name(&self) -> &'static str {
        self.name
    }

    /// Borrow immutable canonical bytes; bindings return defensive owned copies.
    pub fn canonical(&self) -> &[u8] {
        &self.canonical
    }
}

/// Attenuate one delegation against authenticated host observations, without I/O.
///
/// `parent`, `policy`, `executor` and fresh `scope_id` must be supplied by the
/// host, never taken on trust from capability JSON. The host must atomically
/// resolve the active parent, reserve request ID, recheck revocation and fence
/// cancellation/uncertain execution. This function does none of those effects.
pub fn admit(
    parent: &Record,
    request: &Record,
    policy: &Record,
    executor: &str,
    request_bytes: &[u8],
    now_unix_ms: u64,
    scope_id: &str,
) -> Result<Record> {
    admit_for(
        CompositionProfile::Original,
        parent,
        request,
        policy,
        executor,
        request_bytes,
        now_unix_ms,
        scope_id,
    )
}

// The profile is fixed by the typed public entry point, never selected by JSON.
#[allow(clippy::too_many_arguments)]
pub(crate) fn admit_for(
    profile: CompositionProfile,
    parent: &Record,
    request: &Record,
    policy: &Record,
    executor: &str,
    request_bytes: &[u8],
    now_unix_ms: u64,
    scope_id: &str,
) -> Result<Record> {
    if parent.profile != profile || request.profile != profile || policy.profile != profile {
        return Err(CompositionError::Invalid);
    }
    if parent.name != "Context" || request.name != "DelegateRequest" || policy.name != "Policy" {
        return Err(CompositionError::Invalid);
    }
    if request_bytes.len() > MAX_REQUEST_BYTES {
        return Err(CompositionError::Limit);
    }
    let executor: Value =
        serde_json::from_slice(&canonical(executor)?).map_err(|_| CompositionError::Invalid)?;
    if !validator(profile, "Target")?.is_valid(&executor) {
        return Err(CompositionError::Invalid);
    }
    let parent = &parent.value;
    let request = &request.value;
    let policy = &policy.value;
    if executor != parent["executor"]
        || policy["caller_id"] != parent["caller_id"]
        || policy["caller_kind"] != parent["caller_kind"]
        || policy["issuer_node_id"] != parent["issuer_node_id"]
        || request["parent_scope_id"] != parent["scope_id"]
        || request["parent_request_sha256"] != parent["request_sha256"]
        || request["request_sha256"] != codec::sha256(request_bytes)
        || scope_id == parent["scope_id"]
        || scope_id == parent["root_scope_id"]
        || scope_id == request["request_id"]
    {
        return Err(CompositionError::Binding);
    }
    let deadline = request["deadline_unix_ms"]
        .as_u64()
        .expect("validated deadline");
    if now_unix_ms == 0
        || now_unix_ms >= deadline
        || deadline
            > parent["deadline_unix_ms"]
                .as_u64()
                .expect("validated deadline")
        || deadline
            > policy["deadline_unix_ms"]
                .as_u64()
                .expect("validated deadline")
    {
        return Err(CompositionError::Expired);
    }
    let mut ancestry = parent["ancestry"]
        .as_array()
        .expect("validated ancestry")
        .clone();
    if ancestry.len() >= MAX_ANCESTRY {
        return Err(CompositionError::Limit);
    }
    ancestry.push(executor);
    let destination = &request["destination"];
    if parent["policy_revision"] != policy["policy_revision"]
        || !allows(
            profile,
            &parent["executor"]["behavior"],
            &destination["behavior"],
        )
        || ancestry
            .iter()
            .any(|target| identity(target) == identity(destination))
    {
        return Err(CompositionError::Denied);
    }
    let domains: Vec<Value> = parent["allowed_processing_domains"]
        .as_array()
        .expect("validated domains")
        .iter()
        .filter(|domain| {
            policy["allowed_processing_domains"]
                .as_array()
                .expect("validated domains")
                .contains(domain)
        })
        .cloned()
        .collect();
    let targets: Vec<Value> = parent["allowed_targets"]
        .as_array()
        .expect("validated targets")
        .iter()
        .filter(|target| {
            policy["allowed_targets"]
                .as_array()
                .expect("validated targets")
                .contains(target)
                && allows(profile, &destination["behavior"], &target["behavior"])
                && domains.contains(&target["processing_domain"])
                && !ancestry
                    .iter()
                    .any(|ancestor| identity(ancestor) == identity(target))
        })
        .cloned()
        .collect();
    if !targets.contains(destination) {
        return Err(CompositionError::Denied);
    }
    let mut child = parent.clone();
    child["scope_id"] = json!(scope_id);
    child["parent_scope_id"] = parent["scope_id"].clone();
    child["executor"] = destination.clone();
    child["request_sha256"] = request["request_sha256"].clone();
    child["allowed_targets"] = json!(targets);
    child["allowed_processing_domains"] = json!(domains);
    child["ancestry"] = json!(ancestry);
    child["deadline_unix_ms"] = json!(deadline);
    Record::from_profile(profile, "Context", &child.to_string())
}
