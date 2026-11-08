#![cfg(unix)]

use std::fs;
use std::os::unix::fs::{PermissionsExt, symlink};

use rusqlite::Connection;
use skulk_capability_kernel::codec;
use skulk_capability_kernel::journal::{Bounds, Journal, JournalError, Reservation, Update};

fn private_root() -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
    root
}

fn bounds() -> Bounds {
    Bounds {
        retain: 4,
        max_queued: 8,
        reservations: 64,
        ..Bounds::default()
    }
}

#[test]
fn frozen_python_journal_keeps_backend_identity_uncertainty_and_replay_fences() {
    let root = private_root();
    let path = root.path().join("operations.sqlite");
    let connection = Connection::open(&path).unwrap();
    connection
        .execute_batch(include_str!(
            "../../tests/fixtures/author-baseline/sdk-0.4.0-journal.sql"
        ))
        .unwrap();
    drop(connection);
    fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
    let mut journal = Journal::open(&path, bounds(), 2000).unwrap();
    let pending = journal
        .status("pending", "legacy-scope", 2000)
        .unwrap()
        .unwrap();
    assert_eq!(pending.state, "failed");
    assert_eq!(
        pending.error.as_deref(),
        Some("interrupted before submission")
    );
    let uncertain = journal
        .status("uncertain", "legacy-scope", 2000)
        .unwrap()
        .unwrap();
    assert_eq!(uncertain.state, "interrupted");
    assert_eq!(uncertain.submission, "uncertain");
    assert_eq!(uncertain.backend_reference, None);
    let accepted = journal
        .status("accepted", "legacy-scope", 2000)
        .unwrap()
        .unwrap();
    assert_eq!(accepted.state, "interrupted");
    assert_eq!(accepted.submission, "accepted");
    assert_eq!(
        accepted.backend_reference.as_deref(),
        Some("synthetic-backend-job")
    );
    assert_eq!(
        journal.started_at("accepted", "legacy-scope").unwrap(),
        Some(1000)
    );
    assert!(
        journal
            .cancel_requested("accepted", "legacy-scope")
            .unwrap()
    );
    assert_eq!(
        journal.interrupted_accepted("legacy-scope").unwrap(),
        vec![("accepted".into(), "synthetic-backend-job".into())]
    );
    assert_eq!(journal.next_queued("legacy-scope").unwrap(), None);
    assert_eq!(
        journal
            .status("finished", "legacy-scope", 2000)
            .unwrap()
            .unwrap()
            .result_json
            .as_deref(),
        Some(r#"{"artifact":"synthetic-local-result"}"#)
    );
    assert!(
        journal
            .status("pruned", "legacy-scope", 2000)
            .unwrap()
            .is_none()
    );
    let payload = r#"{"label":"pruned"}"#;
    let digest = codec::input_digest("legacy-scope", payload).unwrap();
    assert!(matches!(
        journal.reserve("pruned", &digest, payload, "legacy-scope", 2000),
        Err(JournalError::Retired)
    ));
    let changed = r#"{"label":"different"}"#;
    assert!(matches!(
        journal.reserve(
            "pruned",
            &codec::input_digest("legacy-scope", changed).unwrap(),
            changed,
            "legacy-scope",
            2000
        ),
        Err(JournalError::Conflict)
    ));
    let page = journal.page("legacy-scope", 0, 2000).unwrap();
    assert_eq!(page.active, 0);
    assert_eq!(page.outstanding, 2);
    assert_eq!(page.operations.len(), 4);
    assert!(page.operations.iter().all(|item| item.input_json.is_none()
        && item.result_json.is_none()
        && item.backend_reference.is_none()));
}

#[test]
fn invalid_observation_rolls_back_every_field_and_matching_reserve_is_idempotent() {
    let root = private_root();
    let mut journal = Journal::open(root.path().join("operations.sqlite"), bounds(), 10).unwrap();
    let payload = r#"{"number":1.0}"#;
    let digest = codec::input_digest("scope", payload).unwrap();
    let (kind, before) = journal
        .reserve("op", &digest, payload, "scope", 10)
        .unwrap();
    assert_eq!(kind, Reservation::Created);
    assert!(matches!(
        journal.update(
            "op",
            "scope",
            Update {
                state: Some("running".into()),
                progress: Some(1.5),
                ..Update::default()
            },
            20
        ),
        Err(JournalError::Invalid)
    ));
    assert_eq!(journal.status("op", "scope", 10).unwrap().unwrap(), before);
    assert_eq!(journal.started_at("op", "scope").unwrap(), None);
    assert_eq!(
        journal
            .reserve("op", &digest, payload, "scope", 20)
            .unwrap()
            .0,
        Reservation::Existing
    );
}

#[test]
fn pruning_keeps_uncertain_rows_and_fences_after_display_retirement() {
    let root = private_root();
    let mut journal = Journal::open(
        root.path().join("operations.sqlite"),
        Bounds {
            retain: 1,
            ..bounds()
        },
        10,
    )
    .unwrap();
    let payload = "{}";
    let digest = codec::input_digest("scope", payload).unwrap();
    journal
        .reserve("uncertain", &digest, payload, "scope", 10)
        .unwrap();
    journal
        .update(
            "uncertain",
            "scope",
            Update {
                state: Some("interrupted".into()),
                submission: Some("uncertain".into()),
                ..Update::default()
            },
            10,
        )
        .unwrap();
    for id in ["old", "new"] {
        journal.reserve(id, &digest, payload, "scope", 10).unwrap();
        journal
            .update(
                id,
                "scope",
                Update {
                    state: Some("succeeded".into()),
                    ..Update::default()
                },
                10,
            )
            .unwrap();
    }
    assert!(journal.status("uncertain", "scope", 10).unwrap().is_some());
    assert!(journal.status("old", "scope", 10).unwrap().is_none());
    assert!(matches!(
        journal.reserve("old", &digest, payload, "scope", 10),
        Err(JournalError::Retired)
    ));
    assert_eq!(
        journal.drain_retired(),
        vec![("scope".into(), "old".into())]
    );
    assert!(journal.drain_retired().is_empty());
}

#[test]
fn separate_handles_allocate_monotonic_sequences_and_keep_scope_queues_apart() {
    let root = private_root();
    let path = root.path().join("operations.sqlite");
    let mut first = Journal::open(&path, bounds(), 10).unwrap();
    let mut second = Journal::open(&path, bounds(), 10).unwrap();
    let digest = codec::input_digest("a", "{}").unwrap();
    first.reserve("same", &digest, "{}", "a", 10).unwrap();
    second.reserve("same", &digest, "{}", "b", 10).unwrap();
    first.reserve("later", &digest, "{}", "a", 10).unwrap();
    assert_eq!(
        first
            .status("later", "a", 10)
            .unwrap()
            .unwrap()
            .queue_position,
        Some(1)
    );
    assert_eq!(
        second
            .status("same", "b", 10)
            .unwrap()
            .unwrap()
            .queue_position,
        Some(0)
    );
    let connection = Connection::open(&path).unwrap();
    let distinct: i64 = connection
        .query_row(
            "SELECT COUNT(DISTINCT sequence) FROM operations",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(distinct, 3);
}

#[test]
fn private_paths_reject_symlinks_and_shared_modes_before_recovery() {
    let root = private_root();
    let path = root.path().join("operations.sqlite");
    fs::write(&path, b"").unwrap();
    fs::set_permissions(&path, fs::Permissions::from_mode(0o644)).unwrap();
    assert!(matches!(
        Journal::open(&path, bounds(), 10),
        Err(JournalError::PrivateFile)
    ));
    let alias = root.path().join("alias.sqlite");
    symlink(&path, &alias).unwrap();
    assert!(Journal::open(&alias, bounds(), 10).is_err());
    fs::set_permissions(root.path(), fs::Permissions::from_mode(0o755)).unwrap();
    assert!(matches!(
        Journal::open(&path, bounds(), 10),
        Err(JournalError::PrivateDirectory)
    ));
}

#[test]
fn reconciliation_ownership_is_exclusive_and_released_by_drop() {
    let root = private_root();
    let journal = Journal::open(root.path().join("operations.sqlite"), bounds(), 10).unwrap();
    let owner = journal.ownership().unwrap();
    let file = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(root.path().join("supervisor.lock"))
        .unwrap();
    assert!(matches!(file.try_lock(), Err(fs::TryLockError::WouldBlock)));
    drop(owner);
    assert!(file.try_lock().is_ok());
}

#[test]
fn a_full_replay_fence_refuses_new_ids_without_evicting_old_ones() {
    let root = private_root();
    let mut journal = Journal::open(root.path().join("operations.sqlite"), bounds(), 10).unwrap();
    let digest = codec::input_digest("s", "{}").unwrap();
    for index in 0..64 {
        let id = format!("op-{index}");
        journal.reserve(&id, &digest, "{}", "s", 10).unwrap();
        journal
            .update(
                &id,
                "s",
                Update {
                    state: Some("succeeded".into()),
                    ..Update::default()
                },
                10,
            )
            .unwrap();
    }
    assert!(matches!(
        journal.reserve("new", &digest, "{}", "s", 10),
        Err(JournalError::FenceFull)
    ));
    assert!(matches!(
        journal.reserve("op-0", &digest, "{}", "s", 10),
        Err(JournalError::Retired)
    ));
    assert_eq!(
        journal.reserve("op-63", &digest, "{}", "s", 10).unwrap().0,
        Reservation::Existing
    );
}

#[test]
fn invalid_cancellation_time_cannot_poison_an_existing_operation() {
    let root = private_root();
    let mut journal = Journal::open(root.path().join("operations.sqlite"), bounds(), 10).unwrap();
    let digest = codec::input_digest("s", "{}").unwrap();
    let (_, before) = journal.reserve("op", &digest, "{}", "s", 10).unwrap();
    assert!(matches!(
        journal.request_cancel("op", "s", -1),
        Err(JournalError::Invalid)
    ));
    assert_eq!(journal.status("op", "s", 10).unwrap().unwrap(), before);
    assert!(!journal.cancel_requested("op", "s").unwrap());
    journal.request_cancel("op", "s", 20).unwrap();
    assert!(journal.cancel_requested("op", "s").unwrap());
}
