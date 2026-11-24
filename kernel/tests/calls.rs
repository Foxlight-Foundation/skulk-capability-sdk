//! Independent call framing corpus and fixed resource-bound witnesses.
use serde_json::Value;
use skulk_capability_kernel::calls;
fn unhex(raw: &str) -> Vec<u8> {
    raw.as_bytes()
        .chunks_exact(2)
        .map(|p| u8::from_str_radix(std::str::from_utf8(p).unwrap(), 16).unwrap())
        .collect()
}
fn inputs() -> Value {
    serde_json::from_str(include_str!("../contracts/calls-v1-cases.json")).unwrap()
}
#[test]
fn complete_independent_wire_corpus() {
    let inputs = inputs();
    assert_eq!(calls::profile_digest(), inputs["profile_digest"]);
    for case in inputs["cases"].as_array().unwrap() {
        let frame = if case["action"] == "encode" {
            calls::Frame::from_json(
                case["source"].as_str().unwrap(),
                unhex(case["payload_hex"].as_str().unwrap()),
            )
        } else {
            calls::decode(&unhex(case["packet_hex"].as_str().unwrap()))
        };
        if let Some(expected) = case["canonical"].as_str() {
            let frame = frame.unwrap_or_else(|e| panic!("{}: {e}", case["id"]));
            let wire = calls::encode(&frame);
            assert_eq!(wire, unhex(expected), "{}", case["id"]);
            let roundtrip = calls::decode(&wire).unwrap();
            assert_eq!(calls::encode(&roundtrip), wire);
        } else {
            assert!(frame.is_err(), "{}", case["id"]);
        }
    }
}
#[test]
fn exact_payload_limit_preserves_raw_bytes() {
    let inputs = inputs();
    let case = inputs["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|v| v["id"] == "invoke-unary")
        .unwrap();
    let mut metadata: Value = serde_json::from_str(case["source"].as_str().unwrap()).unwrap();
    let mut payload = b"{\"x\":\"".to_vec();
    payload.resize(calls::MAX_PAYLOAD_BYTES - 2, b'x');
    payload.extend_from_slice(b"\"}");
    assert_eq!(payload.len(), calls::MAX_PAYLOAD_BYTES);
    metadata["payload_size"] = Value::from(payload.len());
    metadata["context"]["request_sha256"] =
        Value::from("7f9c3466a519684a6b543cdbca5819e0bc737954c8a383b52fe5b2f910e20a45");
    let frame = calls::Frame::from_json(&metadata.to_string(), payload.clone()).unwrap();
    assert_eq!(frame.payload(), payload);
    let wire = calls::encode(&frame);
    assert_eq!(calls::decode(&wire).unwrap().payload(), payload);
    payload.push(b' ');
    assert!(calls::Frame::from_json(&metadata.to_string(), payload).is_err());
}

#[test]
fn bounded_header_hint_is_not_complete_body_admission() {
    let inputs = inputs();
    let case = inputs["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["id"] == "invoke-unary")
        .unwrap();
    let source = case["source"].as_str().unwrap();
    let mut payload = unhex(case["payload_hex"].as_str().unwrap());
    assert_eq!(calls::payload_size(source).unwrap(), payload.len());
    let index = payload.windows(5).position(|s| s == b"hello").unwrap();
    payload[index..index + 5].copy_from_slice(b"other");
    assert!(calls::Frame::from_json(source, payload).is_err());
    let mut metadata: Value = serde_json::from_str(source).unwrap();
    metadata["payload_size"] = Value::from(calls::MAX_PAYLOAD_BYTES);
    assert_eq!(
        calls::payload_size(&metadata.to_string()).unwrap(),
        calls::MAX_PAYLOAD_BYTES
    );
    metadata["payload_size"] = Value::from(calls::MAX_PAYLOAD_BYTES + 1);
    assert!(calls::payload_size(&metadata.to_string()).is_err());
    metadata = serde_json::from_str(source).unwrap();
    metadata["context"]["caller_id"] = Value::from("");
    assert!(calls::payload_size(&metadata.to_string()).is_err());
}
