//! Node-API binding to the same codec and journal, with no runtime process.
use std::path::Path;
use std::sync::{Arc, Mutex};

use napi::bindgen_prelude::{Buffer, Utf16String};
use napi::{Error, Result, Status};
use napi_derive::napi;
use skulk_capability_kernel::{
    binding, codec, contracts, journal, managed, operation_logs, operations, portable, schema,
    streams,
};

/// Private qualification surface; source text avoids JavaScript number narrowing.
#[napi]
pub fn schema_matches(definition: String, instance: String) -> Result<bool> {
    schema::Schema::new(&definition)
        .map(|schema| schema.matches(&instance))
        .map_err(|_| Error::new(Status::InvalidArg, "invalid payload schema"))
}

/// Installed descriptor validation uses the owner's fixed root dialect.
#[napi]
pub fn schema_matches_installed(definition: String, instance: String) -> Result<bool> {
    schema::Schema::installed(&definition)
        .map(|schema| schema.matches(&instance))
        .map_err(|_| Error::new(Status::InvalidArg, "invalid payload schema"))
}

/// Exact managed installation-profile fingerprint; no authorization effects.
#[napi]
pub fn managed_profile_digest() -> String {
    managed::profile_digest().into()
}

/// Native admission of a managed manifest or private inherited startup record.
#[napi]
pub fn managed_contract_json(name: String, source: Utf16String) -> Result<Buffer> {
    let source = portable_text(source)?;
    managed::validate_contract_json(&name, &source)
        .map(Buffer::from)
        .map_err(portable_error)
}

/// Full managed identity; never truncate or treat a digest as caller authority.
#[napi]
pub fn managed_contract_digest(name: String, source: Utf16String) -> Result<String> {
    let source = portable_text(source)?;
    managed::contract_digest(&name, &source).map_err(portable_error)
}

/// SDK 1.0 candidate canonical JSON with one bounded numeric meaning.
#[napi]
pub fn portable_canonical_json(source: Utf16String) -> Result<Buffer> {
    let source = portable_text(source)?;
    portable::canonical_json(&source)
        .map(Buffer::from)
        .map_err(portable_error)
}

/// Profile and protocol are mandatory; no annotation can select this contract.
#[napi]
pub fn portable_contract_json(name: String, source: Utf16String) -> Result<Buffer> {
    let source = portable_text(source)?;
    portable::validate_contract_json(&name, &source)
        .map(Buffer::from)
        .map_err(portable_error)
}

/// Full profile/model-domain-separated digest of an admitted new contract.
#[napi]
pub fn portable_contract_digest(name: String, source: Utf16String) -> Result<String> {
    let source = portable_text(source)?;
    portable::contract_digest(&name, &source).map_err(portable_error)
}

/// Qualify portable validation through the generation-owned Node addon.
#[napi]
pub fn portable_schema_matches(definition: Utf16String, instance: Utf16String) -> Result<bool> {
    let definition = portable_text(definition)?;
    let instance = portable_text(instance)?;
    portable::Schema::new(&definition)
        .and_then(|schema| schema.matches(&instance))
        .map_err(portable_error)
}

fn portable_error(error: portable::PortableError) -> Error {
    Error::new(Status::InvalidArg, error.to_string())
}

fn portable_text(source: Utf16String) -> Result<String> {
    // N-API's UTF-8 conversion substitutes malformed UTF-16. Refuse it before
    // computing an identity instead of validating substituted content.
    if source.as_ref().len() > 262_144 {
        return Err(portable_error(portable::PortableError::Limit));
    }
    String::from_utf16(source.as_ref())
        .map_err(|_| portable_error(portable::PortableError::Unicode))
}

/// Profile fingerprint is derived from the same packaged normative contract.
#[napi]
pub fn portable_profile_digest() -> String {
    portable::profile_digest().into()
}

/// Reusable immutable compiled schema; native storage owns its lifetime.
#[napi]
pub struct PortableSchema {
    schema: portable::Schema,
}

#[napi]
impl PortableSchema {
    /// Compile the closed profile before accepting any payload.
    #[napi(constructor)]
    pub fn new(source: Utf16String) -> Result<Self> {
        let source = portable_text(source)?;
        portable::Schema::new(&source)
            .map(|schema| Self { schema })
            .map_err(portable_error)
    }

    /// Reject malformed/over-budget data distinctly from a normal mismatch.
    #[napi]
    pub fn matches(&self, source: Utf16String) -> Result<bool> {
        let source = portable_text(source)?;
        self.schema.matches(&source).map_err(portable_error)
    }
}

fn codec_error(error: codec::CodecError) -> Error {
    Error::new(Status::InvalidArg, error.to_string())
}
fn journal_error(error: journal::JournalError) -> Error {
    Error::new(
        Status::GenericFailure,
        format!("{}: {}", binding::error_code(&error), error),
    )
}
fn points(value: Utf16String) -> Vec<u32> {
    char::decode_utf16(value.as_ref().iter().copied())
        .map(|value| match value {
            Ok(value) => u32::from(value),
            Err(value) => u32::from(value.unpaired_surrogate()),
        })
        .collect()
}

/// Canonical bytes from exact JSON text, without JavaScript JSON.parse narrowing.
#[napi]
pub fn canonical_json(input: String) -> Result<Buffer> {
    codec::canonical(&input)
        .map(Buffer::from)
        .map_err(codec_error)
}

/// Validate the versioned author contract and return native canonical bytes.
#[napi]
pub fn validate_contract_json(name: String, input: String) -> Result<Buffer> {
    contracts::Registry::packaged()
        .and_then(|registry| registry.validate_json(&name, &input))
        .map(Buffer::from)
        .map_err(|error| Error::new(Status::InvalidArg, error.to_string()))
}

/// Opaque native model whose bytes are retained until serialization/framing.
#[napi]
pub struct NativeAuthorContract {
    record: contracts::ValidatedContract,
}
#[napi]
impl NativeAuthorContract {
    /// The root/model name used to normalize this record.
    #[napi(getter)]
    pub fn name(&self) -> String {
        self.record.name().into()
    }
    /// Shared canonical serialization refuses nonsendable constructor values.
    #[napi]
    pub fn canonical(&self) -> Result<Buffer> {
        contracts::Registry::packaged()
            .and_then(|registry| registry.project_value(&self.record))
            .map(Buffer::from)
            .map_err(|_| Error::new(Status::InvalidArg, "contract cannot be serialized"))
    }
    /// Frame arbitrary media without serializing its bytes as Unicode.
    #[napi]
    pub fn stream_packet(&self) -> Result<Buffer> {
        let packet = contracts::Registry::packaged()
            .and_then(|registry| registry.stream_packet(&self.record))
            .map_err(|_| Error::new(Status::InvalidArg, "invalid stream contract"))?;
        streams::encode_packet(&packet)
            .map(Buffer::from)
            .map_err(stream_error)
    }
    /// Approved conversion data permits nested immutable constructor models.
    #[napi]
    pub fn constructor_input(&self) -> Result<String> {
        binding::constructor_transport(self.record.value()).map_err(codec_error)
    }
}
/// Both language bindings use the same constructor normalizer and projection.
#[napi]
pub fn validate_contract_value(name: String, tagged: String) -> Result<NativeAuthorContract> {
    let value = binding::constructor_tagged(&tagged).map_err(codec_error)?;
    let record = contracts::Registry::packaged()
        .and_then(|registry| {
            registry.validate_value(&name, &value, contracts::ConstructorForm::JavaScript)
        })
        .map_err(|_| Error::new(Status::InvalidArg, "invalid constructor value"))?;
    Ok(NativeAuthorContract { record })
}

/// Convert the language facade's tagged value; only the kernel serializes it.
#[napi]
pub fn canonical_tagged(input: String) -> Result<Buffer> {
    codec::encode(&binding::tagged(&input).map_err(codec_error)?)
        .map(Buffer::from)
        .map_err(codec_error)
}

/// Preserve scope Unicode and typed values before identity calculation.
#[napi]
pub fn input_digest_tagged(scope: Utf16String, input: String) -> Result<String> {
    codec::scoped_digest(
        &points(scope),
        binding::tagged(&input).map_err(codec_error)?,
    )
    .map_err(codec_error)
}

/// Experimental journal handle; callers explicitly release reconciliation
/// ownership and close handles. Each transaction owns a short SQLite connection.
#[napi]
pub struct NativeJournal {
    journal: operations::SharedJournal,
}
#[napi]
impl NativeJournal {
    #[napi(constructor)]
    pub fn new(path: String, bounds_json: String, now: f64) -> Result<Self> {
        if !now.is_finite() || now.fract() != 0.0 || !(0.0..=9_007_199_254_740_991.0).contains(&now)
        {
            return Err(Error::new(Status::InvalidArg, "invalid journal timestamp"));
        }
        Ok(Self {
            journal: operations::JournalGroup::new(
                binding::open(Path::new(&path), &bounds_json, now as i64).map_err(journal_error)?,
            ),
        })
    }
    /// Return canonical response JSON; payload kinds remain intact in this text.
    #[napi]
    pub fn transact(&self, request: String) -> Result<String> {
        let mut guard = self
            .journal
            .data
            .lock()
            .map_err(|_| Error::new(Status::GenericFailure, "native journal is busy"))?;
        let journal = guard
            .as_mut()
            .ok_or_else(|| Error::new(Status::InvalidArg, "native journal is closed"))?;
        String::from_utf8(binding::transact(journal, &request).map_err(journal_error)?)
            .map_err(|_| Error::new(Status::GenericFailure, "invalid native encoding"))
    }
    /// Fence reconciliation through the same file used by the Python owner.
    #[napi]
    pub fn acquire_ownership(&self) -> Result<()> {
        self.journal.acquire_ownership().map_err(journal_error)
    }
    /// Refuse releasing ownership while a native operation worker is live.
    #[napi]
    pub fn release_ownership(&self) -> Result<()> {
        self.journal.release_ownership().map_err(journal_error)
    }
    /// Close only after all scoped operation workers have stopped.
    #[napi]
    pub fn close(&self) -> Result<()> {
        self.journal.close().map_err(journal_error)
    }
}

fn stream_error(error: streams::StreamError) -> Error {
    Error::new(Status::GenericFailure, error.to_string())
}

/// Decoded native ingress keeps media binary and JSON number kinds exact.
#[napi(object)]
pub struct NativePacket {
    pub projection: String,
    pub media: Buffer,
}

/// Encode a capped legacy packet, never base64-expanding raw media.
#[napi]
pub fn encode_stream_packet(projection: String, media: Buffer) -> Result<Buffer> {
    if media.len() > streams::MAX_MEDIA_BYTES {
        return Err(stream_error(streams::StreamError::MediaBound));
    }
    let packet =
        streams::Packet::from_projection(&projection, media.to_vec()).map_err(stream_error)?;
    streams::encode_packet(&packet)
        .map(Buffer::from)
        .map_err(stream_error)
}

/// Validate binary framing before allocating its capped media projection.
#[napi]
pub fn decode_stream_packet(bytes: Buffer) -> Result<NativePacket> {
    let packet = streams::decode_packet(&bytes).map_err(stream_error)?;
    Ok(NativePacket {
        projection: packet.projection_json().map_err(stream_error)?,
        media: Buffer::from(packet.media),
    })
}

/// Direct in-process native transport. Language callback cleanup acknowledges
/// finish; AbortSignal/finally paths await close without blocking Node health.
#[napi]
pub struct NativeStream {
    inner: Arc<streams::Session>,
}
#[napi]
impl NativeStream {
    /// Independent input half-close; native failure wins over queued input.
    #[napi]
    pub async fn next_input(&self) -> Result<Option<NativePacket>> {
        let packet = self.inner.next_input().await.map_err(stream_error)?;
        packet
            .map(|packet| {
                Ok(NativePacket {
                    projection: packet.projection_json().map_err(stream_error)?,
                    media: Buffer::from(packet.media),
                })
            })
            .transpose()
    }
    /// Validate and send output, retaining its terminal until callback cleanup.
    #[napi]
    pub async fn send_output(&self, projection: String, media: Buffer) -> Result<()> {
        if media.len() > streams::MAX_MEDIA_BYTES {
            return Err(stream_error(streams::StreamError::MediaBound));
        }
        let packet =
            streams::Packet::from_projection(&projection, media.to_vec()).map_err(stream_error)?;
        self.inner.send_output(packet).await.map_err(stream_error)
    }
    /// Publish terminal plus end after the facade has closed its iterator.
    #[napi]
    pub async fn finish(&self) -> Result<()> {
        self.inner.finish().await.map_err(stream_error)
    }
    /// Await owned task/socket teardown, including an explicit cancellation.
    #[napi]
    pub async fn close(&self) {
        self.inner.close().await;
    }
}

/// Native authenticated stream setup; no runtime helper process is started.
#[napi]
pub async fn open_stream(
    socket: String,
    hello: String,
    call_id: String,
    mode: String,
    seconds: f64,
) -> Result<NativeStream> {
    let mode = streams::Mode::parse(&mode).map_err(stream_error)?;
    let session = streams::Session::open(&socket, &hello, &call_id, mode, seconds)
        .await
        .map_err(stream_error)?;
    Ok(NativeStream {
        inner: Arc::new(session),
    })
}

#[napi_derive::module_init]
fn initialize_reactor() {
    // Module initialization creates only the bounded native reactor. Actual
    // socket/provider activity starts solely through explicit stream methods.
    let runtime = streams::runtime_builder()
        .build()
        .expect("native reactor initialization failed");
    napi::bindgen_prelude::create_custom_tokio_runtime(runtime);
}

fn operation_error(error: operations::OperationError) -> Error {
    Error::new(Status::GenericFailure, error.to_string())
}

/// Native worker and callback bridge; language adapters contain backend behavior only.
#[napi]
pub struct NativeOperationRunner {
    inner: Arc<operations::Runner>,
    _ownership: Arc<Mutex<Option<journal::Ownership>>>,
}
impl Drop for NativeOperationRunner {
    fn drop(&mut self) {
        self.inner.request_close();
    }
}
#[napi]
impl NativeOperationRunner {
    /// Start one scope worker; duplicate starts do not duplicate submissions.
    #[napi]
    pub async fn start(&self) -> Result<()> {
        self.inner.start().await.map_err(operation_error)
    }
    /// Stop callbacks and await interruption persistence/worker cleanup.
    #[napi]
    pub async fn stop(&self) {
        self.inner.stop().await;
    }
    /// Reserve canonical scoped intent; the native engine owns default IDs and fences.
    #[napi]
    pub async fn reserve(&self, operation_id: Option<String>, payload: String) -> Result<String> {
        binding::status_json(
            self.inner
                .reserve(operation_id, payload)
                .await
                .map_err(operation_error)?,
        )
        .map_err(journal_error)
    }
    /// Read current local observation without backend work.
    #[napi]
    pub async fn status(&self, operation_id: String) -> Result<Option<String>> {
        self.inner
            .status(operation_id)
            .await
            .map_err(operation_error)?
            .map(binding::status_json)
            .transpose()
            .map_err(journal_error)
    }
    /// Persist cancellation intent; acknowledgment does not terminalize accepted work.
    #[napi]
    pub async fn cancel(&self, operation_id: String) -> Result<String> {
        binding::status_json(
            self.inner
                .cancel(operation_id)
                .await
                .map_err(operation_error)?,
        )
        .map_err(journal_error)
    }
    /// Read bounded log evidence under the same operation scope.
    #[napi]
    pub async fn log(&self, operation_id: String, offset: f64) -> Result<String> {
        if !offset.is_finite()
            || offset.fract() != 0.0
            || !(0.0..=9_007_199_254_740_992.0).contains(&offset)
        {
            return Err(Error::new(Status::InvalidArg, "invalid log offset"));
        }
        self.inner
            .log_page(operation_id, offset as u64)
            .await
            .map_err(operation_error)
    }
    /// Await current requests/advisories; expired requests are skipped natively.
    #[napi]
    pub async fn next_request(&self) -> Result<Option<String>> {
        self.inner
            .bridge
            .next()
            .await
            .map_err(operation_error)?
            .map(|command| command.json())
            .transpose()
            .map_err(operation_error)
    }
    /// A late/duplicate response cannot change a timed-out operation.
    #[napi]
    pub fn respond(&self, id: f64, response: String) -> Result<bool> {
        if !id.is_finite() || id.fract() != 0.0 || !(1.0..=9_007_199_254_740_991.0).contains(&id) {
            return Err(Error::new(Status::InvalidArg, "invalid callback identity"));
        }
        self.inner
            .bridge
            .respond(id as u64, &response)
            .map_err(operation_error)
    }
}

/// Construct scoped native scheduling around existing state; no external call occurs.
#[napi]
pub fn operation_runner(
    journal: &NativeJournal,
    logs: String,
    scope: String,
    poll_seconds: f64,
    adapter_seconds: f64,
    result_schema: String,
) -> Result<NativeOperationRunner> {
    let bounds = journal
        .journal
        .data
        .lock()
        .map_err(|_| Error::new(Status::GenericFailure, "native journal is busy"))?
        .as_ref()
        .ok_or_else(|| Error::new(Status::InvalidArg, "native journal is closed"))?
        .bounds();
    let logs = operation_logs::Logs::new(Path::new(&logs), bounds.log_bytes as u64)
        .map_err(journal_error)?;
    let inner = operations::Runner::new(
        journal.journal.clone(),
        logs,
        operations::Config {
            scope,
            poll_seconds,
            adapter_seconds,
        },
        &result_schema,
        None,
    )
    .map_err(operation_error)?;
    Ok(NativeOperationRunner {
        inner,
        _ownership: journal.journal.ownership.clone(),
    })
}
