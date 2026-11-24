//! Inference installation semantics, separate from issued managed startup bytes.

use serde_json::{Value, json};
use skulk_capability_kernel::{managed, managed_v2, portable_v2};

fn source_case(identifier: &str) -> Value {
    let cases: Value =
        serde_json::from_str(include_str!("../contracts/managed-v1-cases.json")).unwrap();
    let source = cases["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|case| case["id"] == identifier)
        .unwrap()["source"]
        .as_str()
        .unwrap();
    serde_json::from_str(source).unwrap()
}

fn manifest() -> Value {
    let mut value = source_case("manifest-defaults");
    value["contract_profile"] = json!(managed_v2::PROFILE);
    value["profile_digest"] = json!(managed_v2::profile_digest());
    value["protocol"] = json!(6);
    value["effect"] = json!("inference");
    value["contracts"]["contract_profile"] = json!(portable_v2::PROFILE);
    value["contracts"]["profile_digest"] = json!(portable_v2::profile_digest());
    value["contracts"]["protocol"] = json!(6);
    let descriptor = &mut value["contracts"]["descriptors"][0];
    descriptor["contract_profile"] = json!(portable_v2::PROFILE);
    descriptor["profile_digest"] = json!(portable_v2::profile_digest());
    descriptor["behavior"] = json!("inference");
    descriptor["processing_domain"] = json!("external");
    value
}

fn normalize(value: &Value) -> Value {
    serde_json::from_slice(
        &managed_v2::validate_contract_json("Manifest", &value.to_string()).unwrap(),
    )
    .unwrap()
}

#[test]
fn inference_has_no_implicit_execution_deadline_or_legacy_cohort() {
    let source = manifest();
    let value = normalize(&source);
    assert!(value["bounds"]["inference_seconds"].is_null());
    assert_eq!(value["bounds"]["call_seconds"], 5);
    assert_eq!(value["bounds"]["stream_seconds"], 300);
    assert_eq!(value["bounds"]["concurrency"], 1);
    assert!(managed::Record::from_json("Manifest", &source.to_string()).is_err());
    let admitted = managed_v2::Record::from_json("Manifest", &source.to_string()).unwrap();
    assert_eq!(admitted.name(), "Manifest");
    assert_eq!(admitted.digest().len(), 64);
    assert_eq!(admitted.canonical(), value.to_string().as_bytes());
    assert_eq!(
        admitted.digest(),
        managed_v2::contract_digest("Manifest", &source.to_string()).unwrap()
    );
}

#[test]
fn explicit_inference_deadlines_are_separate_from_read_stream_watchdogs() {
    let mut source = manifest();
    source["bounds"] = json!({"inference_seconds": 7200, "concurrency": 32});
    let value = normalize(&source);
    assert_eq!(value["bounds"]["inference_seconds"], 7200);
    assert_eq!(value["bounds"]["stream_seconds"], 300);
    assert_eq!(value["bounds"]["concurrency"], 32);
    for invalid in [json!(0), json!(-1), json!(false), json!("unlimited")] {
        source["bounds"]["inference_seconds"] = invalid;
        assert!(managed_v2::validate_contract_json("Manifest", &source.to_string()).is_err());
    }
    for invalid in [0, 33] {
        source["bounds"] = json!({"concurrency": invalid});
        assert!(managed_v2::validate_contract_json("Manifest", &source.to_string()).is_err());
    }
}

#[test]
fn inference_cannot_hide_inside_a_read_manifest_or_include_governed_entries() {
    let mut source = manifest();
    source["effect"] = json!("read");
    assert!(managed_v2::validate_contract_json("Manifest", &source.to_string()).is_err());
    source["effect"] = json!("inference");
    for behavior in ["proposal", "execute"] {
        source["contracts"]["descriptors"][0]["behavior"] = json!(behavior);
        assert!(managed_v2::validate_contract_json("Manifest", &source.to_string()).is_err());
    }
}

#[test]
fn processing_domain_is_required_and_profile_downgrades_refuse() {
    let mut source = manifest();
    source["contracts"]["descriptors"][0]
        .as_object_mut()
        .unwrap()
        .remove("processing_domain");
    assert!(managed_v2::validate_contract_json("Manifest", &source.to_string()).is_err());
    source = manifest();
    source["contracts"]["contract_profile"] = json!("skulk.portable/1");
    assert!(managed_v2::validate_contract_json("Manifest", &source.to_string()).is_err());
    source = manifest();
    source["protocol"] = json!(5);
    assert!(managed_v2::validate_contract_json("Manifest", &source.to_string()).is_err());
}

#[test]
fn steward_metadata_cannot_disguise_inference_as_observation() {
    let mut source = manifest();
    source["steward"] = json!([{
        "qualified_id":"example@1.0.0", "behavior":"inference", "risk":"inference"
    }]);
    normalize(&source);
    for risk in ["observation", "billable", "effect"] {
        source["steward"][0]["risk"] = json!(risk);
        assert!(managed_v2::validate_contract_json("Manifest", &source.to_string()).is_err());
    }
}

#[test]
fn private_startup_binds_the_exact_new_manifest_and_refuses_old_identity() {
    let mut startup = source_case("startup-defaults");
    let old_digest = startup["manifest_digest"].clone();
    startup["contract_profile"] = json!(managed_v2::PROFILE);
    startup["profile_digest"] = json!(managed_v2::profile_digest());
    startup["protocol"] = json!(6);
    startup["token"] = json!("c".repeat(64));
    startup["manifest"] = manifest();
    startup["manifest_digest"] =
        json!(managed_v2::contract_digest("Manifest", &startup["manifest"].to_string()).unwrap());
    let record = managed_v2::Record::from_json("Startup", &startup.to_string()).unwrap();
    assert_eq!(record.name(), "Startup");
    assert!(managed::Record::from_json("Startup", &startup.to_string()).is_err());
    startup["manifest_digest"] = old_digest;
    assert!(managed_v2::Record::from_json("Startup", &startup.to_string()).is_err());
    startup["manifest_digest"] = json!("synthetic-do-not-echo-secret");
    let error = managed_v2::Record::from_json("Startup", &startup.to_string())
        .err()
        .unwrap()
        .to_string();
    assert!(!error.contains("synthetic-do-not-echo-secret"));
}

#[test]
fn independent_managed_v2_goldens_match_complete_records_and_refusals() {
    let fixture: Value =
        serde_json::from_str(include_str!("../contracts/managed-v2-cases.json")).unwrap();
    assert_eq!(fixture["profile_digest"], managed_v2::profile_digest());
    for case in fixture["cases"].as_array().unwrap() {
        let result = managed_v2::Record::from_json(
            case["name"].as_str().unwrap(),
            case["source"].as_str().unwrap(),
        );
        match result {
            Ok(record) => {
                assert!(case["error"].is_null(), "{}", case["id"]);
                assert_eq!(
                    std::str::from_utf8(record.canonical()).unwrap(),
                    case["canonical"].as_str().unwrap(),
                    "{}",
                    case["id"]
                );
                assert_eq!(
                    record.digest(),
                    case["digest"].as_str().unwrap(),
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
