//! Shared bounded duplex framing, sequencing and transport lifecycle.
//!
//! A terminal is held until language-owned iterator cleanup is acknowledged.
//! Input and output close independently; malformed/co-ready ingress prevents a
//! success from being emitted. An eight-frame queue applies socket backpressure.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use thiserror::Error;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
use tokio::net::UnixStream;
use tokio::net::unix::OwnedWriteHalf;
use tokio::sync::{Mutex as AsyncMutex, mpsc, watch};
use tokio::task::JoinHandle;
use tokio::time::{Instant, timeout_at};

use crate::codec::{self, Value};
use crate::{handshake, managed, transport};

/// Existing protocol-4 JSON header ceiling.
pub const MAX_HEADER_BYTES: usize = 65_536;
/// Raw media limit, independent of JSON header length.
pub const MAX_MEDIA_BYTES: usize = 1_048_576;
/// Existing per-invocation bounded ingress queue.
pub const INPUT_WINDOW: usize = 8;
type Object = BTreeMap<Vec<u32>, Value>;

/// Sanitized failures do not expose a socket path, token, payload or backend text.
#[derive(Clone, Copy, Debug, Error, PartialEq, Eq)]
pub enum StreamError {
    #[error("invalid stream frame")]
    InvalidFrame,
    #[error("stream header exceeds bound")]
    HeaderBound,
    #[error("stream media exceeds bound")]
    MediaBound,
    #[error("stream identity, sequence or terminal mismatch")]
    Sequence,
    #[error("unexpected caller input")]
    UnexpectedInput,
    #[error("input half-close carries no data")]
    HalfCloseData,
    #[error("caller terminated the stream")]
    CallerTerminated,
    #[error("client streaming produces one final result")]
    ClientChunk,
    #[error("handler omitted its output terminal")]
    MissingTerminal,
    #[error("stream transport failed")]
    Transport,
    #[error("stream deadline expired")]
    Timeout,
    #[error("stream is closed")]
    Closed,
}
type Result<T> = std::result::Result<T, StreamError>;

/// Decoded packet retains exact JSON number/string kinds; media stays binary.
#[derive(Clone, Debug, PartialEq)]
pub struct Packet {
    projection: Value,
    pub media: Vec<u8>,
}
impl Packet {
    /// Validate a language projection whose inline bytes are a separate buffer.
    pub fn from_projection(projection: &str, media: Vec<u8>) -> Result<Self> {
        if projection.len() > MAX_HEADER_BYTES {
            return Err(StreamError::HeaderBound);
        }
        let projection = codec::parse(projection).map_err(|_| StreamError::InvalidFrame)?;
        Self::from_value(projection, media)
    }
    fn from_value(mut projection: Value, media: Vec<u8>) -> Result<Self> {
        let inline = validate_projection(&projection)?;
        if let Value::Object(fields) = &mut projection {
            fields
                .entry(codec::points("kind"))
                .or_insert_with(|| Value::String(codec::points("end")));
            if string(fields, "kind")? != "end" {
                // Match model defaults and the reference Literal[False] profile.
                fields.insert(codec::points("synthetic"), Value::Bool(false));
                for key in ["payload", "media", "error"] {
                    fields.entry(codec::points(key)).or_insert(Value::Null);
                }
                if let Some(Value::Object(media)) = fields.get_mut(&codec::points("media"))
                    && string(media, "kind")? == "inline"
                {
                    for key in [
                        "codec",
                        "sample_rate",
                        "channels",
                        "timestamp_seconds",
                        "duration_seconds",
                    ] {
                        media.entry(codec::points(key)).or_insert(Value::Null);
                    }
                    for key in ["timestamp_seconds", "duration_seconds"] {
                        if let Some(Value::Integer(_)) = get(media, key) {
                            let value = numeric(media, key)?;
                            media.insert(codec::points(key), Value::Float(value));
                        }
                    }
                }
            }
        }
        if media.len() > MAX_MEDIA_BYTES {
            return Err(StreamError::MediaBound);
        }
        if !inline && !media.is_empty() {
            return Err(StreamError::InvalidFrame);
        }
        Ok(Self { projection, media })
    }
    /// JSON projection excludes binary data, as the existing Python header does.
    pub fn projection_json(&self) -> Result<String> {
        String::from_utf8(codec::encode(&self.projection).map_err(|_| StreamError::InvalidFrame)?)
            .map_err(|_| StreamError::InvalidFrame)
    }
    /// Distinguish cleanup acknowledgment from a direction's terminal frame.
    pub fn is_end(&self) -> bool {
        self.kind() == Some("end".into())
    }
    /// Terminal closes one direction; it does not release admission by itself.
    pub fn is_terminal(&self) -> bool {
        matches!(
            self.kind().as_deref(),
            Some("completed" | "failed" | "cancelled")
        )
    }
    fn fields(&self) -> Result<&Object> {
        as_object(&self.projection)
    }
    fn kind(&self) -> Option<String> {
        self.fields()
            .ok()
            .and_then(|fields| string(fields, "kind").ok())
    }
    fn has_content(&self) -> bool {
        self.fields()
            .is_ok_and(|fields| nonnull(fields, "payload") || nonnull(fields, "media"))
    }
}

fn get<'a>(object: &'a Object, key: &str) -> Option<&'a Value> {
    object.get(&codec::points(key))
}
fn as_object(value: &Value) -> Result<&Object> {
    if let Value::Object(value) = value {
        Ok(value)
    } else {
        Err(StreamError::InvalidFrame)
    }
}
fn text(points: &[u32]) -> Result<String> {
    points
        .iter()
        .map(|point| char::from_u32(*point).ok_or(StreamError::InvalidFrame))
        .collect()
}
fn string(object: &Object, key: &str) -> Result<String> {
    match get(object, key) {
        Some(Value::String(value)) => text(value),
        _ => Err(StreamError::InvalidFrame),
    }
}
fn null(value: Option<&Value>) -> bool {
    matches!(value, None | Some(Value::Null))
}
fn nonnull(object: &Object, key: &str) -> bool {
    !null(get(object, key))
}
fn fields_only(object: &Object, allowed: &[&str]) -> Result<()> {
    for key in object.keys() {
        if !allowed.contains(&text(key)?.as_str()) {
            return Err(StreamError::InvalidFrame);
        }
    }
    Ok(())
}
fn bounded_string(object: &Object, key: &str, min: usize, max: Option<usize>) -> Result<()> {
    let Some(Value::String(value)) = get(object, key) else {
        return Err(StreamError::InvalidFrame);
    };
    if value.len() < min || max.is_some_and(|max| value.len() > max) {
        return Err(StreamError::InvalidFrame);
    }
    Ok(())
}
fn natural(object: &Object, key: &str, positive: bool) -> Result<()> {
    let Some(Value::Integer(value)) = get(object, key) else {
        return Err(StreamError::InvalidFrame);
    };
    if value.starts_with('-') || (positive && value == "0") {
        return Err(StreamError::InvalidFrame);
    }
    Ok(())
}
fn numeric(object: &Object, key: &str) -> Result<f64> {
    match get(object, key) {
        Some(Value::Float(value)) => Ok(*value),
        Some(Value::Integer(value)) => value.parse().map_err(|_| StreamError::InvalidFrame),
        _ => Err(StreamError::InvalidFrame),
    }
}
fn digest(object: &Object, key: &str) -> Result<()> {
    let value = string(object, key)?;
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(StreamError::InvalidFrame);
    }
    Ok(())
}

/// Validate the complete retained frame/media/error vocabulary before reading
/// an attachment. Payload schemas remain descriptor-owned validation work.
fn validate_projection(value: &Value) -> Result<bool> {
    let fields = as_object(value)?;
    let kind = if get(fields, "kind").is_none() && fields.len() == 1 {
        "end".into()
    } else {
        string(fields, "kind")?
    };
    bounded_string(fields, "call_id", 1, Some(128))?;
    if kind == "end" {
        fields_only(fields, &["kind", "call_id"])?;
        return Ok(false);
    }
    fields_only(
        fields,
        &[
            "call_id",
            "direction",
            "sequence",
            "kind",
            "synthetic",
            "payload",
            "media",
            "error",
        ],
    )?;
    if !matches!(
        string(fields, "direction")?.as_str(),
        "caller_to_provider" | "provider_to_caller"
    ) {
        return Err(StreamError::InvalidFrame);
    }
    natural(fields, "sequence", false)?;
    if !match get(fields, "synthetic") {
        None | Some(Value::Bool(false)) => true,
        Some(Value::Integer(value)) => value == "0",
        Some(Value::Float(value)) => *value == 0.0,
        _ => false,
    } {
        return Err(StreamError::InvalidFrame);
    }
    if !null(get(fields, "payload")) {
        as_object(get(fields, "payload").ok_or(StreamError::InvalidFrame)?)?;
    }
    let mut inline = false;
    if let Some(media) = get(fields, "media").filter(|value| !matches!(value, Value::Null)) {
        let media = as_object(media)?;
        match string(media, "kind")?.as_str() {
            "inline" => {
                inline = true;
                fields_only(
                    media,
                    &[
                        "kind",
                        "media_type",
                        "codec",
                        "sample_rate",
                        "channels",
                        "timestamp_seconds",
                        "duration_seconds",
                    ],
                )?;
                bounded_string(media, "media_type", 0, Some(256))?;
                if nonnull(media, "codec") {
                    bounded_string(media, "codec", 0, None)?;
                }
                for key in ["sample_rate", "channels"] {
                    if nonnull(media, key) {
                        natural(media, key, true)?;
                    }
                }
                for key in ["timestamp_seconds", "duration_seconds"] {
                    if nonnull(media, key) {
                        let value = numeric(media, key)?;
                        if !value.is_finite() || value < 0.0 {
                            return Err(StreamError::InvalidFrame);
                        }
                    }
                }
            }
            "blob" => {
                fields_only(
                    media,
                    &["kind", "blob_id", "size_bytes", "sha256", "media_type"],
                )?;
                bounded_string(media, "blob_id", 0, None)?;
                bounded_string(media, "media_type", 0, None)?;
                natural(media, "size_bytes", false)?;
                digest(media, "sha256")?;
            }
            _ => return Err(StreamError::InvalidFrame),
        }
    }
    let error =
        if let Some(value) = get(fields, "error").filter(|value| !matches!(value, Value::Null)) {
            let error = as_object(value)?;
            fields_only(error, &["code", "message"])?;
            bounded_string(error, "message", 0, Some(2048))?;
            let code = string(error, "code")?;
            if !matches!(
                code.as_str(),
                "provider_error"
                    | "transport_error"
                    | "unreachable"
                    | "cancelled"
                    | "timeout"
                    | "invalid_frame"
            ) {
                return Err(StreamError::InvalidFrame);
            }
            Some(code)
        } else {
            None
        };
    let sequence_zero = matches!(get(fields,"sequence"),Some(Value::Integer(value)) if value=="0");
    if sequence_zero && kind != "started" {
        return Err(StreamError::InvalidFrame);
    }
    match kind.as_str() {
        "started" => {
            if !sequence_zero
                || nonnull(fields, "payload")
                || nonnull(fields, "media")
                || error.is_some()
            {
                return Err(StreamError::InvalidFrame);
            }
        }
        "chunk" => {
            if (!nonnull(fields, "payload") && !nonnull(fields, "media")) || error.is_some() {
                return Err(StreamError::InvalidFrame);
            }
        }
        "completed" => {
            if error.is_some() {
                return Err(StreamError::InvalidFrame);
            }
        }
        "failed" | "cancelled" => {
            if error.is_none()
                || (error.as_deref() == Some("cancelled")) != (kind == "cancelled")
                || nonnull(fields, "payload")
                || nonnull(fields, "media")
            {
                return Err(StreamError::InvalidFrame);
            }
        }
        _ => return Err(StreamError::InvalidFrame),
    }
    Ok(inline)
}

/// Encode one capped packet; attachment bytes are never base64/JSON expanded.
pub fn encode_packet(packet: &Packet) -> Result<Vec<u8>> {
    let header = Value::Object(BTreeMap::from([
        (codec::points("protocol"), Value::Integer("4".into())),
        (codec::points("frame"), packet.projection.clone()),
        (
            codec::points("media_size"),
            Value::Integer(packet.media.len().to_string()),
        ),
    ]));
    let header = codec::encode(&header).map_err(|_| StreamError::InvalidFrame)?;
    if header.len() > MAX_HEADER_BYTES {
        return Err(StreamError::HeaderBound);
    }
    let mut bytes = Vec::with_capacity(4 + header.len() + packet.media.len());
    bytes.extend_from_slice(&(header.len() as u32).to_be_bytes());
    bytes.extend(header);
    bytes.extend_from_slice(&packet.media);
    Ok(bytes)
}
fn parse_header(header: &[u8]) -> Result<(Value, usize)> {
    if !(2..=MAX_HEADER_BYTES).contains(&header.len()) {
        return Err(StreamError::HeaderBound);
    }
    let header = codec::parse(std::str::from_utf8(header).map_err(|_| StreamError::InvalidFrame)?)
        .map_err(|_| StreamError::InvalidFrame)?;
    let fields = as_object(&header)?;
    fields_only(fields, &["protocol", "frame", "media_size"])?;
    if !match get(fields, "protocol") {
        None => true,
        Some(Value::Integer(value)) => value == "4",
        Some(Value::Float(value)) => *value == 4.0,
        _ => false,
    } {
        return Err(StreamError::InvalidFrame);
    }
    natural(fields, "media_size", false)?;
    let Some(Value::Integer(size)) = get(fields, "media_size") else {
        return Err(StreamError::InvalidFrame);
    };
    let size = size.parse::<usize>().map_err(|_| StreamError::MediaBound)?;
    if size > MAX_MEDIA_BYTES {
        return Err(StreamError::MediaBound);
    }
    let projection = get(fields, "frame")
        .ok_or(StreamError::InvalidFrame)?
        .clone();
    if !validate_projection(&projection)? && size != 0 {
        return Err(StreamError::InvalidFrame);
    }
    Ok((projection, size))
}
/// Decode an already bounded complete packet; extra bytes are a framing error.
pub fn decode_packet(bytes: &[u8]) -> Result<Packet> {
    if bytes.len() < 4 {
        return Err(StreamError::InvalidFrame);
    }
    let size = u32::from_be_bytes(
        bytes[..4]
            .try_into()
            .map_err(|_| StreamError::InvalidFrame)?,
    ) as usize;
    if !(2..=MAX_HEADER_BYTES).contains(&size) {
        return Err(StreamError::HeaderBound);
    }
    if bytes.len() < 4 + size {
        return Err(StreamError::InvalidFrame);
    }
    let (projection, media_size) = parse_header(&bytes[4..4 + size])?;
    if bytes.len() != 4 + size + media_size {
        return Err(StreamError::InvalidFrame);
    }
    Packet::from_value(projection, bytes[4 + size..].to_vec())
}
/// Read lengths and complete header validation before allocating raw media.
pub async fn read_packet(reader: &mut (impl AsyncRead + Unpin)) -> Result<Packet> {
    let mut prefix = [0; 4];
    reader
        .read_exact(&mut prefix)
        .await
        .map_err(|_| StreamError::Transport)?;
    let size = u32::from_be_bytes(prefix) as usize;
    if !(2..=MAX_HEADER_BYTES).contains(&size) {
        return Err(StreamError::HeaderBound);
    }
    let mut header = vec![0; size];
    reader
        .read_exact(&mut header)
        .await
        .map_err(|_| StreamError::Transport)?;
    let (projection, size) = parse_header(&header)?;
    let mut media = vec![0; size];
    reader
        .read_exact(&mut media)
        .await
        .map_err(|_| StreamError::Transport)?;
    Packet::from_value(projection, media)
}
/// Await real socket write backpressure; callers supply the encompassing deadline.
pub async fn write_packet(writer: &mut (impl AsyncWrite + Unpin), packet: &Packet) -> Result<()> {
    writer
        .write_all(&encode_packet(packet)?)
        .await
        .map_err(|_| StreamError::Transport)?;
    writer.flush().await.map_err(|_| StreamError::Transport)
}

/// One direction has its own identity, contiguous sequence and terminal fence.
#[derive(Debug)]
pub struct Sequence {
    call_id: Vec<u32>,
    direction: &'static str,
    next: u64,
    terminal: bool,
}
impl Sequence {
    /// Initialize one direction; output starts at one because Skulk owns started.
    pub fn new(call_id: &str, direction: &'static str, next: u64) -> Self {
        Self {
            call_id: codec::points(call_id),
            direction,
            next,
            terminal: false,
        }
    }
    /// Refuse gaps, duplicates, foreign calls and frames after this direction closes.
    pub fn accept(&mut self, packet: &Packet) -> Result<()> {
        let fields = packet.fields()?;
        if self.terminal
            || get(fields, "call_id") != Some(&Value::String(self.call_id.clone()))
            || string(fields, "direction")? != self.direction
            || get(fields, "sequence") != Some(&Value::Integer(self.next.to_string()))
        {
            return Err(StreamError::Sequence);
        }
        let next = self.next.checked_add(1).ok_or(StreamError::Sequence)?;
        self.next = next;
        self.terminal = packet.is_terminal();
        Ok(())
    }
}

/// Existing non-unary modes; unary has no managed stream connection.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    Server,
    Client,
    Duplex,
}
impl Mode {
    /// Refuse unary/downgraded or unknown managed stream modes.
    pub fn parse(value: &str) -> Result<Self> {
        match value {
            "server_streaming" => Ok(Self::Server),
            "client_streaming" => Ok(Self::Client),
            "bidirectional" => Ok(Self::Duplex),
            _ => Err(StreamError::InvalidFrame),
        }
    }
}

#[derive(Debug)]
struct Lifecycle {
    input: Sequence,
    output: Sequence,
    mode: Mode,
    terminal: Option<Packet>,
    failure: Option<StreamError>,
    closed: bool,
}
impl Lifecycle {
    fn alive(&self) -> Result<()> {
        if let Some(error) = self.failure {
            Err(error)
        } else if self.closed {
            Err(StreamError::Closed)
        } else {
            Ok(())
        }
    }
    fn incoming(&mut self, packet: &Packet) -> Result<()> {
        self.alive()?;
        if packet.is_end() || self.mode == Mode::Server {
            return Err(StreamError::UnexpectedInput);
        }
        self.input.accept(packet)?;
        if packet.kind().as_deref() == Some("completed") && packet.has_content() {
            return Err(StreamError::HalfCloseData);
        }
        if matches!(packet.kind().as_deref(), Some("failed" | "cancelled")) {
            return Err(StreamError::CallerTerminated);
        }
        Ok(())
    }
    fn outgoing(&mut self, packet: Packet) -> Result<Option<Packet>> {
        self.alive()?;
        if packet.is_end() {
            return Err(StreamError::Sequence);
        }
        self.output.accept(&packet)?;
        if self.mode == Mode::Client && packet.kind().as_deref() == Some("chunk") {
            return Err(StreamError::ClientChunk);
        }
        if packet.is_terminal() {
            self.terminal = Some(packet);
            Ok(None)
        } else {
            Ok(Some(packet))
        }
    }
}

#[derive(Clone, Copy)]
enum Wire {
    Legacy,
    Portable,
}
impl Wire {
    async fn read(self, reader: &mut (impl AsyncRead + Unpin)) -> Result<Packet> {
        match self {
            Self::Legacy => read_packet(reader).await,
            Self::Portable => transport::read_packet(reader).await,
        }
    }
    async fn write(self, writer: &mut (impl AsyncWrite + Unpin), packet: &Packet) -> Result<()> {
        match self {
            Self::Legacy => write_packet(writer, packet).await,
            Self::Portable => transport::write_packet(writer, packet).await,
        }
    }
}

/// Native asynchronous transport owner. Iterator execution is language-owned;
/// `finish` is called only after its cleanup has completed. Drop aborts owned
/// tasks/sockets; explicit `close` awaits their teardown. Health uses no session
/// locks and remains independent of a blocked input/output/handler operation.
pub struct Session {
    wire: Wire,
    state: Arc<Mutex<Lifecycle>>,
    input: AsyncMutex<(mpsc::Receiver<Packet>, bool)>,
    writer: Arc<AsyncMutex<OwnedWriteHalf>>,
    failures: watch::Sender<Option<StreamError>>,
    tasks: Mutex<Vec<JoinHandle<()>>>,
    deadline: Instant,
    call_id: String,
}
impl Session {
    /// Authenticate one fresh local invocation before enabling its stream tasks.
    /// `hello` is an already contract-validated control envelope, not logged.
    pub async fn open(
        socket: &str,
        hello: &str,
        call_id: &str,
        mode: Mode,
        seconds: f64,
    ) -> Result<Self> {
        let hello = validate_hello(hello, call_id)?;
        Self::connect(socket, hello, call_id, mode, seconds, Wire::Legacy).await
    }
    /// Derive the stream endpoint and hello from one admitted startup. The host
    /// still owns original caller, signed selection and one-use call admission.
    pub async fn open_portable(
        startup: &managed::Record,
        call_id: &str,
        mode: Mode,
        seconds: f64,
    ) -> Result<Self> {
        let hello = handshake::from_startup(startup, Some(call_id))
            .map_err(|_| StreamError::InvalidFrame)?;
        let value: serde_json::Value =
            serde_json::from_slice(startup.canonical()).map_err(|_| StreamError::InvalidFrame)?;
        let socket = value["stream_socket"]
            .as_str()
            .ok_or(StreamError::InvalidFrame)?;
        Self::connect(
            socket,
            hello.canonical().to_vec(),
            call_id,
            mode,
            seconds,
            Wire::Portable,
        )
        .await
    }
    /// Select language conversion without replacing legacy codec behavior.
    pub fn is_portable(&self) -> bool {
        matches!(self.wire, Wire::Portable)
    }
    /// Admit binding output using the session's explicit protocol, never a fallback.
    pub fn packet(&self, projection: &str, media: Vec<u8>) -> Result<Packet> {
        match self.wire {
            Wire::Legacy => Packet::from_projection(projection, media),
            Wire::Portable => transport::packet(projection, media),
        }
    }
    /// Return the canonical projection belonging to this session's number profile.
    pub fn projection(&self, packet: &Packet) -> Result<String> {
        match self.wire {
            Wire::Legacy => packet.projection_json(),
            Wire::Portable => transport::projection(packet),
        }
    }
    async fn connect(
        socket: &str,
        hello: Vec<u8>,
        call_id: &str,
        mode: Mode,
        seconds: f64,
        wire: Wire,
    ) -> Result<Self> {
        if !seconds.is_finite()
            || !(0.0..=300.0).contains(&seconds)
            || seconds == 0.0
            || !identifier(call_id)
        {
            return Err(StreamError::InvalidFrame);
        }
        let deadline = Instant::now() + Duration::from_secs_f64(seconds);
        let stream = timeout_at(deadline, UnixStream::connect(socket))
            .await
            .map_err(|_| StreamError::Timeout)?
            .map_err(|_| StreamError::Transport)?;
        let (mut reader, mut writer) = stream.into_split();
        timeout_at(deadline, async {
            writer
                .write_all(&(hello.len() as u32).to_be_bytes())
                .await?;
            writer.write_all(&hello).await?;
            writer.flush().await
        })
        .await
        .map_err(|_| StreamError::Timeout)?
        .map_err(|_| StreamError::Transport)?;
        let state = Arc::new(Mutex::new(Lifecycle {
            input: Sequence::new(call_id, "caller_to_provider", 0),
            output: Sequence::new(call_id, "provider_to_caller", 1),
            mode,
            terminal: None,
            failure: None,
            closed: false,
        }));
        let writer = Arc::new(AsyncMutex::new(writer));
        let (failures, _) = watch::channel(None);
        let (sender, receiver) = mpsc::channel(INPUT_WINDOW);
        let reader_state = state.clone();
        let reader_failures = failures.clone();
        let receive = tokio::spawn(async move {
            let result = timeout_at(deadline, async {
                loop {
                    let packet = wire.read(&mut reader).await?;
                    reader_state
                        .lock()
                        .map_err(|_| StreamError::Closed)?
                        .incoming(&packet)?;
                    sender.send(packet).await.map_err(|_| StreamError::Closed)?;
                }
                #[allow(unreachable_code)]
                Ok::<(), StreamError>(())
            })
            .await
            .unwrap_or(Err(StreamError::Timeout));
            if let Err(error) = result {
                fail(&reader_state, &reader_failures, error);
            }
        });
        let timer_state = state.clone();
        let timer_failures = failures.clone();
        let timer_writer = writer.clone();
        let timer = tokio::spawn(async move {
            tokio::time::sleep_until(deadline).await;
            fail(&timer_state, &timer_failures, StreamError::Timeout);
            let _ = timer_writer.lock().await.shutdown().await;
        });
        Ok(Self {
            wire,
            state,
            input: AsyncMutex::new((receiver, false)),
            writer,
            failures,
            tasks: Mutex::new(vec![receive, timer]),
            deadline,
            call_id: call_id.into(),
        })
    }
    /// Await bounded ingress; errors win over already queued data. A completed
    /// caller direction is a half-close and never closes provider output.
    pub async fn next_input(&self) -> Result<Option<Packet>> {
        self.alive()?;
        let mut failures = self.failures.subscribe();
        let mut input = self.input.lock().await;
        self.alive()?;
        if input.1 {
            return Ok(None);
        }
        let packet = tokio::select! { biased;
            _=failures.changed()=>{ self.alive()?; return Err(StreamError::Closed) },
            packet=input.0.recv()=>packet.ok_or(StreamError::Transport)?,
        };
        self.alive()?;
        input.1 = packet.is_terminal();
        Ok(Some(packet))
    }
    /// Validate/sequence a callback output; terminal bytes are held until finish.
    pub async fn send_output(&self, packet: Packet) -> Result<()> {
        let mut cancellation = CancellationFence {
            session: self,
            completed: false,
        };
        let result = self.send_output_inner(packet).await;
        if let Err(error) = result {
            fail(&self.state, &self.failures, error);
            self.close().await;
        }
        cancellation.completed = true;
        result
    }
    async fn send_output_inner(&self, packet: Packet) -> Result<()> {
        // Direct Rust callers cannot bypass the selected protocol's admission.
        if let Wire::Portable = self.wire {
            transport::encode_packet(&packet)?;
        }
        // The writer gate also serializes the sequence transition and prevents
        // concurrent binding calls from placing chunks after the cleanup ack.
        let mut failures = self.failures.subscribe();
        let mut writer = self.writer.lock().await;
        let packet = self
            .state
            .lock()
            .map_err(|_| StreamError::Closed)?
            .outgoing(packet)?;
        if let Some(packet) = packet {
            let result = tokio::select! { biased;
                _=failures.changed()=>{ self.alive()?; Err(StreamError::Closed) },
                result=timeout_at(self.deadline,self.wire.write(&mut *writer,&packet))=>result.map_err(|_|StreamError::Timeout)?,
            };
            if let Err(error) = result {
                fail(&self.state, &self.failures, error);
                return Err(error);
            }
        }
        Ok(())
    }
    /// Publish exactly one output terminal and cleanup ack after iterator close.
    /// Already observed input failure is arbitrated before success publication.
    pub async fn finish(&self) -> Result<()> {
        let mut cancellation = CancellationFence {
            session: self,
            completed: false,
        };
        let result = self.finish_inner().await;
        if let Err(error) = result {
            fail(&self.state, &self.failures, error);
            self.close().await;
        }
        cancellation.completed = true;
        result
    }
    async fn finish_inner(&self) -> Result<()> {
        let mut failures = self.failures.subscribe();
        tokio::task::yield_now().await;
        let mut writer = self.writer.lock().await;
        self.alive()?;
        let terminal = self
            .state
            .lock()
            .map_err(|_| StreamError::Closed)?
            .terminal
            .take()
            .ok_or(StreamError::MissingTerminal)?;
        let end = Packet::from_value(
            Value::Object(BTreeMap::from([
                (codec::points("kind"), Value::String(codec::points("end"))),
                (
                    codec::points("call_id"),
                    Value::String(codec::points(&self.call_id)),
                ),
            ])),
            vec![],
        )?;
        let result = tokio::select! { biased;
            _=failures.changed()=>{ self.alive()?; Err(StreamError::Closed) },
            result=timeout_at(self.deadline,async {
                self.wire.write(&mut *writer,&terminal).await?;
                self.wire.write(&mut *writer,&end).await
            })=>result.map_err(|_|StreamError::Timeout)?,
        };
        if let Err(error) = result {
            fail(&self.state, &self.failures, error);
            return Err(error);
        }
        self.state.lock().map_err(|_| StreamError::Closed)?.closed = true;
        drop(writer);
        self.close().await;
        Ok(())
    }
    /// Stop and await every owned transport task, retaining no detached reader.
    pub async fn close(&self) {
        if let Ok(mut state) = self.state.lock() {
            state.closed = true;
        }
        self.failures.send_replace(Some(StreamError::Closed));
        let tasks = self
            .tasks
            .lock()
            .map(|mut tasks| std::mem::take(&mut *tasks))
            .unwrap_or_default();
        for task in &tasks {
            task.abort();
        }
        for task in tasks {
            let _ = task.await;
        }
        self.failures.send_replace(Some(StreamError::Closed));
        let _ = self.writer.lock().await.shutdown().await;
    }
    fn alive(&self) -> Result<()> {
        if Instant::now() >= self.deadline {
            return Err(StreamError::Timeout);
        }
        self.state.lock().map_err(|_| StreamError::Closed)?.alive()
    }
}
impl Drop for Session {
    fn drop(&mut self) {
        if let Ok(mut state) = self.state.lock() {
            state.closed = true;
        }
        if let Ok(tasks) = self.tasks.lock() {
            for task in tasks.iter() {
                task.abort();
            }
        }
        self.failures.send_replace(Some(StreamError::Closed));
    }
}
fn fail(
    state: &Mutex<Lifecycle>,
    failures: &watch::Sender<Option<StreamError>>,
    error: StreamError,
) {
    if let Ok(mut state) = state.lock()
        && !state.closed
        && state.failure.is_none()
    {
        state.failure = Some(error);
        failures.send_replace(Some(error));
    }
}
fn validate_hello(hello: &str, call_id: &str) -> Result<Vec<u8>> {
    // Bound source processing, including whitespace, before parsing on a
    // shared native worker. Canonical size alone cannot bound this input.
    if hello.len() > MAX_HEADER_BYTES {
        return Err(StreamError::HeaderBound);
    }
    let hello = codec::parse(hello).map_err(|_| StreamError::InvalidFrame)?;
    let fields = as_object(&hello)?;
    fields_only(
        fields,
        &[
            "kind",
            "protocol",
            "call_id",
            "token",
            "capability_node_id",
            "manifest_sha256",
        ],
    )?;
    if string(fields, "kind")? != "stream_hello"
        || get(fields, "protocol") != Some(&Value::Integer("4".into()))
        || string(fields, "call_id")? != call_id
    {
        return Err(StreamError::InvalidFrame);
    }
    bounded_string(fields, "token", 64, Some(64))?;
    if !identifier(&string(fields, "capability_node_id")?) {
        return Err(StreamError::InvalidFrame);
    }
    digest(fields, "manifest_sha256")?;
    let bytes = codec::encode(&hello).map_err(|_| StreamError::InvalidFrame)?;
    if bytes.len() > MAX_HEADER_BYTES {
        return Err(StreamError::HeaderBound);
    }
    Ok(bytes)
}

fn identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._:@-".contains(&byte))
}

// Cancelling a binding await during a partial write must fence every subsequent
// output/finish call. The language-owned finally path explicitly awaits close;
// the native deadline also bounds any retained handle's reader/socket lifetime.
struct CancellationFence<'a> {
    session: &'a Session,
    completed: bool,
}
impl Drop for CancellationFence<'_> {
    fn drop(&mut self) {
        if !self.completed {
            fail(
                &self.session.state,
                &self.session.failures,
                StreamError::Closed,
            );
        }
    }
}

/// Bound native reactor worker/blocking threads independently of host core count.
/// This does not create subprocesses or perform provider/fabric work.
pub fn runtime_builder() -> tokio::runtime::Builder {
    let mut builder = tokio::runtime::Builder::new_multi_thread();
    builder
        .worker_threads(2)
        .max_blocking_threads(4)
        .enable_all();
    builder
}
