//! Independent effect, processing and original-caller authority witnesses.
use serde_json::Value;
use skulk_capability_kernel::{
    composition_v2::{self, Record},
    portable_v2,
};

fn fixture() -> Value {
    serde_json::from_str(include_str!("../contracts/inference-v2-cases.json")).unwrap()
}

#[test]
fn every_independent_authority_witness_matches() {
    let fixture = fixture();
    assert_eq!(composition_v2::profile_digest(), fixture["profile_digest"]);
    assert_eq!(
        portable_v2::profile_digest(),
        fixture["author_profile_digest"]
    );
    for case in fixture["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let source = case["source"].as_str().unwrap();
        let result = if case["action"] == "author" {
            portable_v2::validate_contract_json(name, source)
                .and_then(|bytes| {
                    portable_v2::contract_digest(name, source).map(|digest| (bytes, Some(digest)))
                })
                .map_err(|error| error.to_string())
        } else {
            Record::from_json(name, source)
                .and_then(|record| {
                    if case["action"] == "parse" {
                        return Ok(record);
                    }
                    let parent = Record::from_json("Context", case["parent"].as_str().unwrap())?;
                    let policy = Record::from_json("Policy", case["policy"].as_str().unwrap())?;
                    let bytes = case["request_hex"]
                        .as_str()
                        .unwrap()
                        .as_bytes()
                        .chunks_exact(2)
                        .map(|pair| {
                            u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap()
                        })
                        .collect::<Vec<_>>();
                    composition_v2::admit(
                        &parent,
                        &record,
                        &policy,
                        case["executor"].as_str().unwrap(),
                        &bytes,
                        case["now_unix_ms"].as_u64().unwrap(),
                        case["scope_id"].as_str().unwrap(),
                    )
                })
                .map(|record| (record.canonical().to_vec(), None))
                .map_err(|error| error.to_string())
        };
        if let Some(expected) = case["error"].as_str() {
            let error = result
                .err()
                .unwrap_or_else(|| panic!("expected refusal: {}", case["id"]));
            assert_eq!(error.split(':').next().unwrap(), expected, "{}", case["id"]);
            assert!(!error.contains("synthetic-do-not-echo-secret"));
        } else {
            let (bytes, digest) = result.unwrap_or_else(|error| panic!("{}: {error}", case["id"]));
            assert_eq!(
                bytes,
                case["canonical"].as_str().unwrap().as_bytes(),
                "{}",
                case["id"]
            );
            assert_eq!(digest.as_deref(), case["digest"].as_str(), "{}", case["id"]);
        }
    }
}

#[test]
fn issued_profiles_remain_distinct_and_refuse_new_authority() {
    use skulk_capability_kernel::{composition, portable};
    let fixture = fixture();
    assert_ne!(
        composition::profile_digest(),
        composition_v2::profile_digest()
    );
    assert_ne!(portable::profile_digest(), portable_v2::profile_digest());
    let context = fixture["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["id"] == "parse-inference-context")
        .unwrap();
    assert!(
        composition::Record::from_json("Context", context["source"].as_str().unwrap()).is_err()
    );
    let author = fixture["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["id"] == "author-behavior-inference")
        .unwrap();
    assert!(
        portable::validate_contract_json("Descriptor", author["source"].as_str().unwrap()).is_err()
    );
    let mut old: Value = serde_json::from_str(author["source"].as_str().unwrap()).unwrap();
    old["contract_profile"] = serde_json::json!(portable::PROFILE);
    old["profile_digest"] = serde_json::json!(portable::profile_digest());
    assert!(portable::validate_contract_json("Descriptor", &old.to_string()).is_err());
}
