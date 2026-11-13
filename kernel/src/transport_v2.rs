//! Explicit protocol-six framing; call lifetime is separate from partial I/O.
use crate::{
    calls_v2, handshake_v2, managed_v2,
    streams::{Packet, StreamError},
    transport::{self, TransportProfile},
};
use tokio::{
    io::{AsyncRead, AsyncWrite},
    net::UnixStream,
};

/// Independently selected framing for the inference-capable managed cohort.
pub const PROFILE: &str = "skulk.transport/2";
pub use crate::transport::MAX_RECORD_BYTES;
type Result<T> = std::result::Result<T, StreamError>;
/// Identify complete normative framing/lifecycle/resource semantics.
pub fn profile_digest() -> &'static str {
    transport::profile_digest_for(TransportProfile::Execution)
}
/// Immutable health/shutdown metadata without a context-free invoke form.
pub struct Control {
    pub(crate) inner: transport::Control,
}
impl Control {
    /// Admit only explicit protocol-six control records, without host authority.
    pub fn from_json(source: &str) -> Result<Self> {
        transport::Control::from_profile(TransportProfile::Execution, source)
            .map(|inner| Self { inner })
    }
    /// Explicitly expose canonical bytes for protected IPC.
    pub fn canonical(&self) -> &[u8] {
        self.inner.canonical()
    }
}
/// Frame one admitted control without changing its identity.
pub fn encode_control(control: &Control) -> Vec<u8> {
    transport::encode_control(&control.inner)
}
/// Refuse unknown/legacy identity, bounds, truncation and trailing bytes.
pub fn decode_control(bytes: &[u8]) -> Result<Control> {
    transport::decode_control_for(TransportProfile::Execution, bytes).map(|inner| Control { inner })
}
/// Read under an independent bounded partial-frame watchdog.
pub async fn read_control(reader: &mut (impl AsyncRead + Unpin)) -> Result<Control> {
    transport::read_control_for(TransportProfile::Execution, reader)
        .await
        .map(|inner| Control { inner })
}
/// Write with bounded per-frame backpressure.
pub async fn write_control(
    writer: &mut (impl AsyncWrite + Unpin),
    control: &Control,
) -> Result<()> {
    transport::write_control_for(TransportProfile::Execution, writer, &control.inner).await
}
/// Bind private startup and actual call; the host separately consumes its grant.
pub async fn read_hello(
    reader: &mut (impl AsyncRead + Unpin),
    startup: &managed_v2::Record,
    call_id: Option<&str>,
) -> Result<handshake_v2::Record> {
    transport::read_hello_for(TransportProfile::Execution, reader, &startup.inner, call_id)
        .await
        .map(|inner| handshake_v2::Record { inner })
}
/// Frame an admitted credential-bearing hello for protected IPC.
pub fn encode_hello(hello: &handshake_v2::Record) -> Vec<u8> {
    transport::encode_hello(&hello.inner)
}
/// Admit portable projection and bounded per-frame raw media.
pub fn packet(projection: &str, media: Vec<u8>) -> Result<Packet> {
    transport::packet_for(TransportProfile::Execution, projection, media)
}
/// Export the unchanged portable JSON number/scalar projection.
pub fn projection(packet: &Packet) -> Result<String> {
    transport::projection(packet)
}
/// Encode an explicitly fingerprinted protocol-six media frame.
pub fn encode_packet(packet: &Packet) -> Result<Vec<u8>> {
    transport::encode_packet_for(TransportProfile::Execution, packet)
}
/// Validate full header/bounds before copying raw media.
pub fn decode_packet(bytes: &[u8]) -> Result<Packet> {
    transport::decode_packet_for(TransportProfile::Execution, bytes)
}
/// Allow legitimate computation silence; bound partial prefix/header/body I/O.
pub async fn read_packet(reader: &mut (impl AsyncRead + Unpin)) -> Result<Packet> {
    transport::read_packet_for(TransportProfile::Execution, reader).await
}
/// Await bounded partial I/O independently from overall inference lifetime.
pub async fn write_packet(writer: &mut (impl AsyncWrite + Unpin), packet: &Packet) -> Result<()> {
    transport::write_packet_for(TransportProfile::Execution, writer, packet).await
}

/// Protected channel, independently fenced from admitted invocation lifetime.
pub struct Channel {
    inner: transport::Channel,
}
impl Channel {
    /// Connect to the exact startup and emit its matching protocol-six hello.
    pub async fn connect(startup: &managed_v2::Record, seconds: f64) -> Result<Self> {
        transport::Channel::connect_for(TransportProfile::Execution, &startup.inner, seconds)
            .await
            .map(|inner| Self { inner })
    }
    /// Match the host's expected startup; token one-use remains host-owned.
    pub async fn accept(
        stream: UnixStream,
        startup: &managed_v2::Record,
        seconds: f64,
    ) -> Result<Self> {
        transport::Channel::accept_for(TransportProfile::Execution, stream, &startup.inner, seconds)
            .await
            .map(|inner| Self { inner })
    }
    /// Receive only health/shutdown when that separate control codec is selected.
    pub async fn receive(&self) -> Result<Control> {
        self.inner.receive().await.map(|inner| Control { inner })
    }
    /// Send only the explicit health/shutdown control codec.
    pub async fn send(&self, control: &Control) -> Result<()> {
        self.inner.send(&control.inner).await
    }
    /// Receive only calls2/composition3 metadata and exact body bytes.
    pub async fn receive_call(&self) -> Result<calls_v2::Frame> {
        self.inner
            .receive_call()
            .await
            .map(|inner| calls_v2::Frame { inner })
    }
    /// Send only the profile-bound context-bearing call codec.
    pub async fn send_call(&self, frame: &calls_v2::Frame) -> Result<()> {
        self.inner.send_call(&frame.inner).await
    }
    /// Fence pending operations and await owned channel tasks/socket teardown.
    pub async fn close(&self) {
        self.inner.close().await;
    }
}
