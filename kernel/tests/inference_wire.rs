//! Explicit new wire identity, raw-body binding and independent partial-I/O clocks.
#![cfg(unix)]
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use skulk_capability_kernel::{
    calls, calls_v2, composition_v3, streams::StreamError, transport, transport_v2,
};
use std::time::Duration;
use tokio::io::{AsyncWriteExt, duplex};

fn context(payload: &[u8], deadline: Option<u64>) -> Value {
    let fixture: Value =
        serde_json::from_str(include_str!("../contracts/inference-v2-cases.json")).unwrap();
    let case = fixture["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|case| {
            case["name"] == "Context" && case["action"] == "parse" && !case["canonical"].is_null()
        })
        .unwrap();
    let mut value: Value = serde_json::from_str(case["canonical"].as_str().unwrap()).unwrap();
    value["contract_profile"] = json!(composition_v3::PROFILE);
    value["profile_digest"] = json!(composition_v3::profile_digest());
    value["protocol"] = json!(3);
    value["deadline_unix_ms"] = json!(deadline);
    value["request_sha256"] = json!(
        Sha256::digest(payload)
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>()
    );
    value
}
fn invocation(mode: &str, seconds: Option<f64>) -> (Value, Vec<u8>) {
    let payload = b"{ \"prompt\": \"synthetic input\" }".to_vec();
    let deadline = seconds.map(|_| 7_201_000);
    let value = json!({
        "protocol":2,"contract_profile":calls_v2::PROFILE,"profile_digest":calls_v2::profile_digest(),
        "kind":"invoke","payload_size":payload.len(),"call_id":"call-a","mode":mode,
        "context":context(&payload, deadline),"remaining_seconds":seconds
    });
    (value, payload)
}
#[test]
fn every_mode_preserves_uncapped_or_long_execution_and_exact_raw_body() {
    for mode in [
        "unary",
        "server_streaming",
        "client_streaming",
        "bidirectional",
    ] {
        for seconds in [None, Some(7200.0)] {
            let (value, payload) = invocation(mode, seconds);
            let frame = calls_v2::Frame::from_json(&value.to_string(), payload.clone()).unwrap();
            assert_eq!(frame.payload(), payload);
            let wire = calls_v2::encode(&frame);
            let roundtrip = calls_v2::decode(&wire).unwrap();
            assert_eq!(roundtrip.payload(), payload);
            assert_eq!(calls_v2::encode(&roundtrip), wire);
            assert!(calls::decode(&wire).is_err());
            let mut changed = payload;
            changed.push(b' ');
            assert!(calls_v2::Frame::from_json(&value.to_string(), changed).is_err());
        }
    }
}
#[test]
fn nullable_budget_must_match_context_and_cannot_use_an_older_context() {
    let (mut value, payload) = invocation("unary", None);
    value["remaining_seconds"] = json!(7200);
    assert!(calls_v2::payload_size(&value.to_string()).is_err());
    value["context"]["deadline_unix_ms"] = json!(7_201_000);
    value["remaining_seconds"] = Value::Null;
    assert!(calls_v2::Frame::from_json(&value.to_string(), payload.clone()).is_err());
    value["remaining_seconds"] = json!(7200);
    value["context"]["contract_profile"] = json!("skulk.composition/2");
    assert!(calls_v2::Frame::from_json(&value.to_string(), payload).is_err());
}
#[test]
fn streaming_start_requires_matched_optional_absolute_and_relative_budgets() {
    let mut value = json!({"protocol":2,"contract_profile":calls_v2::PROFILE,
        "profile_digest":calls_v2::profile_digest(),"kind":"delegate_started","payload_size":0,
        "parent_call_id":"parent","request_id":format!("{:064x}",1),"media_call_id":"child",
        "mode":"bidirectional","deadline_unix_ms":null,"remaining_seconds":null});
    assert!(calls_v2::Frame::from_json(&value.to_string(), vec![]).is_ok());
    value["remaining_seconds"] = json!(7200);
    assert!(calls_v2::Frame::from_json(&value.to_string(), vec![]).is_err());
    value["deadline_unix_ms"] = json!(7_201_000);
    assert!(calls_v2::Frame::from_json(&value.to_string(), vec![]).is_ok());
    value["media_call_id"] = value["parent_call_id"].clone();
    assert!(calls_v2::Frame::from_json(&value.to_string(), vec![]).is_err());
}
#[test]
fn transport2_admits_no_context_free_invoke_or_legacy_media_fallback() {
    let health = json!({"protocol":6,"contract_profile":transport_v2::PROFILE,
        "profile_digest":transport_v2::profile_digest(),"kind":"health","nonce":"probe",
        "ready":true,"surfaces":[]});
    let control = transport_v2::Control::from_json(&health.to_string()).unwrap();
    let bytes = transport_v2::encode_control(&control);
    assert_eq!(
        transport_v2::decode_control(&bytes).unwrap().canonical(),
        control.canonical()
    );
    assert!(transport::decode_control(&bytes).is_err());
    let mut invoke = health;
    invoke["kind"] = json!("invoke");
    assert!(transport_v2::Control::from_json(&invoke.to_string()).is_err());
    let packet = transport_v2::packet(
        r#"{"call_id":"call-a","direction":"caller_to_provider","sequence":0,"kind":"started"}"#,
        vec![],
    )
    .unwrap();
    let wire = transport_v2::encode_packet(&packet).unwrap();
    assert_eq!(
        transport_v2::encode_packet(&transport_v2::decode_packet(&wire).unwrap()).unwrap(),
        wire
    );
    assert!(transport::decode_packet(&wire).is_err());
}
#[tokio::test(start_paused = true)]
async fn media_silence_has_no_generation_timer_but_partial_prefix_is_bounded() {
    let (mut sender, mut receiver) = duplex(64);
    let read = tokio::spawn(async move { transport_v2::read_packet(&mut receiver).await });
    tokio::task::yield_now().await;
    tokio::time::advance(Duration::from_secs(1000)).await;
    assert!(!read.is_finished());
    sender.write_all(&[0]).await.unwrap();
    tokio::task::yield_now().await;
    tokio::time::advance(Duration::from_secs(29)).await;
    assert!(!read.is_finished());
    tokio::time::advance(Duration::from_secs(2)).await;
    assert!(matches!(read.await.unwrap(), Err(StreamError::Timeout)));
}
#[tokio::test(start_paused = true)]
async fn call_silence_has_no_generation_timer_but_partial_prefix_is_bounded() {
    let (mut sender, mut receiver) = duplex(64);
    let read = tokio::spawn(async move { calls_v2::read(&mut receiver).await });
    tokio::task::yield_now().await;
    tokio::time::advance(Duration::from_secs(1000)).await;
    assert!(!read.is_finished());
    sender.write_all(&[0]).await.unwrap();
    tokio::task::yield_now().await;
    tokio::time::advance(Duration::from_secs(31)).await;
    assert!(matches!(read.await.unwrap(), Err(StreamError::Timeout)));
}
#[tokio::test(start_paused = true)]
async fn partial_body_and_backpressured_write_cannot_hide_behind_uncapped_lifetime() {
    let (value, payload) = invocation("bidirectional", None);
    let frame = calls_v2::Frame::from_json(&value.to_string(), payload).unwrap();
    let wire = calls_v2::encode(&frame);
    let (mut sender, mut receiver) = duplex(wire.len());
    let read = tokio::spawn(async move { calls_v2::read(&mut receiver).await });
    sender.write_all(&wire[..wire.len() - 1]).await.unwrap();
    tokio::task::yield_now().await;
    tokio::time::advance(Duration::from_secs(31)).await;
    assert!(matches!(read.await.unwrap(), Err(StreamError::Timeout)));
    let (mut sender, _receiver) = duplex(1);
    let write = tokio::spawn(async move { calls_v2::write(&mut sender, &frame).await });
    tokio::task::yield_now().await;
    tokio::time::advance(Duration::from_secs(31)).await;
    assert!(matches!(write.await.unwrap(), Err(StreamError::Timeout)));
}
