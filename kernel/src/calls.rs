//! Explicit composed-call framing; structural metadata never grants authority.

use std::sync::OnceLock;

use serde_json::{Value, json};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

use crate::{codec, composition, portable_json, streams::StreamError};

/// Independently issued call profile; existing control and media bytes stay fixed.
pub const PROFILE: &str = "skulk.calls/1";
/// Metadata is bounded separately from raw request bytes.
pub const MAX_METADATA_BYTES: usize = 262_144;
/// Per-frame resource bound, not an inference output or request-count allowance.
pub const MAX_PAYLOAD_BYTES: usize = 16_777_216;
type Result<T> = std::result::Result<T, StreamError>;

fn specification() -> &'static Value {
    static SPEC: OnceLock<Value> = OnceLock::new();
    SPEC.get_or_init(|| {
        serde_json::from_str(include_str!("../contracts/calls-v1.json"))
            .expect("packaged calls specification")
    })
}

fn canonical(source: &str, bound: usize) -> Result<Vec<u8>> {
    let value = portable_json::parse(source, bound).map_err(|_| StreamError::InvalidFrame)?;
    let bytes = portable_json::encode(&value).map_err(|_| StreamError::InvalidFrame)?;
    if bytes.len() > bound {
        return Err(StreamError::HeaderBound);
    }
    Ok(bytes)
}

/// Fingerprint all normative call schemas and semantics, never caller content.
pub fn profile_digest() -> &'static str {
    static DIGEST: OnceLock<String> = OnceLock::new();
    DIGEST.get_or_init(|| {
        let mut bytes = b"skulk.calls.profile\0".to_vec();
        bytes.extend(
            canonical(
                include_str!("../contracts/calls-v1.json"),
                MAX_METADATA_BYTES,
            )
            .expect("bounded calls specification"),
        );
        codec::sha256(&bytes)
    })
}

fn validator() -> &'static jsonschema::Validator {
    static VALIDATOR: OnceLock<jsonschema::Validator> = OnceLock::new();
    VALIDATOR.get_or_init(|| {
        let schema = json!({"oneOf": specification()["controls"].as_object()
            .expect("closed calls kinds").values().collect::<Vec<_>>()});
        jsonschema::options()
            .with_draft(jsonschema::Draft::Draft202012)
            .offline()
            .should_validate_formats(false)
            .with_pattern_options(jsonschema::PatternOptions::regex())
            .build(&schema)
            .expect("closed calls schema")
    })
}

fn object_payload(payload: &[u8]) -> Result<()> {
    let text = std::str::from_utf8(payload).map_err(|_| StreamError::InvalidFrame)?;
    let value =
        portable_json::parse(text, MAX_PAYLOAD_BYTES).map_err(|_| StreamError::InvalidFrame)?;
    if !matches!(value, codec::Value::Object(_)) {
        return Err(StreamError::InvalidFrame);
    }
    Ok(())
}

fn checked_metadata(source: &str) -> Result<(Vec<u8>, Value)> {
    let bytes = canonical(source, MAX_METADATA_BYTES)?;
    let value: Value = serde_json::from_slice(&bytes).map_err(|_| StreamError::InvalidFrame)?;
    if value["protocol"].as_u64() != Some(1)
        || value["contract_profile"] != PROFILE
        || value["profile_digest"] != profile_digest()
        || !validator().is_valid(&value)
    {
        return Err(StreamError::InvalidFrame);
    }
    let size = value["payload_size"]
        .as_u64()
        .ok_or(StreamError::InvalidFrame)?;
    let empty = matches!(
        value["kind"].as_str(),
        Some("health" | "shutdown" | "cancel" | "delegate_started" | "delegate_finished")
    ) || (!value["error"].is_null()
        && matches!(value["kind"].as_str(), Some("result" | "delegate_result")));
    // Reject impossible body declarations before a network reader allocates or
    // waits for bytes which this kind may never carry.
    if (empty && size != 0) || (!empty && size < 2) {
        return Err(StreamError::InvalidFrame);
    }
    if matches!(
        value["kind"].as_str(),
        Some("delegate_result" | "delegate_started" | "delegate_finished")
    ) {
        sequence(&value["request_id"])?;
    }
    if value["kind"] == "delegate_started" && value["media_call_id"] == value["parent_call_id"] {
        return Err(StreamError::InvalidFrame);
    }
    if matches!(value["kind"].as_str(), Some("invoke" | "delegate")) {
        let (name, field) = if value["kind"] == "invoke" {
            ("Context", "context")
        } else {
            ("DelegateRequest", "request")
        };
        composition::Record::from_json(name, &value[field].to_string())
            .map_err(|_| StreamError::InvalidFrame)?;
        if value["kind"] == "delegate" {
            sequence(&value[field]["request_id"])?;
        }
        if value["kind"] == "invoke"
            && value["mode"] == "unary"
            && value["remaining_seconds"].as_f64().unwrap_or(f64::INFINITY) > 30.0
        {
            return Err(StreamError::InvalidFrame);
        }
    }
    Ok((bytes, value))
}

/// Admit metadata before a host allocates/reads a body. This is only a resource
/// hint: complete frame admission and actual host authority remain mandatory.
pub fn payload_size(source: &str) -> Result<usize> {
    let (_, value) = checked_metadata(source)?;
    Ok(value["payload_size"]
        .as_u64()
        .ok_or(StreamError::InvalidFrame)? as usize)
}

fn sequence(value: &Value) -> Result<()> {
    let number = value
        .as_str()
        .and_then(|text| u64::from_str_radix(text, 16).ok());
    if number.is_none_or(|number| number == 0) {
        return Err(StreamError::InvalidFrame);
    }
    Ok(())
}

/// Immutable admitted frame; representation never prints metadata or raw content.
pub struct Frame {
    metadata: Vec<u8>,
    payload: Vec<u8>,
}

impl Frame {
    /// Admit closed metadata and the exact raw body through compiled semantics.
    /// Host connection/caller/policy/call authority is deliberately not inferred.
    pub fn from_json(source: &str, payload: Vec<u8>) -> Result<Self> {
        if payload.len() > MAX_PAYLOAD_BYTES {
            return Err(StreamError::HeaderBound);
        }
        let (metadata, value) = checked_metadata(source)?;
        if value["payload_size"].as_u64() != Some(payload.len() as u64) {
            return Err(StreamError::InvalidFrame);
        }
        match value["kind"].as_str().ok_or(StreamError::InvalidFrame)? {
            "invoke" | "delegate" => {
                object_payload(&payload)?;
                let field = if value["kind"] == "invoke" {
                    "context"
                } else {
                    "request"
                };
                if value[field]["request_sha256"] != codec::sha256(&payload) {
                    return Err(StreamError::InvalidFrame);
                }
            }
            "result" | "delegate_result" => {
                if value["error"].is_null() {
                    object_payload(&payload)?;
                } else if !payload.is_empty() {
                    return Err(StreamError::InvalidFrame);
                }
            }
            "health" => {
                if !payload.is_empty() {
                    return Err(StreamError::InvalidFrame);
                }
                // Reuse the issued health semantics without changing its bytes
                // or treating its admission as a caller-authority decision.
                let mut legacy = value.clone();
                let fields = legacy.as_object_mut().ok_or(StreamError::InvalidFrame)?;
                fields.remove("payload_size");
                fields.insert("protocol".into(), json!(5));
                fields.insert("contract_profile".into(), json!(crate::transport::PROFILE));
                fields.insert(
                    "profile_digest".into(),
                    json!(crate::transport::profile_digest()),
                );
                crate::transport::Control::from_json(&legacy.to_string())?;
            }
            "shutdown" | "cancel" | "delegate_started" | "delegate_finished" => {
                if !payload.is_empty() {
                    return Err(StreamError::InvalidFrame);
                }
            }
            _ => return Err(StreamError::InvalidFrame),
        }
        Ok(Self { metadata, payload })
    }

    /// Borrow admitted canonical metadata; bindings return defensive copies.
    pub fn metadata(&self) -> &[u8] {
        &self.metadata
    }
    /// Borrow exact payload bytes; never silently canonicalize request content.
    pub fn payload(&self) -> &[u8] {
        &self.payload
    }
}

/// Frame admitted metadata and exact raw bytes without language reserialization.
pub fn encode(frame: &Frame) -> Vec<u8> {
    let mut bytes = Vec::with_capacity(4 + frame.metadata.len() + frame.payload.len());
    bytes.extend_from_slice(&(frame.metadata.len() as u32).to_be_bytes());
    bytes.extend_from_slice(&frame.metadata);
    bytes.extend_from_slice(&frame.payload);
    bytes
}

/// Refuse oversized, truncated and trailing bytes before admitting a full frame.
pub fn decode(bytes: &[u8]) -> Result<Frame> {
    let header: [u8; 4] = bytes
        .get(..4)
        .ok_or(StreamError::InvalidFrame)?
        .try_into()
        .map_err(|_| StreamError::InvalidFrame)?;
    let size = u32::from_be_bytes(header) as usize;
    if !(2..=MAX_METADATA_BYTES).contains(&size) {
        return Err(StreamError::HeaderBound);
    }
    let source = std::str::from_utf8(bytes.get(4..4 + size).ok_or(StreamError::InvalidFrame)?)
        .map_err(|_| StreamError::InvalidFrame)?;
    let (_, metadata) = checked_metadata(source)?;
    let body_size = metadata["payload_size"]
        .as_u64()
        .ok_or(StreamError::InvalidFrame)? as usize;
    if bytes.len() != 4 + size + body_size {
        return Err(StreamError::InvalidFrame);
    }
    Frame::from_json(source, bytes[4 + size..].to_vec())
}

/// Bound both lengths before allocation; outer owner supplies deadline/fencing.
pub async fn read(reader: &mut (impl AsyncRead + Unpin)) -> Result<Frame> {
    let size = reader
        .read_u32()
        .await
        .map_err(|_| StreamError::Transport)? as usize;
    if !(2..=MAX_METADATA_BYTES).contains(&size) {
        return Err(StreamError::HeaderBound);
    }
    let mut header = vec![0; size];
    reader
        .read_exact(&mut header)
        .await
        .map_err(|_| StreamError::Transport)?;
    let source = std::str::from_utf8(&header).map_err(|_| StreamError::InvalidFrame)?;
    let (_, metadata) = checked_metadata(source)?;
    let body_size = metadata["payload_size"]
        .as_u64()
        .ok_or(StreamError::InvalidFrame)? as usize;
    let mut body = vec![0; body_size];
    reader
        .read_exact(&mut body)
        .await
        .map_err(|_| StreamError::Transport)?;
    Frame::from_json(source, body)
}

/// Write through backpressure; interrupted writes must poison the owning channel.
pub async fn write(writer: &mut (impl AsyncWrite + Unpin), frame: &Frame) -> Result<()> {
    writer
        .write_all(&encode(frame))
        .await
        .map_err(|_| StreamError::Transport)
}
