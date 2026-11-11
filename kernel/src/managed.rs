//! Profile-bound managed installation and private inherited-startup records.

use std::collections::BTreeSet;
use std::net::IpAddr;
use std::sync::OnceLock;

use serde_json::{Value, json};

use crate::{codec, portable, portable::PortableError, portable_json};

/// Installation semantics are versioned separately from the frozen descriptors.
pub const PROFILE: &str = "skulk.managed/1";
/// Legacy readers must refuse the new manifest before launching author code.
pub const PROTOCOL: u32 = 5;
const MAX_BYTES: usize = 262_144;

fn specification() -> &'static Value {
    static SPECIFICATION: OnceLock<Value> = OnceLock::new();
    SPECIFICATION.get_or_init(|| {
        serde_json::from_str(include_str!("../contracts/managed-v1.json"))
            .expect("packaged managed specification is JSON")
    })
}

/// Fingerprint the complete managed specification without changing portable/1.
pub fn profile_digest() -> &'static str {
    static DIGEST: OnceLock<String> = OnceLock::new();
    DIGEST.get_or_init(|| {
        let mut domain = b"skulk.managed.profile\0".to_vec();
        domain
            .extend(canonical(include_str!("../contracts/managed-v1.json")).expect("managed spec"));
        codec::sha256(&domain)
    })
}

fn canonical(source: &str) -> Result<Vec<u8>, PortableError> {
    let bytes = portable_json::encode(&portable_json::parse(source, MAX_BYTES)?)?;
    if bytes.len() > MAX_BYTES {
        return Err(PortableError::Limit);
    }
    Ok(bytes)
}

/// Admit one managed manifest/startup with native-only defaults and semantics.
///
/// Startup may contain credentials. No errors include supplied data, and this
/// function performs no logging, I/O, installation or authorization effects.
pub fn validate_contract_json(name: &str, source: &str) -> Result<Vec<u8>, PortableError> {
    if !matches!(name, "Manifest" | "Startup") {
        return Err(PortableError::Contract);
    }
    let mut value = portable_json::serde(&portable_json::parse(source, MAX_BYTES)?)?;
    normalize(name, &mut value)?;
    canonical(&value.to_string())
}

/// Hash the full normalized record under its managed profile and model domain.
pub fn contract_digest(name: &str, source: &str) -> Result<String, PortableError> {
    let mut domain = format!("{PROFILE}/{name}\0").into_bytes();
    domain.extend(validate_contract_json(name, source)?);
    Ok(codec::sha256(&domain))
}

fn defaults(name: &str, value: &mut Value) -> Result<(), PortableError> {
    let fields = value.as_object_mut().ok_or(PortableError::Contract)?;
    if let Some(defaults) = specification()["defaults"][name].as_object() {
        for (key, default) in defaults {
            fields.entry(key).or_insert_with(|| default.clone());
        }
    }
    Ok(())
}

fn normalize(name: &str, value: &mut Value) -> Result<(), PortableError> {
    if value["contract_profile"].as_str() != Some(PROFILE)
        || value["profile_digest"].as_str() != Some(profile_digest())
    {
        return Err(PortableError::Profile);
    }
    if value["protocol"].as_u64() != Some(u64::from(PROTOCOL)) {
        return Err(PortableError::Protocol);
    }
    defaults(name, value)?;
    if name == "Startup" {
        normalize("Manifest", &mut value["manifest"])?;
    } else {
        defaults("Bounds", &mut value["bounds"])?;
        if !value["operations"].is_null() {
            defaults("OperationBounds", &mut value["operations"])?;
        }
        for (key, model, limit) in [
            ("surfaces", "Surface", 4),
            ("credential_inputs", "CredentialInput", 16),
            ("steward", "StewardExposure", 16),
        ] {
            let items = value[key]
                .as_array_mut()
                .filter(|items| items.len() <= limit)
                .ok_or(PortableError::Contract)?;
            for item in items {
                defaults(model, item)?;
            }
        }
        let envelope =
            portable::validate_contract_json("ContractEnvelope", &value["contracts"].to_string())?;
        value["contracts"] = serde_json::from_slice(&envelope).map_err(|_| PortableError::Json)?;
    }
    // These are packaged schemas, never author-supplied schemas or references.
    let model = json!({"$defs":specification()["models"],"$ref":format!("#/$defs/{name}")});
    let validator = jsonschema::options()
        .with_draft(jsonschema::Draft::Draft202012)
        .offline()
        .should_validate_formats(false)
        .with_pattern_options(jsonschema::PatternOptions::regex())
        .build(&model)
        .map_err(|_| PortableError::Contract)?;
    if !validator.is_valid(value) {
        return Err(PortableError::Contract);
    }
    if name == "Manifest" {
        manifest_semantics(value)
    } else {
        startup_semantics(value)
    }
}

fn text<'a>(value: &'a Value, key: &str) -> Result<&'a str, PortableError> {
    value[key].as_str().ok_or(PortableError::Contract)
}

fn items<'a>(value: &'a Value, key: &str) -> Result<&'a [Value], PortableError> {
    value[key]
        .as_array()
        .map(Vec::as_slice)
        .ok_or(PortableError::Contract)
}

fn path(value: &str) -> Result<(), PortableError> {
    if !value.starts_with('/')
        || value.contains('\0')
        || value.split('/').any(|part| matches!(part, "." | ".."))
    {
        return Err(PortableError::Contract);
    }
    Ok(())
}

fn unique(values: &[Value], key: Option<&str>) -> Result<(), PortableError> {
    let mut seen = BTreeSet::new();
    for value in values {
        let identity = match key {
            Some(key) => text(value, key)?,
            None => value.as_str().ok_or(PortableError::Contract)?,
        };
        if !seen.insert(identity) {
            return Err(PortableError::Contract);
        }
    }
    Ok(())
}

fn manifest_semantics(value: &Value) -> Result<(), PortableError> {
    let executable = text(value, "executable")?;
    if executable.starts_with('/')
        || executable.contains(['\0', '\\'])
        || executable
            .split('/')
            .any(|part| matches!(part, "" | "." | ".."))
    {
        return Err(PortableError::Contract);
    }
    text(value, "skulk_requires")?
        .parse::<pep440_rs::VersionSpecifiers>()
        .map_err(|_| PortableError::Contract)?;
    for (key, identity) in [
        ("surfaces", Some("surface_id")),
        ("credential_inputs", Some("credential_id")),
        ("steward", Some("qualified_id")),
        ("runtime_dependencies", None),
    ] {
        unique(items(value, key)?, identity)?;
    }
    if items(value, "runtime_dependencies")?
        .iter()
        .any(|item| item == "pip")
        || (!value["preflight_profile"].is_null()
            && value["contracts"]["configuration_schema"].is_null())
    {
        return Err(PortableError::Contract);
    }
    for surface in items(value, "surfaces")? {
        if surface["kind"] == "link" {
            if surface["entry"] != "/" || surface["websocket"] != false {
                return Err(PortableError::Contract);
            }
        } else {
            path(text(surface, "entry")?)?;
        }
    }
    let descriptors = items(&value["contracts"], "descriptors")?;
    for descriptor in descriptors {
        if (value["effect"] == "read" && descriptor["behavior"] != "read")
            || (descriptor["annotations"]["skulk.operation"] == "1"
                && value["operations"].is_null())
        {
            return Err(PortableError::Contract);
        }
    }
    for exposure in items(value, "steward")? {
        let descriptor = descriptors
            .iter()
            .find(|descriptor| {
                format!(
                    "{}@{}",
                    descriptor["id"].as_str().unwrap_or_default(),
                    descriptor["version"].as_str().unwrap_or_default()
                ) == exposure["qualified_id"]
            })
            .ok_or(PortableError::Contract)?;
        if exposure["behavior"] != descriptor["behavior"]
            || (exposure["behavior"] == "read" && exposure["risk"] != "observation")
            || (exposure["behavior"] != "read"
                && (value["effect"] != "governed" || exposure["risk"] == "observation"))
        {
            return Err(PortableError::Contract);
        }
    }
    Ok(())
}

fn startup_semantics(value: &Value) -> Result<(), PortableError> {
    let executable = text(value, "executable")?;
    path(executable)?;
    let entry = text(&value["manifest"], "executable")?;
    if !executable.ends_with(&format!("/{entry}")) {
        return Err(PortableError::Contract);
    }
    path(text(value, "socket")?)?;
    if !value["stream_socket"].is_null() {
        path(text(value, "stream_socket")?)?;
    } else if items(&value["manifest"]["contracts"], "descriptors")?
        .iter()
        .any(|descriptor| descriptor["io_mode"] != "unary")
    {
        return Err(PortableError::Contract);
    }
    let manifest_digest = contract_digest("Manifest", &value["manifest"].to_string())?;
    if value["manifest_digest"] != manifest_digest {
        return Err(PortableError::Contract);
    }
    if !value["serve_host"].is_null() {
        let address = text(value, "serve_host")?
            .parse::<IpAddr>()
            .map_err(|_| PortableError::Contract)?;
        let address = address.to_canonical();
        if address.is_loopback() || address.is_multicast() || address.is_unspecified() {
            return Err(PortableError::Contract);
        }
    }
    let configuration = &value["configuration"];
    let schema = &value["manifest"]["contracts"]["configuration_schema"];
    if !configuration.is_null() {
        // The settings budget applies to escaped canonical bytes as well as raw
        // input. It is independent of the surrounding inherited startup budget.
        portable::canonical_json(&configuration.to_string())?;
        if schema.is_null()
            || !portable::Schema::new(&schema.to_string())?.matches(&configuration.to_string())?
        {
            return Err(PortableError::Contract);
        }
    } else if !schema.is_null() {
        return Err(PortableError::Contract);
    }
    Ok(())
}

/// Immutable native-admitted author record; startup bytes may contain credentials.
/// No Debug implementation is provided because diagnostics must not expose them.
pub struct Record {
    name: &'static str,
    canonical: Vec<u8>,
    digest: String,
}

impl Record {
    /// Normalize once and retain its full profile/model-domain identity.
    pub fn from_json(name: &str, source: &str) -> Result<Self, PortableError> {
        let canonical = validate_contract_json(name, source)?;
        let mut domain = format!("{PROFILE}/{name}\0").into_bytes();
        domain.extend(&canonical);
        Ok(Self {
            name: if name == "Manifest" {
                "Manifest"
            } else {
                "Startup"
            },
            digest: codec::sha256(&domain),
            canonical,
        })
    }

    /// Return the public record kind without credential fields.
    pub fn name(&self) -> &'static str {
        self.name
    }

    /// Return the complete native identity, never a truncated legacy revision.
    pub fn digest(&self) -> &str {
        &self.digest
    }

    /// Borrow immutable bytes; bindings return copies rather than native storage.
    pub fn canonical(&self) -> &[u8] {
        &self.canonical
    }
}
