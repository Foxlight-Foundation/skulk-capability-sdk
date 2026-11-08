//! Independent new-profile cases and adversarial resource/identity boundaries.

use serde_json::{Value, json};
use skulk_capability_kernel::portable::{self, PortableError, Schema};

fn fixture() -> Value {
    serde_json::from_str(include_str!("../contracts/portable-v1-cases.json")).unwrap()
}

fn check<T>(result: Result<T, PortableError>, case: &Value, accepted: impl FnOnce(T)) {
    if let Some(code) = case["error"].as_str() {
        match result {
            Err(error) => assert!(error.to_string().starts_with(&format!("{code}:"))),
            Ok(_) => panic!("expected bounded refusal {code}"),
        }
    } else {
        accepted(result.unwrap());
    }
}

#[test]
fn independent_portable_json_cases() {
    for case in fixture()["codec"].as_array().unwrap() {
        check(
            portable::canonical_json(case["source"].as_str().unwrap()),
            case,
            |bytes| {
                assert_eq!(bytes, case["canonical"].as_str().unwrap().as_bytes());
                assert_eq!(
                    bytes,
                    portable::canonical_json(std::str::from_utf8(&bytes).unwrap()).unwrap()
                );
            },
        );
    }
}

#[test]
fn independent_portable_schema_cases() {
    for case in fixture()["schemas"].as_array().unwrap() {
        let result = Schema::new(case["schema"].as_str().unwrap())
            .and_then(|schema| schema.matches(case["instance"].as_str().unwrap()));
        check(result, case, |valid| {
            assert_eq!(valid, case["valid"].as_bool().unwrap())
        });
    }
}

#[test]
fn independent_portable_contract_cases() {
    for case in fixture()["contracts"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let source = case["source"].as_str().unwrap();
        check(
            portable::validate_contract_json(name, source),
            case,
            |bytes| {
                assert_eq!(bytes, case["canonical"].as_str().unwrap().as_bytes());
                let digest = portable::contract_digest(name, source).unwrap();
                assert_eq!(digest, case["digest"].as_str().unwrap());
                assert_eq!(digest.len(), 64);
                let acquired = std::str::from_utf8(&bytes).unwrap();
                assert_eq!(
                    bytes,
                    portable::validate_contract_json(name, acquired).unwrap()
                );
                assert_eq!(digest, portable::contract_digest(name, acquired).unwrap());
            },
        );
    }
}

#[test]
fn resource_limits_apply_before_encoding_compilation_and_reference_expansion() {
    let deep = format!("{}null{}", "[".repeat(65), "]".repeat(65));
    assert_eq!(portable::canonical_json(&deep), Err(PortableError::Limit));
    let deeply_hostile = format!("{}null{}", "[".repeat(12_000), "]".repeat(12_000));
    assert_eq!(
        portable::canonical_json(&deeply_hostile),
        Err(PortableError::Limit)
    );
    let escaped = json!("狐".repeat(11_000)).to_string();
    assert_eq!(
        portable::canonical_json(&escaped),
        Err(PortableError::Limit)
    );
    let mut definitions = serde_json::Map::new();
    definitions.insert("n0".into(), json!(true));
    for index in 1..20 {
        let reference = format!("#/$defs/n{}", index - 1);
        definitions.insert(
            format!("n{index}"),
            json!({"allOf":[{"$ref":reference},{"$ref":reference}]}),
        );
    }
    assert!(matches!(
        Schema::new(&json!({"$defs":definitions}).to_string()),
        Err(PortableError::Limit)
    ));
    let mut definitions = serde_json::Map::new();
    definitions.insert("n0".into(), json!(true));
    for index in 1..100 {
        definitions.insert(
            format!("n{index}"),
            json!({"$ref":format!("#/$defs/n{}",index-1)}),
        );
    }
    assert!(matches!(
        Schema::new(&json!({"$defs":definitions}).to_string()),
        Err(PortableError::Limit)
    ));
}

#[test]
fn invalid_payload_is_distinct_from_valid_nonmatching_data() {
    let schema = Schema::new(r#"{"type":"integer"}"#).unwrap();
    assert!(!schema.matches("true").unwrap());
    assert_eq!(
        schema.matches("9007199254740992"),
        Err(PortableError::Number)
    );
    assert_eq!(schema.matches(r#"{"x":1,"x":2}"#), Err(PortableError::Json));
}

#[test]
fn envelope_count_refuses_before_compiling_supplied_descriptors() {
    let mut descriptor: Value =
        serde_json::from_str(fixture()["contracts"][0]["source"].as_str().unwrap()).unwrap();
    descriptor["input_schema"] = json!({"unsupported-synthetic-key":true});
    let oversized = json!({"contract_profile":portable::PROFILE,"profile_digest":portable::profile_digest(),"protocol":portable::PROTOCOL,"descriptors":vec![descriptor;17]});
    assert_eq!(
        portable::validate_contract_json("ContractEnvelope", &oversized.to_string()),
        Err(PortableError::Contract)
    );
}

#[test]
fn profile_specification_and_protocol_are_explicit() {
    assert_eq!(
        portable::profile_digest(),
        fixture()["profile_digest"].as_str().unwrap()
    );
    let spec: Value = serde_json::from_str(include_str!("../contracts/portable-v1.json")).unwrap();
    assert_eq!(spec["profile"], portable::PROFILE);
    assert_eq!(spec["protocol"], portable::PROTOCOL);
    assert_eq!(spec["dialect"], portable::DIALECT);
    assert_eq!(
        spec["limits"],
        json!({"payload_bytes":65536,"schema_bytes":65536,"contract_bytes":262144,"json_depth":64,"json_nodes":16384,"schema_nodes":1024,"schema_expansion":8192,"schema_reference_depth":64,"pattern_bytes":1024,"pattern_count":64,"regex_size":1048576,"safe_integer":9007199254740991i64})
    );
}

#[test]
fn signing_order_cannot_change_admitted_schema_meaning() {
    let first =
        r##"{"$defs":{"n":{"type":"integer"}},"type":"integer","$ref":"#/$defs/n","minimum":2}"##;
    let canonical = portable::canonical_json(first).unwrap();
    let acquired = std::str::from_utf8(&canonical).unwrap();
    for instance in ["null", "true", "1", "2", "2.0", "\"private\""] {
        assert_eq!(
            Schema::new(first).unwrap().matches(instance),
            Schema::new(acquired).unwrap().matches(instance)
        );
    }
    // The historical negated recursive witness receives one eager refusal in
    // both forms instead of inheriting either order-dependent legacy result.
    let old = r##"{"$defs":{"loop":{"type":"integer","$ref":"#/$defs/loop"}},"not":{"$ref":"#/$defs/loop"}}"##;
    let sorted = portable::canonical_json(old).unwrap();
    for source in [old, std::str::from_utf8(&sorted).unwrap()] {
        assert!(matches!(Schema::new(source), Err(PortableError::Cycle)));
    }
}
