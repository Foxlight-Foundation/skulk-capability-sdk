//! Real protocol-six socket lifecycle with optional generation lifetime.
#![cfg(unix)]
use serde_json::{Value, json};
use skulk_capability_kernel::{
    managed_v2,
    streams::{Mode, Packet, Session, StreamError},
    transport_v2,
};
use std::time::Duration;
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{UnixListener, UnixStream},
};

fn startup(socket: &str, mode: Mode) -> managed_v2::Record {
    let fixture: Value =
        serde_json::from_str(include_str!("../contracts/managed-v2-cases.json")).unwrap();
    let id = match mode {
        Mode::Server => "inference-stream-server_streaming",
        Mode::Client => "inference-stream-client_streaming",
        Mode::Duplex => "inference-stream-bidirectional",
    };
    let case = fixture["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["id"] == id)
        .unwrap();
    let mut value: Value = serde_json::from_str(case["canonical"].as_str().unwrap()).unwrap();
    value["socket"] = json!(socket);
    value["stream_socket"] = json!(socket);
    managed_v2::Record::from_json("Startup", &value.to_string()).unwrap()
}
fn packet(direction: &str, sequence: u64, kind: &str, extra: &str) -> Packet {
    transport_v2::packet(&format!(r#"{{"call_id":"call-a","direction":"{direction}","sequence":{sequence},"kind":"{kind}"{extra}}}"#),vec![]).unwrap()
}
async fn connected(mode: Mode, seconds: Option<f64>) -> (tempfile::TempDir, Session, UnixStream) {
    let root = tempfile::Builder::new()
        .prefix("skulk-wire6-")
        .tempdir_in("/tmp")
        .unwrap();
    let socket = root.path().join("sock");
    let listener = UnixListener::bind(&socket).unwrap();
    let startup = startup(socket.to_str().unwrap(), mode);
    let (session, peer) = tokio::join!(
        Session::open_inference(&startup, "call-a", mode, seconds),
        listener.accept()
    );
    let mut peer = peer.unwrap().0;
    transport_v2::read_hello(&mut peer, &startup, Some("call-a"))
        .await
        .unwrap();
    (root, session.unwrap(), peer)
}
async fn half_close(peer: &mut UnixStream, session: &Session) {
    transport_v2::write_packet(peer, &packet("caller_to_provider", 0, "started", ""))
        .await
        .unwrap();
    transport_v2::write_packet(peer, &packet("caller_to_provider", 1, "completed", ""))
        .await
        .unwrap();
    let received = session.next_input().await.unwrap().unwrap();
    let projection: Value =
        serde_json::from_str(&transport_v2::projection(&received).unwrap()).unwrap();
    assert_eq!(projection["kind"], "started");
    assert!(session.next_input().await.unwrap().unwrap().is_terminal());
    assert!(session.next_input().await.unwrap().is_none());
}
#[tokio::test]
async fn every_stream_mode_survives_old_windows_and_holds_terminal_until_cleanup() {
    for mode in [Mode::Server, Mode::Client, Mode::Duplex] {
        for seconds in [None, Some(7200.0)] {
            let (_root, session, mut peer) = connected(mode, seconds).await;
            if mode != Mode::Server {
                half_close(&mut peer, &session).await;
            }
            tokio::time::pause();
            tokio::time::advance(Duration::from_secs(1000)).await;
            tokio::time::resume();
            let terminal_sequence = if mode == Mode::Client {
                1
            } else {
                session
                    .send_output(packet(
                        "provider_to_caller",
                        1,
                        "chunk",
                        r#", "payload":{"text":"after a long computation"}"#,
                    ))
                    .await
                    .unwrap();
                let received = transport_v2::read_packet(&mut peer).await.unwrap();
                let projection: Value =
                    serde_json::from_str(&transport_v2::projection(&received).unwrap()).unwrap();
                assert_eq!(projection["kind"], "chunk");
                2
            };
            session
                .send_output(packet(
                    "provider_to_caller",
                    terminal_sequence,
                    "completed",
                    "",
                ))
                .await
                .unwrap();
            let mut byte = [0u8; 1];
            assert_eq!(
                peer.try_read(&mut byte).unwrap_err().kind(),
                std::io::ErrorKind::WouldBlock
            );
            session.finish().await.unwrap();
            assert!(
                transport_v2::read_packet(&mut peer)
                    .await
                    .unwrap()
                    .is_terminal()
            );
            assert!(transport_v2::read_packet(&mut peer).await.unwrap().is_end());
            assert!(peer.read_u8().await.is_err());
        }
    }
}
#[tokio::test]
async fn real_finite_scope_expiry_still_fences_output_and_uncapped_close_drains_reader() {
    let (_root, session, mut peer) = connected(Mode::Server, Some(1.0)).await;
    tokio::time::pause();
    tokio::time::advance(Duration::from_secs(2)).await;
    tokio::time::resume();
    assert!(matches!(
        session
            .send_output(packet(
                "provider_to_caller",
                1,
                "chunk",
                r#", "payload":{"x":1}"#
            ))
            .await,
        Err(StreamError::Timeout)
    ));
    assert!(peer.read_u8().await.is_err());
    let (_root, session, mut peer) = connected(Mode::Duplex, None).await;
    session.close().await;
    assert!(peer.read_u8().await.is_err());
    assert!(matches!(
        session.next_input().await,
        Err(StreamError::Closed)
    ));
}
#[tokio::test]
async fn observed_hostile_input_fences_an_uncapped_success_without_replay() {
    let (_root, session, mut peer) = connected(Mode::Duplex, None).await;
    transport_v2::write_packet(&mut peer, &packet("caller_to_provider", 0, "started", ""))
        .await
        .unwrap();
    peer.write_all(&u32::MAX.to_be_bytes()).await.unwrap();
    let first = tokio::time::timeout(Duration::from_secs(5), session.next_input())
        .await
        .unwrap();
    if let Ok(first) = first {
        assert!(first.is_some());
        assert!(
            tokio::time::timeout(Duration::from_secs(5), session.next_input())
                .await
                .unwrap()
                .is_err()
        );
    }
    assert!(
        session
            .send_output(packet("provider_to_caller", 1, "completed", ""))
            .await
            .is_err()
    );
    assert!(session.finish().await.is_err());
    assert!(peer.read_u8().await.is_err());
}
