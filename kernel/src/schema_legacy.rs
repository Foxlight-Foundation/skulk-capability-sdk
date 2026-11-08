//! Preserve pinned Draft 2019 coverage for reference-free schema composition.

use std::{
    cell::RefCell,
    collections::{BTreeSet, HashMap},
};

use jsonschema::{Keyword, ValidationError, Validator};
use serde_json::Value;

use crate::{schema::SchemaError, schema_pattern, schema_unicode::Projection};

type Results = HashMap<(String, usize), bool>;

thread_local! {
    // An object may be tested by both its ordinary applicator and coverage
    // helper. Cache only pure reference-free predicates for one validation.
    static RESULTS: RefCell<Option<Results>> = const { RefCell::new(None) };
}

pub(super) struct Evaluation(Option<Results>);

impl Evaluation {
    pub(super) fn enter() -> Self {
        Self(RESULTS.with(|cache| cache.replace(Some(HashMap::new()))))
    }
}

impl Drop for Evaluation {
    fn drop(&mut self) {
        RESULTS.with(|cache| cache.replace(self.0.take()));
    }
}

struct Predicate {
    key: String,
    validator: Validator,
}

impl Predicate {
    fn new(value: &Value, projection: &Projection) -> Result<Self, SchemaError> {
        Ok(Self {
            key: serde_json::to_string(value).map_err(|_| SchemaError)?,
            validator: crate::schema::compile_fragment(value, projection, true)?,
        })
    }

    fn is_valid(&self, instance: &Value) -> bool {
        let key = (self.key.clone(), std::ptr::from_ref(instance) as usize);
        if let Some(valid) = RESULTS.with(|cache| {
            cache
                .borrow()
                .as_ref()
                .and_then(|cache| cache.get(&key).copied())
        }) {
            return valid;
        }
        let valid = self.validator.is_valid(instance);
        RESULTS.with(|cache| {
            if let Some(cache) = cache.borrow_mut().as_mut() {
                cache.insert(key, valid);
            }
        });
        valid
    }
}

/// The pinned helper counts keyword names in schema-valued additional and
/// unevaluated properties. Preserve that behavior through reference-free
/// applicators; resource and recursive reference graphs remain a separate gate.
pub(super) fn prepare(schema: &mut Value, inherited: bool, keyword: &str) {
    let lower = reference_free(schema, true);
    prepare_inner(schema, inherited, keyword, lower);
}

fn prepare_inner(schema: &mut Value, inherited: bool, keyword: &str, lower: bool) {
    let Some(object) = schema.as_object_mut() else {
        return;
    };
    let draft = object
        .get("$schema")
        .and_then(Value::as_str)
        .map_or(inherited, |uri| uri.contains("/draft/2019-09/"));
    let composed = lower && reference_free(&Value::Object(object.clone()), true);
    // Retain the previously qualified direct false-coverage guard even when
    // unused definitions or a resource identity prevent fragment lowering.
    let direct = object.get("unevaluatedProperties") == Some(&Value::Bool(false))
        && object
            .get("additionalProperties")
            .is_some_and(Value::is_object)
        && [
            "$ref",
            "$recursiveRef",
            "$dynamicRef",
            "allOf",
            "anyOf",
            "oneOf",
            "if",
            "dependentSchemas",
        ]
        .iter()
        .all(|key| !object.contains_key(*key));
    let coverage = (draft
        && object.contains_key("unevaluatedProperties")
        // `true` already matches Python and must retain native annotations for
        // enclosing unevaluated-property validators through reference paths.
        && object.get("unevaluatedProperties") != Some(&Value::Bool(true))
        && (composed || direct))
        .then(|| Value::Object(object.clone()));
    for name in [
        "properties",
        "patternProperties",
        "$defs",
        "definitions",
        "dependentSchemas",
        "dependencies",
    ] {
        if let Some(children) = object.get_mut(name).and_then(Value::as_object_mut) {
            for child in children.values_mut() {
                prepare_inner(child, draft, keyword, composed);
            }
        }
    }
    for name in ["allOf", "anyOf", "oneOf", "prefixItems"] {
        if let Some(children) = object.get_mut(name).and_then(Value::as_array_mut) {
            for child in children {
                prepare_inner(child, draft, keyword, composed);
            }
        }
    }
    for name in [
        "additionalProperties",
        "unevaluatedProperties",
        "contains",
        "propertyNames",
        "not",
        "if",
        "then",
        "else",
        "items",
    ] {
        if let Some(child) = object.get_mut(name) {
            if let Some(children) = child.as_array_mut() {
                for child in children {
                    prepare_inner(child, draft, keyword, composed);
                }
            } else {
                prepare_inner(child, draft, keyword, composed);
            }
        }
    }
    if let Some(coverage) = coverage {
        if composed {
            object.remove("unevaluatedProperties");
        }
        object.insert(keyword.into(), coverage);
    }
}

// Reference evaluation and cross-resource draft changes need their own
// qualification. Never flatten them into an unrelated standalone predicate.
fn reference_free(value: &Value, root: bool) -> bool {
    let Some(object) = value.as_object() else {
        return true;
    };
    if ["$ref", "$recursiveRef", "$dynamicRef", "$id", "id"]
        .iter()
        .any(|key| object.contains_key(*key))
        || (!root && object.contains_key("$schema"))
    {
        return false;
    }
    for key in [
        "properties",
        "patternProperties",
        "$defs",
        "definitions",
        "dependentSchemas",
        "dependencies",
    ] {
        if let Some(children) = object.get(key).and_then(Value::as_object)
            && children.values().any(|child| !reference_free(child, false))
        {
            return false;
        }
    }
    for key in ["allOf", "anyOf", "oneOf", "prefixItems"] {
        if let Some(children) = object.get(key).and_then(Value::as_array)
            && children.iter().any(|child| !reference_free(child, false))
        {
            return false;
        }
    }
    for key in [
        "additionalProperties",
        "unevaluatedProperties",
        "contains",
        "propertyNames",
        "not",
        "if",
        "then",
        "else",
        "items",
        "additionalItems",
        "unevaluatedItems",
    ] {
        if let Some(child) = object.get(key) {
            if let Some(children) = child.as_array() {
                if children.iter().any(|child| !reference_free(child, false)) {
                    return false;
                }
            } else if !reference_free(child, false) {
                return false;
            }
        }
    }
    true
}

pub(super) fn keyword_name(schema: &Value) -> String {
    fn present(value: &Value, candidate: &str) -> bool {
        match value {
            Value::String(value) => value.contains(candidate),
            Value::Object(value) => value
                .iter()
                .any(|(key, value)| key.contains(candidate) || present(value, candidate)),
            Value::Array(value) => value.iter().any(|value| present(value, candidate)),
            _ => false,
        }
    }
    for index in 0.. {
        let candidate = format!("__foxlight_legacy_property_coverage_{index}");
        if !present(schema, &candidate) {
            return candidate;
        }
    }
    unreachable!("finite schema always leaves an unused keyword")
}

pub(super) struct Coverage {
    graph: Graph,
    remaining: Validator,
}

/// Names are gathered separately from validation: a valid conditional branch
/// contributes coverage, whereas a triggered dependent schema contributes
/// coverage regardless of whether that dependent schema accepts the object.
struct Graph {
    names: BTreeSet<String>,
    all: bool,
    patterns: Vec<Validator>,
    dependencies: Vec<(String, Graph)>,
    branches: Vec<(Predicate, Graph)>,
    condition: Option<Condition>,
}

struct Condition {
    predicate: Predicate,
    tested: Box<Graph>,
    then: Option<Box<Graph>>,
    otherwise: Option<Box<Graph>>,
}

impl Coverage {
    pub(super) fn new(value: &Value, projection: &Projection) -> Result<Self, SchemaError> {
        Ok(Self {
            graph: Graph::new(value, projection)?,
            remaining: crate::schema::compile_fragment(
                value.get("unevaluatedProperties").ok_or(SchemaError)?,
                projection,
                true,
            )?,
        })
    }
}

impl Graph {
    fn new(value: &Value, projection: &Projection) -> Result<Self, SchemaError> {
        let mut graph = Self {
            names: BTreeSet::new(),
            all: false,
            patterns: Vec::new(),
            dependencies: Vec::new(),
            branches: Vec::new(),
            condition: None,
        };
        let Some(object) = value.as_object() else {
            return Ok(graph);
        };
        for key in [
            "properties",
            "additionalProperties",
            "unevaluatedProperties",
        ] {
            if let Some(value) = object.get(key) {
                if value == &Value::Bool(true) {
                    graph.all = true;
                } else if let Some(fields) = value.as_object() {
                    graph.names.extend(fields.keys().cloned());
                }
            }
        }
        if let Some(patterns) = object.get("patternProperties").and_then(Value::as_object) {
            for pattern in patterns.keys() {
                let mut schema = serde_json::json!({"pattern":pattern});
                schema_pattern::prepare(&mut schema, projection)?;
                graph.patterns.push(
                    jsonschema::options()
                        .offline()
                        .should_validate_formats(false)
                        .with_pattern_options(
                            jsonschema::PatternOptions::fancy_regex().backtrack_limit(100_000),
                        )
                        .build(&schema)
                        .map_err(|_| SchemaError)?,
                );
            }
        }
        if let Some(dependencies) = object.get("dependentSchemas").and_then(Value::as_object) {
            for (key, child) in dependencies {
                graph
                    .dependencies
                    .push((key.clone(), Self::new(child, projection)?));
            }
        }
        for key in ["allOf", "anyOf", "oneOf"] {
            if let Some(children) = object.get(key).and_then(Value::as_array) {
                for child in children {
                    graph.branches.push((
                        Predicate::new(child, projection)?,
                        Self::new(child, projection)?,
                    ));
                }
            }
        }
        if let Some(tested) = object.get("if") {
            graph.condition = Some(Condition {
                predicate: Predicate::new(tested, projection)?,
                tested: Box::new(Self::new(tested, projection)?),
                then: object
                    .get("then")
                    .map(|child| Self::new(child, projection).map(Box::new))
                    .transpose()?,
                otherwise: object
                    .get("else")
                    .map(|child| Self::new(child, projection).map(Box::new))
                    .transpose()?,
            });
        }
        Ok(graph)
    }

    fn collect(&self, instance: &Value, names: &mut BTreeSet<String>) {
        let Some(object) = instance.as_object() else {
            return;
        };
        names.extend(
            self.names
                .iter()
                .filter(|key| object.contains_key(*key))
                .cloned(),
        );
        if self.all {
            names.extend(object.keys().cloned());
        }
        for key in object.keys() {
            if self
                .patterns
                .iter()
                .any(|pattern| pattern.is_valid(&Value::String(key.clone())))
            {
                names.insert(key.clone());
            }
        }
        for (key, graph) in &self.dependencies {
            if object.contains_key(key) {
                graph.collect(instance, names);
            }
        }
        for (predicate, graph) in &self.branches {
            if predicate.is_valid(instance) {
                graph.collect(instance, names);
            }
        }
        if let Some(condition) = &self.condition {
            if condition.predicate.is_valid(instance) {
                condition.tested.collect(instance, names);
                if let Some(graph) = &condition.then {
                    graph.collect(instance, names);
                }
            } else if let Some(graph) = &condition.otherwise {
                graph.collect(instance, names);
            }
        }
    }
}

impl<'i> Keyword<'i> for Coverage {
    fn validate(&self, instance: &'i Value) -> Result<(), ValidationError<'i>> {
        if self.is_valid(instance) {
            Ok(())
        } else {
            Err(ValidationError::custom("unevaluated property refused"))
        }
    }
    fn is_valid(&self, instance: &'i Value) -> bool {
        let Some(object) = instance.as_object() else {
            return true;
        };
        let mut names = BTreeSet::new();
        self.graph.collect(instance, &mut names);
        object
            .iter()
            .all(|(key, value)| names.contains(key) || self.remaining.is_valid(value))
    }
}
