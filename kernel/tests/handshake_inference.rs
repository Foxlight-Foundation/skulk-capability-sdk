//! Exact new startup binding; a matching record is not active caller authority.

use serde_json::{Value, json};
use skulk_capability_kernel::{handshake, handshake_v2, managed_v2, portable_v2};

fn startup() -> managed_v2::Record {
    let manifest = json!({
        "contract_profile":managed_v2::PROFILE,"profile_digest":managed_v2::profile_digest(),
        "protocol":6,"bundle_id":"inference.fixture","bundle_version":"1.0.0",
        "executable":"bundle.pyz","executable_sha256":"a".repeat(64),
        "skulk_requires":">=2.0.3,<3","effect":"inference",
        "contracts":{
            "contract_profile":portable_v2::PROFILE,"profile_digest":portable_v2::profile_digest(),
            "protocol":6,"descriptors":[{
                "contract_profile":portable_v2::PROFILE,"profile_digest":portable_v2::profile_digest(),
                "id":"generate","version":"1.0.0","title":"Generate",
                "description":"Synthetic inference declaration","behavior":"inference",
                "processing_domain":"external","input_schema":{"type":"object"}
            }]
        }
    });
    let value = json!({
        "contract_profile":managed_v2::PROFILE,"profile_digest":managed_v2::profile_digest(),
        "protocol":6,"manifest":manifest,
        "manifest_digest":managed_v2::contract_digest("Manifest", &manifest.to_string()).unwrap(),
        "host_id":"owner-host","transport_id":"fabric-host","node_id":"inference-child",
        "executable":"/staged/bundle.pyz","socket":"/private/control.sock",
        "token":"1".repeat(64)
    });
    managed_v2::Record::from_json("Startup", &value.to_string()).unwrap()
}

#[test]
fn exact_new_hello_binds_all_startup_fields_without_legacy_downgrade() {
    let source = startup();
    let hello = handshake_v2::from_startup(&source, None).unwrap();
    assert_eq!(hello.name(), "Hello");
    let text = std::str::from_utf8(hello.canonical()).unwrap();
    assert!(handshake::Record::from_json("Hello", text).is_err());
    assert_eq!(
        handshake_v2::admit(&source, text, None)
            .unwrap()
            .canonical(),
        hello.canonical()
    );
    for (field, replacement) in [
        ("host_id", "another-host"),
        ("transport_node_id", "another-transport"),
        ("capability_node_id", "another-child"),
        ("bundle_id", "another-bundle"),
        ("bundle_version", "2.0.0"),
    ] {
        let mut fields: Value = serde_json::from_str(text).unwrap();
        fields[field] = json!(replacement);
        assert!(
            handshake_v2::admit(&source, &fields.to_string(), None).is_err(),
            "{field}"
        );
    }
    for field in ["manifest_sha256", "token"] {
        let mut fields: Value = serde_json::from_str(text).unwrap();
        fields[field] = json!("2".repeat(64));
        let error = handshake_v2::admit(&source, &fields.to_string(), None)
            .err()
            .unwrap()
            .to_string();
        assert!(error.starts_with("portable_binding"));
        assert!(!error.contains(&"2".repeat(64)));
    }
}

#[test]
fn stream_hello_requires_the_exact_host_selected_call() {
    let source = startup();
    let hello = handshake_v2::from_startup(&source, Some("call-a")).unwrap();
    let text = std::str::from_utf8(hello.canonical()).unwrap();
    assert_eq!(hello.name(), "StreamHello");
    assert!(handshake_v2::admit(&source, text, Some("call-a")).is_ok());
    assert!(handshake_v2::admit(&source, text, Some("call-b")).is_err());
    assert!(handshake_v2::admit(&source, text, None).is_err());
    assert!(handshake_v2::from_startup(&source, Some("invalid/call")).is_err());
}

#[test]
fn structural_hello_does_not_consume_a_host_nonce_or_accept_caller_claims() {
    let source = startup();
    let hello = handshake_v2::from_startup(&source, None).unwrap();
    let text = std::str::from_utf8(hello.canonical()).unwrap();
    for _ in 0..2 {
        assert!(handshake_v2::admit(&source, text, None).is_ok());
    }
    let mut fields: Value = serde_json::from_str(text).unwrap();
    fields["original_caller_id"] = json!("claimed-operator");
    assert!(handshake_v2::admit(&source, &fields.to_string(), None).is_err());
}

#[test]
fn independent_handshake_v2_goldens_match_complete_records_and_refusals() {
    let fixture: Value =
        serde_json::from_str(include_str!("../contracts/handshake-v2-cases.json")).unwrap();
    assert_eq!(fixture["profile_digest"], handshake_v2::profile_digest());
    for case in fixture["cases"].as_array().unwrap() {
        let source = case["source"].as_str().unwrap();
        let result = (|| {
            if case["action"] == "parse" {
                return handshake_v2::Record::from_json(case["name"].as_str().unwrap(), source);
            }
            let startup =
                managed_v2::Record::from_json("Startup", case["startup"].as_str().unwrap())?;
            let call = case["call_id"].as_str();
            if case["action"] == "create" {
                handshake_v2::from_startup(&startup, call)
            } else {
                handshake_v2::admit(&startup, source, call)
            }
        })();
        match result {
            Ok(record) => {
                assert!(case["error"].is_null(), "{}", case["id"]);
                assert_eq!(
                    std::str::from_utf8(record.canonical()).unwrap(),
                    case["canonical"].as_str().unwrap(),
                    "{}",
                    case["id"]
                );
            }
            Err(error) => assert_eq!(
                error.to_string().split(':').next().unwrap(),
                case["error"].as_str().unwrap_or("expected-success"),
                "{}",
                case["id"]
            ),
        }
    }
}
