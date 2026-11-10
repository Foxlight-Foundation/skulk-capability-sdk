//! Synthetic independent managed-profile witnesses; no installed-owner claims.
use serde_json::Value;
use skulk_capability_kernel::{managed, portable};

#[test]
fn independent_managed_records_have_full_stable_identities_and_bounded_refusals() {
    let fixture: Value =
        serde_json::from_str(include_str!("../contracts/managed-v1-cases.json")).unwrap();
    assert_eq!(managed::profile_digest(), fixture["profile_digest"]);
    assert_eq!(
        portable::profile_digest(),
        fixture["portable_profile_digest"]
    );
    for case in fixture["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let source = case["source"].as_str().unwrap();
        let result = managed::validate_contract_json(name, source);
        if let Some(error) = case["error"].as_str() {
            let actual = result.expect_err(case["id"].as_str().unwrap()).to_string();
            assert_eq!(actual.split(':').next().unwrap(), error, "{}", case["id"]);
            assert!(!actual.contains("synthetic-do-not-echo-secret"));
        } else {
            let actual = result.unwrap_or_else(|error| panic!("{}: {error}", case["id"]));
            assert_eq!(
                actual,
                case["canonical"].as_str().unwrap().as_bytes(),
                "{}",
                case["id"]
            );
            let digest = managed::contract_digest(name, source).unwrap();
            assert_eq!(digest, case["digest"].as_str().unwrap());
            assert_eq!(digest.len(), 64);
            let normalized = std::str::from_utf8(&actual).unwrap();
            assert_eq!(
                actual,
                managed::validate_contract_json(name, normalized).unwrap()
            );
            assert_eq!(digest, managed::contract_digest(name, normalized).unwrap());
        }
    }
}
