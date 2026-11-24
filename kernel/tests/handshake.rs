//! Independent synthetic handshake admission and full startup/call binding.
use serde_json::Value;
use skulk_capability_kernel::{handshake, managed, portable::PortableError};

#[test]
fn independent_handshakes_match_every_expected_observation() {
    let fixture: Value =
        serde_json::from_str(include_str!("../contracts/handshake-v1-cases.json")).unwrap();
    assert_eq!(handshake::profile_digest(), fixture["profile_digest"]);
    for case in fixture["cases"].as_array().unwrap() {
        let result = evaluate(case);
        if let Some(error) = case["error"].as_str() {
            let failure = result
                .err()
                .unwrap_or_else(|| panic!("expected refusal: {}", case["id"]));
            assert_eq!(
                failure.to_string().split(':').next().unwrap(),
                error,
                "{}",
                case["id"]
            );
            assert!(!failure.to_string().contains("synthetic-do-not-echo-secret"));
        } else {
            assert_eq!(
                result.unwrap().canonical(),
                case["canonical"].as_str().unwrap().as_bytes(),
                "{}",
                case["id"]
            );
        }
    }
}

fn evaluate(case: &Value) -> Result<handshake::Record, PortableError> {
    let source = case["source"].as_str().unwrap();
    if case["action"] == "parse" {
        return handshake::Record::from_json(case["name"].as_str().unwrap(), source);
    }
    let startup = managed::Record::from_json("Startup", case["startup"].as_str().unwrap())?;
    let call_id = case["call_id"].as_str();
    if case["action"] == "create" {
        handshake::from_startup(&startup, call_id)
    } else {
        handshake::admit(&startup, source, call_id)
    }
}

#[test]
fn manifest_handles_cannot_be_used_as_a_private_startup() {
    let fixture: Value =
        serde_json::from_str(include_str!("../contracts/managed-v1-cases.json")).unwrap();
    let case = fixture["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["name"] == "Manifest" && c["canonical"].is_string())
        .unwrap();
    let source = case["canonical"].as_str().unwrap();
    let record = managed::Record::from_json("Manifest", source).unwrap();
    assert_eq!(
        record.digest(),
        managed::contract_digest("Manifest", source).unwrap()
    );
    assert_eq!(record.digest().len(), 64);
    assert!(matches!(
        handshake::from_startup(&record, None),
        Err(PortableError::Contract)
    ));
}
