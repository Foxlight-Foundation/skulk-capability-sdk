//! Host observation is pure framing/lifecycle validation, never call authority.
use serde_json::{Value, json};
use skulk_capability_kernel::{streams::HostObserver, transport_v2};

fn packet(side: &str, sequence: u64, kind: &str) -> Vec<u8> {
    let mut value = json!({"call_id":"call", "direction":side, "sequence":sequence, "kind":kind});
    if kind == "chunk" {
        value["payload"] = json!({"item":true});
    }
    wire(value, vec![])
}
fn wire(value: Value, media: Vec<u8>) -> Vec<u8> {
    transport_v2::encode_packet(&transport_v2::packet(&value.to_string(), media).unwrap()).unwrap()
}
fn end(call_id: &str) -> Vec<u8> {
    wire(json!({"kind":"end","call_id":call_id}), vec![])
}

#[test]
fn all_modes_require_exact_terminal_and_end() {
    for mode in ["server_streaming", "client_streaming", "bidirectional"] {
        let mut host = HostObserver::new("call", mode).unwrap();
        assert!(!host.finished());
        if mode != "server_streaming" {
            assert!(!host.input_completed());
            host.accept_input(&packet("caller_to_provider", 0, "started"))
                .unwrap();
            host.accept_input(&packet("caller_to_provider", 1, "completed"))
                .unwrap();
        }
        assert!(host.input_completed());
        if mode != "client_streaming" {
            host.accept_output(&packet("provider_to_caller", 1, "chunk"))
                .unwrap();
        }
        let last = if mode == "client_streaming" { 1 } else { 2 };
        host.accept_output(&packet("provider_to_caller", last, "completed"))
            .unwrap();
        assert!(host.output_terminated());
        assert!(!host.finished());
        host.accept_output(&end("call")).unwrap();
        assert!(host.finished());
        assert!(!host.failed());
        assert!(host.accept_output(&end("call")).is_err());
        assert!(host.failed());
    }
}

#[test]
fn half_close_does_not_stop_provider_output_or_change_media() {
    let mut host = HostObserver::new("call", "bidirectional").unwrap();
    host.accept_input(&packet("caller_to_provider", 0, "started"))
        .unwrap();
    let media = vec![0, 255, 1, 2];
    let bytes = wire(
        json!({"call_id":"call","direction":"caller_to_provider","sequence":1,"kind":"chunk","media":{"kind":"inline","media_type":"audio/pcm"}}),
        media.clone(),
    );
    assert_eq!(host.accept_input(&bytes).unwrap().media, media);
    host.accept_input(&packet("caller_to_provider", 2, "completed"))
        .unwrap();
    host.accept_output(&packet("provider_to_caller", 1, "chunk"))
        .unwrap();
    assert!(host.input_completed() && !host.output_terminated());
    host.accept_output(&packet("provider_to_caller", 2, "completed"))
        .unwrap();
    host.accept_output(&end("call")).unwrap();
    assert!(host.finished());
}

#[test]
fn sequence_mode_and_identity_failures_are_permanent() {
    let cases = [
        (
            "server_streaming",
            true,
            packet("caller_to_provider", 0, "started"),
        ),
        (
            "client_streaming",
            false,
            packet("provider_to_caller", 1, "chunk"),
        ),
        (
            "bidirectional",
            true,
            packet("caller_to_provider", 1, "chunk"),
        ),
        (
            "bidirectional",
            false,
            packet("provider_to_caller", 2, "chunk"),
        ),
        ("bidirectional", false, end("call")),
        (
            "bidirectional",
            false,
            wire(
                json!({"call_id":"foreign","direction":"provider_to_caller","sequence":1,"kind":"chunk","payload":{"item":true}}),
                vec![],
            ),
        ),
    ];
    for (mode, input, bytes) in cases {
        let mut host = HostObserver::new("call", mode).unwrap();
        let result = if input {
            host.accept_input(&bytes)
        } else {
            host.accept_output(&bytes)
        };
        assert!(result.is_err() && host.failed());
        assert!(
            host.accept_output(&packet("provider_to_caller", 1, "completed"))
                .is_err()
        );
        assert!(!host.finished());
    }
}

#[test]
fn malformed_partial_and_cross_profile_packets_cannot_resume() {
    for bytes in [vec![], vec![0], vec![0, 0, 0, 2, b'{', b'}']] {
        let mut host = HostObserver::new("call", "bidirectional").unwrap();
        assert!(host.accept_output(&bytes).is_err());
        assert!(host.failed());
        assert!(
            host.accept_input(&packet("caller_to_provider", 0, "started"))
                .is_err()
        );
    }
}

#[test]
fn terminal_cannot_be_replaced_or_acknowledged_for_another_call() {
    for bad in [
        packet("provider_to_caller", 2, "chunk"),
        packet("provider_to_caller", 2, "completed"),
        end("foreign"),
    ] {
        let mut host = HostObserver::new("call", "server_streaming").unwrap();
        host.accept_output(&packet("provider_to_caller", 1, "completed"))
            .unwrap();
        assert!(host.accept_output(&bad).is_err());
        assert!(!host.finished());
    }
}

#[test]
fn caller_half_close_with_data_poisoned_before_any_success() {
    let mut host = HostObserver::new("call", "bidirectional").unwrap();
    host.accept_input(&packet("caller_to_provider", 0, "started"))
        .unwrap();
    let data = wire(
        json!({"call_id":"call","direction":"caller_to_provider","sequence":1,"kind":"completed","payload":{"forbidden":true}}),
        vec![],
    );
    assert!(host.accept_input(&data).is_err());
    assert!(
        host.accept_output(&packet("provider_to_caller", 1, "completed"))
            .is_err()
    );
}

#[test]
fn header_hint_requires_explicit_profile_and_bounded_media_before_allocation() {
    let bytes = wire(
        json!({"call_id":"call","direction":"caller_to_provider","sequence":1,"kind":"chunk","media":{"kind":"inline","media_type":"audio/pcm"}}),
        vec![0, 255, 1],
    );
    let size = u32::from_be_bytes(bytes[..4].try_into().unwrap()) as usize;
    let header = &bytes[4..4 + size];
    assert_eq!(transport_v2::media_size(header).unwrap(), 3);
    let original: Value = serde_json::from_slice(header).unwrap();
    for (field, value) in [
        ("media_size", json!(1048577)),
        ("media_size", json!(-1)),
        ("protocol", json!(5)),
        ("profile_digest", json!("a".repeat(64))),
    ] {
        let mut changed = original.clone();
        changed[field] = value;
        assert!(transport_v2::media_size(changed.to_string().as_bytes()).is_err());
    }
    assert!(transport_v2::media_size(&vec![b' '; 65537]).is_err());
}

#[test]
fn construction_requires_nonempty_call_and_a_stream_mode() {
    assert!(HostObserver::new("", "bidirectional").is_err());
    assert!(HostObserver::new("call", "unary").is_err());
}
