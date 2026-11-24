//! Nullable expiry must preserve real caller limits without inventing generation caps.

use serde_json::{Value, json};
use skulk_capability_kernel::{composition_v2, composition_v3};

struct Inputs {
    parent: Value,
    request: Value,
    policy: Value,
    executor: String,
    bytes: Vec<u8>,
    now: u64,
    scope: String,
}

fn inputs() -> Inputs {
    let fixture: Value =
        serde_json::from_str(include_str!("../contracts/inference-v2-cases.json")).unwrap();
    let case = fixture["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|case| case["action"] == "admit" && case["error"].is_null())
        .unwrap();
    let record = |field: &str| {
        let mut value: Value = serde_json::from_str(case[field].as_str().unwrap()).unwrap();
        value["contract_profile"] = json!(composition_v3::PROFILE);
        value["profile_digest"] = json!(composition_v3::profile_digest());
        value["protocol"] = json!(3);
        value
    };
    Inputs {
        parent: record("parent"),
        request: record("source"),
        policy: record("policy"),
        executor: case["executor"].as_str().unwrap().to_owned(),
        bytes: case["request_hex"]
            .as_str()
            .unwrap()
            .as_bytes()
            .chunks_exact(2)
            .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
            .collect(),
        now: case["now_unix_ms"].as_u64().unwrap(),
        scope: case["scope_id"].as_str().unwrap().to_owned(),
    }
}

fn admit(input: &Inputs) -> Result<composition_v3::Record, composition_v3::CompositionError> {
    let parent = composition_v3::Record::from_json("Context", &input.parent.to_string())?;
    let request = composition_v3::Record::from_json("DelegateRequest", &input.request.to_string())?;
    let policy = composition_v3::Record::from_json("Policy", &input.policy.to_string())?;
    composition_v3::admit(
        &parent,
        &request,
        &policy,
        &input.executor,
        &input.bytes,
        input.now,
        &input.scope,
    )
}

#[test]
fn every_nullable_combination_intersects_real_expiry_without_a_default_cap() {
    let mut input = inputs();
    let choices = [
        None,
        Some(input.now + 7_200_000),
        Some(input.now + 3_600_000),
    ];
    for parent in choices {
        for request in choices {
            for policy in choices {
                input.parent["deadline_unix_ms"] = json!(parent);
                input.request["deadline_unix_ms"] = json!(request);
                input.policy["deadline_unix_ms"] = json!(policy);
                let child = admit(&input).unwrap();
                let child: Value = serde_json::from_slice(child.canonical()).unwrap();
                let expected = [parent, request, policy].into_iter().flatten().min();
                assert_eq!(child["deadline_unix_ms"], json!(expected));
                for field in [
                    "caller_id",
                    "caller_kind",
                    "issuer_node_id",
                    "root_scope_id",
                ] {
                    assert_eq!(child[field], input.parent[field]);
                }
            }
        }
    }
}

#[test]
fn null_never_revives_expired_authority_or_accepts_an_invalid_clock() {
    for field in ["parent", "request", "policy"] {
        let mut input = inputs();
        input.parent["deadline_unix_ms"] = Value::Null;
        input.request["deadline_unix_ms"] = Value::Null;
        input.policy["deadline_unix_ms"] = Value::Null;
        match field {
            "parent" => input.parent["deadline_unix_ms"] = json!(input.now),
            "request" => input.request["deadline_unix_ms"] = json!(input.now),
            _ => input.policy["deadline_unix_ms"] = json!(input.now),
        }
        assert!(matches!(
            admit(&input),
            Err(composition_v3::CompositionError::Expired)
        ));
    }
    for now in [0, 9_007_199_254_740_992] {
        let mut input = inputs();
        input.parent["deadline_unix_ms"] = Value::Null;
        input.request["deadline_unix_ms"] = Value::Null;
        input.policy["deadline_unix_ms"] = Value::Null;
        input.now = now;
        assert!(matches!(
            admit(&input),
            Err(composition_v3::CompositionError::Expired)
        ));
    }
}

#[test]
fn absence_is_explicit_and_malformed_or_older_profile_deadlines_refuse() {
    let input = inputs();
    for (name, source) in [
        ("Context", input.parent),
        ("DelegateRequest", input.request),
        ("Policy", input.policy),
    ] {
        let mut missing = source.clone();
        missing.as_object_mut().unwrap().remove("deadline_unix_ms");
        assert!(composition_v3::Record::from_json(name, &missing.to_string()).is_err());
        for invalid in [
            json!(0),
            json!(-1),
            json!(true),
            json!("unlimited"),
            json!(9_007_199_254_740_992u64),
        ] {
            let mut value = source.clone();
            value["deadline_unix_ms"] = invalid;
            assert!(composition_v3::Record::from_json(name, &value.to_string()).is_err());
        }
        assert!(composition_v2::Record::from_json(name, &source.to_string()).is_err());
    }
}
