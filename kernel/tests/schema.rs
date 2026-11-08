//! Focused regressions for lossless, permanently offline payload validation.

use skulk_capability_kernel::schema::Schema;

#[test]
fn recursive_descent_preserves_legacy_keyword_exemptions() {
    let schema = Schema::new(r##"{"$schema":"https://json-schema.org/draft/2019-09/schema","$recursiveAnchor":true,"properties":{"child":{"$recursiveRef":"#"}},"unevaluatedProperties":{"type":"integer"}}"##).unwrap();
    assert!(schema.matches(r#"{"child":{"type":"s"}}"#));
    assert!(!schema.matches(r#"{"child":{"x":"s"}}"#));
    assert!(schema.matches(r#"{"child":{"x":1}}"#));
}

#[test]
fn a_nonproductive_negated_cycle_cannot_become_valid() {
    for remaining in ["false", r#"{"type":"integer"}"#] {
        let schema = Schema::new(&format!(r##"{{"$schema":"https://json-schema.org/draft/2019-09/schema","not":{{"$ref":"#"}},"unevaluatedProperties":{remaining}}}"##)).unwrap();
        for value in ["{}", "null", "[]", r#"{"type":"s"}"#] {
            assert!(!schema.matches(value));
        }
    }
}

#[test]
fn guarded_cycles_leave_no_results_in_later_values_or_threads() {
    let schema = Schema::new(r##"{"$schema":"https://json-schema.org/draft/2020-12/schema","dependentSchemas":{"x":{"$ref":"#"}},"unevaluatedProperties":true}"##).unwrap();
    std::thread::scope(|threads| {
        for _ in 0..4 {
            let schema = &schema;
            threads.spawn(move || {
                for _ in 0..16 {
                    assert!(!schema.matches(r#"{"x":1}"#));
                    assert!(schema.matches("{}"));
                }
            });
        }
    });
}

#[test]
fn recursive_graph_resolution_remains_lazy_and_permanently_offline() {
    let schema = Schema::new(r##"{"$schema":"https://json-schema.org/draft/2019-09/schema","$recursiveAnchor":true,"$defs":{"unused":{"$recursiveRef":"#"}},"properties":{"x":{"not":{"$ref":"file:///unavailable"}}},"unevaluatedProperties":true}"##).unwrap();
    assert!(schema.matches("{}"));
    assert!(!schema.matches(r#"{"x":1}"#));
    assert!(schema.matches(r#"{"other":1}"#));
}

#[test]
fn boolean_items_retain_the_legacy_helper_refusal() {
    let schema = Schema::new(r##"{"$schema":"https://json-schema.org/draft/2019-09/schema","items":true,"unevaluatedItems":false,"$defs":{"unused":{"$recursiveRef":"#"}}}"##).unwrap();
    assert!(!schema.matches("[]"));
    assert!(!schema.matches("[1]"));
    assert!(schema.matches("{}"));
}

#[test]
fn a_second_reference_edge_cannot_hide_a_negated_cycle() {
    for leaf in [false, true] {
        let source = format!(
            r##"{{"$schema":"https://json-schema.org/draft/2020-12/schema","$defs":{{"loop":{{"$ref":"#/$defs/loop","$dynamicRef":"#/$defs/leaf"}},"leaf":{leaf}}},"not":{{"$ref":"#/$defs/loop"}}}}"##
        );
        let schema = Schema::new(&source).unwrap();
        for value in ["{}", "1", "null", "[]"] {
            assert!(!schema.matches(value));
        }
    }
}

#[test]
fn keyword_order_is_validation_state_and_never_changes_canonical_identity() {
    let first = r##"{"$schema":"https://json-schema.org/draft/2020-12/schema","$defs":{"loop":{"$ref":"#/$defs/loop","$dynamicRef":"#/$defs/leaf"},"leaf":false},"not":{"$ref":"#/$defs/loop"}}"##;
    let guarded = r##"{"$schema":"https://json-schema.org/draft/2020-12/schema","$defs":{"loop":{"$dynamicRef":"#/$defs/leaf","$ref":"#/$defs/loop"},"leaf":false},"not":{"$ref":"#/$defs/loop"}}"##;
    assert_eq!(
        skulk_capability_kernel::codec::canonical(first).unwrap(),
        skulk_capability_kernel::codec::canonical(guarded).unwrap()
    );
    assert!(!Schema::new(first).unwrap().matches("1"));
    assert!(Schema::new(guarded).unwrap().matches("1"));
}

#[test]
fn oneof_after_first_match_retains_the_parent_resolver() {
    let schema = Schema::new(r##"{"$schema":"https://json-schema.org/draft/2020-12/schema","$id":"https://example.test/root","$defs":{"parent":{"$id":"rules","const":1},"shifted":{"$id":"shift/rules","const":2}},"oneOf":[true,{"$id":"shift/","$ref":"rules"}],"unevaluatedProperties":false}"##).unwrap();
    assert!(!schema.matches("1"));
    assert!(schema.matches("2"));
}

#[test]
fn dynamic_rebinding_cycle_is_guarded_without_unevaluated_coverage() {
    let schema = Schema::new(r##"{"$schema":"https://json-schema.org/draft/2020-12/schema","$id":"https://example.test/root","$defs":{"a":{"$id":"a","$dynamicAnchor":"x","$ref":"base"},"base":{"$id":"base","$dynamicRef":"leaf#x"},"leaf":{"$id":"leaf","$dynamicAnchor":"x"}},"not":{"$ref":"a"}}"##).unwrap();
    assert!(!schema.matches("null"));
    assert!(!schema.matches("{}"));
}

#[test]
fn python_pattern_classes_and_final_newline() {
    let schema = Schema::new(r#"{"pattern":"^\\w+$"}"#).unwrap();
    assert!(schema.matches(r#""\u0661\u00b2\u00e9""#));
    assert!(!schema.matches(r#""\u0301""#));
    assert!(!schema.matches(r#""\u200d""#));
    assert!(
        Schema::new(r#"{"pattern":"^a$"}"#)
            .unwrap()
            .matches(r#""a\n""#)
    );
}

#[test]
fn scalar_projection_never_collides_with_private_use() {
    for point in [r#""\ud800""#, r#""\udfff""#, r#""\udb80\udc00""#] {
        let schema = Schema::new(&format!("{{\"const\":{point}}}")).unwrap();
        assert!(schema.matches(point));
        for other in [r#""\ud800""#, r#""\udfff""#, r#""\udb80\udc00""#] {
            assert_eq!(schema.matches(other), point == other);
        }
    }
    let schema = Schema::new(r#"{"pattern":"[\\uE000-\\U0010FFFF]"}"#).unwrap();
    assert!(!schema.matches(r#""\ud800""#));
    assert!(schema.matches(r#""\udb80\udc00""#));
}

#[test]
fn unavailable_references_remain_lazy_and_cannot_be_negated_into_success() {
    let unused = Schema::new(r#"{"$defs":{"unused":{"$ref":"file:///unavailable"}}}"#).unwrap();
    assert!(unused.matches("1"));
    let negated = Schema::new(r#"{"not":{"$ref":"file:///unavailable"}}"#).unwrap();
    assert!(!negated.matches("1"));
    let first =
        Schema::new(r#"{"anyOf":[true,{"$ref":"https://example.invalid/schema"}]}"#).unwrap();
    assert!(first.matches("1"));
    let last =
        Schema::new(r#"{"anyOf":[{"$ref":"https://example.invalid/schema"},true]}"#).unwrap();
    assert!(!last.matches("1"));
}

#[test]
fn reference_refusal_is_isolated_between_threads_and_later_calls() {
    std::thread::scope(|scope| {
        for index in 0..8 {
            scope.spawn(move || {
                let unknown =
                    Schema::new(r#"{"not":{"$ref":"https://example.invalid/schema"}}"#).unwrap();
                let valid = Schema::new(r#"{"type":"integer"}"#).unwrap();
                for _ in 0..32 {
                    assert!(!unknown.matches(&index.to_string()));
                    assert!(valid.matches(&index.to_string()));
                }
            });
        }
    });
}

#[test]
fn python_float_multiple_behavior_and_exact_wide_integer_bounds() {
    let schema = Schema::new(r#"{"multipleOf":0.1}"#).unwrap();
    assert!(!schema.matches("0.3"));
    assert!(schema.matches("1.0"));
    let wide = Schema::new(r#"{"minimum":9007199254740993}"#).unwrap();
    assert!(!wide.matches("9007199254740992"));
    assert!(wide.matches("9007199254740993"));
}

#[test]
fn json_depth_uses_the_author_envelope_instead_of_serde_parser_defaults() {
    let schema = Schema::new(r#"{"type":"object"}"#).unwrap();
    let mut value = "1".to_owned();
    for _ in 0..199 {
        value = format!("{{\"x\":{value}}}");
    }
    assert!(schema.matches(&value));
    for _ in 0..2 {
        value = format!("{{\"x\":{value}}}");
    }
    assert!(!schema.matches(&value));
    assert!(!schema.matches(r#"{"x":1,"x":2}"#));
}

#[test]
fn encoded_document_bounds_are_checked_before_parsing() {
    assert!(Schema::new(&" ".repeat(65_537)).is_err());
    assert!(!Schema::new("true").unwrap().matches(&" ".repeat(65_537)));
}

#[test]
fn legacy_composition_keeps_pinned_keyword_name_coverage() {
    for body in [
        r#""allOf":[{"additionalProperties":{"type":"integer"}}]"#,
        r#""anyOf":[{"additionalProperties":{"type":"integer"}},false]"#,
        r#""dependentSchemas":{"x":{"additionalProperties":{"type":"integer"}}}"#,
        r#""if":{"required":["x"]},"then":{"additionalProperties":{"type":"integer"}}"#,
    ] {
        let definition = format!(
            r#"{{"$schema":"https://json-schema.org/draft/2019-09/schema",{body},"unevaluatedProperties":false}}"#
        );
        let schema = Schema::new(&definition).unwrap();
        assert!(!schema.matches(r#"{"x":1}"#));
        assert!(schema.matches("{}"));
    }
}

#[test]
fn legacy_schema_valued_unevaluated_properties_exempts_keyword_names() {
    let schema = Schema::new(
        r#"{"$schema":"https://json-schema.org/draft/2019-09/schema","unevaluatedProperties":{"type":"integer"}}"#,
    ).unwrap();
    assert!(schema.matches(r#"{"type":"string"}"#));
    assert!(schema.matches(r#"{"x":1}"#));
    assert!(!schema.matches(r#"{"x":"string"}"#));
    // The latest draft continues to validate the keyword-named value.
    let modern = Schema::new(r#"{"unevaluatedProperties":{"type":"integer"}}"#).unwrap();
    assert!(!modern.matches(r#"{"type":"string"}"#));
}

#[test]
fn legacy_true_coverage_keeps_reference_annotations() {
    let schema = Schema::new(
        r##"{"$schema":"https://json-schema.org/draft/2019-09/schema","$defs":{"v":{"required":["all"],"unevaluatedProperties":true}},"$ref":"#/$defs/v","unevaluatedProperties":false}"##,
    ).unwrap();
    assert!(schema.matches(r#"{"all":1,"extra":1}"#));
}

#[test]
fn previously_qualified_direct_guard_survives_unused_reference_definitions() {
    let schema = Schema::new(
        r#"{"$schema":"https://json-schema.org/draft/2019-09/schema","$defs":{"unused":{"$ref":"https://example.invalid/missing"}},"additionalProperties":{"type":"integer"},"unevaluatedProperties":false}"#,
    ).unwrap();
    assert!(!schema.matches(r#"{"x":1}"#));
    assert!(schema.matches(r#"{"type":1}"#));
}

#[test]
fn native_resource_parent_keeps_descendant_coverage_annotations() {
    let schema = Schema::new(
        r##"{"$schema":"https://json-schema.org/draft/2019-09/schema","$defs":{"v":{"unevaluatedProperties":{"type":"integer"}}},"$ref":"#/$defs/v","unevaluatedProperties":false}"##,
    ).unwrap();
    assert!(schema.matches(r#"{"type":1}"#));
    // Resource-scoped qualification now retains the producer's keyword-name
    // exemption through the referenced schema-valued remaining properties.
    assert!(schema.matches(r#"{"type":"s"}"#));
}

#[test]
fn static_pointer_and_anchor_coverage_preserve_pinned_refusals() {
    for reference in ["#/$defs/v", "#v"] {
        let definition = serde_json::json!({
            "$schema":"https://json-schema.org/draft/2019-09/schema",
            "$defs":{"v":{"$anchor":"v", "additionalProperties":{"type":"integer"}}},
            "$ref":reference,"unevaluatedProperties":false
        });
        let schema = Schema::new(&definition.to_string()).unwrap();
        assert!(!schema.matches(r#"{"x":1}"#));
        assert!(schema.matches(r#"{"type":1}"#));
        assert!(!schema.matches(r#"{"__foxlight_legacy_property_coverage_0":1}"#));
    }
}

#[test]
fn static_predicates_retain_the_enclosing_folder_resource() {
    let schema = Schema::new(r##"{
        "$schema":"https://json-schema.org/draft/2019-09/schema",
        "$id":"https://example.test/dir/root.json",
        "$defs":{"outer":{"$id":"rules/","$defs":{"integer":{"$id":"types/int.json","type":"integer"}},
            "allOf":[{"properties":{"x":{"$ref":"types/int.json"}}}]}},
        "$ref":"rules/","unevaluatedProperties":false
    }"##).unwrap();
    assert!(schema.matches(r#"{"x":1}"#));
    assert!(!schema.matches(r#"{"x":"s"}"#));
    assert!(!schema.matches(r#"{"x":1,"extra":1}"#));
}

#[test]
fn static_definitions_are_meta_validated_before_empty_instances() {
    assert!(
        Schema::new(
            r##"{
        "$schema":"https://json-schema.org/draft/2019-09/schema",
        "$defs":{"v":true},"$ref":"#/$defs/v","unevaluatedProperties":1
    }"##
        )
        .is_err()
    );
}

#[test]
fn nested_legacy_predicates_share_compilation_and_remain_isolated() {
    let mut body = serde_json::json!({"properties":{"x":true}});
    for _ in 0..32 {
        body = serde_json::json!({"allOf":[body], "unevaluatedProperties":false});
    }
    body.as_object_mut().unwrap().insert(
        "$schema".into(),
        serde_json::json!("https://json-schema.org/draft/2019-09/schema"),
    );
    let schema = Schema::new(&body.to_string()).unwrap();
    assert!(schema.matches(r#"{"x":1}"#));
    assert!(!schema.matches(r#"{"y":1}"#));
    let later = Schema::new(r#"{"unevaluatedProperties":{"type":"integer"}}"#).unwrap();
    assert!(!later.matches(r#"{"type":"s"}"#));
}

#[test]
fn installed_root_dialect_is_separate_from_generic_selection() {
    for draft in [
        "http://json-schema.org/draft-03/schema#",
        "http://json-schema.org/draft-04/schema#",
        "http://json-schema.org/draft-07/schema#",
        "https://example.invalid/unknown-draft",
    ] {
        let definition = serde_json::json!({"$schema":draft,"$defs":{"a":{"type":"object"}},"$ref":"#/$defs/a","type":"integer"}).to_string();
        let installed = Schema::installed(&definition).unwrap();
        assert!(!installed.matches("{}"));
        assert!(!installed.matches("1"));
    }
    let legacy = r##"{"$schema":"http://json-schema.org/draft-07/schema#","definitions":{"a":{"type":"object"}},"$ref":"#/definitions/a","type":"integer"}"##;
    assert!(Schema::new(legacy).unwrap().matches("{}"));
    assert!(!Schema::installed(legacy).unwrap().matches("{}"));
    assert!(Schema::installed(r#"{"$schema":1}"#).is_err());
}

#[test]
fn installed_metadata_has_its_own_bound_without_expanding_payloads() {
    let definition = serde_json::json!({"description":"x".repeat(70_000)}).to_string();
    assert!(Schema::new(&definition).is_err());
    let installed = Schema::installed(&definition).unwrap();
    assert!(installed.matches("{}"));
    assert!(installed.matches(&serde_json::json!({"x":"a".repeat(63_000)}).to_string()));
    assert!(!installed.matches(&serde_json::json!({"x":"a".repeat(66_000)}).to_string()));
    assert!(
        Schema::installed(&serde_json::json!({"description":"x".repeat(262_144)}).to_string())
            .is_err()
    );
}
