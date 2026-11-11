//! Independently authored complete caller attenuation witnesses.
use serde_json::Value;
use skulk_capability_kernel::composition::{self, Record};

#[test]
fn every_independent_composition_observation_matches() {
    let fixture: Value =
        serde_json::from_str(include_str!("../contracts/composition-v1-cases.json")).unwrap();
    assert_eq!(composition::profile_digest(), fixture["profile_digest"]);
    for case in fixture["cases"].as_array().unwrap() {
        let record = Record::from_json(
            case["name"].as_str().unwrap(),
            case["source"].as_str().unwrap(),
        );
        let result = record.and_then(|record| {
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
                .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
                .collect::<Vec<_>>();
            composition::admit(
                &parent,
                &record,
                &policy,
                case["executor"].as_str().unwrap(),
                &bytes,
                case["now_unix_ms"].as_u64().unwrap(),
                case["scope_id"].as_str().unwrap(),
            )
        });
        if let Some(expected) = case["error"].as_str() {
            let error = result
                .err()
                .unwrap_or_else(|| panic!("expected refusal: {}", case["id"]));
            assert_eq!(
                error.to_string().split(':').next().unwrap(),
                expected,
                "{}",
                case["id"]
            );
            assert!(!error.to_string().contains("synthetic-do-not-echo-secret"));
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

#[test]
fn actual_request_bound_is_checked_before_hashing_or_attenuation() {
    let fixture: Value =
        serde_json::from_str(include_str!("../contracts/composition-v1-cases.json")).unwrap();
    let case = fixture["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|case| case["id"] == "exact-delegation")
        .unwrap();
    let parent = Record::from_json("Context", case["parent"].as_str().unwrap()).unwrap();
    let policy = Record::from_json("Policy", case["policy"].as_str().unwrap()).unwrap();
    let mut request: Value = serde_json::from_str(case["source"].as_str().unwrap()).unwrap();
    let mut bytes = vec![b'x'; 16_777_216];
    request["request_sha256"] =
        serde_json::json!("a06c26cbac8b80704f420222dae5658b88ff2da96702d12ef7a4223e9361f7c1");
    let request = Record::from_json("DelegateRequest", &request.to_string()).unwrap();
    let args = (
        case["executor"].as_str().unwrap(),
        case["scope_id"].as_str().unwrap(),
    );
    assert!(composition::admit(&parent, &request, &policy, args.0, &bytes, 10_000, args.1).is_ok());
    bytes.push(b'x');
    assert!(matches!(
        composition::admit(&parent, &request, &policy, args.0, &bytes, 10_000, args.1),
        Err(composition::CompositionError::Limit)
    ));
}
