//! Explicit protocol-five record admission and private startup/call binding.

use std::sync::OnceLock;

use serde_json::{Value, json};
use subtle::ConstantTimeEq;

use crate::{codec, managed, portable::PortableError, portable_json};

/// Handshake semantics are independent of the frozen manifest/descriptor profiles.
pub const PROFILE: &str = "skulk.handshake/1";
const MAX_BYTES: usize = 16_384;

fn canonical(source: &str) -> Result<Vec<u8>, PortableError> {
    let bytes = portable_json::encode(&portable_json::parse(source, MAX_BYTES)?)?;
    if bytes.len() > MAX_BYTES {
        return Err(PortableError::Limit);
    }
    Ok(bytes)
}

/// Return the fingerprint of the complete handshake-only specification.
pub fn profile_digest() -> &'static str {
    static DIGEST: OnceLock<String> = OnceLock::new();
    DIGEST.get_or_init(|| {
        let mut domain = b"skulk.handshake.profile\0".to_vec();
        domain.extend(
            canonical(include_str!("../contracts/handshake-v1.json")).expect("handshake spec"),
        );
        codec::sha256(&domain)
    })
}

fn validator(name: &str) -> Result<&'static jsonschema::Validator, PortableError> {
    static HELLO: OnceLock<jsonschema::Validator> = OnceLock::new();
    static STREAM_HELLO: OnceLock<jsonschema::Validator> = OnceLock::new();
    let cell = match name {
        "Hello" => &HELLO,
        "StreamHello" => &STREAM_HELLO,
        _ => return Err(PortableError::Contract),
    };
    Ok(cell.get_or_init(|| {
        let spec: Value = serde_json::from_str(include_str!("../contracts/handshake-v1.json"))
            .expect("handshake specification");
        assert_eq!(spec["managed_profile_digest"], managed::profile_digest());
        jsonschema::options()
            .with_draft(jsonschema::Draft::Draft202012)
            .offline()
            .should_validate_formats(false)
            .with_pattern_options(jsonschema::PatternOptions::regex())
            .build(&spec["models"][name])
            .expect("packaged handshake schema")
    }))
}

/// Immutable handshake bytes; intentionally no Debug/display/secret digest.
///
/// Structural admission never establishes a trusted channel or consumes a nonce.
pub struct Record {
    name: &'static str,
    canonical: Vec<u8>,
}

impl Record {
    /// Admit a closed, fingerprint-bound Hello or StreamHello without I/O.
    pub fn from_json(name: &str, source: &str) -> Result<Self, PortableError> {
        let canonical = canonical(source)?;
        let value: Value = serde_json::from_slice(&canonical).map_err(|_| PortableError::Json)?;
        if value["contract_profile"] != PROFILE || value["profile_digest"] != profile_digest() {
            return Err(PortableError::Profile);
        }
        if value["protocol"].as_u64() != Some(5) {
            return Err(PortableError::Protocol);
        }
        if !validator(name)?.is_valid(&value) {
            return Err(PortableError::Contract);
        }
        Ok(Self {
            name: if name == "Hello" {
                "Hello"
            } else {
                "StreamHello"
            },
            canonical,
        })
    }

    /// Return the public record kind, never any credential-bearing fields.
    pub fn name(&self) -> &'static str {
        self.name
    }

    /// Borrow immutable wire bytes; language bindings must return owned copies.
    pub fn canonical(&self) -> &[u8] {
        &self.canonical
    }
}

fn identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._:@-".contains(&b))
}

/// Derive the exact expected handshake from one native-admitted private startup.
///
/// A call ID selects StreamHello explicitly. The host must first admit that call;
/// neither this pure factory nor the record fingerprint provides that authority.
pub fn from_startup(
    startup: &managed::Record,
    call_id: Option<&str>,
) -> Result<Record, PortableError> {
    if startup.name() != "Startup" || call_id.is_some_and(|value| !identifier(value)) {
        return Err(PortableError::Contract);
    }
    let value: Value =
        serde_json::from_slice(startup.canonical()).map_err(|_| PortableError::Json)?;
    let mut fields = json!({
        "contract_profile": PROFILE,
        "profile_digest": profile_digest(),
        "protocol": 5,
        "capability_node_id": value["node_id"],
        "manifest_sha256": value["manifest_digest"],
        "token": value["token"],
    });
    let name = if let Some(call_id) = call_id {
        fields["kind"] = json!("stream_hello");
        fields["call_id"] = json!(call_id);
        "StreamHello"
    } else {
        fields["kind"] = json!("hello");
        fields["host_id"] = value["host_id"].clone();
        fields["transport_node_id"] = value["transport_id"].clone();
        fields["bundle_id"] = value["manifest"]["bundle_id"].clone();
        fields["bundle_version"] = value["manifest"]["bundle_version"].clone();
        "Hello"
    };
    Record::from_json(name, &fields.to_string())
}

/// Bind an incoming handshake to the expected startup and optional admitted call.
///
/// Public identities compare normally; fixed-width secret tokens use subtle.
/// Mismatches emit one bounded category with no field values. The host still
/// owns channel one-use, current trust, invocation admission and replay state.
pub fn admit(
    startup: &managed::Record,
    source: &str,
    call_id: Option<&str>,
) -> Result<Record, PortableError> {
    let expected = from_startup(startup, call_id)?;
    let received = Record::from_json(expected.name(), source)?;
    let mut expected_fields: Value =
        serde_json::from_slice(expected.canonical()).map_err(|_| PortableError::Json)?;
    let mut received_fields: Value =
        serde_json::from_slice(received.canonical()).map_err(|_| PortableError::Json)?;
    let expected_token = expected_fields
        .as_object_mut()
        .expect("admitted object")
        .remove("token")
        .expect("admitted token");
    let received_token = received_fields
        .as_object_mut()
        .expect("admitted object")
        .remove("token")
        .expect("admitted token");
    let equal = expected_token
        .as_str()
        .expect("admitted token")
        .as_bytes()
        .ct_eq(received_token.as_str().expect("admitted token").as_bytes());
    if !bool::from(equal) || expected_fields != received_fields {
        return Err(PortableError::Binding);
    }
    Ok(received)
}
