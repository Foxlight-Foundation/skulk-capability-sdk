#![cfg(unix)]
//! Real legacy SQLite state and a bounded in-process adapter bridge. No provider
//! request, SSH connection, paid resource or runtime helper process is used.
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::sync::Arc;
use std::sync::atomic::{AtomicI64, Ordering};
use std::time::Duration;

use skulk_capability_kernel::{
    codec,
    journal::{Bounds, Journal},
    operation_logs::Logs,
    operations::{
        Command, Config, JournalGroup, MAX_OPERATION_INPUT_BYTES, MAX_OPERATION_SCOPE_BYTES,
        OVERDUE_ERROR, Runner,
    },
};
use tokio::time::timeout;

fn fixture(
    runtime: i64,
    adapter: f64,
    schema: &str,
) -> (tempfile::TempDir, Arc<Runner>, Arc<AtomicI64>) {
    let root = tempfile::tempdir().unwrap();
    fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let journal = Journal::open(
        root.path().join("operations.sqlite"),
        Bounds {
            max_runtime_seconds: runtime,
            ..Bounds::default()
        },
        100,
    )
    .unwrap();
    let clock = Arc::new(AtomicI64::new(100));
    let tick = clock.clone();
    let runner = Runner::new(
        JournalGroup::new(journal),
        Logs::new(&root.path().join("logs"), 262144).unwrap(),
        Config {
            scope: "scope".into(),
            poll_seconds: 0.005,
            adapter_seconds: adapter,
        },
        schema,
        Some(Arc::new(move || tick.load(Ordering::Relaxed))),
    )
    .unwrap();
    (root, runner, clock)
}
async fn next(runner: &Runner, kind: &str) -> Command {
    loop {
        // Recovery may still be returning from SQLite's two-second busy wait
        // after a lock is released. This is not a production latency assertion.
        let command = timeout(Duration::from_secs(3), runner.bridge.next())
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        if command.kind == "abort" {
            continue;
        }
        assert_eq!(command.kind, kind);
        return command;
    }
}
async fn respond(runner: &Runner, command: &Command, response: &str) {
    assert!(runner.bridge.respond(command.id, response).unwrap());
    tokio::task::yield_now().await;
}
async fn state(runner: &Runner, id: &str, expected: &str) {
    timeout(Duration::from_secs(1), async {
        loop {
            if runner
                .status(id.into())
                .await
                .unwrap()
                .is_some_and(|status| status.state == expected)
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(1)).await;
        }
    })
    .await
    .unwrap();
}

#[tokio::test]
async fn native_submit_persists_uncertainty_before_delivery_then_validates_success() {
    let (_root, runner, _clock) = fixture(
        100,
        0.2,
        r#"{"type":"object","required":["value"],"properties":{"value":{"type":"integer"}}}"#,
    );
    runner
        .reserve(Some("op".into()), r#"{"number":1.0}"#.into())
        .await
        .unwrap();
    runner.start().await.unwrap();
    runner.start().await.unwrap();
    let submit = next(&runner, "submit").await;
    let pending = runner.status("op".into()).await.unwrap().unwrap();
    assert_eq!(pending.submission, "uncertain");
    assert_eq!(pending.state, "running");
    assert_eq!(submit.payload.as_deref(), Some(r#"{"number":1.0}"#));
    respond(
        &runner,
        &submit,
        r#"{"kind":"accepted","reference":"backend-original"}"#,
    )
    .await;
    let observe = next(&runner, "observe").await;
    assert_eq!(observe.reference.as_deref(), Some("backend-original"));
    respond(
        &runner,
        &observe,
        r#"{"kind":"observed","state":"succeeded","result":{"value":2}}"#,
    )
    .await;
    state(&runner, "op", "succeeded").await;
    let done = runner.status("op".into()).await.unwrap().unwrap();
    assert_eq!(done.progress, Some(1.0));
    assert_eq!(done.backend_reference.as_deref(), Some("backend-original"));
    assert_eq!(done.result_json.as_deref(), Some(r#"{"value":2}"#));
    runner.stop().await;
}

#[tokio::test]
async fn timeout_and_stop_while_submit_is_inflight_never_replay_the_create() {
    for stop in [false, true] {
        let (_root, runner, _) = fixture(100, 0.02, "{}");
        runner
            .reserve(Some("op".into()), "{}".into())
            .await
            .unwrap();
        runner.start().await.unwrap();
        let submit = next(&runner, "submit").await;
        if stop {
            runner.stop().await;
        } else {
            state(&runner, "op", "interrupted").await;
            runner.stop().await;
        }
        let status = runner.status("op".into()).await.unwrap().unwrap();
        assert_eq!(status.submission, "uncertain");
        assert!(status.backend_reference.is_none());
        assert_eq!(status.finished_at, None);
        assert!(
            !runner
                .bridge
                .respond(submit.id, r#"{"kind":"accepted","reference":"late"}"#)
                .unwrap()
        );
        runner.start().await.unwrap();
        assert!(
            timeout(Duration::from_millis(30), next(&runner, "submit"))
                .await
                .is_err()
        );
        runner.stop().await;
    }
}

#[tokio::test]
async fn refusal_is_pending_failure_and_a_new_id_can_still_run() {
    let (_root, runner, _) = fixture(100, 0.2, "{}");
    runner
        .reserve(Some("refused".into()), "{}".into())
        .await
        .unwrap();
    runner.start().await.unwrap();
    let submit = next(&runner, "submit").await;
    respond(
        &runner,
        &submit,
        r#"{"kind":"refused","reason":"operator-safe refusal"}"#,
    )
    .await;
    state(&runner, "refused", "failed").await;
    assert_eq!(
        runner
            .status("refused".into())
            .await
            .unwrap()
            .unwrap()
            .submission,
        "pending"
    );
    runner
        .reserve(Some("later".into()), "{}".into())
        .await
        .unwrap();
    assert_eq!(
        next(&runner, "submit").await.operation_id.as_deref(),
        Some("later")
    );
    runner.stop().await;
}

#[tokio::test]
async fn cancel_acknowledgment_does_not_claim_the_backend_has_stopped() {
    let (_root, runner, _) = fixture(100, 0.2, "{}");
    runner
        .reserve(Some("op".into()), "{}".into())
        .await
        .unwrap();
    runner.start().await.unwrap();
    let submit = next(&runner, "submit").await;
    respond(
        &runner,
        &submit,
        r#"{"kind":"accepted","reference":"original"}"#,
    )
    .await;
    let observe = next(&runner, "observe").await;
    runner.cancel("op".into()).await.unwrap();
    respond(
        &runner,
        &observe,
        r#"{"kind":"observed","state":"running","progress":0.5}"#,
    )
    .await;
    let cancel = next(&runner, "cancel").await;
    assert_eq!(cancel.reference.as_deref(), Some("original"));
    respond(&runner, &cancel, r#"{"kind":"cancel_accepted"}"#).await;
    let observe = next(&runner, "observe").await;
    let status = runner.status("op".into()).await.unwrap().unwrap();
    assert_eq!(status.state, "running");
    assert_eq!(status.stage.as_deref(), Some("cancelling"));
    respond(
        &runner,
        &observe,
        r#"{"kind":"observed","state":"cancelled"}"#,
    )
    .await;
    state(&runner, "op", "cancelled").await;
    runner.stop().await;
}

#[tokio::test]
async fn stopped_accepted_work_reconciles_original_reference_before_new_work() {
    let (_root, runner, _) = fixture(100, 0.2, "{}");
    runner
        .reserve(Some("old".into()), "{}".into())
        .await
        .unwrap();
    runner.start().await.unwrap();
    let submit = next(&runner, "submit").await;
    respond(
        &runner,
        &submit,
        r#"{"kind":"accepted","reference":"accepted-original"}"#,
    )
    .await;
    let old = next(&runner, "observe").await;
    runner.stop().await;
    assert_eq!(
        runner.status("old".into()).await.unwrap().unwrap().state,
        "interrupted"
    );
    assert!(
        !runner
            .bridge
            .respond(
                old.id,
                r#"{"kind":"observed","state":"succeeded","result":{}}"#
            )
            .unwrap()
    );
    runner
        .reserve(Some("new".into()), "{}".into())
        .await
        .unwrap();
    runner.start().await.unwrap();
    let reconcile = next(&runner, "observe").await;
    assert_eq!(reconcile.reference.as_deref(), Some("accepted-original"));
    respond(
        &runner,
        &reconcile,
        r#"{"kind":"observed","state":"succeeded","result":{}}"#,
    )
    .await;
    assert_eq!(
        next(&runner, "submit").await.operation_id.as_deref(),
        Some("new")
    );
    runner.stop().await;
}

#[tokio::test]
async fn overdue_unanswered_job_stays_owned_and_no_longer_blocks_the_queue() {
    let (_root, runner, clock) = fixture(1, 0.015, "{}");
    runner
        .reserve(Some("old".into()), "{}".into())
        .await
        .unwrap();
    runner.start().await.unwrap();
    let submit = next(&runner, "submit").await;
    respond(
        &runner,
        &submit,
        r#"{"kind":"accepted","reference":"owned-old"}"#,
    )
    .await;
    let observe = next(&runner, "observe").await;
    clock.store(102, Ordering::Relaxed);
    respond(
        &runner,
        &observe,
        r#"{"kind":"observed","state":"running"}"#,
    )
    .await;
    let cancel = next(&runner, "cancel").await;
    respond(&runner, &cancel, r#"{"kind":"cancel_accepted"}"#).await;
    for _ in 0..3 {
        let _ = next(&runner, "observe").await;
        tokio::time::sleep(Duration::from_millis(25)).await;
    }
    state(&runner, "old", "interrupted").await;
    let owned = runner.status("old".into()).await.unwrap().unwrap();
    assert_eq!(owned.backend_reference.as_deref(), Some("owned-old"));
    assert_eq!(owned.error.as_deref(), Some(OVERDUE_ERROR));
    runner
        .reserve(Some("new".into()), "{}".into())
        .await
        .unwrap();
    assert_eq!(
        next(&runner, "submit").await.operation_id.as_deref(),
        Some("new")
    );
    runner.stop().await;
}

#[tokio::test]
async fn result_schema_and_storage_bounds_fail_the_result_without_replaying() {
    for (schema, result, error) in [
        (
            r#"{"type":"object","required":["expected"]}"#,
            r#"{"other":1}"#.to_owned(),
            "operation result does not match its schema",
        ),
        (
            "{}",
            format!(r#"{{"text":"{}"}}"#, "x".repeat(8192)),
            "operation result exceeds the bound",
        ),
    ] {
        let (_root, runner, _) = fixture(100, 0.2, schema);
        runner
            .reserve(Some("op".into()), "{}".into())
            .await
            .unwrap();
        runner.start().await.unwrap();
        let submit = next(&runner, "submit").await;
        respond(&runner, &submit, r#"{"kind":"accepted","reference":"job"}"#).await;
        let observe = next(&runner, "observe").await;
        respond(
            &runner,
            &observe,
            &format!(r#"{{"kind":"observed","state":"succeeded","result":{result}}}"#),
        )
        .await;
        state(&runner, "op", "failed").await;
        let status = runner.status("op".into()).await.unwrap().unwrap();
        assert!(status.result_json.is_none());
        assert_eq!(status.error.as_deref(), Some(error));
        runner.stop().await;
    }
}

#[tokio::test]
async fn scoped_default_id_and_queued_cancel_keep_the_legacy_identity() {
    let (_root, runner, _) = fixture(100, 0.2, "{}");
    let created = runner
        .reserve(None, r#"{"number":1.0}"#.into())
        .await
        .unwrap();
    let digest = codec::input_digest("scope", r#"{"number":1.0}"#).unwrap();
    assert_eq!(created.input_digest, digest);
    assert_eq!(created.operation_id, "4abb62911bdfdc0c8a850ed6218868f8");
    // The legacy default ID hashes raw digest ASCII, not its JSON string form.
    // Confirm against a fixed Python-generated receipt instead of regenerating here.
    runner.cancel(created.operation_id.clone()).await.unwrap();
    assert_eq!(
        runner
            .status(created.operation_id)
            .await
            .unwrap()
            .unwrap()
            .state,
        "cancelled"
    );
    runner.stop().await;
}

#[tokio::test]
async fn legacy_fixture_recovers_without_any_submission_for_accepted_or_uncertain_rows() {
    let root = tempfile::tempdir().unwrap();
    fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let path = root.path().join("operations.sqlite");
    let conn = rusqlite::Connection::open(&path).unwrap();
    conn.execute_batch(include_str!(
        "../../tests/fixtures/author-baseline/sdk-0.4.0-journal.sql"
    ))
    .unwrap();
    drop(conn);
    fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
    let journal = Journal::open(&path, Bounds::default(), 2000).unwrap();
    let runner = Runner::new(
        JournalGroup::new(journal),
        Logs::new(&root.path().join("logs"), 262144).unwrap(),
        Config {
            scope: "legacy-scope".into(),
            poll_seconds: 0.005,
            adapter_seconds: 0.2,
        },
        "{}",
        Some(Arc::new(|| 2000)),
    )
    .unwrap();
    runner.start().await.unwrap();
    let cancel = next(&runner, "cancel").await;
    assert_eq!(cancel.reference.as_deref(), Some("synthetic-backend-job"));
    respond(&runner, &cancel, r#"{"kind":"cancel_accepted"}"#).await;
    let observe = next(&runner, "observe").await;
    respond(
        &runner,
        &observe,
        r#"{"kind":"observed","state":"cancelled"}"#,
    )
    .await;
    state(&runner, "accepted", "cancelled").await;
    runner.stop().await;
    assert_eq!(
        runner
            .status("uncertain".into())
            .await
            .unwrap()
            .unwrap()
            .submission,
        "uncertain"
    );
    assert_eq!(
        runner
            .status("accepted".into())
            .await
            .unwrap()
            .unwrap()
            .submitted_at,
        1000
    );
}

#[test]
fn logging_is_scoped_bounded_and_never_follows_a_planted_link() {
    use std::os::unix::fs::symlink;
    let root = tempfile::tempdir().unwrap();
    fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let directory = root.path().join("logs");
    let logs = Logs::new(&directory, 32).unwrap();
    logs.append("scope", "op", "safe line");
    assert_eq!(logs.read("scope", "op", 0).unwrap().text, "safe line\n");
    assert_eq!(logs.read("other", "op", 0).unwrap().text, "");
    logs.append("scope", "op", &"x".repeat(40));
    assert_eq!(logs.read("scope", "op", 0).unwrap().text, "safe line\n");
    let path = fs::read_dir(&directory)
        .unwrap()
        .next()
        .unwrap()
        .unwrap()
        .path();
    fs::remove_file(&path).unwrap();
    let target = root.path().join("untouched");
    fs::write(&target, "original").unwrap();
    symlink(&target, &path).unwrap();
    logs.append("scope", "op", "must not land");
    assert_eq!(logs.read("scope", "op", 0).unwrap().text, "");
    assert_eq!(fs::read_to_string(target).unwrap(), "original");
}

#[tokio::test]
async fn malformed_active_observation_does_not_terminalize_owned_backend_work() {
    let (_root, runner, _) = fixture(100, 0.2, "{}");
    runner
        .reserve(Some("op".into()), "{}".into())
        .await
        .unwrap();
    runner.start().await.unwrap();
    let submit = next(&runner, "submit").await;
    respond(
        &runner,
        &submit,
        r#"{"kind":"accepted","reference":"owned"}"#,
    )
    .await;
    let observe = next(&runner, "observe").await;
    respond(
        &runner,
        &observe,
        r#"{"kind":"observed","state":"interrupted","progress":2.0}"#,
    )
    .await;
    let observe = next(&runner, "observe").await;
    assert_eq!(
        runner.status("op".into()).await.unwrap().unwrap().state,
        "running"
    );
    respond(
        &runner,
        &observe,
        r#"{"kind":"observed","state":"succeeded","result":{}}"#,
    )
    .await;
    state(&runner, "op", "succeeded").await;
    runner.stop().await;
}

#[tokio::test]
async fn overlong_reference_stays_uncertain_and_a_late_cancel_failure_cannot_hide_success() {
    let (_root, runner, _) = fixture(100, 0.2, "{}");
    runner
        .reserve(Some("long".into()), "{}".into())
        .await
        .unwrap();
    runner.start().await.unwrap();
    let submit = next(&runner, "submit").await;
    respond(
        &runner,
        &submit,
        &format!(
            r#"{{"kind":"accepted","reference":"{}"}}"#,
            "x".repeat(1025)
        ),
    )
    .await;
    state(&runner, "long", "interrupted").await;
    assert!(
        runner
            .status("long".into())
            .await
            .unwrap()
            .unwrap()
            .backend_reference
            .is_none()
    );
    runner
        .reserve(Some("op".into()), "{}".into())
        .await
        .unwrap();
    let submit = next(&runner, "submit").await;
    respond(
        &runner,
        &submit,
        r#"{"kind":"accepted","reference":"actual-job"}"#,
    )
    .await;
    let observe = next(&runner, "observe").await;
    runner.cancel("op".into()).await.unwrap();
    respond(
        &runner,
        &observe,
        r#"{"kind":"observed","state":"running"}"#,
    )
    .await;
    let cancel = next(&runner, "cancel").await;
    respond(
        &runner,
        &cancel,
        r#"{"kind":"failed","error_type":"RuntimeError"}"#,
    )
    .await;
    let observe = next(&runner, "observe").await;
    respond(
        &runner,
        &observe,
        r#"{"kind":"observed","state":"succeeded","result":{}}"#,
    )
    .await;
    state(&runner, "op", "succeeded").await;
    runner.stop().await;
}

#[tokio::test]
async fn scopes_sharing_a_journal_cannot_submit_or_cancel_each_others_rows() {
    let root = tempfile::tempdir().unwrap();
    fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let journal = JournalGroup::new(
        Journal::open(
            root.path().join("operations.sqlite"),
            Bounds::default(),
            100,
        )
        .unwrap(),
    );
    let build = |scope: &str| {
        Runner::new(
            journal.clone(),
            Logs::new(&root.path().join("logs"), 262144).unwrap(),
            Config {
                scope: scope.into(),
                poll_seconds: 0.005,
                adapter_seconds: 0.2,
            },
            "{}",
            Some(Arc::new(|| 100)),
        )
        .unwrap()
    };
    let first = build("first");
    let second = build("second");
    first
        .reserve(Some("same".into()), r#"{"scope":"first"}"#.into())
        .await
        .unwrap();
    second
        .reserve(Some("same".into()), r#"{"scope":"second"}"#.into())
        .await
        .unwrap();
    first.cancel("same".into()).await.unwrap();
    assert_eq!(
        second.status("same".into()).await.unwrap().unwrap().state,
        "queued"
    );
    second.start().await.unwrap();
    let submit = next(&second, "submit").await;
    assert_eq!(submit.payload.as_deref(), Some(r#"{"scope":"second"}"#));
    assert!(first.status("unknown".into()).await.unwrap().is_none());
    first.stop().await;
    second.stop().await;
}

#[tokio::test]
async fn runtime_bound_survives_reconciliation_and_never_treats_ack_as_termination() {
    let (_root, runner, clock) = fixture(1, 0.2, "{}");
    runner
        .reserve(Some("op".into()), "{}".into())
        .await
        .unwrap();
    runner.start().await.unwrap();
    let submit = next(&runner, "submit").await;
    respond(
        &runner,
        &submit,
        r#"{"kind":"accepted","reference":"owned"}"#,
    )
    .await;
    let _ = next(&runner, "observe").await;
    runner.stop().await;
    clock.store(102, Ordering::Relaxed);
    runner.start().await.unwrap();
    let cancel = next(&runner, "cancel").await;
    respond(&runner, &cancel, r#"{"kind":"cancel_accepted"}"#).await;
    let observe = next(&runner, "observe").await;
    assert_eq!(
        runner.status("op".into()).await.unwrap().unwrap().state,
        "running"
    );
    respond(
        &runner,
        &observe,
        r#"{"kind":"observed","state":"cancelled"}"#,
    )
    .await;
    state(&runner, "op", "cancelled").await;
    assert_eq!(
        runner
            .status("op".into())
            .await
            .unwrap()
            .unwrap()
            .error
            .as_deref(),
        Some("runtime bound exceeded")
    );
    runner.stop().await;
}

#[tokio::test]
async fn stop_wakes_an_idle_dispatcher_and_can_restart_without_an_extra_worker() {
    let (_root, runner, _) = fixture(100, 0.2, "{}");
    runner.start().await.unwrap();
    let bridge = runner.bridge.clone();
    let dispatcher = tokio::spawn(async move { bridge.next().await });
    tokio::task::yield_now().await;
    runner.stop().await;
    assert!(dispatcher.await.unwrap().unwrap().is_none());
    runner.start().await.unwrap();
    runner
        .reserve(Some("new".into()), "{}".into())
        .await
        .unwrap();
    let _ = next(&runner, "submit").await;
    runner.stop().await;
}

#[test]
fn schema_construction_never_fetches_network_or_files() {
    let root = tempfile::tempdir().unwrap();
    fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let journal = JournalGroup::new(
        Journal::open(
            root.path().join("operations.sqlite"),
            Bounds::default(),
            100,
        )
        .unwrap(),
    );
    for schema in [
        r#"{"$ref":"https://example.invalid/secret-schema"}"#,
        r#"{"$ref":"file:///synthetic/private-schema"}"#,
    ] {
        assert!(
            Runner::new(
                journal.clone(),
                Logs::new(&root.path().join("logs"), 262144).unwrap(),
                Config {
                    scope: "scope".into(),
                    poll_seconds: 0.005,
                    adapter_seconds: 0.2
                },
                schema,
                None
            )
            .is_err()
        );
    }
}

#[tokio::test]
async fn adapter_reports_nonfinite_result_as_unservable_without_replaying_backend_work() {
    let (_root, runner, _) = fixture(100, 0.2, "{}");
    runner
        .reserve(Some("op".into()), "{}".into())
        .await
        .unwrap();
    runner.start().await.unwrap();
    let submit = next(&runner, "submit").await;
    respond(
        &runner,
        &submit,
        r#"{"kind":"accepted","reference":"owned"}"#,
    )
    .await;
    let observe = next(&runner, "observe").await;
    respond(
        &runner,
        &observe,
        r#"{"kind":"observed","state":"succeeded","result_refused":"nonfinite"}"#,
    )
    .await;
    state(&runner, "op", "failed").await;
    let status = runner.status("op".into()).await.unwrap().unwrap();
    assert_eq!(
        status.error.as_deref(),
        Some("operation result holds a non-finite number")
    );
    assert!(status.result_json.is_none());
    assert_eq!(status.backend_reference.as_deref(), Some("owned"));
    runner.stop().await;
}

#[tokio::test]
async fn duplicate_scope_worker_is_fenced_before_any_callback_can_be_delivered() {
    let root = tempfile::tempdir().unwrap();
    fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let path = root.path().join("operations.sqlite");
    let group = JournalGroup::new(Journal::open(&path, Bounds::default(), 100).unwrap());
    let build = || {
        Runner::new(
            group.clone(),
            Logs::new(&root.path().join("logs"), 262144).unwrap(),
            Config {
                scope: "same".into(),
                poll_seconds: 0.005,
                adapter_seconds: 0.2,
            },
            "{}",
            Some(Arc::new(|| 100)),
        )
        .unwrap()
    };
    let first = build();
    let second = build();
    first.reserve(Some("op".into()), "{}".into()).await.unwrap();
    first.start().await.unwrap();
    assert!(second.start().await.is_err());
    let submit = next(&first, "submit").await;
    assert_eq!(submit.operation_id.as_deref(), Some("op"));
    assert!(group.close().is_err());
    assert!(group.release_ownership().is_err());
    first.stop().await;
    second.start().await.unwrap();
    assert!(
        timeout(Duration::from_millis(30), next(&second, "submit"))
            .await
            .is_err()
    );
    second.stop().await;
    group.close().unwrap();
}

#[test]
fn separate_journal_handles_claim_once_and_cancellation_cannot_overwrite_uncertainty() {
    let root = tempfile::tempdir().unwrap();
    fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let path = root.path().join("operations.sqlite");
    let mut first = Journal::open(&path, Bounds::default(), 100).unwrap();
    let second = Journal::open(&path, Bounds::default(), 100).unwrap();
    let digest = codec::input_digest("scope", "{}").unwrap();
    first.reserve("op", &digest, "{}", "scope", 100).unwrap();
    let claimed = first.claim_next("scope", 101).unwrap().unwrap();
    assert_eq!(claimed.submission, "uncertain");
    assert_eq!(claimed.state, "running");
    assert!(second.claim_next("scope", 101).unwrap().is_none());
    let cancelled = second.cancel_intent("op", "scope", 102).unwrap();
    assert_eq!(cancelled.state, "running");
    assert_eq!(cancelled.submission, "uncertain");
    assert!(first.cancel_requested("op", "scope").unwrap());
    assert!(
        first
            .status("op", "scope", 102)
            .unwrap()
            .unwrap()
            .finished_at
            .is_none()
    );
    first
        .reserve("queued", &digest, "{}", "scope", 103)
        .unwrap();
    assert_eq!(
        second.cancel_intent("queued", "scope", 104).unwrap().state,
        "cancelled"
    );
    assert!(first.claim_next("scope", 105).unwrap().is_none());
}

#[test]
fn recovery_refuses_a_live_process_owner_before_rewriting_running_state() {
    let root = tempfile::tempdir().unwrap();
    fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let path = root.path().join("operations.sqlite");
    let mut journal = Journal::open(&path, Bounds::default(), 100).unwrap();
    let digest = codec::input_digest("scope", "{}").unwrap();
    journal.reserve("op", &digest, "{}", "scope", 100).unwrap();
    journal.claim_next("scope", 101).unwrap();
    let owner = journal.ownership().unwrap();
    assert!(matches!(
        Journal::open(&path, Bounds::default(), 102),
        Err(skulk_capability_kernel::journal::JournalError::Busy)
    ));
    assert_eq!(
        journal.status("op", "scope", 102).unwrap().unwrap().state,
        "running"
    );
    drop(owner);
    let recovered = Journal::open(&path, Bounds::default(), 103).unwrap();
    assert_eq!(
        recovered.status("op", "scope", 103).unwrap().unwrap().state,
        "interrupted"
    );
}

#[test]
fn timing_validation_refuses_overflow_zero_rounded_and_unbounded_durations() {
    for seconds in [f64::MAX, f64::MIN_POSITIVE, 86401.0, f64::NAN, 0.0, -1.0] {
        assert!(
            Config {
                scope: "scope".into(),
                poll_seconds: seconds,
                adapter_seconds: 1.0
            }
            .validate()
            .is_err()
        );
        assert!(
            Config {
                scope: "scope".into(),
                poll_seconds: 1.0,
                adapter_seconds: seconds
            }
            .validate()
            .is_err()
        );
    }
    assert!(
        Config {
            scope: "scope".into(),
            poll_seconds: 0.005,
            adapter_seconds: 0.02
        }
        .validate()
        .is_ok()
    );
}

#[test]
fn concurrent_claim_and_cancel_have_one_serializable_outcome() {
    let root = tempfile::tempdir().unwrap();
    fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let path = root.path().join("operations.sqlite");
    let mut first = Journal::open(&path, Bounds::default(), 100).unwrap();
    let second = Journal::open(&path, Bounds::default(), 100).unwrap();
    let digest = codec::input_digest("scope", "{}").unwrap();
    first.reserve("op", &digest, "{}", "scope", 100).unwrap();
    let gate = Arc::new(std::sync::Barrier::new(2));
    let one = gate.clone();
    let two = gate.clone();
    let claim = std::thread::spawn(move || {
        one.wait();
        first.claim_next("scope", 101).unwrap()
    });
    let cancel = std::thread::spawn(move || {
        two.wait();
        second.cancel_intent("op", "scope", 101).unwrap()
    });
    let claimed = claim.join().unwrap();
    let cancelled = cancel.join().unwrap();
    if let Some(claimed) = claimed {
        assert_eq!(claimed.state, "running");
        assert_eq!(cancelled.state, "running");
        assert_eq!(cancelled.submission, "uncertain");
    } else {
        assert_eq!(cancelled.state, "cancelled");
        assert_eq!(cancelled.submission, "pending");
    }
}

#[tokio::test]
async fn transient_dequeue_storage_error_retries_existing_intent_without_another_wake() {
    let root = tempfile::tempdir().unwrap();
    fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let path = root.path().join("operations.sqlite");
    let mut journal = Journal::open(&path, Bounds::default(), 100).unwrap();
    let digest = codec::input_digest("scope", "{}").unwrap();
    journal.reserve("op", &digest, "{}", "scope", 100).unwrap();
    let group = JournalGroup::new(journal);
    let runner = Runner::new(
        group,
        Logs::new(&root.path().join("logs"), 262144).unwrap(),
        Config {
            scope: "scope".into(),
            poll_seconds: 0.005,
            adapter_seconds: 0.2,
        },
        "{}",
        Some(Arc::new(|| 100)),
    )
    .unwrap();
    let connection = rusqlite::Connection::open(&path).unwrap();
    connection.execute_batch("BEGIN IMMEDIATE;").unwrap();
    runner.start().await.unwrap();
    // Both the interrupted-row query and the claim can meet an exhausted busy
    // timeout. Releasing the writer must retry the already queued reservation;
    // this test deliberately emits no reserve/enqueue notification at all.
    tokio::time::sleep(Duration::from_millis(4300)).await;
    connection.execute_batch("COMMIT;").unwrap();
    let command = timeout(Duration::from_secs(3), runner.bridge.next())
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    assert_eq!(command.kind, "submit");
    assert_eq!(command.operation_id.as_deref(), Some("op"));
    runner.stop().await;
}

#[tokio::test]
async fn unreadable_accepted_record_blocks_fresh_work_until_recovery_succeeds() {
    let (root, runner, _) = fixture(100, 0.2, "{}");
    runner
        .reserve(Some("owned".into()), "{}".into())
        .await
        .unwrap();
    runner.start().await.unwrap();
    let submit = next(&runner, "submit").await;
    respond(
        &runner,
        &submit,
        r#"{"kind":"accepted","reference":"accepted-original"}"#,
    )
    .await;
    next(&runner, "observe").await;
    runner.stop().await;
    runner
        .reserve(Some("fresh".into()), "{}".into())
        .await
        .unwrap();
    let connection = rusqlite::Connection::open(root.path().join("operations.sqlite")).unwrap();
    // SQLite accepts this storage fault, but decoding Status refuses the text
    // as a float. The accepted-ID query still succeeds and the fresh row is
    // readable: treating the failed status read as absent would admit it.
    connection
        .execute(
            "UPDATE operations SET progress='temporarily unreadable' WHERE operation_id='owned'",
            [],
        )
        .unwrap();
    assert!(runner.status("owned".into()).await.is_err());
    runner.start().await.unwrap();
    assert!(
        timeout(Duration::from_millis(80), next(&runner, "submit"))
            .await
            .is_err()
    );
    connection
        .execute(
            "UPDATE operations SET progress=0.0 WHERE operation_id='owned'",
            [],
        )
        .unwrap();
    // Repair emits no enqueue notification. The polling retry must reobserve
    // the exact retained reference before delivering the fresh reservation.
    let observe = next(&runner, "observe").await;
    assert_eq!(observe.reference.as_deref(), Some("accepted-original"));
    respond(
        &runner,
        &observe,
        r#"{"kind":"observed","state":"succeeded","result":{}}"#,
    )
    .await;
    assert_eq!(
        next(&runner, "submit").await.operation_id.as_deref(),
        Some("fresh")
    );
    runner.stop().await;
}

#[tokio::test]
async fn restart_same_handle_recovers_accepted_work_after_locked_shutdown_cleanup() {
    let (root, runner, _) = fixture(100, 2.0, "{}");
    runner
        .reserve(Some("owned".into()), "{}".into())
        .await
        .unwrap();
    runner.start().await.unwrap();
    let submit = next(&runner, "submit").await;
    respond(
        &runner,
        &submit,
        r#"{"kind":"accepted","reference":"accepted-original"}"#,
    )
    .await;
    next(&runner, "observe").await;
    runner
        .reserve(Some("fresh".into()), "{}".into())
        .await
        .unwrap();
    let connection = rusqlite::Connection::open(root.path().join("operations.sqlite")).unwrap();
    connection.execute_batch("BEGIN IMMEDIATE;").unwrap();
    // The independent writer outlasts the cleanup status read's busy timeout. Stop
    // remains bounded, but cannot persist the interrupted transition yet.
    timeout(Duration::from_secs(10), runner.stop())
        .await
        .unwrap();
    connection.execute_batch("COMMIT;").unwrap();
    let retained = runner.status("owned".into()).await.unwrap().unwrap();
    assert_eq!(retained.state, "running");
    assert_eq!(
        retained.backend_reference.as_deref(),
        Some("accepted-original")
    );
    runner.start().await.unwrap();
    let observe = next(&runner, "observe").await;
    assert_eq!(observe.reference.as_deref(), Some("accepted-original"));
    assert_eq!(
        connection
            .query_row(
                "SELECT started_at FROM operations WHERE operation_id='owned'",
                [],
                |row| row.get::<_, i64>(0)
            )
            .unwrap(),
        100
    );
    respond(
        &runner,
        &observe,
        r#"{"kind":"observed","state":"succeeded","result":{}}"#,
    )
    .await;
    assert_eq!(
        next(&runner, "submit").await.operation_id.as_deref(),
        Some("fresh")
    );
    runner.stop().await;
}

#[tokio::test]
async fn failed_terminal_persistence_reconciles_owned_work_before_fresh_admission() {
    let (root, runner, _) = fixture(100, 0.2, "{}");
    runner
        .reserve(Some("owned".into()), "{}".into())
        .await
        .unwrap();
    runner.start().await.unwrap();
    let submit = next(&runner, "submit").await;
    respond(
        &runner,
        &submit,
        r#"{"kind":"accepted","reference":"accepted-original"}"#,
    )
    .await;
    let observed = next(&runner, "observe").await;
    runner
        .reserve(Some("fresh".into()), "{}".into())
        .await
        .unwrap();
    let connection = rusqlite::Connection::open(root.path().join("operations.sqlite")).unwrap();
    connection.execute_batch("BEGIN IMMEDIATE;").unwrap();
    respond(
        &runner,
        &observed,
        r#"{"kind":"observed","state":"succeeded","result":{}}"#,
    )
    .await;
    // Exhaust the terminal update and subsequent recovery read. Neither the
    // failure nor later lock release may turn owned work into an empty queue.
    assert!(
        timeout(Duration::from_secs(8), runner.bridge.next())
            .await
            .is_err()
    );
    connection.execute_batch("COMMIT;").unwrap();
    let observe = next(&runner, "observe").await;
    assert_eq!(observe.reference.as_deref(), Some("accepted-original"));
    respond(
        &runner,
        &observe,
        r#"{"kind":"observed","state":"succeeded","result":{}}"#,
    )
    .await;
    assert_eq!(
        next(&runner, "submit").await.operation_id.as_deref(),
        Some("fresh")
    );
    runner.stop().await;
}

#[tokio::test]
async fn terminal_storage_error_does_not_report_an_invalid_backend_result() {
    let (root, runner, _) = fixture(100, 0.2, "{}");
    runner
        .reserve(Some("owned".into()), "{}".into())
        .await
        .unwrap();
    runner.start().await.unwrap();
    let submit = next(&runner, "submit").await;
    respond(
        &runner,
        &submit,
        r#"{"kind":"accepted","reference":"accepted-original"}"#,
    )
    .await;
    let observed = next(&runner, "observe").await;
    runner
        .reserve(Some("fresh".into()), "{}".into())
        .await
        .unwrap();
    let connection = rusqlite::Connection::open(root.path().join("operations.sqlite")).unwrap();
    // A real SQLite failure affects this terminal write, while a fallback
    // failed-state write would succeed. Storage refusal must not be mistaken
    // for invalid backend data, regardless of busy-timeout scheduling.
    connection.execute_batch("CREATE TRIGGER fail_terminal BEFORE UPDATE OF state ON operations WHEN NEW.state='succeeded' BEGIN SELECT RAISE(FAIL,'synthetic storage refusal'); END;").unwrap();
    respond(
        &runner,
        &observed,
        r#"{"kind":"observed","state":"succeeded","result":{}}"#,
    )
    .await;
    let observe = next(&runner, "observe").await;
    assert_eq!(observe.reference.as_deref(), Some("accepted-original"));
    let status = runner.status("owned".into()).await.unwrap().unwrap();
    assert!(matches!(status.state.as_str(), "interrupted" | "running"));
    assert!(status.finished_at.is_none());
    assert_ne!(
        status.error.as_deref(),
        Some("operation result is unusable")
    );
    connection
        .execute_batch("DROP TRIGGER fail_terminal;")
        .unwrap();
    respond(
        &runner,
        &observe,
        r#"{"kind":"observed","state":"succeeded","result":{}}"#,
    )
    .await;
    assert_eq!(
        next(&runner, "submit").await.operation_id.as_deref(),
        Some("fresh")
    );
    assert_eq!(
        runner.status("owned".into()).await.unwrap().unwrap().state,
        "succeeded"
    );
    runner.stop().await;
}

#[tokio::test]
async fn inactive_facade_reservation_wakes_the_idle_scope_owner() {
    let root = tempfile::tempdir().unwrap();
    fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let group = JournalGroup::new(
        Journal::open(
            root.path().join("operations.sqlite"),
            Bounds::default(),
            100,
        )
        .unwrap(),
    );
    let build = || {
        Runner::new(
            group.clone(),
            Logs::new(&root.path().join("logs"), 262144).unwrap(),
            Config {
                scope: "scope".into(),
                poll_seconds: 0.005,
                adapter_seconds: 0.2,
            },
            "{}",
            Some(Arc::new(|| 100)),
        )
        .unwrap()
    };
    let owner = build();
    let facade = build();
    owner
        .reserve(Some("first".into()), "{}".into())
        .await
        .unwrap();
    owner.start().await.unwrap();
    assert!(facade.start().await.is_err());
    let submit = next(&owner, "submit").await;
    respond(
        &owner,
        &submit,
        r#"{"kind":"accepted","reference":"first-reference"}"#,
    )
    .await;
    let observe = next(&owner, "observe").await;
    respond(
        &owner,
        &observe,
        r#"{"kind":"observed","state":"succeeded","result":{}}"#,
    )
    .await;
    state(&owner, "first", "succeeded").await;
    assert!(
        timeout(Duration::from_millis(80), owner.bridge.next())
            .await
            .is_err()
    );
    facade
        .reserve(Some("second".into()), "{}".into())
        .await
        .unwrap();
    assert_eq!(
        next(&owner, "submit").await.operation_id.as_deref(),
        Some("second")
    );
    owner.stop().await;
    facade.stop().await;
}

#[tokio::test]
async fn oversized_source_or_canonical_input_refuses_before_reservation_and_bridge_delivery() {
    let (_root, runner, _) = fixture(100, 0.2, "{}");
    let oversized = format!(r#"{{"value":"{}"}}"#, "x".repeat(MAX_OPERATION_INPUT_BYTES));
    let expanded = format!(
        r#"{{"value":"{}"}}"#,
        "🦊".repeat(MAX_OPERATION_INPUT_BYTES / 8)
    );
    assert!(expanded.len() < MAX_OPERATION_INPUT_BYTES);
    for payload in [oversized, expanded] {
        assert!(
            runner
                .reserve(Some("bounded".into()), payload)
                .await
                .is_err()
        );
        assert!(runner.status("bounded".into()).await.unwrap().is_none());
    }
    let near_limit = format!(
        r#"{{"value":"{}"}}"#,
        "x".repeat(MAX_OPERATION_INPUT_BYTES - 16)
    );
    runner
        .reserve(Some("bounded".into()), near_limit.clone())
        .await
        .unwrap();
    runner.start().await.unwrap();
    let submit = next(&runner, "submit").await;
    assert_eq!(submit.payload.as_deref(), Some(near_limit.as_str()));
    assert!(submit.json().unwrap().len() <= MAX_OPERATION_INPUT_BYTES + 8192);
    runner.stop().await;
}

#[tokio::test]
async fn reservation_through_an_independent_handle_reaches_the_idle_owner() {
    let root = tempfile::tempdir().unwrap();
    fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let path = root.path().join("operations.sqlite");
    // Both handles open before ownership starts. They intentionally share no
    // in-process wake registry, just the durable database and supervisor fence.
    let first = JournalGroup::new(Journal::open(&path, Bounds::default(), 100).unwrap());
    let second = JournalGroup::new(Journal::open(&path, Bounds::default(), 100).unwrap());
    let build = |group| {
        Runner::new(
            group,
            Logs::new(&root.path().join("logs"), 262144).unwrap(),
            Config {
                scope: "scope".into(),
                poll_seconds: 0.005,
                adapter_seconds: 0.2,
            },
            "{}",
            Some(Arc::new(|| 100)),
        )
        .unwrap()
    };
    let owner = build(first);
    let facade = build(second);
    owner.start().await.unwrap();
    assert!(
        timeout(Duration::from_millis(80), owner.bridge.next())
            .await
            .is_err()
    );
    facade
        .reserve(Some("outside".into()), "{}".into())
        .await
        .unwrap();
    assert_eq!(
        next(&owner, "submit").await.operation_id.as_deref(),
        Some("outside")
    );
    owner.stop().await;
    facade.stop().await;
}

#[tokio::test]
async fn accepted_reference_waits_for_durability_across_stop_and_restarts_without_create() {
    let (root, runner, _) = fixture(100, 2.0, "{}");
    runner
        .reserve(Some("owned".into()), "{}".into())
        .await
        .unwrap();
    runner
        .reserve(Some("fresh".into()), "{}".into())
        .await
        .unwrap();
    runner.start().await.unwrap();
    let submit = next(&runner, "submit").await;
    let connection = rusqlite::Connection::open(root.path().join("operations.sqlite")).unwrap();
    connection.execute_batch("BEGIN IMMEDIATE;").unwrap();
    respond(
        &runner,
        &submit,
        r#"{"kind":"accepted","reference":"accepted-original"}"#,
    )
    .await;
    // Exhaust a real busy timeout before stop. The accepted identity must
    // remain owned even though it has not yet reached durable storage.
    assert!(
        timeout(Duration::from_millis(2500), runner.bridge.next())
            .await
            .is_err()
    );
    let clone = runner.clone();
    let mut stopping = tokio::spawn(async move { clone.stop().await });
    assert!(
        timeout(Duration::from_millis(80), &mut stopping)
            .await
            .is_err()
    );
    connection.execute_batch("COMMIT;").unwrap();
    timeout(Duration::from_secs(5), stopping)
        .await
        .unwrap()
        .unwrap();
    let retained = runner.status("owned".into()).await.unwrap().unwrap();
    assert_eq!(retained.submission, "accepted");
    assert_eq!(
        retained.backend_reference.as_deref(),
        Some("accepted-original")
    );
    assert_eq!(retained.state, "interrupted");
    runner.start().await.unwrap();
    let observe = next(&runner, "observe").await;
    assert_eq!(observe.reference.as_deref(), Some("accepted-original"));
    respond(
        &runner,
        &observe,
        r#"{"kind":"observed","state":"succeeded","result":{}}"#,
    )
    .await;
    assert_eq!(
        next(&runner, "submit").await.operation_id.as_deref(),
        Some("fresh")
    );
    runner.stop().await;
}

#[tokio::test]
async fn co_ready_accepted_reply_and_stop_preserve_the_returned_identity() {
    let (_root, runner, _) = fixture(100, 2.0, "{}");
    runner
        .reserve(Some("owned".into()), "{}".into())
        .await
        .unwrap();
    runner.start().await.unwrap();
    let submit = next(&runner, "submit").await;
    // No yield between reply and stop: both select branches become ready on
    // the worker's next poll, while the reply contains the only known identity.
    assert!(
        runner
            .bridge
            .respond(
                submit.id,
                r#"{"kind":"accepted","reference":"accepted-original"}"#
            )
            .unwrap()
    );
    runner.request_stop();
    runner.stop().await;
    let retained = runner.status("owned".into()).await.unwrap().unwrap();
    assert_eq!(retained.submission, "accepted");
    assert_eq!(
        retained.backend_reference.as_deref(),
        Some("accepted-original")
    );
    runner.start().await.unwrap();
    assert_eq!(
        next(&runner, "observe").await.reference.as_deref(),
        Some("accepted-original")
    );
    runner.stop().await;
}

#[tokio::test]
async fn definitive_refusal_survives_storage_retries_and_stop_without_uncertain_work() {
    let (root, runner, _) = fixture(100, 2.0, "{}");
    runner
        .reserve(Some("refused".into()), "{}".into())
        .await
        .unwrap();
    runner
        .reserve(Some("fresh".into()), "{}".into())
        .await
        .unwrap();
    runner.start().await.unwrap();
    let submit = next(&runner, "submit").await;
    let connection = rusqlite::Connection::open(root.path().join("operations.sqlite")).unwrap();
    // Refuse the definitive terminal write while leaving cleanup writes usable.
    // Losing the adapter response would otherwise create false uncertain work.
    connection.execute_batch("CREATE TRIGGER refuse_terminal BEFORE UPDATE OF state ON operations WHEN NEW.state='failed' BEGIN SELECT RAISE(FAIL,'synthetic transient refusal'); END;").unwrap();
    respond(
        &runner,
        &submit,
        r#"{"kind":"refused","reason":"provider declined"}"#,
    )
    .await;
    assert!(
        timeout(Duration::from_millis(80), runner.bridge.next())
            .await
            .is_err()
    );
    let clone = runner.clone();
    let mut stopping = tokio::spawn(async move { clone.stop().await });
    assert!(
        timeout(Duration::from_millis(80), &mut stopping)
            .await
            .is_err()
    );
    connection
        .execute_batch("DROP TRIGGER refuse_terminal;")
        .unwrap();
    timeout(Duration::from_secs(3), stopping)
        .await
        .unwrap()
        .unwrap();
    let refused = runner.status("refused".into()).await.unwrap().unwrap();
    assert_eq!(refused.state, "failed");
    assert_eq!(refused.submission, "pending");
    assert_eq!(refused.error.as_deref(), Some("provider declined"));
    assert!(refused.finished_at.is_some());
    runner.start().await.unwrap();
    assert_eq!(
        next(&runner, "submit").await.operation_id.as_deref(),
        Some("fresh")
    );
    runner.stop().await;
}

#[test]
fn scope_source_bytes_are_bounded_before_registration_or_digest_work() {
    let config = |scope| Config {
        scope,
        poll_seconds: 0.005,
        adapter_seconds: 0.2,
    };
    assert!(
        config("x".repeat(MAX_OPERATION_SCOPE_BYTES))
            .validate()
            .is_ok()
    );
    assert!(
        config("x".repeat(MAX_OPERATION_SCOPE_BYTES + 1))
            .validate()
            .is_err()
    );
    assert!(
        config("🦊".repeat(MAX_OPERATION_SCOPE_BYTES / 4 + 1))
            .validate()
            .is_err()
    );
    // Empty legacy namespaces remain supported; this is a byte bound rather
    // than a new interpretation of author/descriptor identity.
    assert!(config(String::new()).validate().is_ok());
}

#[tokio::test]
async fn terminal_close_during_ownership_wait_cannot_spawn_or_retain_an_orphan() {
    let (root, runner, _) = fixture(100, 2.0, "{}");
    runner
        .reserve(Some("queued".into()), "{}".into())
        .await
        .unwrap();
    let held = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(root.path().join("supervisor.lock"))
        .unwrap();
    held.lock().unwrap();
    let clone = runner.clone();
    let mut starting = tokio::spawn(async move { clone.start().await });
    assert!(
        timeout(Duration::from_millis(80), &mut starting)
            .await
            .is_err()
    );
    runner.request_close();
    drop(held);
    assert!(
        timeout(Duration::from_secs(3), starting)
            .await
            .unwrap()
            .unwrap()
            .is_err()
    );
    assert!(runner.start().await.is_err());
    let status = runner.status("queued".into()).await.unwrap().unwrap();
    assert_eq!(status.state, "queued");
    assert_eq!(status.submission, "pending");
    assert!(runner.bridge.next().await.unwrap().is_none());
    // A newly acquired startup lease must also be released on abort, while
    // pre-existing/manual ownership remains governed by its original owner.
    Journal::open(
        root.path().join("operations.sqlite"),
        Bounds::default(),
        101,
    )
    .unwrap();
    runner.stop().await;
}

#[tokio::test]
async fn invalid_result_and_stage_terminalize_safely_instead_of_reconciling_forever() {
    let (_root, runner, _) = fixture(100, 0.2, r#"{"type":"object","required":["required"]}"#);
    runner
        .reserve(Some("bad".into()), "{}".into())
        .await
        .unwrap();
    runner.start().await.unwrap();
    let submit = next(&runner, "submit").await;
    respond(
        &runner,
        &submit,
        r#"{"kind":"accepted","reference":"owned"}"#,
    )
    .await;
    let observe = next(&runner, "observe").await;
    respond(
        &runner,
        &observe,
        &format!(
            r#"{{"kind":"observed","state":"succeeded","stage":"{}","result":{{"wrong":1}}}}"#,
            "x".repeat(65)
        ),
    )
    .await;
    state(&runner, "bad", "failed").await;
    assert_eq!(
        runner
            .status("bad".into())
            .await
            .unwrap()
            .unwrap()
            .error
            .as_deref(),
        Some("operation result does not match its schema")
    );
    runner
        .reserve(Some("later".into()), "{}".into())
        .await
        .unwrap();
    assert_eq!(
        next(&runner, "submit").await.operation_id.as_deref(),
        Some("later")
    );
    runner.stop().await;
}
