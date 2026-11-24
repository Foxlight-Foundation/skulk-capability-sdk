//! Explicit protocol-five control and binary framing, independent of host authority.

use std::sync::OnceLock;

use serde_json::{Value, json};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

use crate::{
    codec, handshake, managed, portable_json,
    streams::{Packet, StreamError},
};

/// Independent wire semantics; frozen descriptor/startup/hello profiles stay intact.
pub const PROFILE: &str = "skulk.transport/1";
/// Fixed per-record resource ceiling; not a total inference-output allowance.
pub const MAX_RECORD_BYTES: usize = 65_536;
type Result<T> = std::result::Result<T, StreamError>;

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum TransportProfile {
    Original,
    Execution,
}
impl TransportProfile {
    fn identity(self) -> &'static str {
        match self {
            Self::Original => PROFILE,
            Self::Execution => crate::transport_v2::PROFILE,
        }
    }
    fn protocol(self) -> u64 {
        match self {
            Self::Original => 5,
            Self::Execution => 6,
        }
    }
    fn source(self) -> &'static str {
        match self {
            Self::Original => include_str!("../contracts/transport-v1.json"),
            Self::Execution => include_str!("../contracts/transport-v2.json"),
        }
    }
    fn handshake(self) -> crate::handshake::HandshakeProfile {
        match self {
            Self::Original => crate::handshake::HandshakeProfile::Legacy,
            Self::Execution => crate::handshake::HandshakeProfile::Inference,
        }
    }
    fn calls(self) -> crate::calls::CallsProfile {
        match self {
            Self::Original => crate::calls::CallsProfile::Original,
            Self::Execution => crate::calls::CallsProfile::Execution,
        }
    }
}
fn specification(profile: TransportProfile) -> &'static Value {
    static ORIGINAL: OnceLock<Value> = OnceLock::new();
    static EXECUTION: OnceLock<Value> = OnceLock::new();
    let cell = match profile {
        TransportProfile::Original => &ORIGINAL,
        TransportProfile::Execution => &EXECUTION,
    };
    cell.get_or_init(|| {
        let spec: Value = serde_json::from_str(profile.source()).expect("wire specification");
        if profile == TransportProfile::Execution {
            assert_eq!(
                spec["hello_profile_digest"],
                crate::handshake_v2::profile_digest()
            );
        }
        spec
    })
}
fn canonical(source: &str, limit: usize) -> Result<Vec<u8>> {
    let value = portable_json::parse(source, limit).map_err(|_| StreamError::InvalidFrame)?;
    let bytes = portable_json::encode(&value).map_err(|_| StreamError::InvalidFrame)?;
    if bytes.len() > limit {
        return Err(StreamError::HeaderBound);
    }
    Ok(bytes)
}
/// Fingerprint the complete normative wire semantics, never message content.
pub fn profile_digest() -> &'static str {
    profile_digest_for(TransportProfile::Original)
}
pub(crate) fn profile_digest_for(profile: TransportProfile) -> &'static str {
    static ORIGINAL: OnceLock<String> = OnceLock::new();
    static EXECUTION: OnceLock<String> = OnceLock::new();
    let cell = match profile {
        TransportProfile::Original => &ORIGINAL,
        TransportProfile::Execution => &EXECUTION,
    };
    cell.get_or_init(|| {
        let mut bytes = b"skulk.transport.profile\0".to_vec();
        bytes.extend(canonical(profile.source(), 262_144).expect("bounded wire specification"));
        codec::sha256(&bytes)
    })
}
fn validator(profile: TransportProfile, name: &str) -> &'static jsonschema::Validator {
    static ORIGINAL: [OnceLock<jsonschema::Validator>; 3] = [const { OnceLock::new() }; 3];
    static EXECUTION: [OnceLock<jsonschema::Validator>; 3] = [const { OnceLock::new() }; 3];
    let index = match name {
        "control" => 0,
        "packet" => 1,
        "header" => 2,
        _ => unreachable!(),
    };
    let cell = match profile {
        TransportProfile::Original => &ORIGINAL[index],
        TransportProfile::Execution => &EXECUTION[index],
    };
    cell.get_or_init(|| {
        let spec=specification(profile);
        let schema=if name=="control" {json!({"oneOf":spec["controls"].as_object().expect("control kinds").values().collect::<Vec<_>>()})} else {spec[name].clone()};
        jsonschema::options().with_draft(jsonschema::Draft::Draft202012).offline().should_validate_formats(false)
            .with_pattern_options(jsonschema::PatternOptions::regex()).build(&schema).expect("closed wire schema")
    })
}
fn identified(profile: TransportProfile, value: &Value) -> Result<()> {
    if value["protocol"].as_u64() != Some(profile.protocol())
        || value["contract_profile"] != profile.identity()
        || value["profile_digest"] != profile_digest_for(profile)
    {
        return Err(StreamError::InvalidFrame);
    }
    Ok(())
}

/// Immutable admitted control record; intentionally no Debug or content digest.
pub struct Control {
    profile: TransportProfile,
    canonical: Vec<u8>,
}
impl Control {
    /// Validate one closed protocol-five record; host destination/authority checks remain separate.
    pub fn from_json(source: &str) -> Result<Self> {
        Self::from_profile(TransportProfile::Original, source)
    }
    pub(crate) fn from_profile(profile: TransportProfile, source: &str) -> Result<Self> {
        let canonical = canonical(source, MAX_RECORD_BYTES)?;
        let value: Value =
            serde_json::from_slice(&canonical).map_err(|_| StreamError::InvalidFrame)?;
        identified(profile, &value)?;
        if !validator(profile, "control").is_valid(&value) {
            return Err(StreamError::InvalidFrame);
        }
        if value["kind"] == "result" && value["payload"].is_null() == value["error"].is_null() {
            return Err(StreamError::InvalidFrame);
        }
        if value["kind"] == "health" {
            let mut identities = std::collections::BTreeSet::new();
            for surface in value["surfaces"]
                .as_array()
                .ok_or(StreamError::InvalidFrame)?
            {
                if !identities.insert(
                    surface["surface_id"]
                        .as_str()
                        .ok_or(StreamError::InvalidFrame)?,
                ) || (!surface["port"].is_null() && !surface["url"].is_null())
                    || (surface["ready"] == true
                        && surface["port"].is_null()
                        && surface["url"].is_null())
                    || surface["url"].as_str().is_some_and(|url| {
                        !url.starts_with("https://")
                            || url.len() <= 8
                            || url.chars().any(char::is_whitespace)
                    })
                {
                    return Err(StreamError::InvalidFrame);
                }
            }
        }
        Ok(Self { profile, canonical })
    }
    /// Borrow immutable canonical bytes; bindings return an owned copy.
    pub fn canonical(&self) -> &[u8] {
        &self.canonical
    }
}
fn frame(bytes: &[u8]) -> Vec<u8> {
    let mut wire = Vec::with_capacity(4 + bytes.len());
    wire.extend_from_slice(&(bytes.len() as u32).to_be_bytes());
    wire.extend_from_slice(bytes);
    wire
}
fn bounded_body(bytes: &[u8], limit: usize) -> Result<&[u8]> {
    let prefix: [u8; 4] = bytes
        .get(..4)
        .ok_or(StreamError::InvalidFrame)?
        .try_into()
        .map_err(|_| StreamError::InvalidFrame)?;
    let size = u32::from_be_bytes(prefix) as usize;
    if !(2..=limit).contains(&size) {
        return Err(StreamError::HeaderBound);
    }
    if bytes.len() != 4 + size {
        return Err(StreamError::InvalidFrame);
    }
    Ok(&bytes[4..])
}
/// Encode one validated control without changing its canonical identity.
pub fn encode_control(control: &Control) -> Vec<u8> {
    frame(control.canonical())
}
/// Refuse truncated/trailing/oversized control bytes before interpreting content.
pub fn decode_control(bytes: &[u8]) -> Result<Control> {
    decode_control_for(TransportProfile::Original, bytes)
}
pub(crate) fn decode_control_for(profile: TransportProfile, bytes: &[u8]) -> Result<Control> {
    Control::from_profile(
        profile,
        std::str::from_utf8(bounded_body(bytes, MAX_RECORD_BYTES)?)
            .map_err(|_| StreamError::InvalidFrame)?,
    )
}
async fn read_body_inner(
    reader: &mut (impl AsyncRead + Unpin),
    limit: usize,
    first: Option<u8>,
) -> Result<Vec<u8>> {
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
    if !(2..=limit).contains(&size) {
        return Err(StreamError::HeaderBound);
    }
    let mut bytes = vec![0; size];
    reader
        .read_exact(&mut bytes)
        .await
        .map_err(|_| StreamError::Transport)?;
    Ok(bytes)
}
async fn read_body_for(
    profile: TransportProfile,
    reader: &mut (impl AsyncRead + Unpin),
    limit: usize,
) -> Result<Vec<u8>> {
    if profile == TransportProfile::Execution {
        let first = reader.read_u8().await.map_err(|_| StreamError::Transport)?;
        tokio::time::timeout(
            std::time::Duration::from_secs(30),
            read_body_inner(reader, limit, Some(first)),
        )
        .await
        .map_err(|_| StreamError::Timeout)?
    } else {
        read_body_inner(reader, limit, None).await
    }
}
/// Read and admit one control; the owning channel supplies deadline/cancellation fencing.
pub async fn read_control(reader: &mut (impl AsyncRead + Unpin)) -> Result<Control> {
    read_control_for(TransportProfile::Original, reader).await
}
pub(crate) async fn read_control_for(
    profile: TransportProfile,
    reader: &mut (impl AsyncRead + Unpin),
) -> Result<Control> {
    let bytes = read_body_for(profile, reader, MAX_RECORD_BYTES).await?;
    Control::from_profile(
        profile,
        std::str::from_utf8(&bytes).map_err(|_| StreamError::InvalidFrame)?,
    )
}
/// Write through real backpressure; an interrupted write makes the channel uncertain.
pub async fn write_control(
    writer: &mut (impl AsyncWrite + Unpin),
    control: &Control,
) -> Result<()> {
    write_control_for(TransportProfile::Original, writer, control).await
}
pub(crate) async fn write_control_for(
    profile: TransportProfile,
    writer: &mut (impl AsyncWrite + Unpin),
    control: &Control,
) -> Result<()> {
    if control.profile != profile {
        return Err(StreamError::InvalidFrame);
    }
    let bytes = encode_control(control);
    let write = async {
        writer
            .write_all(&bytes)
            .await
            .map_err(|_| StreamError::Transport)?;
        writer.flush().await.map_err(|_| StreamError::Transport)
    };
    if profile == TransportProfile::Execution {
        tokio::time::timeout(std::time::Duration::from_secs(30), write)
            .await
            .map_err(|_| StreamError::Timeout)?
    } else {
        write.await
    }
}
/// Admit the first hello against the host's private expected startup and admitted call.
/// This neither consumes host nonces nor grants execution authority.
pub async fn read_hello(
    reader: &mut (impl AsyncRead + Unpin),
    startup: &managed::Record,
    call_id: Option<&str>,
) -> Result<handshake::Record> {
    read_hello_for(TransportProfile::Original, reader, startup, call_id).await
}
pub(crate) async fn read_hello_for(
    profile: TransportProfile,
    reader: &mut (impl AsyncRead + Unpin),
    startup: &managed::Record,
    call_id: Option<&str>,
) -> Result<handshake::Record> {
    let bytes = read_body_for(profile, reader, 16_384).await?;
    handshake::admit_for(
        profile.handshake(),
        startup,
        std::str::from_utf8(&bytes).map_err(|_| StreamError::InvalidFrame)?,
        call_id,
    )
    .map_err(|_| StreamError::InvalidFrame)
}
/// Frame a native-admitted hello; it remains credential-bearing protected IPC.
pub fn encode_hello(hello: &handshake::Record) -> Vec<u8> {
    frame(hello.canonical())
}

/// Admit portable scalar JSON and closed fields before sharing the established lifecycle.
pub fn packet(projection: &str, media: Vec<u8>) -> Result<Packet> {
    packet_for(TransportProfile::Original, projection, media)
}
pub(crate) fn packet_for(
    profile: TransportProfile,
    projection: &str,
    media: Vec<u8>,
) -> Result<Packet> {
    let canonical = canonical(projection, MAX_RECORD_BYTES)?;
    let value: Value = serde_json::from_slice(&canonical).map_err(|_| StreamError::InvalidFrame)?;
    if !validator(profile, "packet").is_valid(&value) {
        return Err(StreamError::InvalidFrame);
    }
    Packet::from_projection(
        std::str::from_utf8(&canonical).map_err(|_| StreamError::InvalidFrame)?,
        media,
    )
}
/// Export portable canonical projection bytes, preserving the one-number-kind rule.
pub fn projection(packet: &Packet) -> Result<String> {
    String::from_utf8(canonical(&packet.projection_json()?, MAX_RECORD_BYTES)?)
        .map_err(|_| StreamError::InvalidFrame)
}
/// Encode an explicitly fingerprinted stream header followed by bounded raw media.
pub fn encode_packet(value: &Packet) -> Result<Vec<u8>> {
    encode_packet_for(TransportProfile::Original, value)
}
pub(crate) fn encode_packet_for(profile: TransportProfile, value: &Packet) -> Result<Vec<u8>> {
    let projection = projection(value)?;
    let admitted = packet_for(profile, &projection, vec![])?;
    // Validate binary consistency separately without copying the attachment.
    let metadata: Value =
        serde_json::from_str(&projection).map_err(|_| StreamError::InvalidFrame)?;
    if value.media.len() > crate::streams::MAX_MEDIA_BYTES {
        return Err(StreamError::MediaBound);
    }
    if metadata["media"]["kind"] != "inline" && !value.media.is_empty() {
        return Err(StreamError::InvalidFrame);
    }
    drop(admitted);
    let header = json!({"contract_profile":profile.identity(),"profile_digest":profile_digest_for(profile),"protocol":profile.protocol(),"frame":metadata,"media_size":value.media.len()});
    let header = canonical(&header.to_string(), MAX_RECORD_BYTES)?;
    let mut bytes = frame(&header);
    bytes.extend_from_slice(&value.media);
    Ok(bytes)
}
pub(crate) fn header(profile: TransportProfile, bytes: &[u8]) -> Result<(String, usize)> {
    let canonical = canonical(
        std::str::from_utf8(bytes).map_err(|_| StreamError::InvalidFrame)?,
        MAX_RECORD_BYTES,
    )?;
    let value: Value = serde_json::from_slice(&canonical).map_err(|_| StreamError::InvalidFrame)?;
    identified(profile, &value)?;
    if !validator(profile, "header").is_valid(&value) {
        return Err(StreamError::InvalidFrame);
    }
    let size = value["media_size"]
        .as_u64()
        .ok_or(StreamError::InvalidFrame)? as usize;
    let projection = value["frame"].to_string();
    packet_for(profile, &projection, vec![])?;
    if size != 0 && value["frame"]["media"]["kind"] != "inline" {
        return Err(StreamError::InvalidFrame);
    }
    Ok((projection, size))
}
/// Validate the complete header before allocating/copying any attachment.
pub fn decode_packet(bytes: &[u8]) -> Result<Packet> {
    decode_packet_for(TransportProfile::Original, bytes)
}
pub(crate) fn decode_packet_for(profile: TransportProfile, bytes: &[u8]) -> Result<Packet> {
    let prefix: [u8; 4] = bytes
        .get(..4)
        .ok_or(StreamError::InvalidFrame)?
        .try_into()
        .map_err(|_| StreamError::InvalidFrame)?;
    let size = u32::from_be_bytes(prefix) as usize;
    if !(2..=MAX_RECORD_BYTES).contains(&size) {
        return Err(StreamError::HeaderBound);
    }
    let raw = bytes.get(4..4 + size).ok_or(StreamError::InvalidFrame)?;
    let (projection, length) = header(profile, raw)?;
    if bytes.len() != 4 + size + length {
        return Err(StreamError::InvalidFrame);
    }
    packet_for(profile, &projection, bytes[4 + size..].to_vec())
}
/// Read a bounded header and admit it before allocating the bounded binary payload.
pub async fn read_packet(reader: &mut (impl AsyncRead + Unpin)) -> Result<Packet> {
    read_packet_for(TransportProfile::Original, reader).await
}
pub(crate) async fn read_packet_for(
    profile: TransportProfile,
    reader: &mut (impl AsyncRead + Unpin),
) -> Result<Packet> {
    if profile == TransportProfile::Execution {
        let first = reader.read_u8().await.map_err(|_| StreamError::Transport)?;
        tokio::time::timeout(
            std::time::Duration::from_secs(30),
            read_packet_inner(profile, reader, Some(first)),
        )
        .await
        .map_err(|_| StreamError::Timeout)?
    } else {
        read_packet_inner(profile, reader, None).await
    }
}
async fn read_packet_inner(
    profile: TransportProfile,
    reader: &mut (impl AsyncRead + Unpin),
    first: Option<u8>,
) -> Result<Packet> {
    let raw = read_body_inner(reader, MAX_RECORD_BYTES, first).await?;
    let (projection, size) = header(profile, &raw)?;
    let mut media = vec![0; size];
    reader
        .read_exact(&mut media)
        .await
        .map_err(|_| StreamError::Transport)?;
    packet_for(profile, &projection, media)
}
/// Await bounded per-frame backpressure without inventing an invocation deadline.
pub async fn write_packet(writer: &mut (impl AsyncWrite + Unpin), packet: &Packet) -> Result<()> {
    write_packet_for(TransportProfile::Original, writer, packet).await
}
pub(crate) async fn write_packet_for(
    profile: TransportProfile,
    writer: &mut (impl AsyncWrite + Unpin),
    packet: &Packet,
) -> Result<()> {
    let bytes = encode_packet_for(profile, packet)?;
    let write = async {
        writer
            .write_all(&bytes)
            .await
            .map_err(|_| StreamError::Transport)?;
        writer.flush().await.map_err(|_| StreamError::Transport)
    };
    if profile == TransportProfile::Execution {
        tokio::time::timeout(std::time::Duration::from_secs(30), write)
            .await
            .map_err(|_| StreamError::Timeout)?
    } else {
        write.await
    }
}

use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
};
use tokio::{
    net::{
        UnixStream,
        unix::{OwnedReadHalf, OwnedWriteHalf},
    },
    sync::{Mutex as AsyncMutex, watch},
    task::JoinHandle,
    time::{Instant, timeout_at},
};

struct ChannelState {
    closed: AtomicBool,
    failures: watch::Sender<bool>,
    reader: AsyncMutex<OwnedReadHalf>,
    writer: AsyncMutex<OwnedWriteHalf>,
    budget: std::time::Duration,
    activity: watch::Sender<Instant>,
}
impl ChannelState {
    fn alive(&self) -> Result<()> {
        if self.closed.load(Ordering::Acquire) {
            Err(StreamError::Closed)
        } else {
            Ok(())
        }
    }
    fn fail(&self) {
        self.closed.store(true, Ordering::Release);
        self.failures.send_replace(true);
    }
}
struct ChannelFence<'a> {
    state: &'a ChannelState,
    completed: bool,
}
impl Drop for ChannelFence<'_> {
    fn drop(&mut self) {
        if !self.completed {
            self.state.fail();
        }
    }
}

/// One protected native control channel. Any interrupted/invalid I/O fences the
/// whole channel; reconnect is explicit and never replays an outstanding request.
pub struct Channel {
    profile: TransportProfile,
    state: Arc<ChannelState>,
    timer: Mutex<Option<JoinHandle<()>>>,
}
impl Channel {
    /// Connect only to the admitted startup endpoint and emit its exact hello.
    /// Seconds bounds connect/hello, each later operation and idle time; successful
    /// records renew idle time so healthy long-running children stay connected.
    /// Admission of startup origin, current trust and one-use is host-owned.
    pub async fn connect(startup: &managed::Record, seconds: f64) -> Result<Self> {
        Self::connect_for(TransportProfile::Original, startup, seconds).await
    }
    pub(crate) async fn connect_for(
        profile: TransportProfile,
        startup: &managed::Record,
        seconds: f64,
    ) -> Result<Self> {
        if !seconds.is_finite() || seconds <= 0.0 || seconds > 300.0 {
            return Err(StreamError::InvalidFrame);
        }
        let hello = handshake::from_startup_for(profile.handshake(), startup, None)
            .map_err(|_| StreamError::InvalidFrame)?;
        let value: Value =
            serde_json::from_slice(startup.canonical()).map_err(|_| StreamError::InvalidFrame)?;
        let socket = value["socket"].as_str().ok_or(StreamError::InvalidFrame)?;
        let deadline = Instant::now() + std::time::Duration::from_secs_f64(seconds);
        let mut stream = timeout_at(deadline, UnixStream::connect(socket))
            .await
            .map_err(|_| StreamError::Timeout)?
            .map_err(|_| StreamError::Transport)?;
        timeout_at(deadline, stream.write_all(&encode_hello(&hello)))
            .await
            .map_err(|_| StreamError::Timeout)?
            .map_err(|_| StreamError::Transport)?;
        Ok(Self::attached(
            profile,
            stream,
            std::time::Duration::from_secs_f64(seconds),
        ))
    }
    /// Accept a host-owned socket after binding its hello to the host's expected
    /// startup. The caller must atomically consume its own one-use channel grant.
    pub async fn accept(
        stream: UnixStream,
        startup: &managed::Record,
        seconds: f64,
    ) -> Result<Self> {
        Self::accept_for(TransportProfile::Original, stream, startup, seconds).await
    }
    pub(crate) async fn accept_for(
        profile: TransportProfile,
        mut stream: UnixStream,
        startup: &managed::Record,
        seconds: f64,
    ) -> Result<Self> {
        if !seconds.is_finite() || seconds <= 0.0 || seconds > 300.0 {
            return Err(StreamError::InvalidFrame);
        }
        let deadline = Instant::now() + std::time::Duration::from_secs_f64(seconds);
        timeout_at(
            deadline,
            read_hello_for(profile, &mut stream, startup, None),
        )
        .await
        .map_err(|_| StreamError::Timeout)??;
        Ok(Self::attached(
            profile,
            stream,
            std::time::Duration::from_secs_f64(seconds),
        ))
    }
    fn attached(
        profile: TransportProfile,
        stream: UnixStream,
        budget: std::time::Duration,
    ) -> Self {
        let (reader, writer) = stream.into_split();
        let (failures, mut failure) = watch::channel(false);
        let (activity, mut last_activity) = watch::channel(Instant::now());
        let state = Arc::new(ChannelState {
            closed: AtomicBool::new(false),
            failures,
            reader: AsyncMutex::new(reader),
            writer: AsyncMutex::new(writer),
            budget,
            activity,
        });
        let expiry = state.clone();
        let timer = tokio::spawn(async move {
            loop {
                let idle_deadline = *last_activity.borrow_and_update() + budget;
                tokio::select! { biased;
                    _=failure.changed()=>break,
                    changed=last_activity.changed()=>{if changed.is_err() {break;}},
                    _=tokio::time::sleep_until(idle_deadline)=>break,
                }
            }
            expiry.fail();
            let _ = expiry.writer.lock().await.shutdown().await;
        });
        Self {
            profile,
            state,
            timer: Mutex::new(Some(timer)),
        }
    }
    /// Read exactly one bounded control; cancellation/EOF/timeout invalidates all
    /// later I/O. Lock admission is included in each operation deadline.
    pub async fn receive(&self) -> Result<Control> {
        let mut fence = ChannelFence {
            state: &self.state,
            completed: false,
        };
        let mut failure = self.state.failures.subscribe();
        self.state.alive()?;
        let result = tokio::select! {biased;
            _=failure.changed()=>Err(StreamError::Closed),
            result=timeout_at(Instant::now()+self.state.budget,async {
                let mut reader=self.state.reader.lock().await;
                self.state.alive()?;
                read_control_for(self.profile, &mut *reader).await
            })=>result.map_err(|_|StreamError::Timeout)?,
        };
        if result.is_err() {
            self.state.fail();
        } else {
            self.state.activity.send_replace(Instant::now());
        }
        fence.completed = true;
        result
    }
    /// Await bounded socket backpressure; an interrupted write is never retried.
    pub async fn send(&self, control: &Control) -> Result<()> {
        let mut fence = ChannelFence {
            state: &self.state,
            completed: false,
        };
        let mut failure = self.state.failures.subscribe();
        self.state.alive()?;
        let result = tokio::select! {biased;
            _=failure.changed()=>Err(StreamError::Closed),
            result=timeout_at(Instant::now()+self.state.budget,async {
                let mut writer=self.state.writer.lock().await;
                self.state.alive()?;
                write_control_for(self.profile, &mut *writer,control).await
            })=>result.map_err(|_|StreamError::Timeout)?,
        };
        if result.is_err() {
            self.state.fail();
        } else {
            self.state.activity.send_replace(Instant::now());
        }
        fence.completed = true;
        result
    }
    /// Read only the explicitly selected calls profile with the same I/O fences.
    pub async fn receive_call(&self) -> Result<crate::calls::Frame> {
        let mut fence = ChannelFence {
            state: &self.state,
            completed: false,
        };
        let mut failure = self.state.failures.subscribe();
        self.state.alive()?;
        let result = tokio::select! {biased;
            _=failure.changed()=>Err(StreamError::Closed),
            result=timeout_at(Instant::now()+self.state.budget,async {
                let mut reader=self.state.reader.lock().await;
                self.state.alive()?;
                crate::calls::read_for(self.profile.calls(), &mut *reader).await
            })=>result.map_err(|_|StreamError::Timeout)?,
        };
        if result.is_err() {
            self.state.fail();
        } else {
            self.state.activity.send_replace(Instant::now());
        }
        fence.completed = true;
        result
    }
    /// Send only the explicitly selected calls profile; never downgrade a frame.
    pub async fn send_call(&self, frame: &crate::calls::Frame) -> Result<()> {
        if frame.profile() != self.profile.calls() {
            return Err(StreamError::InvalidFrame);
        }
        let mut fence = ChannelFence {
            state: &self.state,
            completed: false,
        };
        let mut failure = self.state.failures.subscribe();
        self.state.alive()?;
        let result = tokio::select! {biased;
            _=failure.changed()=>Err(StreamError::Closed),
            result=timeout_at(Instant::now()+self.state.budget,async {
                let mut writer=self.state.writer.lock().await;
                self.state.alive()?;
                crate::calls::write_for(self.profile.calls(), &mut *writer,frame).await
            })=>result.map_err(|_|StreamError::Timeout)?,
        };
        if result.is_err() {
            self.state.fail();
        } else {
            self.state.activity.send_replace(Instant::now());
        }
        fence.completed = true;
        result
    }
    /// Fence pending operations, shut down the socket and await the owned timer.
    pub async fn close(&self) {
        self.state.fail();
        let task = self.timer.lock().ok().and_then(|mut timer| timer.take());
        if let Some(task) = task {
            let _ = task.await;
        }
        let _ = self.state.writer.lock().await.shutdown().await;
    }
}
impl Drop for Channel {
    fn drop(&mut self) {
        self.state.fail();
        if let Ok(timer) = self.timer.get_mut()
            && let Some(task) = timer.take()
        {
            task.abort();
        }
    }
}
