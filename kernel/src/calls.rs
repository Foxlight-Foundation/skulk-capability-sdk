//! Explicit composed-call framing; structural metadata never grants authority.

use std::sync::OnceLock;

use serde_json::{Value, json};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

use crate::{codec, composition, composition_v3, portable_json, streams::StreamError};

/// Independently issued call profile; existing control and media bytes stay fixed.
pub const PROFILE: &str = "skulk.calls/1";
/// Metadata is bounded separately from raw request bytes.
pub const MAX_METADATA_BYTES: usize = 262_144;
/// Per-frame resource bound, not an inference output or request-count allowance.
pub const MAX_PAYLOAD_BYTES: usize = 16_777_216;
type Result<T> = std::result::Result<T, StreamError>;

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum CallsProfile {
    Original,
    Execution,
}
impl CallsProfile {
    fn identity(self) -> &'static str {
        match self {
            Self::Original => PROFILE,
            Self::Execution => crate::calls_v2::PROFILE,
        }
    }
    fn protocol(self) -> u64 {
        match self {
            Self::Original => 1,
            Self::Execution => 2,
        }
    }
    fn source(self) -> &'static str {
        match self {
            Self::Original => include_str!("../contracts/calls-v1.json"),
            Self::Execution => include_str!("../contracts/calls-v2.json"),
        }
    }
}

fn specification(profile: CallsProfile) -> &'static Value {
    static ORIGINAL: OnceLock<Value> = OnceLock::new();
    static EXECUTION: OnceLock<Value> = OnceLock::new();
    let cell = match profile {
        CallsProfile::Original => &ORIGINAL,
        CallsProfile::Execution => &EXECUTION,
    };
    cell.get_or_init(|| {
        let spec: Value =
            serde_json::from_str(profile.source()).expect("packaged calls specification");
        if profile == CallsProfile::Execution {
            assert_eq!(
                spec["composition_profile_digest"],
                composition_v3::profile_digest()
            );
            assert_eq!(
                spec["protected_hello_profile_digest"],
                crate::handshake_v2::profile_digest()
            );
            assert_eq!(
                spec["media_profile_digest"],
                crate::transport_v2::profile_digest()
            );
        }
        spec
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
    profile_digest_for(CallsProfile::Original)
}

pub(crate) fn profile_digest_for(profile: CallsProfile) -> &'static str {
    static ORIGINAL: OnceLock<String> = OnceLock::new();
    static EXECUTION: OnceLock<String> = OnceLock::new();
    let cell = match profile {
        CallsProfile::Original => &ORIGINAL,
        CallsProfile::Execution => &EXECUTION,
    };
    cell.get_or_init(|| {
        let mut bytes = b"skulk.calls.profile\0".to_vec();
        bytes.extend(
            canonical(profile.source(), MAX_METADATA_BYTES).expect("bounded calls specification"),
        );
        codec::sha256(&bytes)
    })
}

fn validator(profile: CallsProfile) -> &'static jsonschema::Validator {
    static ORIGINAL: OnceLock<jsonschema::Validator> = OnceLock::new();
    static EXECUTION: OnceLock<jsonschema::Validator> = OnceLock::new();
    let cell = match profile {
        CallsProfile::Original => &ORIGINAL,
        CallsProfile::Execution => &EXECUTION,
    };
    cell.get_or_init(|| {
        let schema = json!({"oneOf": specification(profile)["controls"].as_object()
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

fn checked_metadata(profile: CallsProfile, source: &str) -> Result<(Vec<u8>, Value)> {
    let bytes = canonical(source, MAX_METADATA_BYTES)?;
    let value: Value = serde_json::from_slice(&bytes).map_err(|_| StreamError::InvalidFrame)?;
    if value["protocol"].as_u64() != Some(profile.protocol())
        || value["contract_profile"] != profile.identity()
        || value["profile_digest"] != profile_digest_for(profile)
        || !validator(profile).is_valid(&value)
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
        match profile {
            CallsProfile::Original => {
                composition::Record::from_json(name, &value[field].to_string()).map(drop)
            }
            CallsProfile::Execution => {
                composition_v3::Record::from_json(name, &value[field].to_string()).map(drop)
            }
        }
        .map_err(|_| StreamError::InvalidFrame)?;
        if value["kind"] == "delegate" {
            sequence(&value[field]["request_id"])?;
        }
        if profile == CallsProfile::Original
            && value["kind"] == "invoke"
            && value["mode"] == "unary"
            && value["remaining_seconds"].as_f64().unwrap_or(f64::INFINITY) > 30.0
        {
            return Err(StreamError::InvalidFrame);
        }
    }
    if profile == CallsProfile::Execution {
        let deadline = match value["kind"].as_str() {
            Some("invoke") => Some(&value["context"]["deadline_unix_ms"]),
            Some("delegate_started") => Some(&value["deadline_unix_ms"]),
            _ => None,
        };
        if deadline
            .is_some_and(|deadline| deadline.is_null() != value["remaining_seconds"].is_null())
        {
            return Err(StreamError::InvalidFrame);
        }
    }
    Ok((bytes, value))
}

/// Admit metadata before a host allocates/reads a body. This is only a resource
/// hint: complete frame admission and actual host authority remain mandatory.
pub fn payload_size(source: &str) -> Result<usize> {
    payload_size_for(CallsProfile::Original, source)
}
pub(crate) fn payload_size_for(profile: CallsProfile, source: &str) -> Result<usize> {
    let (_, value) = checked_metadata(profile, source)?;
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
    profile: CallsProfile,
    metadata: Vec<u8>,
    payload: Vec<u8>,
}

impl Frame {
    /// Admit closed metadata and the exact raw body through compiled semantics.
    /// Host connection/caller/policy/call authority is deliberately not inferred.
    pub fn from_json(source: &str, payload: Vec<u8>) -> Result<Self> {
        Self::from_profile(CallsProfile::Original, source, payload)
    }
    pub(crate) fn from_profile(
        profile: CallsProfile,
        source: &str,
        payload: Vec<u8>,
    ) -> Result<Self> {
        if payload.len() > MAX_PAYLOAD_BYTES {
            return Err(StreamError::HeaderBound);
        }
        let (metadata, value) = checked_metadata(profile, source)?;
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
                match profile {
                    CallsProfile::Original => {
                        fields.insert("protocol".into(), json!(5));
                        fields.insert("contract_profile".into(), json!(crate::transport::PROFILE));
                        fields.insert(
                            "profile_digest".into(),
                            json!(crate::transport::profile_digest()),
                        );
                        crate::transport::Control::from_json(&legacy.to_string()).map(drop)?;
                    }
                    CallsProfile::Execution => {
                        fields.insert("protocol".into(), json!(6));
                        fields.insert(
                            "contract_profile".into(),
                            json!(crate::transport_v2::PROFILE),
                        );
                        fields.insert(
                            "profile_digest".into(),
                            json!(crate::transport_v2::profile_digest()),
                        );
                        crate::transport_v2::Control::from_json(&legacy.to_string()).map(drop)?;
                    }
                }
            }
            "shutdown" | "cancel" | "delegate_started" | "delegate_finished" => {
                if !payload.is_empty() {
                    return Err(StreamError::InvalidFrame);
                }
            }
            _ => return Err(StreamError::InvalidFrame),
        }
        Ok(Self {
            profile,
            metadata,
            payload,
        })
    }

    pub(crate) fn profile(&self) -> CallsProfile {
        self.profile
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
    decode_for(CallsProfile::Original, bytes)
}
pub(crate) fn decode_for(profile: CallsProfile, bytes: &[u8]) -> Result<Frame> {
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
    let (_, metadata) = checked_metadata(profile, source)?;
    let body_size = metadata["payload_size"]
        .as_u64()
        .ok_or(StreamError::InvalidFrame)? as usize;
    if bytes.len() != 4 + size + body_size {
        return Err(StreamError::InvalidFrame);
    }
    Frame::from_profile(profile, source, bytes[4 + size..].to_vec())
}

/// Bound both lengths before allocation; outer owner supplies deadline/fencing.
pub async fn read(reader: &mut (impl AsyncRead + Unpin)) -> Result<Frame> {
    read_for(CallsProfile::Original, reader).await
}
pub(crate) async fn read_for(
    profile: CallsProfile,
    reader: &mut (impl AsyncRead + Unpin),
) -> Result<Frame> {
    if profile == CallsProfile::Execution {
        // A quiet model has no partial frame. Start this resource watchdog only
        // after input begins; the host still owns call lifetime and cancellation.
        let first = reader.read_u8().await.map_err(|_| StreamError::Transport)?;
        tokio::time::timeout(
            std::time::Duration::from_secs(30),
            read_inner(profile, reader, Some(first)),
        )
        .await
        .map_err(|_| StreamError::Timeout)?
    } else {
        read_inner(profile, reader, None).await
    }
}
async fn read_inner(
    profile: CallsProfile,
    reader: &mut (impl AsyncRead + Unpin),
    first: Option<u8>,
) -> Result<Frame> {
    let size = if let Some(first) = first {
        let mut prefix = [first, 0, 0, 0];
        reader
            .read_exact(&mut prefix[1..])
            .await
            .map_err(|_| StreamError::Transport)?;
        u32::from_be_bytes(prefix)
    } else {
        reader
            .read_u32()
            .await
            .map_err(|_| StreamError::Transport)?
    } as usize;
    if !(2..=MAX_METADATA_BYTES).contains(&size) {
        return Err(StreamError::HeaderBound);
    }
    let mut header = vec![0; size];
    reader
        .read_exact(&mut header)
        .await
        .map_err(|_| StreamError::Transport)?;
    let source = std::str::from_utf8(&header).map_err(|_| StreamError::InvalidFrame)?;
    let (_, metadata) = checked_metadata(profile, source)?;
    let body_size = metadata["payload_size"]
        .as_u64()
        .ok_or(StreamError::InvalidFrame)? as usize;
    let mut body = vec![0; body_size];
    reader
        .read_exact(&mut body)
        .await
        .map_err(|_| StreamError::Transport)?;
    Frame::from_profile(profile, source, body)
}

/// Write through backpressure; interrupted writes must poison the owning channel.
pub async fn write(writer: &mut (impl AsyncWrite + Unpin), frame: &Frame) -> Result<()> {
    write_for(CallsProfile::Original, writer, frame).await
}
pub(crate) async fn write_for(
    profile: CallsProfile,
    writer: &mut (impl AsyncWrite + Unpin),
    frame: &Frame,
) -> Result<()> {
    if frame.profile != profile {
        return Err(StreamError::InvalidFrame);
    }
    let bytes = encode(frame);
    let write = writer.write_all(&bytes);
    if profile == CallsProfile::Execution {
        tokio::time::timeout(std::time::Duration::from_secs(30), write)
            .await
            .map_err(|_| StreamError::Timeout)?
            .map_err(|_| StreamError::Transport)
    } else {
        write.await.map_err(|_| StreamError::Transport)
    }
}
