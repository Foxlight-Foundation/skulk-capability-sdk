//! Thin conversions for the shared parent-bound discovery codec.
use super::{portable_text, stream_error};
use napi::{
    Result,
    bindgen_prelude::{Buffer, Utf16String},
};
use napi_derive::napi;
use skulk_capability_kernel::discovery;
#[napi]
pub fn discovery_profile_digest() -> &'static str {
    discovery::profile_digest()
}
#[napi]
pub fn discovery_socket(source: Utf16String) -> Result<String> {
    discovery::socket(&portable_text(source)?).map_err(stream_error)
}
#[napi]
pub fn discovery_payload_size(source: Utf16String) -> Result<u32> {
    discovery::payload_size(&portable_text(source)?)
        .map(|s| s as u32)
        .map_err(stream_error)
}
#[napi]
pub fn discovery_request(
    startup: Utf16String,
    context: Utf16String,
    parent: Utf16String,
    sequence: Utf16String,
) -> Result<Buffer> {
    let sequence = portable_text(sequence)?
        .parse::<u64>()
        .map_err(|_| stream_error(skulk_capability_kernel::streams::StreamError::InvalidFrame))?;
    discovery::request(
        &portable_text(startup)?,
        &portable_text(context)?,
        &portable_text(parent)?,
        sequence,
    )
    .map(|f| Buffer::from(discovery::encode(&f)))
    .map_err(stream_error)
}
#[napi(object)]
pub struct DiscoveryFrame {
    pub metadata: Buffer,
    pub payload: Buffer,
}
#[napi]
pub fn decode_discovery_frame(packet: Buffer) -> Result<DiscoveryFrame> {
    let f = discovery::decode(&packet).map_err(stream_error)?;
    Ok(DiscoveryFrame {
        metadata: Buffer::from(f.metadata()),
        payload: Buffer::from(f.payload()),
    })
}
#[napi]
pub fn admit_discovery_request(startup: Utf16String, packet: Buffer) -> Result<Buffer> {
    discovery::admit(&portable_text(startup)?, &packet)
        .map(Buffer::from)
        .map_err(stream_error)
}
#[napi]
pub fn discovery_reply(
    startup: Utf16String,
    request: Utf16String,
    payload: Buffer,
    error: Option<String>,
) -> Result<Buffer> {
    if payload.len() > discovery::MAX_PAYLOAD_BYTES {
        return Err(stream_error(
            skulk_capability_kernel::streams::StreamError::HeaderBound,
        ));
    }
    discovery::reply(
        &portable_text(startup)?,
        &portable_text(request)?,
        payload.to_vec(),
        error.as_deref(),
    )
    .map(|f| Buffer::from(discovery::encode(&f)))
    .map_err(stream_error)
}
#[napi(object)]
pub struct DiscoveryReply {
    pub error: Option<String>,
    pub payload: Buffer,
}
#[napi]
pub fn discovery_response(
    startup: Utf16String,
    request: Utf16String,
    packet: Buffer,
) -> Result<DiscoveryReply> {
    let (error, payload) =
        discovery::response(&portable_text(startup)?, &portable_text(request)?, &packet)
            .map_err(stream_error)?;
    Ok(DiscoveryReply {
        error,
        payload: Buffer::from(payload),
    })
}
