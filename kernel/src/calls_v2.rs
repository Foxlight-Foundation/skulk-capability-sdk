//! Explicit inference calls; structural frames grant no caller authority.
use crate::{
    calls::{self, CallsProfile},
    streams::StreamError,
};
use tokio::io::{AsyncRead, AsyncWrite};

/// Independently identified call cohort with nullable execution deadlines.
pub const PROFILE: &str = "skulk.calls/2";
pub use crate::calls::{MAX_METADATA_BYTES, MAX_PAYLOAD_BYTES};
type Result<T> = std::result::Result<T, StreamError>;

/// Fingerprint every normative schema and rule, never request content.
pub fn profile_digest() -> &'static str {
    calls::profile_digest_for(CallsProfile::Execution)
}
/// Validate metadata before allocation; active host authority remains mandatory.
pub fn payload_size(source: &str) -> Result<usize> {
    calls::payload_size_for(CallsProfile::Execution, source)
}
/// Native-admitted frame without diagnostic exposure of caller or body data.
pub struct Frame {
    pub(crate) inner: calls::Frame,
}
impl Frame {
    /// Bind exact raw payload bytes to the new context-bearing metadata.
    pub fn from_json(source: &str, payload: Vec<u8>) -> Result<Self> {
        calls::Frame::from_profile(CallsProfile::Execution, source, payload)
            .map(|inner| Self { inner })
    }
    /// Explicitly expose canonical metadata for protected host-owned IPC.
    pub fn metadata(&self) -> &[u8] {
        self.inner.metadata()
    }
    /// Explicitly expose exact body bytes without reserialization.
    pub fn payload(&self) -> &[u8] {
        self.inner.payload()
    }
}
/// Encode an admitted frame without changing its body identity.
pub fn encode(frame: &Frame) -> Vec<u8> {
    calls::encode(&frame.inner)
}
/// Refuse mixed, truncated, oversized or trailing frames without downgrade.
pub fn decode(bytes: &[u8]) -> Result<Frame> {
    calls::decode_for(CallsProfile::Execution, bytes).map(|inner| Frame { inner })
}
/// Bound partial I/O independently from the host-admitted overall call lifetime.
pub async fn read(reader: &mut (impl AsyncRead + Unpin)) -> Result<Frame> {
    calls::read_for(CallsProfile::Execution, reader)
        .await
        .map(|inner| Frame { inner })
}
/// Await bounded write backpressure; interrupted writes must fence the channel.
pub async fn write(writer: &mut (impl AsyncWrite + Unpin), frame: &Frame) -> Result<()> {
    calls::write_for(CallsProfile::Execution, writer, &frame.inner).await
}
