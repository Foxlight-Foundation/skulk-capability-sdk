#![cfg(unix)]
//! Native stream receipts use independently captured Python packets and real
//! local sockets. Timing checks assert ownership/bounds, not machine speed.
use std::fs;
use std::time::Duration;

use serde_json::Value;
use skulk_capability_kernel::{
    codec,
    streams::{self, Mode, Packet, Session, StreamError},
};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{UnixListener, UnixStream};
use tokio::time::timeout;

fn unhex(value: &str) -> Vec<u8> {
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
        .collect()
}
fn packet(direction: &str, sequence: u64, kind: &str, extra: &str) -> Packet {
    Packet::from_projection(&format!(r#"{{"call_id":"call","direction":"{direction}","sequence":{sequence},"kind":"{kind}"{extra}}}"#),vec![]).unwrap()
}
fn hello() -> String {
    format!(
        r#"{{"kind":"stream_hello","protocol":4,"call_id":"call","token":"{}","capability_node_id":"node","manifest_sha256":"{}"}}"#,
        "0".repeat(64),
        "a".repeat(64)
    )
}
async fn read_hello(socket: &mut UnixStream) {
    let length = socket.read_u32().await.unwrap() as usize;
    let mut bytes = vec![0; length];
    socket.read_exact(&mut bytes).await.unwrap();
    let value: Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(value["kind"], "stream_hello");
}
async fn connected(mode: Mode, seconds: f64) -> (tempfile::TempDir, Session, UnixStream) {
    let root = tempfile::Builder::new()
        .prefix("skulk-stream-")
        .tempdir_in("/tmp")
        .unwrap();
    let path = root.path().join("sock");
    let listener = UnixListener::bind(&path).unwrap();
    let hello = hello();
    let (session, peer) = tokio::join!(
        Session::open(path.to_str().unwrap(), &hello, "call", mode, seconds),
        listener.accept()
    );
    let mut peer = peer.unwrap().0;
    read_hello(&mut peer).await;
    (root, session.unwrap(), peer)
}

#[test]
fn independently_frozen_python_packets_decode_without_changing_numbers_or_media() {
    let reference: Value = serde_json::from_str(include_str!(
        "../../tests/fixtures/author-baseline/sdk-0.5.0-streams.json"
    ))
    .unwrap();
    for vector in reference["packets"].as_array().unwrap() {
        let packet = streams::decode_packet(&unhex(vector["wire_hex"].as_str().unwrap())).unwrap();
        assert_eq!(packet.media, unhex(vector["media_hex"].as_str().unwrap()));
        assert_eq!(
            packet.projection_json().unwrap().as_bytes(),
            codec::canonical(&vector["projection"].to_string()).unwrap()
        );
        assert_eq!(
            streams::decode_packet(&streams::encode_packet(&packet).unwrap()).unwrap(),
            packet
        );
    }
}

#[test]
fn frame_media_and_error_contracts_refuse_ambiguous_or_unbounded_content() {
    for raw in [
        r#"{"call_id":"call","direction":"caller_to_provider","sequence":0,"kind":"chunk","payload":{}}"#,
        r#"{"call_id":"call","direction":"caller_to_provider","sequence":1,"kind":"completed","error":{"code":"timeout","message":"x"}}"#,
        r#"{"call_id":"call","direction":"caller_to_provider","sequence":1,"kind":"cancelled","error":{"code":"provider_error","message":"x"}}"#,
        r#"{"call_id":"call","direction":"caller_to_provider","sequence":1,"kind":"chunk","media":{"kind":"inline","media_type":"audio/pcm","data":"not-inline"}}"#,
        r#"{"call_id":"call","direction":"caller_to_provider","sequence":1,"kind":"chunk","synthetic":true,"payload":{}}"#,
        r#"{"call_id":"call","kind":"end","extra":"secret"}"#,
    ] {
        assert!(Packet::from_projection(raw, vec![]).is_err());
    }
    let body = r#"{"call_id":"call","direction":"provider_to_caller","sequence":1,"kind":"chunk","media":{"kind":"inline","media_type":"audio/pcm"}}"#;
    let packet = Packet::from_projection(body, vec![255; streams::MAX_MEDIA_BYTES]).unwrap();
    assert_eq!(
        streams::decode_packet(&streams::encode_packet(&packet).unwrap()).unwrap(),
        packet
    );
    assert_eq!(
        Packet::from_projection(body, vec![0; streams::MAX_MEDIA_BYTES + 1]),
        Err(StreamError::MediaBound)
    );
    assert_eq!(
        Packet::from_projection(r#"{"kind":"end","call_id":"call"}"#, vec![1]),
        Err(StreamError::InvalidFrame)
    );
}

#[tokio::test]
async fn hostile_headers_are_refused_before_waiting_for_declared_media() {
    for header in [
        r#"{"protocol":3,"frame":{"kind":"end","call_id":"call"},"media_size":0}"#,
        r#"{"protocol":4,"frame":{"kind":"end","call_id":"call"},"media_size":1}"#,
        r#"{"protocol":4,"frame":{"kind":"end","call_id":"call"},"media_size":0,"media_size":0}"#,
        r#"{"protocol":4,"frame":{"kind":"end","call_id":"call"},"media_size":NaN}"#,
        r#"{"protocol":4,"frame":{"kind":"end","call_id":"call"},"media_size":1048577}"#,
    ] {
        let (mut writer, mut reader) = tokio::io::duplex(4096);
        writer
            .write_all(&(header.len() as u32).to_be_bytes())
            .await
            .unwrap();
        writer.write_all(header.as_bytes()).await.unwrap();
        assert!(
            timeout(
                Duration::from_millis(100),
                streams::read_packet(&mut reader)
            )
            .await
            .unwrap()
            .is_err()
        );
    }
    let (mut writer, mut reader) = tokio::io::duplex(16);
    writer.write_u32(u32::MAX).await.unwrap();
    assert_eq!(
        timeout(
            Duration::from_millis(100),
            streams::read_packet(&mut reader)
        )
        .await
        .unwrap(),
        Err(StreamError::HeaderBound)
    );
    // Defaults accepted by the existing Python header/end models remain valid.
    let raw = br#"{"frame":{"call_id":"call"},"media_size":0}"#;
    let mut data = (raw.len() as u32).to_be_bytes().to_vec();
    data.extend(raw);
    assert!(streams::decode_packet(&data).unwrap().is_end());
}

#[tokio::test]
async fn half_close_and_cleanup_ack_are_separate_and_terminal_waits_for_cleanup() {
    let (_root, session, mut peer) = connected(Mode::Duplex, 2.0).await;
    streams::write_packet(&mut peer, &packet("caller_to_provider", 0, "started", ""))
        .await
        .unwrap();
    streams::write_packet(&mut peer, &packet("caller_to_provider", 1, "completed", ""))
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
        streams::read_packet(&mut peer)
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
    assert!(streams::read_packet(&mut peer).await.unwrap().is_terminal());
    assert!(streams::read_packet(&mut peer).await.unwrap().is_end());
    assert_eq!(peer.read(&mut byte).await.unwrap(), 0);
    assert_eq!(session.finish().await, Err(StreamError::Closed));
}

#[tokio::test]
async fn co_ready_ingress_faults_fence_a_held_success_for_both_input_modes() {
    for mode in [Mode::Client, Mode::Duplex] {
        for fault in ["cancelled", "gap", "half_close_data"] {
            let (_root, session, mut peer) = connected(mode, 2.0).await;
            streams::write_packet(&mut peer, &packet("caller_to_provider", 0, "started", ""))
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
            streams::write_packet(&mut peer, &bad).await.unwrap();
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
    streams::write_packet(&mut peer, &packet("caller_to_provider", 0, "started", ""))
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
    streams::write_packet(&mut peer, &packet("caller_to_provider", 0, "started", ""))
        .await
        .unwrap();
    let bytes = vec![1; streams::MAX_MEDIA_BYTES];
    let write = async {
        for sequence in 1..40 {
            let frame=Packet::from_projection(&format!(r#"{{"call_id":"call","direction":"caller_to_provider","sequence":{sequence},"kind":"chunk","media":{{"kind":"inline","media_type":"audio/pcm"}}}}"#),bytes.clone()).unwrap();
            streams::write_packet(&mut peer, &frame).await.unwrap();
        }
    };
    assert!(timeout(Duration::from_millis(50), write).await.is_err());
    session.close().await;
}

#[tokio::test]
async fn invalid_handshake_is_refused_before_connecting_or_revealing_secrets() {
    let bad = hello().replace("\"protocol\":4", "\"protocol\":3");
    assert!(matches!(
        Session::open("/synthetic/no-socket", &bad, "call", Mode::Duplex, 1.0).await,
        Err(StreamError::InvalidFrame)
    ));
    for seconds in [0.0, -1.0, f64::NAN, 301.0] {
        assert!(matches!(
            Session::open(
                "/synthetic/no-socket",
                &hello(),
                "call",
                Mode::Duplex,
                seconds
            )
            .await,
            Err(StreamError::InvalidFrame)
        ));
    }
    assert!(!StreamError::Transport.to_string().contains("synthetic"));
    // Keep the independent fixture on disk, not an expectation regenerated by Rust.
    assert!(
        fs::metadata(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../tests/fixtures/author-baseline/sdk-0.5.0-streams.json"
        ))
        .unwrap()
        .len()
            > 0
    );
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

#[tokio::test]
async fn oversized_hello_source_is_refused_before_parsing_or_connecting() {
    for hello in [
        " ".repeat(streams::MAX_HEADER_BYTES + 1),
        format!("{}{}", hello(), " ".repeat(streams::MAX_HEADER_BYTES)),
    ] {
        assert!(matches!(
            Session::open("/synthetic/no-socket", &hello, "call", Mode::Duplex, 1.0).await,
            Err(StreamError::HeaderBound)
        ));
    }
}
