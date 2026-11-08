//! Lazy, permanently offline reference refusal matching the Python producer.

use std::{cell::Cell, collections::BTreeSet};

use jsonschema::{Keyword, Retrieve, Uri, ValidationError, ValidationOptions, Validator};
use serde_json::Value;

thread_local! {
    static REFUSED: Cell<bool> = const { Cell::new(false) };
}

/// Compile unused references without retrieval; evaluating one still refuses
/// the whole validation even underneath `not` or another negating applicator.
pub(super) fn options(schema: &Value) -> ValidationOptions<'static> {
    let (keyword, resource) = unavailable_resource(schema);
    jsonschema::options()
        .with_retriever(Unavailable { resource })
        .with_keyword(keyword, |_, _, _| Ok(Box::new(Refusal)))
}

/// Populate only inert placeholders; actual use is refused by the graph owner.
pub(super) fn registry(
    schema: &Value,
    base: &str,
) -> Result<referencing::Registry<'static>, crate::schema::SchemaError> {
    let draft = referencing::Draft::Draft202012.detect(schema);
    let (_, resource) = unavailable_resource(schema);
    referencing::Registry::new()
        .draft(draft)
        .retriever(Unavailable { resource })
        .add(base, draft.create_resource(schema.clone()))
        .and_then(|builder| builder.prepare())
        .map_err(|_| crate::schema::SchemaError)
}

fn unavailable_resource(schema: &Value) -> (String, Value) {
    let mut keys = BTreeSet::new();
    let mut references = Vec::new();
    collect(schema, &mut keys, &mut references);
    let mut index = 0;
    let keyword = loop {
        let candidate = format!("__foxlight_unavailable_reference_{index}");
        if !keys.contains(&candidate) {
            break candidate;
        }
        index += 1;
    };
    let mut resource = serde_json::json!({keyword.clone(): true});
    for reference in references {
        if let Some((_, fragment)) = reference.split_once('#') {
            if let Some(pointer) = fragment.strip_prefix('/') {
                let components: Vec<String> = pointer
                    .split('/')
                    .map(|part| part.replace("~1", "/").replace("~0", "~"))
                    .collect();
                insert_pointer(&mut resource, &components, &keyword);
            } else if !fragment.is_empty() {
                let name = format!("anchor_{index}");
                index += 1;
                resource
                    .as_object_mut()
                    .expect("resource object")
                    .entry("$defs")
                    .or_insert_with(|| serde_json::json!({}))
                    .as_object_mut()
                    .expect("definitions object")
                    .insert(
                        name,
                        serde_json::json!({"$anchor":fragment, keyword.clone(): true}),
                    );
            }
        }
    }
    (keyword, resource)
}

/// Each validation has isolated refusal state, including concurrent workers.
pub(super) fn matches(validator: &Validator, instance: &Value) -> bool {
    let (valid, unavailable) = evaluate(validator, instance);
    valid && !unavailable
}

pub(super) fn unavailable(validator: &Validator, instance: &Value) -> bool {
    evaluate(validator, instance).1
}

fn evaluate(validator: &Validator, instance: &Value) -> (bool, bool) {
    let _evaluation = crate::schema_legacy::Evaluation::enter();
    let _scoped = crate::schema_scoped::Evaluation::enter();
    let _graph = crate::schema_graph::Evaluation::enter();
    REFUSED.with(|refused| {
        let previous = refused.replace(false);
        let valid = validator.is_valid(instance);
        let unavailable = refused.replace(previous);
        (valid, unavailable)
    })
}

/// Coverage resolution errors must refuse the whole invocation, including
/// evaluation beneath a negating applicator.
pub(super) fn refuse() {
    REFUSED.with(|refused| refused.set(true));
}

struct Unavailable {
    resource: Value,
}

impl Retrieve for Unavailable {
    fn retrieve(
        &self,
        _uri: &Uri<String>,
    ) -> Result<Value, Box<dyn std::error::Error + Send + Sync>> {
        // Never open a socket, path or subprocess, regardless of URI scheme.
        Ok(self.resource.clone())
    }
}

struct Refusal;

impl<'i> Keyword<'i> for Refusal {
    fn validate(&self, _instance: &'i Value) -> Result<(), ValidationError<'i>> {
        REFUSED.with(|refused| refused.set(true));
        Err(ValidationError::custom("unavailable schema reference"))
    }

    fn is_valid(&self, _instance: &'i Value) -> bool {
        REFUSED.with(|refused| refused.set(true));
        false
    }
}

fn collect(schema: &Value, keys: &mut BTreeSet<String>, references: &mut Vec<String>) {
    match schema {
        Value::Object(object) => {
            for (key, value) in object {
                keys.insert(key.clone());
                if matches!(key.as_str(), "$ref" | "$dynamicRef" | "$recursiveRef")
                    && let Some(reference) = value.as_str()
                {
                    references.push(reference.into());
                }
                collect(value, keys, references);
            }
        }
        Value::Array(values) => {
            for value in values {
                collect(value, keys, references);
            }
        }
        _ => (),
    }
}

fn insert_pointer(resource: &mut Value, components: &[String], keyword: &str) {
    let mut node = resource;
    for component in components {
        if !node.is_object() {
            *node = serde_json::json!({});
        }
        node = node
            .as_object_mut()
            .expect("pointer object")
            .entry(component.clone())
            .or_insert_with(|| serde_json::json!({}));
    }
    if !node.is_object() {
        *node = serde_json::json!({});
    }
    node.as_object_mut()
        .expect("pointer target")
        .insert(keyword.into(), Value::Bool(true));
}
