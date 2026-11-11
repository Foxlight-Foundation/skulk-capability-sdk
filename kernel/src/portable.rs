//! SDK 1.0 candidate: one explicit, bounded contract independent of legacy rules.

use std::collections::BTreeSet;
use std::sync::OnceLock;

use serde_json::{Value, json};
use thiserror::Error;

use crate::{codec, portable_json, portable_schema};

/// Identity-bearing author/schema profile; unknown profiles are never ignored.
pub const PROFILE: &str = "skulk.portable/1";
/// The new envelope is deliberately outside legacy readers' protocol window.
pub const PROTOCOL: u32 = 5;
/// This is a closed profile of this dialect, not unrestricted draft support.
pub const DIALECT: &str = "https://json-schema.org/draft/2020-12/schema";

/// Bind negotiated profile identity to the packaged normative specification.
pub fn profile_digest() -> &'static str {
    static DIGEST: OnceLock<String> = OnceLock::new();
    DIGEST.get_or_init(|| {
        let mut domain = b"skulk.portable.profile\0".to_vec();
        domain.extend(
            canonical_with_limit(include_str!("../contracts/portable-v1.json"), 262_144)
                .expect("packaged profile is bounded scalar JSON"),
        );
        codec::sha256(&domain)
    })
}

/// Bounded public failures never contain supplied keys, schemas or payloads.
#[derive(Debug, Error, PartialEq, Eq)]
pub enum PortableError {
    #[error("portable_json: invalid finite JSON or duplicate key")]
    Json,
    #[error("portable_unicode: Unicode scalar values required")]
    Unicode,
    #[error("portable_number: number exceeds the safe binary64 domain")]
    Number,
    #[error("portable_limit: contract resource bound exceeded")]
    Limit,
    #[error("portable_schema: invalid schema")]
    Schema,
    #[error("portable_keyword: unsupported schema keyword")]
    Keyword,
    #[error("portable_dialect: unsupported schema dialect or resource")]
    Dialect,
    #[error("portable_reference: root-local named definition required")]
    Reference,
    #[error("portable_cycle: recursive schemas are not in this profile")]
    Cycle,
    #[error("portable_pattern: unsupported or over-budget regular expression")]
    Pattern,
    #[error("portable_contract: invalid author contract")]
    Contract,
    #[error("portable_profile: unsupported author profile")]
    Profile,
    #[error("portable_protocol: unsupported author protocol")]
    Protocol,
    #[error("portable_binding: handshake does not match expected startup or call")]
    Binding,
}

pub(super) fn specification() -> &'static Value {
    static SPECIFICATION: OnceLock<Value> = OnceLock::new();
    SPECIFICATION.get_or_init(|| {
        serde_json::from_str(include_str!("../contracts/portable-v1.json"))
            .expect("packaged portable contract is JSON")
    })
}

fn options() -> jsonschema::ValidationOptions<'static> {
    jsonschema::options()
        .with_draft(jsonschema::Draft::Draft202012)
        .offline()
        .should_validate_formats(false)
        .with_pattern_options(
            jsonschema::PatternOptions::regex()
                .size_limit(1_048_576)
                .dfa_size_limit(1_048_576),
        )
}

/// Canonical scalar JSON: sorted code points, ASCII escaping, one numeric kind.
pub fn canonical_json(source: &str) -> Result<Vec<u8>, PortableError> {
    canonical_with_limit(source, 65_536)
}

fn canonical_with_limit(source: &str, limit: usize) -> Result<Vec<u8>, PortableError> {
    let bytes = portable_json::encode(&portable_json::parse(source, limit)?)?;
    if bytes.len() > limit {
        return Err(PortableError::Limit);
    }
    Ok(bytes)
}

/// Permanently offline compiled schema with eager reference/vocabulary admission.
pub struct Schema {
    validator: jsonschema::Validator,
}

impl Schema {
    /// Compile only the documented closed, acyclic profile before execution.
    pub fn new(source: &str) -> Result<Self, PortableError> {
        let canonical = canonical_with_limit(source, 65_536)?;
        let definition = serde_json::from_slice(&canonical).map_err(|_| PortableError::Json)?;
        portable_schema::admit(&definition)?;
        let validator = options()
            .build(&definition)
            .map_err(|_| PortableError::Schema)?;
        Ok(Self { validator })
    }

    /// Distinguish malformed/over-budget JSON from a valid nonmatching payload.
    pub fn matches(&self, source: &str) -> Result<bool, PortableError> {
        let canonical = canonical_json(source)?;
        let instance = serde_json::from_slice(&canonical).map_err(|_| PortableError::Json)?;
        Ok(self.validator.is_valid(&instance))
    }
}

/// Normalize one new model without changing any issued legacy model or reader.
pub fn validate_contract_json(name: &str, source: &str) -> Result<Vec<u8>, PortableError> {
    let value = portable_json::parse(source, 262_144)?;
    let mut value = portable_json::serde(&value)?;
    normalize_contract(name, &mut value)?;
    let text = serde_json::to_string(&value).map_err(|_| PortableError::Json)?;
    canonical_with_limit(&text, 262_144)
}

/// Full SHA-256 with model/profile domain separation; never a legacy revision.
pub fn contract_digest(name: &str, source: &str) -> Result<String, PortableError> {
    let canonical = validate_contract_json(name, source)?;
    let mut domain = format!("{PROFILE}/{name}\0").into_bytes();
    domain.extend(canonical);
    Ok(codec::sha256(&domain))
}

fn normalize_contract(name: &str, value: &mut Value) -> Result<(), PortableError> {
    let fields = value.as_object_mut().ok_or(PortableError::Contract)?;
    if fields.get("contract_profile").and_then(Value::as_str) != Some(PROFILE)
        || fields.get("profile_digest").and_then(Value::as_str) != Some(profile_digest())
    {
        return Err(PortableError::Profile);
    }
    match name {
        "Descriptor" => {
            for (key, default) in [
                ("io_mode", json!("unary")),
                ("output_schema", Value::Null),
                ("input_chunk_schema", Value::Null),
                ("output_chunk_schema", Value::Null),
                ("annotations", json!({})),
            ] {
                fields.entry(key).or_insert(default);
            }
        }
        "ContractEnvelope" => {
            if fields.get("protocol").and_then(Value::as_u64) != Some(u64::from(PROTOCOL)) {
                return Err(PortableError::Protocol);
            }
            fields.entry("configuration_schema").or_insert(Value::Null);
            let descriptors = fields
                .get_mut("descriptors")
                .and_then(Value::as_array_mut)
                .filter(|descriptors| (1..=16).contains(&descriptors.len()))
                .ok_or(PortableError::Contract)?;
            for descriptor in descriptors {
                normalize_contract("Descriptor", descriptor)?;
            }
        }
        _ => return Err(PortableError::Contract),
    }
    let model = json!({"$defs": specification()["models"], "$ref": format!("#/$defs/{name}")});
    if !options()
        .build(&model)
        .map_err(|_| PortableError::Contract)?
        .is_valid(value)
    {
        return Err(PortableError::Contract);
    }
    if name == "Descriptor" {
        let mode = value["io_mode"].as_str().ok_or(PortableError::Contract)?;
        for (key, required) in [
            (
                "input_chunk_schema",
                matches!(mode, "client_streaming" | "bidirectional"),
            ),
            (
                "output_chunk_schema",
                matches!(mode, "server_streaming" | "bidirectional"),
            ),
        ] {
            if value[key].is_null() == required {
                return Err(PortableError::Contract);
            }
        }
        for key in [
            "input_schema",
            "output_schema",
            "input_chunk_schema",
            "output_chunk_schema",
        ] {
            compile_optional(&value[key])?;
        }
    } else {
        let mut identities = BTreeSet::new();
        for descriptor in value["descriptors"]
            .as_array()
            .ok_or(PortableError::Contract)?
        {
            let identity = (descriptor["id"].as_str(), descriptor["version"].as_str());
            if !identities.insert(identity) {
                return Err(PortableError::Contract);
            }
        }
        compile_optional(&value["configuration_schema"])?;
    }
    Ok(())
}

fn compile_optional(value: &Value) -> Result<(), PortableError> {
    if !value.is_null() {
        Schema::new(&serde_json::to_string(value).map_err(|_| PortableError::Json)?)?;
    }
    Ok(())
}
