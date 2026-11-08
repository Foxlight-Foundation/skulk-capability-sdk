//! Owner-only SQLite journal compatible with the original Python table layout.
//!
//! Reservations are durable replay fences, separate from retained display rows.
//! Recovery never submits work. Accepted and uncertain jobs retain their identity
//! and remain visible for reconciliation; pruning cannot erase that obligation.

use std::fs::{self, DirBuilder, File, OpenOptions, TryLockError};
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use rusqlite::{
    Connection, OpenFlags, OptionalExtension, Transaction, TransactionBehavior, params,
};
use thiserror::Error;

use crate::codec;

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS reservations (
 key TEXT PRIMARY KEY, input_digest TEXT NOT NULL, reserved_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS operations (
 key TEXT PRIMARY KEY, operation_id TEXT NOT NULL, scope TEXT NOT NULL,
 state TEXT NOT NULL, submission TEXT NOT NULL, progress REAL, stage TEXT,
 submitted_at INTEGER NOT NULL, started_at INTEGER, updated_at INTEGER NOT NULL,
 finished_at INTEGER, input_digest TEXT NOT NULL, input_json TEXT NOT NULL,
 backend_reference TEXT, result_json TEXT, error TEXT,
 cancel_requested INTEGER NOT NULL DEFAULT 0, sequence INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS operations_sequence ON operations(sequence);
";
const PAGE_SIZE: i64 = 16;
const MAX_OFFSET: i64 = 1 << 53;
const MAX_INPUT_ECHO_BYTES: usize = 24576;
const MAX_RESULT_BYTES: usize = 8192;
const MAX_DATABASE_BYTES: u64 = 128 * 1024 * 1024;

/// Storage and contract refusals carry no path, query, or caller-supplied text.
#[derive(Debug, Error)]
pub enum JournalError {
    #[error("private storage operation failed")]
    Storage(#[from] rusqlite::Error),
    #[error("private storage operation failed")]
    Io(#[from] std::io::Error),
    #[error("invalid journal bounds or record")]
    Invalid,
    #[error("operation id is reserved under a different input")]
    Conflict,
    #[error("operation id was already used")]
    Retired,
    #[error("operation queue is full")]
    QueueFull,
    #[error("operation journal is full; retire it before new starts")]
    FenceFull,
    #[error("unknown operation")]
    Unknown,
    #[error("operation journal is busy")]
    Busy,
    #[error("file must be owner-only and owned by this user")]
    PrivateFile,
    #[error("directory must be owner-only and owned by this user")]
    PrivateDirectory,
    #[error("file exceeds contract bound")]
    FileSize,
    #[error("invalid operation JSON")]
    Json(#[from] codec::CodecError),
}

type Result<T> = std::result::Result<T, JournalError>;

/// Full author-declared bounds; the runner and log engine consume the same set.
#[derive(Clone, Copy, Debug)]
pub struct Bounds {
    pub max_queued: i64,
    pub max_runtime_seconds: i64,
    pub stale_after_seconds: i64,
    pub retain: i64,
    pub log_bytes: i64,
    pub reservations: i64,
}

impl Default for Bounds {
    fn default() -> Self {
        Self {
            max_queued: 4,
            max_runtime_seconds: 3600,
            stale_after_seconds: 30,
            retain: 64,
            log_bytes: 262144,
            reservations: 65536,
        }
    }
}

impl Bounds {
    /// Apply the frozen operation-bounds contract before opening private state.
    pub fn validate(self) -> Result<Self> {
        if !(1..=64).contains(&self.max_queued)
            || !(1..=86400).contains(&self.max_runtime_seconds)
            || !(1..=3600).contains(&self.stale_after_seconds)
            || !(1..=1024).contains(&self.retain)
            || !(0..=16 * 1024 * 1024).contains(&self.log_bytes)
            || !(64..=1_000_000).contains(&self.reservations)
        {
            return Err(JournalError::Invalid);
        }
        Ok(self)
    }
}

/// Retained observation; JSON strings remain lossless until a binding decodes them.
#[derive(Clone, Debug, PartialEq)]
pub struct Status {
    pub operation_id: String,
    pub state: String,
    pub submission: String,
    pub progress: Option<f64>,
    pub stage: Option<String>,
    pub queue_position: Option<i64>,
    pub submitted_at: i64,
    pub updated_at: i64,
    pub finished_at: Option<i64>,
    pub stale: bool,
    pub input_digest: String,
    pub backend_reference: Option<String>,
    pub input_json: Option<String>,
    pub result_json: Option<String>,
    pub error: Option<String>,
}

impl Status {
    fn validate(&self) -> Result<()> {
        if !identifier(&self.operation_id)
            || !state(&self.state)
            || !submission(&self.submission)
            || !digest(&self.input_digest)
            || self.submitted_at < 0
            || self.updated_at < 0
            || self.finished_at.is_some_and(|value| value < 0)
            || self
                .progress
                .is_some_and(|value| !value.is_finite() || !(0.0..=1.0).contains(&value))
            || self
                .stage
                .as_ref()
                .is_some_and(|value| value.chars().count() > 64)
            || self
                .error
                .as_ref()
                .is_some_and(|value| value.chars().count() > 256)
            || self
                .backend_reference
                .as_ref()
                .is_some_and(|value| value.chars().count() > 1024)
        {
            return Err(JournalError::Invalid);
        }
        for value in [&self.input_json, &self.result_json].into_iter().flatten() {
            if !matches!(codec::parse(value)?, codec::Value::Object(_)) {
                return Err(JournalError::Invalid);
            }
        }
        Ok(())
    }
}

/// The same optional update fields as the legacy journal API.
#[derive(Clone, Default, Debug)]
pub struct Update {
    pub state: Option<String>,
    pub submission: Option<String>,
    pub progress: Option<f64>,
    pub stage: Option<String>,
    pub backend_reference: Option<String>,
    pub result_json: Option<String>,
    pub error: Option<String>,
}

/// A compact page omits large inputs, results and backend references.
#[derive(Debug)]
pub struct Page {
    pub operations: Vec<Status>,
    pub next_offset: Option<i64>,
    pub active: i64,
    pub outstanding: i64,
}

/// Whether a reserve created new intent or observed an existing matching record.
#[derive(Debug, PartialEq, Eq)]
pub enum Reservation {
    Created,
    Existing,
}

/// Logical operation state; each method owns a short durable SQLite transaction.
#[derive(Debug)]
pub struct Journal {
    path: PathBuf,
    bounds: Bounds,
    retired: Vec<(String, String)>,
}

impl Journal {
    /// Open/recover a journal without creating or replaying any backend request.
    pub fn open(path: impl AsRef<Path>, bounds: Bounds, now: i64) -> Result<Self> {
        let journal = Self {
            path: path.as_ref().to_owned(),
            bounds: bounds.validate()?,
            retired: Vec::new(),
        };
        if now < 0 {
            return Err(JournalError::Invalid);
        }
        private_directory(
            journal
                .path
                .parent()
                .ok_or(JournalError::PrivateDirectory)?,
        )?;
        let _recovery_owner = journal.ownership()?;
        let mut connection = journal.connection()?;
        connection.execute_batch(SCHEMA)?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        transaction.execute(
            "UPDATE operations SET state='failed',finished_at=?1,updated_at=?1,
             error='interrupted before submission' WHERE state IN ('queued','running') AND submission='pending'",
            [now],
        )?;
        transaction.execute(
            "UPDATE operations SET state='interrupted',updated_at=?1 WHERE state IN ('queued','running')",
            [now],
        )?;
        transaction.commit()?;
        Ok(journal)
    }

    fn connection(&self) -> Result<Connection> {
        let parent = self.path.parent().ok_or(JournalError::PrivateDirectory)?;
        private_directory(parent)?;
        // macOS /var and /tmp are trusted system aliases. Resolve the already
        // checked parent, while still rejecting a symlink at the database leaf.
        let resolved = parent
            .canonicalize()?
            .join(self.path.file_name().ok_or(JournalError::PrivateFile)?);
        private_file(&resolved, MAX_DATABASE_BYTES)?;
        // SQLite reopens the path; NOFOLLOW also protects that second open.
        let flags = OpenFlags::SQLITE_OPEN_READ_WRITE
            | OpenFlags::from_bits_retain(rusqlite::ffi::SQLITE_OPEN_NOFOLLOW);
        let connection = Connection::open_with_flags(&resolved, flags)?;
        connection.busy_timeout(Duration::from_secs(2))?;
        connection.execute_batch("PRAGMA journal_mode=DELETE; PRAGMA synchronous=FULL;")?;
        limit_database(&connection, MAX_DATABASE_BYTES)?;
        Ok(connection)
    }

    /// Return the validated declared bounds shared by scheduling and logs.
    pub fn bounds(&self) -> Bounds {
        self.bounds
    }

    /// Reserve atomically; capacity failure never removes a replay fence.
    pub fn reserve(
        &mut self,
        operation_id: &str,
        digest_value: &str,
        payload: &str,
        scope: &str,
        now: i64,
    ) -> Result<(Reservation, Status)> {
        if !identifier(operation_id) || !digest(digest_value) || now < 0 {
            return Err(JournalError::Invalid);
        }
        let key = key(scope, operation_id);
        let mut connection = self.connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let fence: Option<String> = transaction
            .query_row(
                "SELECT input_digest FROM reservations WHERE key=?1",
                [&key],
                |row| row.get(0),
            )
            .optional()?;
        if let Some(fence) = fence {
            if fence != digest_value {
                return Err(JournalError::Conflict);
            }
            let existing = self
                .read(&transaction, &key, now)?
                .ok_or(JournalError::Retired)?;
            transaction.commit()?;
            return Ok((Reservation::Existing, existing));
        }
        let input_json = object_json(payload)?;
        let queued: i64 = transaction.query_row(
            "SELECT COUNT(*) FROM operations WHERE state='queued'",
            [],
            |row| row.get(0),
        )?;
        if queued >= self.bounds.max_queued {
            return Err(JournalError::QueueFull);
        }
        let reserved: i64 =
            transaction.query_row("SELECT COUNT(*) FROM reservations", [], |row| row.get(0))?;
        if reserved >= self.bounds.reservations {
            return Err(JournalError::FenceFull);
        }
        // Derive the sequence inside the write transaction: separate owners
        // cannot allocate the same sequence from stale in-memory counters.
        let sequence: i64 = transaction.query_row(
            "SELECT COALESCE(MAX(sequence),0)+1 FROM operations",
            [],
            |row| row.get(0),
        )?;
        transaction.execute(
            "INSERT INTO reservations VALUES (?1,?2,?3)",
            params![key, digest_value, now],
        )?;
        transaction.execute(
            "INSERT INTO operations (key,operation_id,scope,state,submission,submitted_at,updated_at,input_digest,input_json,sequence)
             VALUES (?1,?2,?3,'queued','pending',?4,?4,?5,?6,?7)",
            params![key, operation_id, scope, now, digest_value, input_json, sequence],
        )?;
        let retired = self.prune(&transaction)?;
        let created = self
            .read(&transaction, &key, now)?
            .ok_or(JournalError::Unknown)?;
        transaction.commit()?;
        self.retired.extend(retired);
        Ok((Reservation::Created, created))
    }

    /// Record an observation; an invalid update rolls back as one transaction.
    pub fn update(
        &self,
        operation_id: &str,
        scope: &str,
        update: Update,
        now: i64,
    ) -> Result<Status> {
        if now < 0 {
            return Err(JournalError::Invalid);
        }
        let mut connection = self.connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let key = key(scope, operation_id);
        self.read(&transaction, &key, now)?
            .ok_or(JournalError::Unknown)?;
        transaction.execute(
            "UPDATE operations SET updated_at=?1 WHERE key=?2",
            params![now, key],
        )?;
        if let Some(state_value) = &update.state {
            if !state(state_value) {
                return Err(JournalError::Invalid);
            }
            transaction.execute(
                "UPDATE operations SET state=?1 WHERE key=?2",
                params![state_value, key],
            )?;
            if state_value == "running" {
                transaction.execute("UPDATE operations SET started_at=COALESCE(started_at,?1),finished_at=NULL WHERE key=?2", params![now,key])?;
            } else if matches!(state_value.as_str(), "succeeded" | "failed" | "cancelled") {
                transaction.execute(
                    "UPDATE operations SET finished_at=?1 WHERE key=?2",
                    params![now, key],
                )?;
            }
        }
        if let Some(value) = update.submission {
            if !submission(&value) {
                return Err(JournalError::Invalid);
            }
            transaction.execute(
                "UPDATE operations SET submission=?1 WHERE key=?2",
                params![value, key],
            )?;
        }
        if let Some(value) = update.progress {
            if !value.is_finite() || !(0.0..=1.0).contains(&value) {
                return Err(JournalError::Invalid);
            }
            transaction.execute(
                "UPDATE operations SET progress=?1 WHERE key=?2",
                params![value, key],
            )?;
        }
        if let Some(value) = update.stage {
            if value.chars().count() > 64 {
                return Err(JournalError::Invalid);
            }
            transaction.execute(
                "UPDATE operations SET stage=?1 WHERE key=?2",
                params![value, key],
            )?;
        }
        if let Some(value) = update.backend_reference {
            if value.chars().count() > 1024 {
                return Err(JournalError::Invalid);
            }
            transaction.execute(
                "UPDATE operations SET backend_reference=?1 WHERE key=?2",
                params![value, key],
            )?;
        }
        if let Some(value) = update.result_json {
            let value = object_json(&value)?;
            if value.len() > MAX_RESULT_BYTES {
                return Err(JournalError::Invalid);
            }
            transaction.execute(
                "UPDATE operations SET result_json=?1 WHERE key=?2",
                params![value, key],
            )?;
        }
        if update.error.is_some() || update.state.as_ref().is_some_and(|value| !active(value)) {
            let error = update
                .error
                .map(|value| value.chars().take(256).collect::<String>());
            transaction.execute(
                "UPDATE operations SET error=?1 WHERE key=?2",
                params![error, key],
            )?;
        }
        let updated = self
            .read(&transaction, &key, now)?
            .ok_or(JournalError::Unknown)?;
        transaction.commit()?;
        Ok(updated)
    }

    /// Return a retained observation without causing backend work.
    pub fn status(&self, operation_id: &str, scope: &str, now: i64) -> Result<Option<Status>> {
        let mut connection = self.connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let result = self.read(&transaction, &key(scope, operation_id), now)?;
        transaction.commit()?;
        Ok(result)
    }

    /// Preserve the original first-running timestamp across recovery/reobservation.
    pub fn started_at(&self, operation_id: &str, scope: &str) -> Result<Option<i64>> {
        let connection = self.connection()?;
        Ok(connection
            .query_row(
                "SELECT started_at FROM operations WHERE key=?1",
                [key(scope, operation_id)],
                |row| row.get(0),
            )
            .optional()?
            .flatten())
    }

    /// Return the exact stored input for an existing operation.
    pub fn payload(&self, operation_id: &str, scope: &str) -> Result<Option<String>> {
        let connection = self.connection()?;
        Ok(connection
            .query_row(
                "SELECT input_json FROM operations WHERE key=?1",
                [key(scope, operation_id)],
                |row| row.get(0),
            )
            .optional()?)
    }

    /// Persist cancellation intent without claiming that a backend has stopped.
    pub fn request_cancel(&self, operation_id: &str, scope: &str, now: i64) -> Result<()> {
        if now < 0 {
            return Err(JournalError::Invalid);
        }
        let connection = self.connection()?;
        connection.execute(
            "UPDATE operations SET cancel_requested=1,updated_at=?1 WHERE key=?2",
            params![now, key(scope, operation_id)],
        )?;
        Ok(())
    }

    /// Observe persisted cancellation intent.
    pub fn cancel_requested(&self, operation_id: &str, scope: &str) -> Result<bool> {
        let connection = self.connection()?;
        let value: Option<i64> = connection
            .query_row(
                "SELECT cancel_requested FROM operations WHERE key=?1",
                [key(scope, operation_id)],
                |row| row.get(0),
            )
            .optional()?;
        Ok(value.is_some_and(|value| value != 0))
    }

    /// Atomically claim one queued row and persist uncertainty before a callback.
    /// A competing worker/cancel can never claim the same reservation twice.
    pub fn claim_next(&self, scope: &str, now: i64) -> Result<Option<Status>> {
        if now < 0 {
            return Err(JournalError::Invalid);
        }
        let mut connection = self.connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let selected:Option<String>=transaction.query_row("SELECT key FROM operations WHERE scope=?1 AND state='queued' ORDER BY sequence ASC LIMIT 1",[scope],|row|row.get(0)).optional()?;
        let Some(key) = selected else {
            transaction.commit()?;
            return Ok(None);
        };
        let changed=transaction.execute("UPDATE operations SET state='running',submission='uncertain',stage='submitting',started_at=COALESCE(started_at,?1),finished_at=NULL,updated_at=?1 WHERE key=?2 AND state='queued'",params![now,key])?;
        let status = if changed == 1 {
            self.read(&transaction, &key, now)?
        } else {
            None
        };
        transaction.commit()?;
        Ok(status)
    }

    /// Cancel only while still queued; otherwise retain backend cancellation intent.
    /// The state decision and update share the same durable write transaction.
    pub fn cancel_intent(&self, operation_id: &str, scope: &str, now: i64) -> Result<Status> {
        if now < 0 {
            return Err(JournalError::Invalid);
        }
        let mut connection = self.connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let key = key(scope, operation_id);
        let before = self
            .read(&transaction, &key, now)?
            .ok_or(JournalError::Unknown)?;
        if before.state == "queued" {
            transaction.execute("UPDATE operations SET state='cancelled',finished_at=?1,updated_at=?1,error=NULL WHERE key=?2 AND state='queued'",params![now,key])?;
            let after = self
                .read(&transaction, &key, now)?
                .ok_or(JournalError::Unknown)?;
            transaction.commit()?;
            return Ok(after);
        }
        if before.state == "running"
            || (before.state == "interrupted" && before.backend_reference.is_some())
        {
            transaction.execute(
                "UPDATE operations SET cancel_requested=1,updated_at=?1 WHERE key=?2",
                params![now, key],
            )?;
        }
        transaction.commit()?;
        Ok(before)
    }

    /// Select the oldest queued ID; the execution engine owns submit admission.
    pub fn next_queued(&self, scope: &str) -> Result<Option<String>> {
        let connection = self.connection()?;
        Ok(connection.query_row("SELECT operation_id FROM operations WHERE state='queued' AND scope=?1 ORDER BY sequence ASC LIMIT 1",[scope],|row|row.get(0)).optional()?)
    }

    /// Accepted interrupted jobs can be observed by their original backend identity.
    pub fn interrupted_accepted(&self, scope: &str) -> Result<Vec<(String, String)>> {
        let connection = self.connection()?;
        let mut statement=connection.prepare("SELECT operation_id,backend_reference FROM operations WHERE state='interrupted' AND submission='accepted' AND backend_reference IS NOT NULL AND scope=?1 ORDER BY sequence ASC")?;
        Ok(statement
            .query_map([scope], |row| Ok((row.get(0)?, row.get(1)?)))?
            .collect::<std::result::Result<Vec<_>, _>>()?)
    }

    /// Recover only this fenced worker's abandoned running rows. Queued intent
    /// and other scopes remain untouched; accepted references/start times survive.
    pub(crate) fn recover_running(&self, scope: &str, now: i64) -> Result<()> {
        if now < 0 {
            return Err(JournalError::Invalid);
        }
        let mut connection = self.connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        transaction.execute(
            "UPDATE operations SET state='failed',finished_at=?1,updated_at=?1,
             error='interrupted before submission' WHERE scope=?2 AND state='running' AND submission='pending'",
            params![now, scope],
        )?;
        transaction.execute(
            "UPDATE operations SET state='interrupted',updated_at=?1 WHERE scope=?2 AND state='running'",
            params![now, scope],
        )?;
        transaction.commit()?;
        Ok(())
    }

    /// Return a bounded history page and whole-scope active/outstanding counts.
    pub fn page(&self, scope: &str, offset: i64, now: i64) -> Result<Page> {
        if !(0..=MAX_OFFSET).contains(&offset) {
            return Err(JournalError::Invalid);
        }
        let mut connection = self.connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let keys: Vec<String> = {
            let mut statement=transaction.prepare("SELECT key FROM operations WHERE scope=?1 ORDER BY sequence DESC LIMIT ?2 OFFSET ?3")?;
            statement
                .query_map(params![scope, PAGE_SIZE + 1, offset], |row| row.get(0))?
                .collect::<std::result::Result<Vec<_>, _>>()?
        };
        let mut operations = Vec::new();
        for key in keys.iter().take(PAGE_SIZE as usize) {
            if let Some(mut item) = self.read(&transaction, key, now)? {
                item.input_json = None;
                item.result_json = None;
                item.backend_reference = None;
                operations.push(item);
            }
        }
        let active = transaction.query_row(
            "SELECT COUNT(*) FROM operations WHERE scope=?1 AND state IN ('queued','running')",
            [scope],
            |row| row.get(0),
        )?;
        let outstanding = transaction.query_row(
            "SELECT COUNT(*) FROM operations WHERE scope=?1 AND state='interrupted'",
            [scope],
            |row| row.get(0),
        )?;
        transaction.commit()?;
        Ok(Page {
            operations,
            next_offset: if keys.len() > PAGE_SIZE as usize {
                Some(offset + PAGE_SIZE)
            } else {
                None
            },
            active,
            outstanding,
        })
    }

    /// Drain retired display rows for log cleanup; reservation fences remain.
    pub fn drain_retired(&mut self) -> Vec<(String, String)> {
        std::mem::take(&mut self.retired)
    }

    /// Fence a complete reconciliation using the same lock name as Python.
    pub fn ownership(&self) -> Result<Ownership> {
        let parent = self.path.parent().ok_or(JournalError::PrivateDirectory)?;
        let file = private_file(&parent.join("supervisor.lock"), MAX_DATABASE_BYTES)?;
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            match file.try_lock() {
                Ok(()) => return Ok(Ownership { _file: file }),
                Err(TryLockError::WouldBlock) if Instant::now() < deadline => {
                    std::thread::sleep(Duration::from_millis(5))
                }
                Err(TryLockError::WouldBlock) => return Err(JournalError::Busy),
                Err(TryLockError::Error(error)) => return Err(error.into()),
            }
        }
    }

    fn prune(&self, transaction: &Transaction<'_>) -> Result<Vec<(String, String)>> {
        const PREDICATE: &str = "state NOT IN ('queued','running','interrupted') AND submission!='uncertain' AND key NOT IN (SELECT key FROM operations ORDER BY sequence DESC LIMIT ?1)";
        let retired = {
            let mut statement = transaction.prepare(&format!(
                "SELECT scope,operation_id FROM operations WHERE {PREDICATE}"
            ))?;
            statement
                .query_map([self.bounds.retain], |row| Ok((row.get(0)?, row.get(1)?)))?
                .collect::<std::result::Result<Vec<_>, _>>()?
        };
        transaction.execute(
            &format!("DELETE FROM operations WHERE {PREDICATE}"),
            [self.bounds.retain],
        )?;
        Ok(retired)
    }

    fn read(&self, connection: &Connection, key: &str, now: i64) -> Result<Option<Status>> {
        let item=connection.query_row(
            "SELECT operation_id,state,submission,progress,stage,submitted_at,updated_at,finished_at,input_digest,backend_reference,result_json,error,scope,input_json,sequence FROM operations WHERE key=?1",
            [key],|row|{
                let input:String=row.get(13)?;
                Ok((Status {operation_id:row.get(0)?,state:row.get(1)?,submission:row.get(2)?,progress:row.get(3)?,stage:row.get(4)?,
                    submitted_at:row.get(5)?,updated_at:row.get(6)?,finished_at:row.get(7)?,input_digest:row.get(8)?,backend_reference:row.get(9)?,result_json:row.get(10)?,error:row.get(11)?,
                    input_json:if input.len()<=MAX_INPUT_ECHO_BYTES {Some(input)} else {None},queue_position:None,stale:false},row.get::<_,String>(12)?,row.get::<_,i64>(14)?))
            }).optional()?;
        let Some((mut item, scope, sequence)) = item else {
            return Ok(None);
        };
        item.stale = active(&item.state)
            && now.saturating_sub(item.updated_at) > self.bounds.stale_after_seconds;
        if item.state == "queued" {
            item.queue_position = Some(connection.query_row(
                "SELECT COUNT(*) FROM operations WHERE state='queued' AND scope=?1 AND sequence<?2",
                params![scope, sequence],
                |row| row.get(0),
            )?);
        }
        item.validate()?;
        Ok(Some(item))
    }
}

/// Held ownership is released by closing its file, including on every error path.
pub struct Ownership {
    _file: File,
}

pub(crate) fn private_directory(path: &Path) -> Result<()> {
    DirBuilder::new().recursive(true).mode(0o700).create(path)?;
    let info = fs::symlink_metadata(path)?;
    if !info.is_dir()
        || info.uid() != rustix::process::getuid().as_raw()
        || info.mode() & 0o077 != 0
    {
        return Err(JournalError::PrivateDirectory);
    }
    Ok(())
}

pub(crate) fn private_file(path: &Path, limit: u64) -> Result<File> {
    let mut options = OpenOptions::new();
    options
        .read(true)
        .write(true)
        .mode(0o600)
        .custom_flags((rustix::fs::OFlags::NOFOLLOW | rustix::fs::OFlags::NONBLOCK).bits() as i32);
    let file = match options.create_new(true).open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
            options.create_new(false).open(path)?
        }
        Err(error) => return Err(error.into()),
    };
    let info = file.metadata()?;
    if !info.is_file()
        || info.uid() != rustix::process::getuid().as_raw()
        || info.mode() & 0o077 != 0
    {
        return Err(JournalError::PrivateFile);
    }
    if info.len() > limit {
        return Err(JournalError::FileSize);
    }
    Ok(file)
}

fn object_json(value: &str) -> Result<String> {
    let parsed = codec::parse(value)?;
    if !matches!(parsed, codec::Value::Object(_)) {
        return Err(JournalError::Invalid);
    }
    Ok(String::from_utf8(codec::encode(&parsed)?).expect("ASCII encoding"))
}

/// SQLite enforces the file ceiling before growth, so a full write rolls back
/// instead of poisoning every subsequent open with an oversized database.
fn limit_database(connection: &Connection, bytes: u64) -> Result<()> {
    let page_size: i64 = connection.query_row("PRAGMA page_size", [], |row| row.get(0))?;
    let page_size = u64::try_from(page_size).map_err(|_| JournalError::Invalid)?;
    let pages = bytes.checked_div(page_size).ok_or(JournalError::Invalid)?;
    let pages = i64::try_from(pages).map_err(|_| JournalError::Invalid)?;
    let accepted: i64 =
        connection.query_row(&format!("PRAGMA max_page_count={pages}"), [], |row| {
            row.get(0)
        })?;
    if accepted > pages {
        return Err(JournalError::FileSize);
    }
    Ok(())
}

fn key(scope: &str, operation_id: &str) -> String {
    format!("{scope}\x1f{operation_id}")
}
fn identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|v| v.is_ascii_alphanumeric() || b"._:@-".contains(&v))
}
fn digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v))
}
fn active(value: &str) -> bool {
    matches!(value, "queued" | "running")
}
fn state(value: &str) -> bool {
    matches!(
        value,
        "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted"
    )
}
fn submission(value: &str) -> bool {
    matches!(value, "pending" | "uncertain" | "accepted")
}

#[cfg(all(test, unix))]
mod quota_tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    #[test]
    fn sqlite_capacity_failure_rolls_back_without_poisoning_retained_state() {
        let root = tempfile::tempdir().unwrap();
        std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        let path = root.path().join("operations.sqlite");
        let mut journal = Journal::open(&path, Bounds::default(), 100).unwrap();
        let digest = codec::input_digest("scope", "{}").unwrap();
        journal
            .reserve("kept", &digest, "{}", "scope", 100)
            .unwrap();
        let connection = journal.connection().unwrap();
        // Exercise the same limiter with a small private test quota: real SQLite
        // must refuse growth atomically, without allocating a 128 MiB fixture.
        let quota = 128 * 1024;
        limit_database(&connection, quota).unwrap();
        connection
            .execute_batch("CREATE TABLE quota_probe (value BLOB);")
            .unwrap();
        assert!(
            connection
                .execute(
                    "INSERT INTO quota_probe VALUES(zeroblob(?1))",
                    [i64::try_from(2 * quota).unwrap()]
                )
                .is_err()
        );
        let count: i64 = connection
            .query_row("SELECT COUNT(*) FROM quota_probe", [], |row| row.get(0))
            .unwrap();
        assert_eq!(count, 0);
        assert!(std::fs::metadata(&path).unwrap().len() <= quota);
        assert_eq!(
            journal.status("kept", "scope", 101).unwrap().unwrap().state,
            "queued"
        );
    }
}
