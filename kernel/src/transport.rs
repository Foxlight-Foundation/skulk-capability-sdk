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

fn specification() -> &'static Value {
    static SPEC: OnceLock<Value> = OnceLock::new();
    SPEC.get_or_init(|| {
        serde_json::from_str(include_str!("../contracts/transport-v1.json"))
            .expect("wire specification")
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
    static DIGEST: OnceLock<String> = OnceLock::new();
    DIGEST.get_or_init(|| {
        let mut bytes = b"skulk.transport.profile\0".to_vec();
        bytes.extend(
            canonical(include_str!("../contracts/transport-v1.json"), 262_144)
                .expect("bounded wire specification"),
        );
        codec::sha256(&bytes)
    })
}
fn validator(name: &str) -> &'static jsonschema::Validator {
    static CONTROL: OnceLock<jsonschema::Validator> = OnceLock::new();
    static PACKET: OnceLock<jsonschema::Validator> = OnceLock::new();
    static HEADER: OnceLock<jsonschema::Validator> = OnceLock::new();
    let cell = match name {
        "control" => &CONTROL,
        "packet" => &PACKET,
        "header" => &HEADER,
        _ => unreachable!(),
    };
    cell.get_or_init(|| {
        let spec=specification();
        let schema=if name=="control" {json!({"oneOf":spec["controls"].as_object().expect("control kinds").values().collect::<Vec<_>>()})} else {spec[name].clone()};
        jsonschema::options().with_draft(jsonschema::Draft::Draft202012).offline().should_validate_formats(false)
            .with_pattern_options(jsonschema::PatternOptions::regex()).build(&schema).expect("closed wire schema")
    })
}
fn identified(value: &Value) -> Result<()> {
    if value["protocol"].as_u64() != Some(5)
        || value["contract_profile"] != PROFILE
        || value["profile_digest"] != profile_digest()
    {
        return Err(StreamError::InvalidFrame);
    }
    Ok(())
}

/// Immutable admitted control record; intentionally no Debug or content digest.
pub struct Control {
    canonical: Vec<u8>,
}
impl Control {
    /// Validate one closed protocol-five record; host destination/authority checks remain separate.
    pub fn from_json(source: &str) -> Result<Self> {
        let canonical = canonical(source, MAX_RECORD_BYTES)?;
        let value: Value =
            serde_json::from_slice(&canonical).map_err(|_| StreamError::InvalidFrame)?;
        identified(&value)?;
        if !validator("control").is_valid(&value) {
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
        Ok(Self { canonical })
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
    Control::from_json(
        std::str::from_utf8(bounded_body(bytes, MAX_RECORD_BYTES)?)
            .map_err(|_| StreamError::InvalidFrame)?,
    )
}
async fn read_body(reader: &mut (impl AsyncRead + Unpin), limit: usize) -> Result<Vec<u8>> {
    let size = reader
        .read_u32()
        .await
        .map_err(|_| StreamError::Transport)? as usize;
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
/// Read and admit one control; the owning channel supplies deadline/cancellation fencing.
pub async fn read_control(reader: &mut (impl AsyncRead + Unpin)) -> Result<Control> {
    let bytes = read_body(reader, MAX_RECORD_BYTES).await?;
    Control::from_json(std::str::from_utf8(&bytes).map_err(|_| StreamError::InvalidFrame)?)
}
/// Write through real backpressure; an interrupted write makes the channel uncertain.
pub async fn write_control(
    writer: &mut (impl AsyncWrite + Unpin),
    control: &Control,
) -> Result<()> {
    writer
        .write_all(&encode_control(control))
        .await
        .map_err(|_| StreamError::Transport)?;
    writer.flush().await.map_err(|_| StreamError::Transport)
}
/// Admit the first hello against the host's private expected startup and admitted call.
/// This neither consumes host nonces nor grants execution authority.
pub async fn read_hello(
    reader: &mut (impl AsyncRead + Unpin),
    startup: &managed::Record,
    call_id: Option<&str>,
) -> Result<handshake::Record> {
    let bytes = read_body(reader, 16_384).await?;
    handshake::admit(
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
    let canonical = canonical(projection, MAX_RECORD_BYTES)?;
    let value: Value = serde_json::from_slice(&canonical).map_err(|_| StreamError::InvalidFrame)?;
    if !validator("packet").is_valid(&value) {
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
    let projection = projection(value)?;
    let admitted = packet(&projection, vec![])?;
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
    let header = json!({"contract_profile":PROFILE,"profile_digest":profile_digest(),"protocol":5,"frame":metadata,"media_size":value.media.len()});
    let header = canonical(&header.to_string(), MAX_RECORD_BYTES)?;
    let mut bytes = frame(&header);
    bytes.extend_from_slice(&value.media);
    Ok(bytes)
}
fn header(bytes: &[u8]) -> Result<(String, usize)> {
    let canonical = canonical(
        std::str::from_utf8(bytes).map_err(|_| StreamError::InvalidFrame)?,
        MAX_RECORD_BYTES,
    )?;
    let value: Value = serde_json::from_slice(&canonical).map_err(|_| StreamError::InvalidFrame)?;
    identified(&value)?;
    if !validator("header").is_valid(&value) {
        return Err(StreamError::InvalidFrame);
    }
    let size = value["media_size"]
        .as_u64()
        .ok_or(StreamError::InvalidFrame)? as usize;
    let projection = value["frame"].to_string();
    packet(&projection, vec![])?;
    if size != 0 && value["frame"]["media"]["kind"] != "inline" {
        return Err(StreamError::InvalidFrame);
    }
    Ok((projection, size))
}
/// Validate the complete header before allocating/copying any attachment.
pub fn decode_packet(bytes: &[u8]) -> Result<Packet> {
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
    let (projection, length) = header(raw)?;
    if bytes.len() != 4 + size + length {
        return Err(StreamError::InvalidFrame);
    }
    packet(&projection, bytes[4 + size..].to_vec())
}
/// Read a bounded header and admit it before allocating the bounded binary payload.
pub async fn read_packet(reader: &mut (impl AsyncRead + Unpin)) -> Result<Packet> {
    let raw = read_body(reader, MAX_RECORD_BYTES).await?;
    let (projection, size) = header(&raw)?;
    let mut media = vec![0; size];
    reader
        .read_exact(&mut media)
        .await
        .map_err(|_| StreamError::Transport)?;
    packet(&projection, media)
}
/// Await socket backpressure under the session's absolute deadline.
pub async fn write_packet(writer: &mut (impl AsyncWrite + Unpin), packet: &Packet) -> Result<()> {
    writer
        .write_all(&encode_packet(packet)?)
        .await
        .map_err(|_| StreamError::Transport)?;
    writer.flush().await.map_err(|_| StreamError::Transport)
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
    state: Arc<ChannelState>,
    timer: Mutex<Option<JoinHandle<()>>>,
}
impl Channel {
    /// Connect only to the admitted startup endpoint and emit its exact hello.
    /// Seconds bounds connect/hello, each later operation and idle time; successful
    /// records renew idle time so healthy long-running children stay connected.
    /// Admission of startup origin, current trust and one-use is host-owned.
    pub async fn connect(startup: &managed::Record, seconds: f64) -> Result<Self> {
        if !seconds.is_finite() || seconds <= 0.0 || seconds > 300.0 {
            return Err(StreamError::InvalidFrame);
        }
        let hello =
            handshake::from_startup(startup, None).map_err(|_| StreamError::InvalidFrame)?;
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
            stream,
            std::time::Duration::from_secs_f64(seconds),
        ))
    }
    /// Accept a host-owned socket after binding its hello to the host's expected
    /// startup. The caller must atomically consume its own one-use channel grant.
    pub async fn accept(
        mut stream: UnixStream,
        startup: &managed::Record,
        seconds: f64,
    ) -> Result<Self> {
        if !seconds.is_finite() || seconds <= 0.0 || seconds > 300.0 {
            return Err(StreamError::InvalidFrame);
        }
        let deadline = Instant::now() + std::time::Duration::from_secs_f64(seconds);
        timeout_at(deadline, read_hello(&mut stream, startup, None))
            .await
            .map_err(|_| StreamError::Timeout)??;
        Ok(Self::attached(
            stream,
            std::time::Duration::from_secs_f64(seconds),
        ))
    }
    fn attached(stream: UnixStream, budget: std::time::Duration) -> Self {
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
                read_control(&mut *reader).await
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
                write_control(&mut *writer,control).await
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
