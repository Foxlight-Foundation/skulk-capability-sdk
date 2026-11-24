#![cfg(unix)]
//! Protocol-five exercises the shared lifecycle on real sockets, independently of v4.
use serde_json::{Value, json};
use skulk_capability_kernel::{
    managed,
    streams::{self, Mode, Packet, Session, StreamError},
    transport,
};
use std::time::Duration;
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{UnixListener, UnixStream},
    time::timeout,
};
fn startup(socket: &str) -> managed::Record {
    let cases: Value =
        serde_json::from_str(include_str!("../contracts/managed-v1-cases.json")).unwrap();
    let case = cases["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["name"] == "Startup" && c.get("canonical").is_some())
        .unwrap();
    let mut value: Value = serde_json::from_str(case["canonical"].as_str().unwrap()).unwrap();
    value["socket"] = json!(socket);
    value["stream_socket"] = json!(socket);
    value["token"] = json!("a".repeat(64));
    managed::Record::from_json("Startup", &value.to_string()).unwrap()
}
fn packet(direction: &str, sequence: u64, kind: &str, extra: &str) -> Packet {
    transport::packet(&format!(r#"{{"call_id":"call","direction":"{direction}","sequence":{sequence},"kind":"{kind}"{extra}}}"#),vec![]).unwrap()
}
async fn connected(mode: Mode, seconds: f64) -> (tempfile::TempDir, Session, UnixStream) {
    let root = tempfile::Builder::new()
        .prefix("skulk-wire5-")
        .tempdir_in("/tmp")
        .unwrap();
    let path = root.path().join("sock");
    let listener = UnixListener::bind(&path).unwrap();
    let startup = startup(path.to_str().unwrap());
    let (session, peer) = tokio::join!(
        Session::open_portable(&startup, "call", mode, seconds),
        listener.accept()
    );
    let mut peer = peer.unwrap().0;
    transport::read_hello(&mut peer, &startup, Some("call"))
        .await
        .unwrap();
    (root, session.unwrap(), peer)
}
#[tokio::test]
async fn half_close_and_cleanup_ack_are_separate_and_terminal_waits_for_cleanup() {
    let (_root, session, mut peer) = connected(Mode::Duplex, 2.0).await;
    transport::write_packet(&mut peer, &packet("caller_to_provider", 0, "started", ""))
        .await
        .unwrap();
    transport::write_packet(&mut peer, &packet("caller_to_provider", 1, "completed", ""))
        .await
        .unwrap();
    assert_eq!(
        session
            .next_input()
            .await
            .unwrap()
            .unwrap()
            .projection_json()
            .unwrap(),
        packet("caller_to_provider", 0, "started", "")
            .projection_json()
            .unwrap()
    );
    assert!(session.next_input().await.unwrap().unwrap().is_terminal());
    assert!(session.next_input().await.unwrap().is_none());
    session
        .send_output(packet(
            "provider_to_caller",
            1,
            "chunk",
            r#", "payload":{"text":"ok"}"#,
        ))
        .await
        .unwrap();
    assert_eq!(
        transport::read_packet(&mut peer)
            .await
            .unwrap()
            .projection_json()
            .unwrap(),
        packet(
            "provider_to_caller",
            1,
            "chunk",
            r#", "payload":{"text":"ok"}"#
        )
        .projection_json()
        .unwrap()
    );
    session
        .send_output(packet("provider_to_caller", 2, "completed", ""))
        .await
        .unwrap();
    let mut byte = [0];
    assert!(
        timeout(Duration::from_millis(20), peer.read(&mut byte))
            .await
            .is_err()
    );
    session.finish().await.unwrap();
    assert!(
        transport::read_packet(&mut peer)
            .await
            .unwrap()
            .is_terminal()
    );
    assert!(transport::read_packet(&mut peer).await.unwrap().is_end());
    assert_eq!(peer.read(&mut byte).await.unwrap(), 0);
    assert_eq!(session.finish().await, Err(StreamError::Closed));
}

#[tokio::test]
async fn co_ready_ingress_faults_fence_a_held_success_for_both_input_modes() {
    for mode in [Mode::Client, Mode::Duplex] {
        for fault in ["cancelled", "gap", "half_close_data"] {
            let (_root, session, mut peer) = connected(mode, 2.0).await;
            transport::write_packet(&mut peer, &packet("caller_to_provider", 0, "started", ""))
                .await
                .unwrap();
            session.next_input().await.unwrap();
            session
                .send_output(packet("provider_to_caller", 1, "completed", ""))
                .await
                .unwrap();
            let bad = match fault {
                "cancelled" => packet(
                    "caller_to_provider",
                    1,
                    "cancelled",
                    r#", "error":{"code":"cancelled","message":"synthetic"}"#,
                ),
                "gap" => packet("caller_to_provider", 99, "chunk", r#", "payload":{}"#),
                _ => packet("caller_to_provider", 1, "completed", r#", "payload":{}"#),
            };
            transport::write_packet(&mut peer, &bad).await.unwrap();
            assert!(session.next_input().await.is_err());
            assert!(session.finish().await.is_err());
            assert!(peer.read_to_end(&mut vec![]).await.unwrap() == 0);
        }
    }
}

#[tokio::test]
async fn all_modes_refuse_invalid_direction_lifecycle_and_missing_terminal() {
    let (_root, session, _peer) = connected(Mode::Client, 2.0).await;
    assert_eq!(
        session
            .send_output(packet(
                "provider_to_caller",
                1,
                "chunk",
                r#", "payload":{}"#
            ))
            .await,
        Err(StreamError::ClientChunk)
    );
    let (_root, session, mut peer) = connected(Mode::Server, 2.0).await;
    transport::write_packet(&mut peer, &packet("caller_to_provider", 0, "started", ""))
        .await
        .unwrap();
    assert_eq!(
        session.next_input().await,
        Err(StreamError::UnexpectedInput)
    );
    session.close().await;
    let (_root, session, _peer) = connected(Mode::Server, 2.0).await;
    assert_eq!(session.finish().await, Err(StreamError::MissingTerminal));
    let (_root, session, _peer) = connected(Mode::Duplex, 2.0).await;
    assert_eq!(
        session
            .send_output(packet("provider_to_caller", 2, "completed", ""))
            .await,
        Err(StreamError::Sequence)
    );
}

#[tokio::test]
async fn native_deadline_and_drop_close_owned_sockets_without_host_progress() {
    let (_root, session, mut peer) = connected(Mode::Duplex, 0.05).await;
    assert_eq!(session.next_input().await, Err(StreamError::Timeout));
    let mut byte = [0];
    assert_eq!(
        timeout(Duration::from_millis(500), peer.read(&mut byte))
            .await
            .unwrap()
            .unwrap(),
        0
    );
    session.close().await;
    let (_root, session, mut peer) = connected(Mode::Duplex, 2.0).await;
    drop(session);
    assert_eq!(
        timeout(Duration::from_millis(500), peer.read(&mut byte))
            .await
            .unwrap()
            .unwrap(),
        0
    );
}

#[tokio::test]
async fn bounded_ingress_stops_reading_when_the_eight_frame_window_is_full() {
    // Large frames saturate the Unix buffer; a ninth decoded frame may be
    // pending, as in the original receive/read-then-put loop, but never an
    // unbounded queue. Host health/execution remains outside this blocked task.
    let (_root, session, mut peer) = connected(Mode::Duplex, 2.0).await;
    transport::write_packet(&mut peer, &packet("caller_to_provider", 0, "started", ""))
        .await
        .unwrap();
    let bytes = vec![1; streams::MAX_MEDIA_BYTES];
    let write = async {
        for sequence in 1..40 {
            let frame=Packet::from_projection(&format!(r#"{{"call_id":"call","direction":"caller_to_provider","sequence":{sequence},"kind":"chunk","media":{{"kind":"inline","media_type":"audio/pcm"}}}}"#),bytes.clone()).unwrap();
            transport::write_packet(&mut peer, &frame).await.unwrap();
        }
    };
    assert!(timeout(Duration::from_millis(50), write).await.is_err());
    session.close().await;
}

#[tokio::test]
async fn explicit_close_interrupts_a_backpressured_write_before_the_deadline() {
    let (_root, session, _peer) = connected(Mode::Server, 2.0).await;
    let session = std::sync::Arc::new(session);
    let frame=Packet::from_projection(r#"{"call_id":"call","direction":"provider_to_caller","sequence":1,"kind":"chunk","media":{"kind":"inline","media_type":"audio/pcm"}}"#,vec![1;streams::MAX_MEDIA_BYTES]).unwrap();
    let writer_session = session.clone();
    let write = tokio::spawn(async move { writer_session.send_output(frame).await });
    tokio::time::sleep(Duration::from_millis(20)).await;
    timeout(Duration::from_millis(500), session.close())
        .await
        .unwrap();
    assert_eq!(write.await.unwrap(), Err(StreamError::Closed));
}

#[tokio::test]
async fn cancelled_partial_output_cannot_later_publish_a_successful_terminal() {
    let (_root, session, _peer) = connected(Mode::Server, 2.0).await;
    let session = std::sync::Arc::new(session);
    let frame=Packet::from_projection(r#"{"call_id":"call","direction":"provider_to_caller","sequence":1,"kind":"chunk","media":{"kind":"inline","media_type":"audio/pcm"}}"#,vec![1;streams::MAX_MEDIA_BYTES]).unwrap();
    let writer_session = session.clone();
    let write = tokio::spawn(async move { writer_session.send_output(frame).await });
    tokio::time::sleep(Duration::from_millis(20)).await;
    write.abort();
    assert!(write.await.unwrap_err().is_cancelled());
    assert_eq!(session.finish().await, Err(StreamError::Closed));
    session.close().await;
}

fn control(kind: &str) -> Value {
    json!({"kind":kind,"contract_profile":transport::PROFILE,"profile_digest":transport::profile_digest(),"protocol":5})
}
#[test]
fn complete_control_and_binary_vectors_match_independent_oracle() {
    let fixture: Value =
        serde_json::from_str(include_str!("../contracts/transport-v1-cases.json")).unwrap();
    assert_eq!(fixture["profile_digest"], transport::profile_digest());
    for case in fixture["cases"].as_array().unwrap() {
        let source = case["source"].as_str().unwrap();
        let actual = if case["action"] == "control" {
            transport::Control::from_json(source).map(|c| transport::encode_control(&c))
        } else {
            transport::packet(source, unhex(case["media_hex"].as_str().unwrap()))
                .and_then(|packet| transport::encode_packet(&packet))
        };
        if case["valid"] == false {
            assert!(actual.is_err(), "{}", case["id"]);
        } else {
            let expected = unhex(case["wire_hex"].as_str().unwrap());
            assert_eq!(actual.unwrap(), expected, "{}", case["id"]);
            if case["action"] == "control" {
                assert!(transport::decode_control(&expected).is_ok());
            } else {
                assert!(transport::decode_packet(&expected).is_ok());
            }
        }
    }
}
fn unhex(source: &str) -> Vec<u8> {
    source
        .as_bytes()
        .chunks_exact(2)
        .map(|s| u8::from_str_radix(std::str::from_utf8(s).unwrap(), 16).unwrap())
        .collect()
}
#[tokio::test]
async fn hostile_headers_refuse_before_reading_media_and_profiles_never_fallback() {
    let valid = json!({"protocol":5,"contract_profile":transport::PROFILE,"profile_digest":transport::profile_digest(),"frame":{"kind":"end","call_id":"call"},"media_size":0});
    for key in [
        "protocol",
        "contract_profile",
        "profile_digest",
        "frame",
        "media_size",
    ] {
        let mut bad = valid.clone();
        bad.as_object_mut().unwrap().remove(key);
        let raw = bad.to_string();
        let (mut writer, mut reader) = tokio::io::duplex(2048);
        writer.write_u32(raw.len() as u32).await.unwrap();
        writer.write_all(raw.as_bytes()).await.unwrap();
        assert!(
            timeout(Duration::from_secs(1), transport::read_packet(&mut reader))
                .await
                .unwrap()
                .is_err()
        );
    }
    for (key, value) in [
        ("media_size", json!(1048577)),
        ("media_size", json!(1)),
        ("protocol", json!(4)),
        ("profile_digest", json!("0".repeat(64))),
    ] {
        let mut bad = valid.clone();
        bad[key] = value;
        let raw = bad.to_string();
        let (mut writer, mut reader) = tokio::io::duplex(2048);
        writer.write_u32(raw.len() as u32).await.unwrap();
        writer.write_all(raw.as_bytes()).await.unwrap();
        assert!(
            timeout(Duration::from_secs(1), transport::read_packet(&mut reader))
                .await
                .unwrap()
                .is_err()
        );
    }
    let packet = packet("caller_to_provider", 0, "started", "");
    assert!(transport::decode_packet(&streams::encode_packet(&packet).unwrap()).is_err());
    assert!(streams::decode_packet(&transport::encode_packet(&packet).unwrap()).is_err());
    let valid = transport::encode_packet(&packet).unwrap();
    for wire in [
        &valid[..valid.len() - 1],
        &[valid.as_slice(), &[0]].concat(),
    ] {
        assert!(transport::decode_packet(wire).is_err());
    }
}
async fn control_pair(seconds: f64) -> (tempfile::TempDir, transport::Channel, UnixStream) {
    let root = tempfile::Builder::new()
        .prefix("skulk-control5-")
        .tempdir_in("/tmp")
        .unwrap();
    let path = root.path().join("sock");
    let listener = UnixListener::bind(&path).unwrap();
    let expected = startup(path.to_str().unwrap());
    let (channel, peer) = tokio::join!(
        transport::Channel::connect(&expected, seconds),
        listener.accept()
    );
    let mut peer = peer.unwrap().0;
    transport::read_hello(&mut peer, &expected, None)
        .await
        .unwrap();
    (root, channel.unwrap(), peer)
}
#[tokio::test]
async fn control_readers_preserve_complete_records_and_poison_invalid_or_cancelled_io() {
    let (_root, channel, mut peer) = control_pair(2.0).await;
    let message = transport::Control::from_json(&control("shutdown").to_string()).unwrap();
    channel.send(&message).await.unwrap();
    assert_eq!(
        transport::read_control(&mut peer)
            .await
            .unwrap()
            .canonical(),
        message.canonical()
    );
    let bytes = transport::encode_control(&message);
    for byte in bytes {
        peer.write_all(&[byte]).await.unwrap();
    }
    assert_eq!(
        channel.receive().await.unwrap().canonical(),
        message.canonical()
    );
    peer.write_u32(u32::MAX).await.unwrap();
    assert!(matches!(
        channel.receive().await,
        Err(StreamError::HeaderBound)
    ));
    assert_eq!(channel.send(&message).await, Err(StreamError::Closed));
    channel.close().await;
    let (_root, channel, mut peer) = control_pair(2.0).await;
    peer.write_all(&[0, 0]).await.unwrap();
    assert!(
        timeout(Duration::from_millis(30), channel.receive())
            .await
            .is_err()
    );
    assert_eq!(channel.send(&message).await, Err(StreamError::Closed));
    channel.close().await;
}
#[tokio::test]
async fn idle_control_deadline_and_drop_close_owned_sockets() {
    let (_root, channel, mut peer) = control_pair(0.05).await;
    assert!(matches!(
        channel.receive().await,
        Err(StreamError::Timeout | StreamError::Closed)
    ));
    let mut byte = [0];
    assert_eq!(
        timeout(Duration::from_secs(1), peer.read(&mut byte))
            .await
            .unwrap()
            .unwrap(),
        0
    );
    channel.close().await;
    let (_root, channel, mut peer) = control_pair(2.0).await;
    drop(channel);
    assert_eq!(
        timeout(Duration::from_secs(1), peer.read(&mut byte))
            .await
            .unwrap()
            .unwrap(),
        0
    );
}

#[tokio::test]
async fn healthy_control_records_renew_idle_time_without_extending_partial_io() {
    let (_root, channel, mut peer) = control_pair(0.3).await;
    let record = transport::Control::from_json(&control("shutdown").to_string()).unwrap();
    for _ in 0..5 {
        tokio::time::sleep(Duration::from_millis(80)).await;
        channel.send(&record).await.unwrap();
        assert_eq!(
            transport::read_control(&mut peer)
                .await
                .unwrap()
                .canonical(),
            record.canonical()
        );
    }
    channel.close().await;
}

#[tokio::test]
async fn host_control_accept_binds_exact_startup_before_any_control() {
    let (mut peer, stream) = UnixStream::pair().unwrap();
    let expected = startup("/synthetic/socket");
    let mut wrong: Value = serde_json::from_slice(expected.canonical()).unwrap();
    wrong["host_id"] = json!("other-host");
    let wrong = managed::Record::from_json("Startup", &wrong.to_string()).unwrap();
    let hello = skulk_capability_kernel::handshake::from_startup(&wrong, None).unwrap();
    peer.write_all(&transport::encode_hello(&hello))
        .await
        .unwrap();
    assert!(matches!(
        transport::Channel::accept(stream, &expected, 1.0).await,
        Err(StreamError::InvalidFrame)
    ));
}
