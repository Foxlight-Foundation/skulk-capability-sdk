//! Independent frozen Python references, not expectations rebuilt by Rust.

use serde_json::Value;
use skulk_capability_kernel::codec::{self, CodecError};

#[test]
fn both_python_references_keep_signed_bytes_and_scoped_hashes() {
    for raw in [
        include_str!("../../tests/fixtures/author-baseline/sdk-0.4.0.json"),
        include_str!("../../tests/fixtures/author-baseline/sdk-0.5.0.json"),
    ] {
        let reference: Value = serde_json::from_str(raw).unwrap();
        for group in ["canonical_cases", "manifest_cases"] {
            for case in reference[group].as_array().unwrap() {
                let actual = codec::canonical(&case["message"].to_string()).unwrap();
                assert_eq!(actual, case["canonical_utf8"].as_str().unwrap().as_bytes());
                assert_eq!(
                    codec::document_digest(&case["message"].to_string()).unwrap(),
                    case["sha256"].as_str().unwrap()
                );
            }
        }
        for case in reference["operation_input_cases"].as_array().unwrap() {
            assert_eq!(
                codec::input_digest(case["scope"].as_str().unwrap(), &case["input"].to_string())
                    .unwrap(),
                case["sha256"].as_str().unwrap()
            );
        }
    }
}

#[test]
fn numeric_and_unicode_profiles_are_not_default_json_serialization() {
    for (raw, expected) in [
        ("1", "1"),
        ("1.00", "1.0"),
        ("-0", "0"),
        ("-0.0", "-0.0"),
        ("1e-7", "1e-07"),
        ("1e20", "1e+20"),
        ("1e-4", "0.0001"),
        ("1e15", "1000000000000000.0"),
        ("1e16", "1e+16"),
        ("9007199254740993", "9007199254740993"),
        (r#""\ud800""#, r#""\ud800""#),
        (r#"{"𐀀":1,"\ue000":2}"#, r#"{"\ue000":2,"\ud800\udc00":1}"#),
    ] {
        assert_eq!(codec::canonical(raw).unwrap(), expected.as_bytes(), "{raw}");
    }
    assert_eq!(
        codec::input_digest("s", r#"{"text":"\ud800"}"#).unwrap(),
        "58a01e59583df2f8c03dd2ac0dc87f941666db49b9d8dc4b5abf53bc053c3017"
    );
}

#[test]
fn malformed_and_ambiguous_inputs_fail_without_echoing_content() {
    for raw in ["[1,]", "{", "01", "1.", "true false", r#""\x01""#, "NaN"] {
        assert!(codec::canonical(raw).is_err(), "{raw}");
    }
    assert_eq!(
        codec::canonical(r#"{"a":1,"\u0061":2}"#),
        Err(CodecError::DuplicateKey)
    );
    assert_eq!(codec::canonical("1e999"), Err(CodecError::NonFinite));
    assert_eq!(codec::input_digest("s", "[]"), Err(CodecError::InputShape));
}

#[test]
fn deep_json_is_bounded_without_using_the_process_stack_as_a_failure_signal() {
    let allowed = format!("{}null{}", "[".repeat(513), "]".repeat(513));
    assert_eq!(codec::canonical(&allowed).unwrap(), allowed.as_bytes());
    let refused = format!("{}null{}", "[".repeat(1026), "]".repeat(1026));
    assert_eq!(codec::canonical(&refused), Err(CodecError::Nesting));
}
