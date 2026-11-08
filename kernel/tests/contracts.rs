//! Independently frozen Python contracts and native typed normalization.
use skulk_capability_kernel::contracts::Registry;

#[test]
fn metadata_lane_declarations_match_the_released_author_contract() {
    let registry = Registry::packaged().unwrap();
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../tests/fixtures/author-baseline/sdk-0.5.5.json"
    ))
    .unwrap();
    let base = &fixture["manifest_cases"][0]["message"]["descriptors"][0];
    for mode in [
        "unary",
        "server_streaming",
        "client_streaming",
        "bidirectional",
    ] {
        for admission in ["metadata", "unknown", ""] {
            for input in [
                serde_json::json!({"type":"object", "additionalProperties":false}),
                serde_json::json!({"type":"object", "additionalProperties":0}),
                serde_json::json!({"type":"object", "additionalProperties":false, "properties":{}}),
            ] {
                let mut descriptor = base.clone();
                descriptor["io_mode"] = serde_json::json!(mode);
                descriptor["input_chunk_schema"] =
                    if matches!(mode, "client_streaming" | "bidirectional") {
                        serde_json::json!({})
                    } else {
                        serde_json::Value::Null
                    };
                descriptor["output_chunk_schema"] =
                    if matches!(mode, "server_streaming" | "bidirectional") {
                        serde_json::json!({})
                    } else {
                        serde_json::Value::Null
                    };
                descriptor["annotations"] = serde_json::json!({"skulk.admission":admission});
                descriptor["input_schema"] = input.clone();
                let expected = mode == "unary"
                    && admission == "metadata"
                    && input == serde_json::json!({"type":"object", "additionalProperties":false});
                assert_eq!(
                    registry
                        .validate_json("Descriptor", &descriptor.to_string())
                        .is_ok(),
                    expected,
                    "{mode} / {admission} / {input}"
                );
            }
        }
    }
}

#[test]
fn frozen_wire_and_manifest_projections_keep_exact_signed_bytes() {
    let registry = Registry::packaged().unwrap();
    for source in [
        include_str!("../../tests/fixtures/author-baseline/sdk-0.4.0.json"),
        include_str!("../../tests/fixtures/author-baseline/sdk-0.5.0.json"),
        include_str!("../../tests/fixtures/author-baseline/sdk-0.5.5.json"),
    ] {
        let fixture: serde_json::Value = serde_json::from_str(source).unwrap();
        for case in fixture["canonical_cases"].as_array().unwrap() {
            let bytes = registry
                .validate_json("WireMessage", &case["message"].to_string())
                .unwrap();
            assert_eq!(
                String::from_utf8(bytes).unwrap(),
                case["canonical_utf8"].as_str().unwrap()
            );
        }
        for case in fixture["manifest_cases"].as_array().unwrap() {
            let bytes = registry
                .validate_json("Manifest", &case["message"].to_string())
                .unwrap();
            assert_eq!(
                String::from_utf8(bytes).unwrap(),
                case["canonical_utf8"].as_str().unwrap()
            );
        }
    }
}

#[test]
fn strict_types_defaults_literals_and_integer_bounds_match_python() {
    let registry = Registry::packaged().unwrap();
    let bytes = registry
        .validate_json(
            "Bounds",
            r#"{"startup_seconds":1,"concurrency":true,"log_bytes":0.0}"#,
        )
        .unwrap();
    let text = String::from_utf8(bytes).unwrap();
    assert!(text.contains("\"startup_seconds\":1.0"));
    assert!(text.contains("\"concurrency\":1"));
    assert!(text.contains("\"log_bytes\":0"));
    assert!(!text.contains("stream_seconds"));
    for source in [
        r#"{"max_children":1.0}"#,
        r#"{"startup_seconds":true}"#,
        r#"{"max_children":true}"#,
        r#"{"unexpected":1}"#,
    ] {
        assert!(registry.validate_json("Bounds", source).is_err());
    }
    assert!(
        registry
            .validate_json(
                "OperationCall",
                r#"{"op":"list","offset":9007199254740992}"#
            )
            .is_ok()
    );
    assert!(
        registry
            .validate_json(
                "OperationCall",
                r#"{"op":"list","offset":9007199254740993}"#
            )
            .is_err()
    );
    assert!(
        registry
            .validate_json("OperationCall", r#"{"op":"status"}"#)
            .is_err()
    );
    assert!(
        registry
            .validate_json("OperationCall", r#"{"op":"start","input":{},"offset":0}"#)
            .is_err()
    );
}

#[test]
fn cross_field_protocol_surface_and_result_constraints_are_native() {
    let registry = Registry::packaged().unwrap();
    assert!(
        registry
            .validate_json(
                "Surface",
                r#"{"surface_id":"x","title":"x","kind":"link","entry":"/other"}"#
            )
            .is_err()
    );
    assert!(
        registry
            .validate_json(
                "SurfaceReport",
                r#"{"surface_id":"x","ready":true,"url":"https://host/path?token=secret"}"#
            )
            .is_err()
    );
    assert!(
        registry
            .validate_json(
                "WireMessage",
                r#"{"protocol":4,"kind":"result","call_id":"x","payload":{},"error":"timeout"}"#
            )
            .is_err()
    );
    assert!(
        registry
            .validate_json("WireMessage", r#"{"protocol":4.0,"kind":"shutdown"}"#)
            .is_err()
    );
    assert!(
        registry
            .validate_json("WireMessage", r#"{"protocol":3,"kind":"shutdown"}"#)
            .is_ok()
    );
    assert!(
        registry
            .validate_json("WireMessage", r#"{"protocol":2,"kind":"shutdown"}"#)
            .is_err()
    );
}

#[test]
fn operation_authority_is_checked_before_effectful_dispatch() {
    let registry = Registry::packaged().unwrap();
    for source in [
        r#"{"op":"status","operation_id":"x"}"#,
        r#"{"op":"list"}"#,
        r#"{"op":"log","operation_id":"x"}"#,
    ] {
        assert!(registry.authorize_operation_json(source, "read").is_ok());
    }
    for source in [
        r#"{"op":"start","input":{}}"#,
        r#"{"op":"cancel","operation_id":"x"}"#,
        r#"{"op":"plan","input":{}}"#,
    ] {
        assert!(registry.authorize_operation_json(source, "read").is_err());
        assert!(registry.authorize_operation_json(source, "effect").is_ok());
        assert!(
            registry
                .authorize_operation_json(source, "sdk-account")
                .is_err()
        );
    }
}

#[test]
fn compatibility_and_address_grammars_refuse_marker_and_zone_delimiters() {
    let registry = Registry::packaged().unwrap();
    let reference: serde_json::Value = serde_json::from_str(include_str!(
        "../../tests/fixtures/author-baseline/sdk-0.5.0.json"
    ))
    .unwrap();
    let mut manifest = reference["manifest_cases"][0]["message"].clone();
    for requirement in ["===foo;bar", "===foo)", "===foo bar"] {
        manifest["skulk_requires"] = serde_json::Value::String(requirement.into());
        assert!(
            registry
                .validate_json("Manifest", &manifest.to_string())
                .is_err()
        );
    }
    manifest["skulk_requires"] = serde_json::Value::String("===custom-build".into());
    assert!(
        registry
            .validate_json("Manifest", &manifest.to_string())
            .is_ok()
    );
    let mut startup = serde_json::json!({"socket":"/synthetic/control","token":"synthetic","node_id":"n","host_id":"h","transport_id":"t","manifest":manifest});
    for address in ["fe80::1%eth0/bad", "fe80::1%", "fe80::1%eth0%bad"] {
        startup["serve_host"] = serde_json::Value::String(address.into());
        assert!(
            registry
                .validate_json("Startup", &startup.to_string())
                .is_err()
        );
    }
    for address in ["fe80::1%eth0", "fe80::1%eth0 bad", "100.64.0.1"] {
        startup["serve_host"] = serde_json::Value::String(address.into());
        assert!(
            registry
                .validate_json("Startup", &startup.to_string())
                .is_ok()
        );
    }
    for url in [
        "https://[fe80::1%]/",
        "https://[fe80::1%eth0%bad]/",
        "https://[fe80::1%eth0/bad]/",
    ] {
        let report = serde_json::json!({"surface_id":"page","ready":true,"url":url});
        assert!(
            registry
                .validate_json("SurfaceReport", &report.to_string())
                .is_err()
        );
    }
    for url in [
        "https://[fe80::1%eth0]/",
        "https://[::1]/",
        "https://[fe80::1%eth0 bad]/",
    ] {
        let report = serde_json::json!({"surface_id":"page","ready":true,"url":url});
        assert!(
            registry
                .validate_json("SurfaceReport", &report.to_string())
                .is_ok()
        );
    }
}

#[test]
fn serve_address_classifies_mapped_ipv4_without_rewriting_surface_urls() {
    let registry = Registry::packaged().unwrap();
    let reference: serde_json::Value = serde_json::from_str(include_str!(
        "../../tests/fixtures/author-baseline/sdk-0.5.0.json"
    ))
    .unwrap();
    let mut startup = serde_json::json!({"socket":"/synthetic/control","token":"synthetic","node_id":"n","host_id":"h","transport_id":"t","manifest":reference["manifest_cases"][0]["message"]});
    for (address, accepted) in [
        ("::ffff:192.0.2.1", true),
        ("::ffff:192.0.2.1%eth0", true),
        ("::127.0.0.1", true),
        ("::ffff:127.0.0.1", false),
        ("::ffff:7f00:1", false),
        ("::ffff:127.0.0.1%eth0", false),
        ("::ffff:0.0.0.0", false),
        ("::ffff:224.0.0.1", false),
        ("::ffff:e000:1", false),
    ] {
        startup["serve_host"] = serde_json::Value::String(address.into());
        assert_eq!(
            registry
                .validate_json("Startup", &startup.to_string())
                .is_ok(),
            accepted,
            "{address}"
        );
        let report = serde_json::json!({"surface_id":"page","ready":true,"url":format!("https://[{address}]/")});
        assert!(
            registry
                .validate_json("SurfaceReport", &report.to_string())
                .is_ok(),
            "{address} remains a valid browser surface"
        );
    }
}

#[test]
fn structured_tuples_and_json_payload_kinds_follow_the_python_constructor() {
    use skulk_capability_kernel::codec::{self, Value};
    use skulk_capability_kernel::contracts::ConstructorForm;
    let registry = Registry::packaged().unwrap();
    let mut page = codec::parse(r#"{"active":0,"operations":[]}"#).unwrap();
    assert!(
        registry
            .validate_value("OperationPage", &page, ConstructorForm::Python)
            .is_err()
    );
    let javascript = registry
        .validate_value("OperationPage", &page, ConstructorForm::JavaScript)
        .unwrap();
    if let Value::Object(fields) = &mut page {
        fields.insert(codec::points("operations"), Value::Tuple(vec![]));
    }
    let python = registry
        .validate_value("OperationPage", &page, ConstructorForm::Python)
        .unwrap();
    assert_eq!(
        registry.project_value(&python).unwrap(),
        registry.project_value(&javascript).unwrap()
    );
    let mut operation = codec::parse(r#"{"op":"start","input":{}}"#).unwrap();
    if let Value::Object(fields) = &mut operation {
        fields.insert(
            codec::points("input"),
            Value::Object(std::collections::BTreeMap::from([(
                vec![0xd800],
                Value::String(vec![0xd800]),
            )])),
        );
    }
    let validated = registry
        .validate_value("OperationCall", &operation, ConstructorForm::Python)
        .unwrap();
    assert_eq!(registry.project_value(&validated).unwrap(), br#"{"input":{"\ufffd\ufffd\ufffd":"\ud800"},"offset":null,"op":"start","operation_id":null}"#);
    if let Value::Object(fields) = &mut operation {
        fields.insert(
            codec::points("input"),
            Value::Object(std::collections::BTreeMap::from([(
                codec::points("x"),
                Value::Tuple(vec![]),
            )])),
        );
    }
    assert!(
        registry
            .validate_value("OperationCall", &operation, ConstructorForm::Python)
            .is_err()
    );
}

#[test]
fn raw_constructor_media_is_valid_before_its_json_serialization_boundary() {
    use skulk_capability_kernel::codec::{self, Value};
    use skulk_capability_kernel::contracts::ConstructorForm;
    let registry = Registry::packaged().unwrap();
    let mut media = codec::parse(r#"{"media_type":"audio/test","data":"abc"}"#).unwrap();
    assert!(
        registry
            .validate_value("InlineMedia", &media, ConstructorForm::Python)
            .is_err()
    );
    if let Value::Object(fields) = &mut media {
        fields.insert(codec::points("data"), Value::Bytes(vec![0xff, 0, 0x80]));
    }
    let validated = registry
        .validate_value("InlineMedia", &media, ConstructorForm::Python)
        .unwrap();
    assert_eq!(validated.name(), "InlineMedia");
    assert!(
        matches!(validated.value(), Value::Object(fields) if fields.get(&codec::points("data"))==Some(&Value::Bytes(vec![0xff,0,0x80])))
    );
    assert!(registry.project_value(&validated).is_err());
    if let Value::Object(fields) = &mut media {
        fields.insert(codec::points("data"), Value::Bytes(vec![b'a', 0, b'b']));
    }
    let validated = registry
        .validate_value("InlineMedia", &media, ConstructorForm::Python)
        .unwrap();
    assert!(
        String::from_utf8(registry.project_value(&validated).unwrap())
            .unwrap()
            .contains(r#""data":"a\u0000b""#)
    );
    if let Value::Object(fields) = &mut media {
        fields.insert(codec::points("data"), Value::Bytes(vec![0; 1_048_577]));
    }
    assert!(
        registry
            .validate_value("InlineMedia", &media, ConstructorForm::Python)
            .is_err()
    );
}

#[test]
fn constructor_json_numbers_are_distinct_from_finite_wire_serialization() {
    use skulk_capability_kernel::codec::{self, Value};
    use skulk_capability_kernel::contracts::ConstructorForm;
    let registry = Registry::packaged().unwrap();
    for number in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        let mut input = codec::parse(r#"{"op":"start","input":{}}"#).unwrap();
        if let Value::Object(fields) = &mut input {
            fields.insert(
                codec::points("input"),
                Value::Object(std::collections::BTreeMap::from([(
                    codec::points("x"),
                    Value::Float(number),
                )])),
            );
        }
        let validated = registry
            .validate_value("OperationCall", &input, ConstructorForm::Python)
            .unwrap();
        assert!(registry.project_value(&validated).is_err());
    }
}

#[test]
fn structured_stream_media_crosses_the_binary_boundary_without_utf8_encoding() {
    use skulk_capability_kernel::codec::{self, Value};
    use skulk_capability_kernel::contracts::ConstructorForm;
    let registry = Registry::packaged().unwrap();
    let mut frame = codec::parse(r#"{"call_id":"c","direction":"provider_to_caller","sequence":1,"kind":"chunk","media":{"kind":"inline","data":"","media_type":"audio/test"}}"#).unwrap();
    if let Value::Object(fields) = &mut frame
        && let Some(Value::Object(media)) = fields.get_mut(&codec::points("media"))
    {
        media.insert(codec::points("data"), Value::Bytes(vec![0xff, 0, 0x80]));
    }
    let validated = registry
        .validate_value("StreamPacket", &frame, ConstructorForm::Python)
        .unwrap();
    assert!(registry.project_value(&validated).is_err());
    let packet = registry.stream_packet(&validated).unwrap();
    assert_eq!(packet.media, vec![0xff, 0, 0x80]);
    assert!(!packet.projection_json().unwrap().contains(r#""data""#));
    let encoded = skulk_capability_kernel::streams::encode_packet(&packet).unwrap();
    assert_eq!(
        skulk_capability_kernel::streams::decode_packet(&encoded).unwrap(),
        packet
    );
}
