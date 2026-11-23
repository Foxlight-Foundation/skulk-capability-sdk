//! Optional parent-bound metadata framing. Parsing never supplies caller authority.

use std::{path::PathBuf, sync::OnceLock};

use hmac::{Hmac, KeyInit, Mac};
use serde_json::{Value, json};
use sha2::Sha256;

use crate::{codec, composition_v3, managed_v2, portable_json, streams::StreamError};

pub const PROFILE: &str = "skulk.discovery/1";
pub const MAX_METADATA_BYTES: usize = 4096;
pub const MAX_PAYLOAD_BYTES: usize = 8 * 1024 * 1024;
const SOURCE: &str = include_str!("../contracts/discovery-v1.json");
type Result<T> = std::result::Result<T, StreamError>;

fn invalid() -> StreamError {
    StreamError::InvalidFrame
}
fn canonical(source: &str, limit: usize) -> Result<Vec<u8>> {
    portable_json::encode(&portable_json::parse(source, limit).map_err(|_| invalid())?)
        .map_err(|_| invalid())
}
pub fn profile_digest() -> &'static str {
    static DIGEST: OnceLock<String> = OnceLock::new();
    DIGEST.get_or_init(|| {
        let mut bytes = b"skulk.discovery.profile\0".to_vec();
        bytes.extend(canonical(SOURCE, 262144).expect("bounded discovery specification"));
        codec::sha256(&bytes)
    })
}
fn validate(source: &str) -> Result<(Value, Vec<u8>)> {
    static SCHEMA: OnceLock<jsonschema::Validator> = OnceLock::new();
    let validator = SCHEMA.get_or_init(|| {
        let spec: Value = serde_json::from_str(SOURCE).expect("packaged discovery specification");
        let schema = json!({"oneOf": [spec["models"]["Request"], spec["models"]["Response"]]});
        jsonschema::options()
            .with_draft(jsonschema::Draft::Draft202012)
            .offline()
            .should_validate_formats(false)
            .with_pattern_options(jsonschema::PatternOptions::regex())
            .build(&schema)
            .expect("closed discovery schema")
    });
    let bytes = canonical(source, MAX_METADATA_BYTES)?;
    let value: Value = serde_json::from_slice(&bytes).map_err(|_| invalid())?;
    if !validator.is_valid(&value) || value["profile_digest"] != profile_digest() {
        return Err(invalid());
    }
    let size = value["payload_size"].as_u64().ok_or_else(invalid)?;
    if (value["kind"] == "discover" || !value["error"].is_null()) && size != 0 {
        return Err(invalid());
    }
    let sequence = value["request_id"].as_str().ok_or_else(invalid)?;
    if sequence[..48].bytes().any(|b| b != b'0')
        || u64::from_str_radix(&sequence[48..], 16).map_err(|_| invalid())? == 0
    {
        return Err(invalid());
    }
    Ok((value, bytes))
}
fn unhex(value: &str) -> Result<Vec<u8>> {
    if !value.len().is_multiple_of(2) {
        return Err(invalid());
    }
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|p| {
            u8::from_str_radix(std::str::from_utf8(p).map_err(|_| invalid())?, 16)
                .map_err(|_| invalid())
        })
        .collect()
}
fn proof(startup: &Value, value: &Value, response: bool) -> Result<Hmac<Sha256>> {
    let mut unsigned = value.clone();
    unsigned
        .as_object_mut()
        .ok_or_else(invalid)?
        .remove(if response {
            "owner_proof"
        } else {
            "startup_proof"
        });
    let key = unhex(startup["token"].as_str().ok_or_else(invalid)?)?;
    let mut mac = Hmac::<Sha256>::new_from_slice(&key).map_err(|_| invalid())?;
    mac.update(if response {
        b"skulk.discovery.response/1\0"
    } else {
        b"skulk.discovery.request/1\0"
    });
    mac.update(&canonical(&unsigned.to_string(), MAX_METADATA_BYTES)?);
    Ok(mac)
}
fn sign(startup: &Value, value: &mut Value, response: bool) -> Result<()> {
    let bytes = proof(startup, value, response)?.finalize().into_bytes();
    value[if response {
        "owner_proof"
    } else {
        "startup_proof"
    }] = Value::String(bytes.iter().map(|b| format!("{b:02x}")).collect());
    Ok(())
}
fn startup(source: &str) -> Result<Value> {
    let record = managed_v2::Record::from_json("Startup", source).map_err(|_| invalid())?;
    serde_json::from_slice(record.canonical()).map_err(|_| invalid())
}
/// Resolve only the optional sibling in the admitted private child directory.
pub fn socket(source: &str) -> Result<String> {
    let value = startup(source)?;
    let path = PathBuf::from(value["socket"].as_str().ok_or_else(invalid)?);
    path.with_file_name("discovery.sock")
        .into_os_string()
        .into_string()
        .map_err(|_| invalid())
}
/// Owned sensitive metadata and exact body bytes; no diagnostic formatting.
pub struct Frame {
    metadata: Vec<u8>,
    payload: Vec<u8>,
}
impl Frame {
    pub fn metadata(&self) -> &[u8] {
        &self.metadata
    }
    pub fn payload(&self) -> &[u8] {
        &self.payload
    }
}
/// Build a read-only query from actual startup/context; this does not authenticate them.
pub fn request(
    startup_source: &str,
    context_source: &str,
    parent: &str,
    sequence: u64,
) -> Result<Frame> {
    if sequence == 0 {
        return Err(invalid());
    }
    let startup = startup(startup_source)?;
    let context =
        composition_v3::Record::from_json("Context", context_source).map_err(|_| invalid())?;
    let context: Value = serde_json::from_slice(context.canonical()).map_err(|_| invalid())?;
    if context["executor"]["node_id"] != startup["node_id"] {
        return Err(invalid());
    }
    let mut value = json!({"protocol":1,"contract_profile":PROFILE,"profile_digest":profile_digest(),
        "kind":"discover","request_id":format!("{sequence:064x}"),"parent_call_id":parent,
        "parent_scope_id":context["scope_id"],"parent_request_sha256":context["request_sha256"],
        "node_id":startup["node_id"],"host_id":startup["host_id"],
        "transport_node_id":startup["transport_id"],"manifest_sha256":startup["manifest_digest"],
        "payload_size":0});
    sign(&startup, &mut value, false)?;
    let (_, metadata) = validate(&value.to_string())?;
    Ok(Frame {
        metadata,
        payload: Vec::new(),
    })
}
/// Validate a bounded allocation hint before reading any separate response body.
pub fn payload_size(source: &str) -> Result<usize> {
    let (value, _) = validate(source)?;
    usize::try_from(value["payload_size"].as_u64().ok_or_else(invalid)?).map_err(|_| invalid())
}
/// Decode one complete frame; callers separately require EOF and current scope custody.
pub fn decode(packet: &[u8]) -> Result<Frame> {
    let prefix: [u8; 4] = packet
        .get(..4)
        .ok_or_else(invalid)?
        .try_into()
        .map_err(|_| invalid())?;
    let length = u32::from_be_bytes(prefix) as usize;
    if !(2..=MAX_METADATA_BYTES).contains(&length) {
        return Err(invalid());
    }
    let end = 4 + length;
    let source =
        std::str::from_utf8(packet.get(4..end).ok_or_else(invalid)?).map_err(|_| invalid())?;
    let (value, metadata) = validate(source)?;
    let size = payload_size(source)?;
    if packet.len() != end + size {
        return Err(invalid());
    }
    let payload = packet[end..].to_vec();
    if value["kind"] == "directory" && value["directory_sha256"] != codec::sha256(&payload) {
        return Err(invalid());
    }
    Ok(Frame { metadata, payload })
}
/// Encode only an already admitted frame without changing its body.
pub fn encode(frame: &Frame) -> Vec<u8> {
    let mut packet = (frame.metadata.len() as u32).to_be_bytes().to_vec();
    packet.extend(&frame.metadata);
    packet.extend(&frame.payload);
    packet
}
/// Verify the request HMAC against private startup, returning no credential.
pub fn admit(startup_source: &str, packet: &[u8]) -> Result<Vec<u8>> {
    let frame = decode(packet)?;
    let value: Value = serde_json::from_slice(frame.metadata()).map_err(|_| invalid())?;
    let startup = startup(startup_source)?;
    if value["kind"] != "discover" || !frame.payload().is_empty() {
        return Err(invalid());
    }
    for (field, key) in [
        ("node_id", "node_id"),
        ("host_id", "host_id"),
        ("transport_node_id", "transport_id"),
        ("manifest_sha256", "manifest_digest"),
    ] {
        if value[field] != startup[key] {
            return Err(invalid());
        }
    }
    let supplied = unhex(value["startup_proof"].as_str().ok_or_else(invalid)?)?;
    proof(&startup, &value, false)?
        .verify_slice(&supplied)
        .map_err(|_| invalid())?;
    Ok(json!({"request_id":value["request_id"],"parent_call_id":value["parent_call_id"],
        "parent_scope_id":value["parent_scope_id"],"parent_request_sha256":value["parent_request_sha256"]}).to_string().into_bytes())
}
/// Frame either exact directory bytes or a body-free bounded refusal.
pub fn reply(
    startup_source: &str,
    request_source: &str,
    body: Vec<u8>,
    error: Option<&str>,
) -> Result<Frame> {
    let (request, _) = validate(request_source)?;
    let (_, metadata) = validate(request_source)?;
    admit(
        startup_source,
        &encode(&Frame {
            metadata,
            payload: Vec::new(),
        }),
    )?;
    if request["kind"] != "discover"
        || body.len() > MAX_PAYLOAD_BYTES
        || (error.is_some() && !body.is_empty())
    {
        return Err(invalid());
    }
    if error.is_none() {
        // The outer directory has its own byte/depth/cardinality bounds. The
        // portable node budget belongs to each descriptor, which the typed
        // directory reader admits separately; aggregating it here rejects
        // valid rich catalogs well below the directory's declared limits.
        // The directory array and offer add two levels around a descriptor's
        // own frozen depth64 budget.
        let value = codec::parse_bounded(std::str::from_utf8(&body).map_err(|_| invalid())?, 66)
            .map_err(|_| invalid())?;
        if !matches!(value, codec::Value::Array(values) if values.len() <= 4096) {
            return Err(invalid());
        }
    }
    let mut value = json!({"protocol":1,"contract_profile":PROFILE,"profile_digest":profile_digest(),
        "kind":"directory","request_id":request["request_id"],"parent_call_id":request["parent_call_id"],
        "parent_scope_id":request["parent_scope_id"],"parent_request_sha256":request["parent_request_sha256"],
        "payload_size":body.len(),"directory_sha256":codec::sha256(&body),"error":error});
    sign(&startup(startup_source)?, &mut value, true)?;
    let (_, metadata) = validate(&value.to_string())?;
    Ok(Frame {
        metadata,
        payload: body,
    })
}
/// Match every original request identifier before exposing response bytes.
pub fn response(
    startup_source: &str,
    request_source: &str,
    packet: &[u8],
) -> Result<(Option<String>, Vec<u8>)> {
    let (request, _) = validate(request_source)?;
    let (_, metadata) = validate(request_source)?;
    admit(
        startup_source,
        &encode(&Frame {
            metadata,
            payload: Vec::new(),
        }),
    )?;
    let frame = decode(packet)?;
    let value: Value = serde_json::from_slice(frame.metadata()).map_err(|_| invalid())?;
    if request["kind"] != "discover" || value["kind"] != "directory" {
        return Err(invalid());
    }
    for field in [
        "request_id",
        "parent_call_id",
        "parent_scope_id",
        "parent_request_sha256",
    ] {
        if request[field] != value[field] {
            return Err(invalid());
        }
    }
    let supplied = unhex(value["owner_proof"].as_str().ok_or_else(invalid)?)?;
    proof(&startup(startup_source)?, &value, true)?
        .verify_slice(&supplied)
        .map_err(|_| invalid())?;
    Ok((value["error"].as_str().map(str::to_owned), frame.payload))
}
