//! Caller transport uses actual protected sockets and compiled lifecycle rules.
#![cfg(unix)]
use serde_json::{Value, json};
use skulk_capability_kernel::{
    caller_streams::CallerSession,
    managed_v2,
    streams::{Packet, StreamError},
    transport_v2,
};
use std::time::Duration;
use tokio::{
    io::AsyncReadExt,
    net::{UnixListener, UnixStream},
};

async fn connected(
    mode: &str,
    seconds: Option<f64>,
) -> (tempfile::TempDir, CallerSession, UnixStream) {
    let root = tempfile::Builder::new()
        .prefix("caller-")
        .tempdir_in("/tmp")
        .unwrap();
    let socket = root.path().join("sock");
    let listener = UnixListener::bind(&socket).unwrap();
    let fixture: Value =
        serde_json::from_str(include_str!("../contracts/managed-v2-cases.json")).unwrap();
    let id = format!("inference-stream-{mode}");
    let case = fixture["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|case| case["id"] == id)
        .unwrap();
    let mut source: Value = serde_json::from_str(case["canonical"].as_str().unwrap()).unwrap();
    source["socket"] = json!(socket);
    source["stream_socket"] = json!(socket);
    let startup = managed_v2::Record::from_json("Startup", &source.to_string()).unwrap();
    let (session, peer) = tokio::join!(
        CallerSession::open(&startup, "child", mode, seconds),
        listener.accept()
    );
    let mut peer = peer.unwrap().0;
    transport_v2::read_hello(&mut peer, &startup, Some("child"))
        .await
        .unwrap();
    (root, session.unwrap(), peer)
}
fn packet(direction: &str, sequence: u64, kind: &str) -> Packet {
    transport_v2::packet(
        &json!({"call_id":"child","direction":direction,"sequence":sequence,"kind":kind, "payload": if kind == "chunk" {json!({"x":1})} else {Value::Null}})
            .to_string(),
        vec![],
    )
    .unwrap()
}
async fn end(peer: &mut UnixStream, sequence: u64) {
    transport_v2::write_packet(peer, &packet("provider_to_caller", sequence, "completed"))
        .await
        .unwrap();
    transport_v2::write_packet(
        peer,
        &transport_v2::packet(r#"{"kind":"end","call_id":"child"}"#, vec![]).unwrap(),
    )
    .await
    .unwrap();
}

#[tokio::test]
async fn all_stream_modes_require_terminal_and_end_without_an_implicit_generation_cap() {
    for mode in ["server_streaming", "client_streaming", "bidirectional"] {
        for seconds in [None, Some(7200.0)] {
            let (_root, session, mut peer) = connected(mode, seconds).await;
            if mode != "server_streaming" {
                for (sequence, kind) in [(0, "started"), (1, "completed")] {
                    session
                        .send_input(packet("caller_to_provider", sequence, kind))
                        .await
                        .unwrap();
                    assert_eq!(
                        transport_v2::read_packet(&mut peer)
                            .await
                            .unwrap()
                            .is_terminal(),
                        kind == "completed"
                    );
                }
            }
            tokio::time::pause();
            tokio::time::advance(Duration::from_secs(1000)).await;
            tokio::time::resume();
            let terminal_sequence = if mode == "client_streaming" {
                1
            } else {
                let chunk = session.packet(r#"{"call_id":"child","direction":"provider_to_caller","sequence":1,"kind":"chunk","payload":{"count":9007199254740991,"fraction":1.25},"media":{"kind":"inline","media_type":"audio/pcm"}}"#, vec![0,255,1]).unwrap();
                transport_v2::write_packet(&mut peer, &chunk).await.unwrap();
                let received = session.next_output().await.unwrap().unwrap();
                assert_eq!(received.media, vec![0, 255, 1]);
                assert!(
                    session
                        .projection(&received)
                        .unwrap()
                        .contains("\"fraction\":1.25")
                );
                2
            };
            end(&mut peer, terminal_sequence).await;
            assert!(session.next_output().await.unwrap().unwrap().is_terminal());
            assert!(session.next_output().await.unwrap().is_none());
            assert!(session.next_output().await.unwrap().is_none());
            session.finish().await.unwrap();
            assert!(peer.read_u8().await.is_err());
        }
    }
}

#[tokio::test]
async fn input_media_and_half_close_preserve_the_opposite_direction() {
    let (_root, session, mut peer) = connected("bidirectional", None).await;
    session
        .send_input(packet("caller_to_provider", 0, "started"))
        .await
        .unwrap();
    transport_v2::read_packet(&mut peer).await.unwrap();
    let media = session.packet(r#"{"call_id":"child","direction":"caller_to_provider","sequence":1,"kind":"chunk","media":{"kind":"inline","media_type":"audio/pcm"}}"#, vec![0,255]).unwrap();
    session.send_input(media).await.unwrap();
    assert_eq!(
        transport_v2::read_packet(&mut peer).await.unwrap().media,
        vec![0, 255]
    );
    session
        .send_input(packet("caller_to_provider", 2, "completed"))
        .await
        .unwrap();
    assert!(
        transport_v2::read_packet(&mut peer)
            .await
            .unwrap()
            .is_terminal()
    );
    end(&mut peer, 1).await;
    assert!(session.next_output().await.unwrap().unwrap().is_terminal());
    assert!(session.next_output().await.unwrap().is_none());
    session.finish().await.unwrap();
}

#[tokio::test]
async fn invalid_caller_direction_sequence_mode_and_half_close_are_sticky() {
    for (mode, bad) in [
        ("server_streaming", packet("caller_to_provider", 0, "started")),
        ("bidirectional", packet("caller_to_provider", 1, "chunk")),
        ("bidirectional", packet("provider_to_caller", 0, "started")),
        ("bidirectional", transport_v2::packet(r#"{"call_id":"other","direction":"caller_to_provider","sequence":0,"kind":"started"}"#, vec![]).unwrap()),
        ("bidirectional", transport_v2::packet(r#"{"call_id":"child","direction":"caller_to_provider","sequence":1,"kind":"completed","payload":{"x":1}}"#, vec![]).unwrap()),
    ] {
        let (_root, session, mut peer) = connected(mode, None).await;
        if bad.is_terminal() { session.send_input(packet("caller_to_provider", 0, "started")).await.unwrap(); transport_v2::read_packet(&mut peer).await.unwrap(); }
        assert!(session.send_input(bad).await.is_err());
        assert!(session.next_output().await.is_err());
        assert!(peer.read_u8().await.is_err());
    }
}

#[tokio::test]
async fn provider_protocol_failures_do_not_become_successful_eof() {
    for (mode, bad) in [
        ("client_streaming", packet("provider_to_caller", 1, "chunk")),
        (
            "server_streaming",
            packet("provider_to_caller", 2, "completed"),
        ),
        (
            "bidirectional",
            packet("caller_to_provider", 1, "completed"),
        ),
        (
            "bidirectional",
            transport_v2::packet(r#"{"kind":"end","call_id":"child"}"#, vec![]).unwrap(),
        ),
    ] {
        let (_root, session, mut peer) = connected(mode, None).await;
        transport_v2::write_packet(&mut peer, &bad).await.unwrap();
        assert!(session.next_output().await.is_err());
        assert!(session.finish().await.is_err());
    }
    let (_root, session, mut peer) = connected("server_streaming", None).await;
    transport_v2::write_packet(&mut peer, &packet("provider_to_caller", 1, "completed"))
        .await
        .unwrap();
    assert!(session.next_output().await.unwrap().unwrap().is_terminal());
    drop(peer);
    assert!(session.next_output().await.is_err());
    assert!(session.finish().await.is_err());
}

#[tokio::test]
async fn undrained_end_and_expiry_cannot_finish_successfully() {
    let (_root, session, mut peer) = connected("server_streaming", None).await;
    end(&mut peer, 1).await;
    assert!(matches!(
        session.finish().await,
        Err(StreamError::MissingTerminal)
    ));
    let (_root, session, mut peer) = connected("bidirectional", Some(1.0)).await;
    tokio::time::pause();
    tokio::time::advance(Duration::from_secs(2)).await;
    tokio::time::resume();
    assert!(matches!(
        session.next_output().await,
        Err(StreamError::Timeout)
    ));
    assert!(peer.read_u8().await.is_err());
}

#[tokio::test]
async fn abandoned_receive_and_repeated_close_fence_future_work() {
    let (_root, session, mut peer) = connected("bidirectional", None).await;
    assert!(
        tokio::time::timeout(Duration::from_millis(20), session.next_output())
            .await
            .is_err()
    );
    assert!(
        session
            .send_input(packet("caller_to_provider", 0, "started"))
            .await
            .is_err()
    );
    tokio::join!(session.close(), session.close());
    assert!(peer.read_u8().await.is_err());
    let (_root, session, mut peer) = connected("server_streaming", None).await;
    drop(session);
    assert!(
        tokio::time::timeout(Duration::from_secs(2), peer.read_u8())
            .await
            .unwrap()
            .is_err()
    );
}
