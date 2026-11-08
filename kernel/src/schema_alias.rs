//! Retain original JSON Pointer targets when regex dictionary keys are lowered.

use std::collections::BTreeMap;

use fluent_uri::{Uri, UriRef};
use serde_json::Value;

use crate::{schema_pattern, schema_unicode::Projection};

type Aliases = BTreeMap<(String, String), String>;

#[derive(Clone)]
struct Scope {
    uri: String,
    old_root: String,
    new_root: String,
    draft4: bool,
}

impl Scope {
    fn root() -> Self {
        Self {
            uri: "json-schema:///".into(),
            old_root: String::new(),
            new_root: String::new(),
            draft4: false,
        }
    }
    fn at(&self, node: &Value, old: &str, new: &str) -> Self {
        let mut scope = self.clone();
        if let Some(uri) = node.get("$schema").and_then(Value::as_str) {
            scope.draft4 = uri.contains("draft-04");
        }
        if let Some(id) = node
            .get(if scope.draft4 { "id" } else { "$id" })
            .and_then(Value::as_str)
            && let Some(uri) = resolve(id, &scope.uri)
        {
            let uri = uri.split('#').next().unwrap_or(&uri).to_owned();
            if uri != scope.uri {
                scope.uri = uri;
                scope.old_root = old.into();
                scope.new_root = new.into();
            }
        }
        scope
    }
}

/// Walk only real schema children when rewriting references. Const, enum and
/// annotation strings must remain data even when they resemble a reference.
pub(super) fn rewrite(original: &Value, prepared: &mut Value, projection: &Projection) {
    let mut aliases = BTreeMap::new();
    collect(
        original,
        prepared,
        &Scope::root(),
        "",
        "",
        projection,
        &mut aliases,
    );
    references(prepared, &Scope::root(), &aliases);
}

fn token(key: &str) -> String {
    key.replace('~', "~0").replace('/', "~1")
}

fn collect(
    old: &Value,
    new: &Value,
    scope: &Scope,
    old_path: &str,
    new_path: &str,
    projection: &Projection,
    aliases: &mut Aliases,
) {
    let scope = scope.at(old, old_path, new_path);
    match (old, new) {
        (Value::Object(old), Value::Object(new)) => {
            for (key, old_value) in old {
                let Some(new_value) = new.get(key) else {
                    continue;
                };
                let old_path = format!("{old_path}/{}", token(key));
                let new_path = format!("{new_path}/{}", token(key));
                if key == "patternProperties" {
                    if let (Some(old), Some(new)) = (old_value.as_object(), new_value.as_object()) {
                        for (name, value) in old {
                            let renamed = schema_pattern::pattern(name, projection)
                                .ok()
                                .filter(|name| new.contains_key(name))
                                .unwrap_or_else(|| name.clone());
                            let old_path = format!("{old_path}/{}", token(name));
                            let new_path = format!("{new_path}/{}", token(&renamed));
                            if name != &renamed {
                                aliases.insert(
                                    (scope.uri.clone(), old_path[scope.old_root.len()..].into()),
                                    new_path[scope.new_root.len()..].into(),
                                );
                            }
                            if let Some(new_value) = new.get(&renamed) {
                                collect(
                                    value, new_value, &scope, &old_path, &new_path, projection,
                                    aliases,
                                );
                            }
                        }
                    }
                } else {
                    collect(
                        old_value, new_value, &scope, &old_path, &new_path, projection, aliases,
                    );
                }
            }
        }
        (Value::Array(old), Value::Array(new)) => {
            for (index, (old, new)) in old.iter().zip(new).enumerate() {
                collect(
                    old,
                    new,
                    &scope,
                    &format!("{old_path}/{index}"),
                    &format!("{new_path}/{index}"),
                    projection,
                    aliases,
                );
            }
        }
        _ => (),
    }
}

fn references(schema: &mut Value, scope: &Scope, aliases: &Aliases) {
    let scope = scope.at(schema, "", "");
    let Some(object) = schema.as_object_mut() else {
        return;
    };
    for keyword in ["$ref", "$dynamicRef", "$recursiveRef"] {
        if let Some(value) = object.get_mut(keyword)
            && let Some(source) = value.as_str()
            && let Some((base, fragment)) = source.split_once('#')
            && let Some(fragment) = decode(fragment)
            && let Some(uri) = resolve(base, &scope.uri)
        {
            let uri = uri.split('#').next().unwrap_or(&uri);
            if let Some(((.., old), new)) = aliases
                .iter()
                .filter(|((resource, old), _)| {
                    resource == uri
                        && (fragment == *old || fragment.starts_with(&format!("{old}/")))
                })
                .max_by_key(|((_, old), _)| old.len())
            {
                let target = format!("{new}{}", &fragment[old.len()..]);
                *value = Value::String(format!("{base}#{}", encode(&target)));
            }
        }
    }
    for name in [
        "properties",
        "patternProperties",
        "$defs",
        "definitions",
        "dependentSchemas",
    ] {
        if let Some(children) = object.get_mut(name).and_then(Value::as_object_mut) {
            for child in children.values_mut() {
                references(child, &scope, aliases);
            }
        }
    }
    for name in ["allOf", "anyOf", "oneOf", "prefixItems"] {
        if let Some(children) = object.get_mut(name).and_then(Value::as_array_mut) {
            for child in children {
                references(child, &scope, aliases);
            }
        }
    }
    for name in [
        "additionalProperties",
        "unevaluatedProperties",
        "additionalItems",
        "unevaluatedItems",
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
                    references(child, &scope, aliases);
                }
            } else {
                references(child, &scope, aliases);
            }
        }
    }
    if let Some(children) = object
        .get_mut("dependencies")
        .and_then(Value::as_object_mut)
    {
        for child in children.values_mut() {
            references(child, &scope, aliases);
        }
    }
}

fn resolve(reference: &str, base: &str) -> Option<String> {
    let base = Uri::parse(base).ok()?;
    Some(
        UriRef::parse(reference)
            .ok()?
            .resolve_against(&base)
            .ok()?
            .as_str()
            .to_owned(),
    )
}

fn decode(source: &str) -> Option<String> {
    let bytes = source.as_bytes();
    let mut output = Vec::new();
    let mut at = 0;
    while at < bytes.len() {
        if bytes[at] == b'%' {
            let text = std::str::from_utf8(bytes.get(at + 1..at + 3)?).ok()?;
            output.push(u8::from_str_radix(text, 16).ok()?);
            at += 3;
        } else {
            output.push(bytes[at]);
            at += 1;
        }
    }
    String::from_utf8(output).ok()
}

fn encode(source: &str) -> String {
    source
        .bytes()
        .map(|byte| {
            if byte.is_ascii_alphanumeric() || b"-._~/".contains(&byte) {
                char::from(byte).to_string()
            } else {
                format!("%{byte:02X}")
            }
        })
        .collect()
}
