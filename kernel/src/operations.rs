//! One native worker per descriptor scope with durable backend ownership.
//!
//! The bounded in-process bridge invokes language adapters, not a helper process.
//! Uncertainty is persisted before submit; timeouts/stop never replay a create.
//! Accepted jobs reconcile by the original reference, and cancel acceptance is
//! never proof of termination. Logging is evidence, not operation authority.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, Weak};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use thiserror::Error;
use tokio::sync::{Mutex as AsyncMutex, Notify, mpsc, oneshot, watch};
use tokio::task::JoinHandle;

use crate::{
    codec,
    journal::{Bounds, Journal, JournalError, Status, Update},
    operation_logs::Logs,
};

/// Existing parked-row policy; late answers remain recoverable without blocking new work.
pub const OVERDUE_ERROR: &str = "runtime bound exceeded and the backend stopped answering";
const OVERDUE_RETRY: i64 = 60;
const MIN_WAIT: f64 = 0.05;
const MAX_BRIDGE_BYTES: usize = 65536;
/// Source and canonical input ceiling for this private qualification interface.
pub const MAX_OPERATION_INPUT_BYTES: usize = 65536;
/// Bound the private descriptor namespace before registration/digest allocation.
pub const MAX_OPERATION_SCOPE_BYTES: usize = 2048;
const MAX_COMMAND_BYTES: usize = MAX_OPERATION_INPUT_BYTES + 8192;

/// One shared native journal, kept alive by both its facade and scope workers.
pub type SharedJournal = Arc<JournalGroup>;

/// One journal/owner shared across independent descriptor scopes in one child.
pub struct JournalGroup {
    pub data: Mutex<Option<Journal>>,
    pub ownership: Arc<Mutex<Option<crate::journal::Ownership>>>,
    scopes: Mutex<HashSet<String>>,
    wakes: Mutex<HashMap<String, Weak<Notify>>>,
}
impl JournalGroup {
    /// Wrap recovered state without starting a worker or acquiring a persistent lease.
    pub fn new(journal: Journal) -> SharedJournal {
        Arc::new(Self {
            data: Mutex::new(Some(journal)),
            ownership: Arc::new(Mutex::new(None)),
            scopes: Mutex::new(HashSet::new()),
            wakes: Mutex::new(HashMap::new()),
        })
    }
    fn scope_wake(&self, scope: &str) -> Result<Arc<Notify>> {
        let mut wakes = self.wakes.lock().map_err(|_| OperationError::Stopped)?;
        if let Some(wake) = wakes.get(scope).and_then(Weak::upgrade) {
            return Ok(wake);
        }
        // Inactive facades share the owner's wake; retired scopes do not leave
        // permanently retained registry entries or keep a worker alive.
        wakes.retain(|_, wake| wake.strong_count() != 0);
        let wake = Arc::new(Notify::new());
        wakes.insert(scope.into(), Arc::downgrade(&wake));
        Ok(wake)
    }
    fn acquire_scope(self: &Arc<Self>, scope: String) -> Result<ScopeOwner> {
        let mut scopes = self.scopes.lock().map_err(|_| OperationError::Stopped)?;
        if scopes.contains(&scope) {
            return Err(OperationError::Storage(JournalError::Busy));
        }
        let data = self.data.lock().map_err(|_| OperationError::Stopped)?;
        let journal = data.as_ref().ok_or(OperationError::Stopped)?;
        let mut ownership = self.ownership.lock().map_err(|_| OperationError::Stopped)?;
        let acquired_ownership = ownership.is_none();
        if acquired_ownership {
            *ownership = Some(journal.ownership()?);
        }
        scopes.insert(scope.clone());
        Ok(ScopeOwner {
            group: self.clone(),
            scope,
            acquired_ownership,
            aborted: false,
        })
    }
    /// Acquire the existing supervisor fence before any native execution.
    pub fn acquire_ownership(&self) -> std::result::Result<(), JournalError> {
        let scopes = self.scopes.lock().map_err(|_| JournalError::Busy)?;
        if !scopes.is_empty() {
            return Err(JournalError::Busy);
        }
        let data = self.data.lock().map_err(|_| JournalError::Busy)?;
        let journal = data.as_ref().ok_or(JournalError::Invalid)?;
        let mut ownership = self.ownership.lock().map_err(|_| JournalError::Busy)?;
        if ownership.is_some() {
            return Err(JournalError::Busy);
        }
        *ownership = Some(journal.ownership()?);
        Ok(())
    }
    /// Never release the process fence while a scope worker is still live.
    pub fn release_ownership(&self) -> std::result::Result<(), JournalError> {
        let scopes = self.scopes.lock().map_err(|_| JournalError::Busy)?;
        if !scopes.is_empty() {
            return Err(JournalError::Busy);
        }
        self.ownership
            .lock()
            .map_err(|_| JournalError::Busy)?
            .take();
        Ok(())
    }
    /// Close shared state only after every scope worker has stopped.
    pub fn close(&self) -> std::result::Result<(), JournalError> {
        let scopes = self.scopes.lock().map_err(|_| JournalError::Busy)?;
        if !scopes.is_empty() {
            return Err(JournalError::Busy);
        }
        self.data.lock().map_err(|_| JournalError::Busy)?.take();
        self.ownership
            .lock()
            .map_err(|_| JournalError::Busy)?
            .take();
        Ok(())
    }
}
struct ScopeOwner {
    group: SharedJournal,
    scope: String,
    acquired_ownership: bool,
    aborted: bool,
}
impl ScopeOwner {
    fn abort(mut self) {
        self.aborted = true;
    }
}
impl Drop for ScopeOwner {
    fn drop(&mut self) {
        if let Ok(mut scopes) = self.group.scopes.lock() {
            scopes.remove(&self.scope);
            if self.aborted
                && self.acquired_ownership
                && scopes.is_empty()
                && let Ok(mut ownership) = self.group.ownership.lock()
            {
                ownership.take();
            }
        }
    }
}

/// Native/test clock; production uses wall time, as the legacy persisted deadline does.
pub type Clock = Arc<dyn Fn() -> i64 + Send + Sync>;

/// Sanitized failures never include callback payloads, references or credentials.
#[derive(Debug, Error)]
pub enum OperationError {
    #[error("invalid operation runner configuration or callback")]
    Invalid,
    #[error("operation runner is stopped")]
    Stopped,
    #[error("operation callback failed")]
    Callback,
    #[error("operation callback deadline expired")]
    Timeout,
    #[error("operation storage failed")]
    Storage(#[from] JournalError),
}
type Result<T> = std::result::Result<T, OperationError>;

/// Validated timing policy for one scope. Adapter windows never exceed remaining runtime.
#[derive(Clone)]
pub struct Config {
    pub scope: String,
    pub poll_seconds: f64,
    pub adapter_seconds: f64,
}
impl Config {
    /// Refuse busy-spin/nonfinite clocks before creating any worker or callback.
    pub fn validate(self) -> Result<Self> {
        if self.scope.len() > MAX_OPERATION_SCOPE_BYTES
            || !self.poll_seconds.is_finite()
            || self.poll_seconds <= 0.0
            || !self.adapter_seconds.is_finite()
            || self.adapter_seconds <= 0.0
        {
            return Err(OperationError::Invalid);
        }
        for seconds in [self.poll_seconds, self.adapter_seconds] {
            if seconds > 86400.0
                || Duration::try_from_secs_f64(seconds).map_or(true, |duration| duration.is_zero())
            {
                return Err(OperationError::Invalid);
            }
        }
        Ok(self)
    }
}

/// Exact JSON command for the small language adapter dispatcher.
#[derive(Clone, Debug)]
pub struct Command {
    pub id: u64,
    pub kind: &'static str,
    pub operation_id: Option<String>,
    pub reference: Option<String>,
    pub payload: Option<String>,
}
impl Command {
    /// Encode command data with the same protected, lossless JSON profile.
    pub fn json(&self) -> Result<String> {
        let mut fields = BTreeMap::from([
            (
                codec::points("id"),
                codec::Value::Integer(self.id.to_string()),
            ),
            (
                codec::points("kind"),
                codec::Value::String(codec::points(self.kind)),
            ),
        ]);
        for (key, value) in [
            ("operation_id", &self.operation_id),
            ("reference", &self.reference),
        ] {
            if let Some(value) = value {
                fields.insert(
                    codec::points(key),
                    codec::Value::String(codec::points(value)),
                );
            }
        }
        if let Some(payload) = &self.payload {
            if payload.len() > MAX_OPERATION_INPUT_BYTES {
                return Err(OperationError::Invalid);
            }
            fields.insert(
                codec::points("input"),
                codec::parse(payload).map_err(|_| OperationError::Invalid)?,
            );
        }
        let encoded =
            codec::encode(&codec::Value::Object(fields)).map_err(|_| OperationError::Invalid)?;
        if encoded.len() > MAX_COMMAND_BYTES {
            return Err(OperationError::Invalid);
        }
        String::from_utf8(encoded).map_err(|_| OperationError::Invalid)
    }
}
struct Pending {
    reply: oneshot::Sender<String>,
    delivered: bool,
}
/// Callback requests/responses live in this process and are bounded independently of health.
pub struct Bridge {
    sender: mpsc::Sender<Command>,
    receiver: AsyncMutex<mpsc::Receiver<Command>>,
    pending: Mutex<HashMap<u64, Pending>>,
    sequence: AtomicU64,
    stop: watch::Sender<bool>,
}
impl Bridge {
    fn new(stop: watch::Sender<bool>) -> Self {
        let (sender, receiver) = mpsc::channel(8);
        Self {
            sender,
            receiver: AsyncMutex::new(receiver),
            pending: Mutex::new(HashMap::new()),
            sequence: AtomicU64::new(0),
            stop,
        }
    }
    /// Await one current callback or cancellation advisory; expired work is never delivered.
    pub async fn next(&self) -> Result<Option<Command>> {
        let mut stopped = self.stop.subscribe();
        let mut receiver = self.receiver.lock().await;
        loop {
            if *stopped.borrow() {
                return Ok(None);
            }
            let command = tokio::select! {biased;_=stopped.changed()=>continue,command=receiver.recv()=>command.ok_or(OperationError::Stopped)?};
            if command.kind == "abort" {
                return Ok(Some(command));
            }
            let mut pending = self.pending.lock().map_err(|_| OperationError::Stopped)?;
            if let Some(entry) = pending.get_mut(&command.id) {
                entry.delivered = true;
                return Ok(Some(command));
            }
        }
    }
    /// A late/duplicate reply is refused; it cannot mutate a timed-out operation.
    pub fn respond(&self, id: u64, response: &str) -> Result<bool> {
        if response.len() > MAX_BRIDGE_BYTES {
            return Err(OperationError::Invalid);
        }
        codec::parse(response).map_err(|_| OperationError::Invalid)?;
        let mut pending = self.pending.lock().map_err(|_| OperationError::Stopped)?;
        let Some(entry) = pending.get(&id) else {
            return Ok(false);
        };
        if !entry.delivered {
            return Err(OperationError::Invalid);
        }
        let entry = pending.remove(&id).ok_or(OperationError::Invalid)?;
        Ok(entry.reply.send(response.into()).is_ok())
    }
    async fn request(
        self: &Arc<Self>,
        kind: &'static str,
        operation_id: Option<String>,
        reference: Option<String>,
        payload: Option<String>,
        seconds: f64,
    ) -> Result<String> {
        if *self.stop.borrow() {
            return Err(OperationError::Stopped);
        }
        let id = self
            .sequence
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |id| {
                id.checked_add(1).filter(|next| *next < (1 << 53))
            })
            .map_err(|_| OperationError::Invalid)?
            + 1;
        let (reply, receive) = oneshot::channel();
        self.pending
            .lock()
            .map_err(|_| OperationError::Stopped)?
            .insert(
                id,
                Pending {
                    reply,
                    delivered: false,
                },
            );
        let _retire = RequestGuard {
            bridge: self.clone(),
            id,
        };
        let mut stopped = self.stop.subscribe();
        if *stopped.borrow() {
            return Err(OperationError::Stopped);
        }
        let callback = async {
            self.sender
                .send(Command {
                    id,
                    kind,
                    operation_id,
                    reference,
                    payload,
                })
                .await
                .map_err(|_| OperationError::Callback)?;
            receive.await.map_err(|_| OperationError::Callback)
        };
        // A delivered reply can contain the only accepted backend identity.
        // Consume a co-ready reply before shutdown cancels the receiver.
        tokio::select! {biased;result=tokio::time::timeout(Duration::from_secs_f64(seconds),callback)=>result.map_err(|_|OperationError::Timeout)?,_=stopped.changed()=>Err(OperationError::Stopped)}
    }
}
struct RequestGuard {
    bridge: Arc<Bridge>,
    id: u64,
}
impl Drop for RequestGuard {
    fn drop(&mut self) {
        if let Ok(mut pending) = self.bridge.pending.lock()
            && let Some(entry) = pending.remove(&self.id)
            && entry.delivered
        {
            let _ = self.bridge.sender.try_send(Command {
                id: self.id,
                kind: "abort",
                operation_id: None,
                reference: None,
                payload: None,
            });
        }
    }
}

/// Shared runner state; every public facade calls this same scheduler.
pub struct Runner {
    journal: SharedJournal,
    logs: Logs,
    config: Config,
    clock: Clock,
    bounds: Bounds,
    result_schema: crate::schema::Schema,
    stop: watch::Sender<bool>,
    pub bridge: Arc<Bridge>,
    wake: Arc<Notify>,
    task: AsyncMutex<Option<JoinHandle<()>>>,
    closed: Mutex<bool>,
    current: Mutex<Option<String>>,
    overdue: Mutex<HashMap<String, tokio::time::Instant>>,
}
impl Runner {
    /// Construct an offline schema validator and scoped worker without submitting anything.
    pub fn new(
        journal: SharedJournal,
        logs: Logs,
        config: Config,
        result_schema: &str,
        clock: Option<Clock>,
    ) -> Result<Arc<Self>> {
        let config = config.validate()?;
        if result_schema.len() > MAX_BRIDGE_BYTES {
            return Err(OperationError::Invalid);
        }
        let result_schema =
            crate::schema::Schema::new(result_schema).map_err(|_| OperationError::Invalid)?;
        let bounds = journal
            .data
            .lock()
            .map_err(|_| OperationError::Stopped)?
            .as_ref()
            .ok_or(OperationError::Stopped)?
            .bounds();
        let (stop, _) = watch::channel(true);
        let bridge = Arc::new(Bridge::new(stop.clone()));
        let wake = journal.scope_wake(&config.scope)?;
        Ok(Arc::new(Self {
            journal,
            logs,
            config,
            clock: clock.unwrap_or_else(|| {
                Arc::new(|| {
                    SystemTime::now()
                        .duration_since(UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_secs() as i64
                })
            }),
            bounds,
            result_schema,
            stop,
            bridge,
            wake,
            task: AsyncMutex::new(None),
            closed: Mutex::new(false),
            current: Mutex::new(None),
            overdue: Mutex::new(HashMap::new()),
        }))
    }
    /// Start one native task; repeated starts never create another worker.
    pub async fn start(self: &Arc<Self>) -> Result<()> {
        let mut task = self.task.lock().await;
        if *self.closed.lock().map_err(|_| OperationError::Stopped)? {
            return Err(OperationError::Stopped);
        }
        if task.is_some() {
            return Ok(());
        }
        let group = self.journal.clone();
        let scope = self.config.scope.clone();
        let owner = tokio::task::spawn_blocking(move || group.acquire_scope(scope))
            .await
            .map_err(|_| OperationError::Stopped)??;
        // Facade drop is terminal and may arrive during the bounded ownership
        // wait. Serialize the final reset/spawn with that drop-time signal.
        let closed = match self.closed.lock() {
            Ok(closed) => closed,
            Err(_) => {
                owner.abort();
                return Err(OperationError::Stopped);
            }
        };
        if *closed {
            owner.abort();
            return Err(OperationError::Stopped);
        }
        self.stop.send_replace(false);
        let runner = self.clone();
        *task = Some(tokio::spawn(async move {
            let _owner = owner;
            runner.run().await;
        }));
        Ok(())
    }
    /// Stop adapter waits, preserve active ownership and await the native worker.
    pub async fn stop(&self) {
        let mut task = self.task.lock().await;
        self.request_stop();
        if let Some(worker) = task.as_mut() {
            let _ = worker.await;
        }
        *task = None;
    }
    /// Nonblocking drop/shutdown signal; the owned worker persists interruption.
    pub fn request_stop(&self) {
        self.stop.send_replace(true);
        self.wake.notify_waiters();
    }
    /// Terminal facade-drop signal; pending/later startup cannot reset it.
    /// Explicit stop remains restartable for the supported same-handle path.
    pub fn request_close(&self) {
        if let Ok(mut closed) = self.closed.lock() {
            *closed = true;
            self.request_stop();
        } else {
            self.request_stop();
        }
    }
    /// Wake scheduling after new reservation/cancel intent without creating work itself.
    pub fn enqueue(&self) {
        self.wake.notify_one();
    }
    /// Reserve a scoped ID with the exact legacy digest/default-ID algorithm.
    pub async fn reserve(&self, id: Option<String>, payload: String) -> Result<Status> {
        if payload.len() > MAX_OPERATION_INPUT_BYTES {
            return Err(OperationError::Invalid);
        }
        let payload = codec::canonical(&payload).map_err(|_| OperationError::Invalid)?;
        if payload.len() > MAX_OPERATION_INPUT_BYTES {
            return Err(OperationError::Invalid);
        }
        let payload = String::from_utf8(payload).map_err(|_| OperationError::Invalid)?;
        let digest = codec::input_digest(&self.config.scope, &payload)
            .map_err(|_| OperationError::Invalid)?;
        let id = id.unwrap_or_else(|| codec::sha256(digest.as_bytes())[..32].into());
        let scope = self.config.scope.clone();
        let now = (self.clock)();
        let (kind, status) = self
            .db(move |journal| journal.reserve(&id, &digest, &payload, &scope, now))
            .await?;
        if kind == crate::journal::Reservation::Created {
            self.enqueue();
        }
        Ok(status)
    }
    /// Queued cancellation is terminal locally; accepted work persists intent and remains owned.
    pub async fn cancel(&self, id: String) -> Result<Status> {
        let scope = self.config.scope.clone();
        let now = (self.clock)();
        let status = self
            .db(move |journal| journal.cancel_intent(&id, &scope, now))
            .await?;
        self.enqueue();
        Ok(status)
    }
    /// Observe one scoped record with no backend request or admission side effect.
    pub async fn status(&self, id: String) -> Result<Option<Status>> {
        let scope = self.config.scope.clone();
        let now = (self.clock)();
        self.db(move |journal| journal.status(&id, &scope, now))
            .await
    }
    /// Return bounded scoped evidence for an existing record; never enqueue work.
    pub async fn log_page(&self, id: String, offset: u64) -> Result<String> {
        if self.status(id.clone()).await?.is_none() {
            return Err(OperationError::Storage(JournalError::Unknown));
        }
        let logs = self.logs.clone();
        let scope = self.config.scope.clone();
        let operation_id = id.clone();
        let page = tokio::task::spawn_blocking(move || logs.read(&scope, &id, offset))
            .await
            .map_err(|_| OperationError::Stopped)??;
        let value = codec::Value::Object(BTreeMap::from([
            (
                codec::points("operation_id"),
                codec::Value::String(codec::points(&operation_id)),
            ),
            (
                codec::points("offset"),
                codec::Value::Integer(page.offset.to_string()),
            ),
            (
                codec::points("next_offset"),
                codec::Value::Integer(page.next_offset.to_string()),
            ),
            (
                codec::points("truncated"),
                codec::Value::Bool(page.truncated),
            ),
            (
                codec::points("text"),
                codec::Value::String(codec::points(&page.text)),
            ),
        ]));
        String::from_utf8(codec::encode(&value).map_err(|_| OperationError::Invalid)?)
            .map_err(|_| OperationError::Invalid)
    }
    async fn db<T: Send + 'static>(
        &self,
        call: impl FnOnce(&mut Journal) -> std::result::Result<T, JournalError> + Send + 'static,
    ) -> Result<T> {
        let journal = self.journal.clone();
        tokio::task::spawn_blocking(move || {
            let mut guard = journal.data.lock().map_err(|_| JournalError::Busy)?;
            call(guard.as_mut().ok_or(JournalError::Invalid)?)
        })
        .await
        .map_err(|_| OperationError::Stopped)?
        .map_err(OperationError::Storage)
    }
    async fn update(&self, id: String, update: Update) -> Result<Status> {
        let scope = self.config.scope.clone();
        let now = (self.clock)();
        self.db(move |journal| journal.update(&id, &scope, update, now))
            .await
    }
    async fn interrupted(&self) -> Result<Vec<(String, String)>> {
        let scope = self.config.scope.clone();
        self.db(move |journal| journal.interrupted_accepted(&scope))
            .await
    }
    async fn pause(&self, seconds: f64) {
        let mut stopped = self.stop.subscribe();
        if *stopped.borrow() {
            return;
        }
        tokio::select! {_=stopped.changed()=>{},_=tokio::time::sleep(Duration::from_secs_f64(seconds))=>{}}
    }
    async fn run(self: &Arc<Self>) {
        // Stop/fault cleanup may fail while SQLite is locked. Reusing this
        // handle must recover owned rows without requiring a journal reopen.
        let mut needs_recovery = true;
        while !*self.stop.borrow() {
            if needs_recovery {
                let scope = self.config.scope.clone();
                let now = (self.clock)();
                if self
                    .db(move |journal| journal.recover_running(&scope, now))
                    .await
                    .is_err()
                {
                    self.pause(self.config.poll_seconds).await;
                    continue;
                }
                needs_recovery = false;
            }
            let Ok(interrupted) = self.interrupted().await else {
                self.pause(self.config.poll_seconds).await;
                continue;
            };
            for (id, reference) in &interrupted {
                if *self.stop.borrow() {
                    break;
                }
                let due = self
                    .overdue
                    .lock()
                    .ok()
                    .and_then(|retry| retry.get(id).copied())
                    .unwrap_or_else(tokio::time::Instant::now);
                if tokio::time::Instant::now() < due {
                    continue;
                }
                if let Ok(mut retry) = self.overdue.lock() {
                    retry.remove(id);
                }
                needs_recovery = self.execute(id.clone(), Some(reference.clone())).await;
                if needs_recovery {
                    break;
                }
            }
            if *self.stop.borrow() {
                break;
            }
            if needs_recovery {
                self.pause(self.config.poll_seconds).await;
                continue;
            }
            let Ok(interrupted) = self.interrupted().await else {
                self.pause(self.config.poll_seconds).await;
                continue;
            };
            let mut blocking = false;
            for (id, _) in interrupted {
                // Only a successfully read overdue record permits fresh work.
                // Storage failures are not evidence that ownership disappeared.
                if !matches!(
                    self.status(id).await,
                    Ok(Some(status)) if status.error.as_deref() == Some(OVERDUE_ERROR)
                ) {
                    blocking = true;
                    break;
                }
            }
            if blocking {
                self.pause(self.config.poll_seconds).await;
                continue;
            }
            let scope = self.config.scope.clone();
            let now = (self.clock)();
            let queued = match self
                .db(move |journal| journal.claim_next(&scope, now))
                .await
            {
                Ok(queued) => queued,
                Err(_) => {
                    self.pause(self.config.poll_seconds).await;
                    continue;
                }
            };
            if let Some(status) = queued {
                needs_recovery = self.execute(status.operation_id, None).await;
                continue;
            }
            let mut stopped = self.stop.subscribe();
            if *stopped.borrow() {
                break;
            }
            // Other handles/processes may commit into this fenced journal but
            // cannot share our Notify. A bounded idle scan prevents stranded
            // intent; same-group reservations still wake immediately. Parked
            // jobs retain their own monotonic retry deadline across each scan.
            tokio::select! {_=stopped.changed()=>{},_=self.wake.notified()=>{},_=tokio::time::sleep(Duration::from_secs(1))=>{}}
        }
    }
    /// Return whether durable recovery must finish before another admission.
    async fn execute(&self, id: String, reference: Option<String>) -> bool {
        let mut needs_recovery = false;
        if let Ok(mut current) = self.current.lock() {
            *current = Some(id.clone());
        }
        if let Err(error) = self.execute_guarded(&id, reference).await {
            let status = match self.status(id.clone()).await {
                Ok(status) => status,
                Err(_) => {
                    needs_recovery = true;
                    None
                }
            };
            if let Some(status) = status {
                let owned = status.backend_reference.is_some() || status.submission == "uncertain";
                if status.state == "running" || !matches!(error, OperationError::Stopped) {
                    let interrupted = matches!(error, OperationError::Stopped) || owned;
                    needs_recovery = self
                        .update(
                            id.clone(),
                            Update {
                                state: Some(
                                    if interrupted { "interrupted" } else { "failed" }.into(),
                                ),
                                error: if matches!(error, OperationError::Stopped) {
                                    if status.backend_reference.is_none() {
                                        Some("backend submission uncertain".into())
                                    } else {
                                        None
                                    }
                                } else {
                                    Some("operation worker failure".into())
                                },
                                ..Update::default()
                            },
                        )
                        .await
                        .is_err();
                }
            }
        }
        if let Ok(mut current) = self.current.lock() {
            *current = None;
        }
        if let Ok(retired) = self.db(|journal| Ok(journal.drain_retired())).await {
            for (scope, id) in retired {
                self.logs.retire(&scope, &id);
            }
        }
        needs_recovery
    }
    async fn execute_guarded(&self, id: &str, reference: Option<String>) -> Result<()> {
        let scope = self.config.scope.clone();
        let mut reference = reference;
        if reference.is_none() {
            let op = id.to_owned();
            let payload = self.db(move |journal| journal.payload(&op, &scope)).await?;
            let Some(payload) = payload else {
                return Ok(());
            };
            // The atomic dequeue already persisted running/uncertain before this
            // payload read or callback, so cancellation/another worker cannot steal it.
            let response = self
                .bridge
                .request(
                    "submit",
                    Some(id.into()),
                    None,
                    Some(payload),
                    self.config.adapter_seconds,
                )
                .await;
            match response {
                Err(OperationError::Stopped) => return Err(OperationError::Stopped),
                Err(_) => {
                    self.uncertain(id, "backend submission uncertain").await?;
                    return Ok(());
                }
                Ok(response) => {
                    let response = parse_response(&response)?;
                    match text(&response, "kind")?.as_str() {
                        "refused" => {
                            let reason = text(&response, "reason")?;
                            self.persist_outcome(
                                id,
                                Update {
                                    state: Some("failed".into()),
                                    submission: Some("pending".into()),
                                    error: Some(reason.clone()),
                                    ..Update::default()
                                },
                            )
                            .await;
                            self.log(id, format!("submit refused: {reason}")).await;
                            return Ok(());
                        }
                        "accepted" => {
                            let value = text(&response, "reference")?;
                            if value.chars().count() > 1024 {
                                self.uncertain(id, "backend reference exceeds the bound")
                                    .await?;
                                return Ok(());
                            }
                            reference = Some(value);
                        }
                        _ => {
                            self.uncertain(id, "backend submission uncertain").await?;
                            return Ok(());
                        }
                    }
                }
            }
            self.persist_outcome(
                id,
                Update {
                    submission: Some("accepted".into()),
                    backend_reference: reference.clone(),
                    stage: Some("accepted".into()),
                    ..Update::default()
                },
            )
            .await;
            self.log(id, ("accepted by backend").to_owned()).await;
        } else {
            self.update(
                id.into(),
                Update {
                    state: Some("running".into()),
                    stage: Some("reconciling".into()),
                    ..Update::default()
                },
            )
            .await?;
            self.log(id, ("reconciling accepted job").to_owned()).await;
        }
        let reference = reference.ok_or(OperationError::Invalid)?;
        let scope = self.config.scope.clone();
        let op = id.to_owned();
        let started = self
            .db(move |journal| journal.started_at(&op, &scope))
            .await?
            .unwrap_or_else(|| (self.clock)());
        let mut cancel_sent: Option<&str> = None;
        let mut failures = 0;
        loop {
            if *self.stop.borrow() {
                return Err(OperationError::Stopped);
            }
            let scope = self.config.scope.clone();
            let op = id.to_owned();
            let requested = self
                .db(move |journal| journal.cancel_requested(&op, &scope))
                .await?;
            let overdue = (self.clock)().saturating_sub(started) >= self.bounds.max_runtime_seconds;
            let mut cancel_failed = false;
            if (requested || overdue) && cancel_sent.is_none() {
                match self
                    .bridge
                    .request(
                        "cancel",
                        None,
                        Some(reference.clone()),
                        None,
                        self.wait_seconds(started, overdue),
                    )
                    .await
                {
                    Err(OperationError::Stopped) => return Err(OperationError::Stopped),
                    Ok(response)
                        if parse_response(&response)
                            .ok()
                            .and_then(|value| text(&value, "kind").ok())
                            .as_deref()
                            == Some("cancel_accepted") =>
                    {
                        cancel_sent = Some(if requested {
                            "cancel requested"
                        } else {
                            "runtime bound exceeded"
                        });
                        self.update(
                            id.into(),
                            Update {
                                stage: Some("cancelling".into()),
                                ..Update::default()
                            },
                        )
                        .await?;
                        self.log(id, ("cancel accepted by backend").to_owned())
                            .await;
                    }
                    _ => {
                        cancel_failed = true;
                        self.log(id, ("cancel failed: callback").to_owned()).await;
                    }
                }
            }
            let observed = self
                .bridge
                .request(
                    "observe",
                    None,
                    Some(reference.clone()),
                    None,
                    self.wait_seconds(started, overdue),
                )
                .await;
            let observed = match observed {
                Err(OperationError::Stopped) => return Err(OperationError::Stopped),
                Ok(response) => parse_observation(&response),
                Err(error) => Err(error),
            };
            let observation = match observed {
                Ok(value) => value,
                Err(_) => {
                    failures += 1;
                    self.log(id, ("observe failed: callback").to_owned()).await;
                    if overdue && failures >= 3 {
                        self.update(
                            id.into(),
                            Update {
                                state: Some("interrupted".into()),
                                stage: Some("overdue".into()),
                                error: Some(OVERDUE_ERROR.into()),
                                ..Update::default()
                            },
                        )
                        .await?;
                        if let Ok(mut retry) = self.overdue.lock() {
                            retry.insert(
                                id.into(),
                                tokio::time::Instant::now()
                                    + Duration::from_secs(OVERDUE_RETRY as u64),
                            );
                        }
                        return Ok(());
                    }
                    self.pause(self.config.poll_seconds).await;
                    continue;
                }
            };
            failures = 0;
            if matches!(
                observation.state.as_str(),
                "queued" | "running" | "interrupted"
            ) {
                let update = Update {
                    state: Some("running".into()),
                    progress: observation.progress,
                    stage: if cancel_failed {
                        Some("cancel failed".into())
                    } else if cancel_sent.is_some() {
                        Some("cancelling".into())
                    } else {
                        observation.stage
                    },
                    ..Update::default()
                };
                if self.update(id.into(), update).await.is_err() {
                    self.log(id, ("malformed observation dropped").to_owned())
                        .await;
                }
                self.pause(self.config.poll_seconds).await;
                continue;
            }
            if observation.state == "succeeded"
                && observation.result_refused.as_deref() == Some("nonfinite")
            {
                self.update(
                    id.into(),
                    Update {
                        state: Some("failed".into()),
                        error: Some("operation result holds a non-finite number".into()),
                        ..Update::default()
                    },
                )
                .await?;
                break;
            }
            if observation.state == "succeeded"
                && !self.result_matches(observation.result.as_deref())
            {
                self.update(
                    id.into(),
                    Update {
                        state: Some("failed".into()),
                        stage: observation
                            .stage
                            .filter(|stage| stage.chars().count() <= 64),
                        error: Some("operation result does not match its schema".into()),
                        ..Update::default()
                    },
                )
                .await?;
                break;
            }
            let mut error = observation.error;
            if observation.state == "cancelled"
                && error.is_none()
                && cancel_sent == Some("runtime bound exceeded")
            {
                error = Some("runtime bound exceeded".into());
            }
            let succeeded = observation.state == "succeeded";
            if succeeded
                && observation
                    .result
                    .as_ref()
                    .is_some_and(|value| value.len() > 8192)
            {
                self.update(
                    id.into(),
                    Update {
                        state: Some("failed".into()),
                        error: Some("operation result exceeds the bound".into()),
                        ..Update::default()
                    },
                )
                .await?;
                break;
            }
            let result = self
                .update(
                    id.into(),
                    Update {
                        state: Some(observation.state.clone()),
                        progress: if succeeded {
                            Some(1.0)
                        } else {
                            observation.progress
                        },
                        stage: observation.stage,
                        result_json: if succeeded { observation.result } else { None },
                        error,
                        ..Update::default()
                    },
                )
                .await;
            match result {
                Ok(_) => {}
                Err(OperationError::Storage(JournalError::Invalid | JournalError::Json(_))) => {
                    self.update(
                        id.into(),
                        Update {
                            state: Some("failed".into()),
                            error: Some("operation result is unusable".into()),
                            ..Update::default()
                        },
                    )
                    .await?;
                }
                // A valid backend result is not unusable merely because its
                // durable write failed. Keep ownership and retry reconciliation.
                Err(error) => return Err(error),
            }
            self.log(id, format!("finished: {}", observation.state))
                .await;
            break;
        }
        Ok(())
    }
    /// Retain a definitive submit outcome and scope lease until durable transfer.
    /// Stop cannot discard acceptance/refusal; each storage attempt is bounded.
    async fn persist_outcome(&self, id: &str, outcome: Update) {
        loop {
            if self.update(id.into(), outcome.clone()).await.is_ok() {
                return;
            }
            // This durability barrier deliberately survives stop/drop signals.
            // Ordinary pause would wake immediately on stop and spin on errors.
            tokio::time::sleep(Duration::from_secs_f64(
                self.config.poll_seconds.clamp(MIN_WAIT, 1.0),
            ))
            .await;
        }
    }
    fn wait_seconds(&self, started: i64, overdue: bool) -> f64 {
        if overdue {
            self.config.adapter_seconds
        } else {
            let remaining =
                (self.bounds.max_runtime_seconds - (self.clock)().saturating_sub(started)) as f64;
            self.config.adapter_seconds.min(remaining.max(MIN_WAIT))
        }
    }
    async fn log(&self, id: &str, line: String) {
        let logs = self.logs.clone();
        let scope = self.config.scope.clone();
        let id = id.to_owned();
        let _ = tokio::task::spawn_blocking(move || logs.append(&scope, &id, &line)).await;
    }
    async fn uncertain(&self, id: &str, error: &str) -> Result<()> {
        self.update(
            id.into(),
            Update {
                state: Some("interrupted".into()),
                error: Some(error.into()),
                ..Update::default()
            },
        )
        .await?;
        Ok(())
    }
    fn result_matches(&self, result: Option<&str>) -> bool {
        result.is_some_and(|value| self.result_schema.matches(value))
    }
}

#[derive(Debug)]
struct Observation {
    state: String,
    progress: Option<f64>,
    stage: Option<String>,
    result: Option<String>,
    error: Option<String>,
    result_refused: Option<String>,
}
type Fields = BTreeMap<Vec<u32>, codec::Value>;
fn parse_response(input: &str) -> Result<Fields> {
    match codec::parse(input).map_err(|_| OperationError::Invalid)? {
        codec::Value::Object(fields) => Ok(fields),
        _ => Err(OperationError::Invalid),
    }
}
fn text(fields: &Fields, key: &str) -> Result<String> {
    match fields.get(&codec::points(key)) {
        Some(codec::Value::String(points)) => points
            .iter()
            .map(|point| char::from_u32(*point).ok_or(OperationError::Invalid))
            .collect(),
        _ => Err(OperationError::Invalid),
    }
}
fn optional_text(fields: &Fields, key: &str) -> Result<Option<String>> {
    if matches!(
        fields.get(&codec::points(key)),
        None | Some(codec::Value::Null)
    ) {
        Ok(None)
    } else {
        Ok(Some(text(fields, key)?))
    }
}
fn parse_observation(input: &str) -> Result<Observation> {
    let fields = parse_response(input)?;
    if text(&fields, "kind")? != "observed" {
        return Err(OperationError::Callback);
    }
    let state = text(&fields, "state")?;
    if !matches!(
        state.as_str(),
        "queued" | "running" | "interrupted" | "succeeded" | "failed" | "cancelled"
    ) {
        return Err(OperationError::Invalid);
    }
    let progress = match fields.get(&codec::points("progress")) {
        None | Some(codec::Value::Null) => None,
        Some(codec::Value::Float(value)) => Some(*value),
        Some(codec::Value::Integer(value)) => {
            Some(value.parse().map_err(|_| OperationError::Invalid)?)
        }
        _ => return Err(OperationError::Invalid),
    };
    let result = fields
        .get(&codec::points("result"))
        .filter(|value| !matches!(value, codec::Value::Null))
        .map(codec::encode)
        .transpose()
        .map_err(|_| OperationError::Invalid)?
        .map(String::from_utf8)
        .transpose()
        .map_err(|_| OperationError::Invalid)?;
    Ok(Observation {
        state,
        progress,
        stage: optional_text(&fields, "stage")?,
        result,
        error: optional_text(&fields, "error")?,
        result_refused: optional_text(&fields, "result_refused")?,
    })
}
