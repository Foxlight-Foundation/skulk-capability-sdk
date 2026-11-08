//! Legacy bounded, offset-addressed operation evidence; logging is never authority.
use std::fs::{self, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::{Path, PathBuf};

use crate::{codec, journal};

/// At most this many raw bytes fit in a legacy escaped log reply.
pub const CHUNK_BYTES: usize = 8192;
/// Offset bound matches SQLite/history and the original log contract.
pub const MAX_OFFSET: u64 = 1 << 53;

/// One bounded legacy log observation; UTF-8 replacement never changes byte offsets.
#[derive(Debug, PartialEq, Eq)]
pub struct LogPage {
    pub offset: u64,
    pub next_offset: u64,
    pub truncated: bool,
    pub text: String,
}

/// Private evidence store keyed by the exact scope/operation pair.
#[derive(Clone)]
pub struct Logs {
    directory: PathBuf,
    limit: u64,
}
impl Logs {
    /// Initialize owner-only storage without changing an existing shared directory.
    pub fn new(directory: &Path, limit: u64) -> Result<Self, journal::JournalError> {
        journal::private_directory(directory)?;
        Ok(Self {
            directory: directory.into(),
            limit,
        })
    }
    fn path(&self, scope: &str, id: &str) -> PathBuf {
        let key = format!("{scope}\x1f{id}");
        let digest = codec::sha256(key.as_bytes());
        self.directory.join(format!("{}.log", &digest[..32]))
    }
    /// Append only operator-safe text; a refused/lost log cannot fail its operation.
    pub fn append(&self, scope: &str, id: &str, line: &str) {
        let path = self.path(scope, id);
        if journal::private_directory(&self.directory).is_err() {
            return;
        }
        // Inspect this actual descriptor, not a checked path reopened later.
        // NONBLOCK prevents a substituted FIFO from blocking an evidence write.
        let Ok(mut file) = OpenOptions::new()
            .append(true)
            .create(true)
            .mode(0o600)
            .custom_flags(
                (rustix::fs::OFlags::NOFOLLOW | rustix::fs::OFlags::NONBLOCK).bits() as i32,
            )
            .open(&path)
        else {
            return;
        };
        let Ok(meta) = file.metadata() else { return };
        if !meta.is_file()
            || meta.uid() != rustix::process::getuid().as_raw()
            || meta.mode() & 0o077 != 0
        {
            return;
        }
        let encoded = format!("{}\n", line.trim_end_matches('\n'));
        if meta.len().saturating_add(encoded.len() as u64) <= self.limit {
            let _ = file.write_all(encoded.as_bytes());
        }
    }
    /// Retire display evidence only; the journal's replay reservation remains.
    pub fn retire(&self, scope: &str, id: &str) {
        let _ = fs::remove_file(self.path(scope, id));
    }
    /// Refuse invalid offsets and return empty evidence for vanished/unsafe files.
    pub fn read(
        &self,
        scope: &str,
        id: &str,
        offset: u64,
    ) -> Result<LogPage, journal::JournalError> {
        if offset > MAX_OFFSET {
            return Err(journal::JournalError::Invalid);
        }
        let mut page = LogPage {
            offset,
            next_offset: offset,
            truncated: false,
            text: String::new(),
        };
        let path = self.path(scope, id);
        if !path.exists() || journal::private_directory(&self.directory).is_err() {
            return Ok(page);
        }
        let Ok(mut file) = OpenOptions::new()
            .read(true)
            .custom_flags(
                (rustix::fs::OFlags::NOFOLLOW | rustix::fs::OFlags::NONBLOCK).bits() as i32,
            )
            .open(path)
        else {
            return Ok(page);
        };
        let Ok(meta) = file.metadata() else {
            return Ok(page);
        };
        if !meta.is_file()
            || meta.uid() != rustix::process::getuid().as_raw()
            || meta.mode() & 0o077 != 0
        {
            return Ok(page);
        }
        if file.seek(SeekFrom::Start(offset)).is_err() {
            return Ok(page);
        }
        let mut bytes = Vec::with_capacity(CHUNK_BYTES + 1);
        if file
            .take((CHUNK_BYTES + 1) as u64)
            .read_to_end(&mut bytes)
            .is_err()
        {
            return Ok(page);
        }
        let count = bytes.len();
        let size = count.min(CHUNK_BYTES);
        page.next_offset += size as u64;
        page.truncated = count > CHUNK_BYTES;
        page.text = String::from_utf8_lossy(&bytes[..size]).into_owned();
        Ok(page)
    }
}
