//! Eager closed-vocabulary admission, with no legacy schema projection.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::Value;

use crate::portable::{PortableError, specification};

struct Node {
    children: Vec<String>,
    reference: Option<String>,
}

/// Reject unsupported syntax and reference expansion before native compilation.
pub(super) fn admit(definition: &Value) -> Result<(), PortableError> {
    let mut nodes = BTreeMap::new();
    let mut patterns = 0;
    visit(definition, "", &mut nodes, &mut patterns)?;
    let mut complete = BTreeMap::new();
    for path in nodes.keys() {
        expanded(path, &nodes, &mut BTreeSet::new(), &mut complete)?;
    }
    Ok(())
}

fn visit(
    schema: &Value,
    path: &str,
    nodes: &mut BTreeMap<String, Node>,
    patterns: &mut usize,
) -> Result<(), PortableError> {
    if nodes.len() >= 1024 {
        return Err(PortableError::Limit);
    }
    nodes.insert(
        path.into(),
        Node {
            children: Vec::new(),
            reference: None,
        },
    );
    if schema.is_boolean() {
        return Ok(());
    }
    let fields = schema.as_object().ok_or(PortableError::Schema)?;
    let allowed = specification()["schema_keywords"]
        .as_array()
        .ok_or(PortableError::Contract)?;
    let mut children = Vec::new();
    let mut reference = None;
    for (key, value) in fields {
        if !allowed.iter().any(|item| item.as_str() == Some(key)) {
            return Err(PortableError::Keyword);
        }
        match key.as_str() {
            "$schema" => {
                if !path.is_empty() || value.as_str() != Some(super::portable::DIALECT) {
                    return Err(PortableError::Dialect);
                }
            }
            "$defs" | "properties" | "patternProperties" | "dependentSchemas" => {
                if key == "$defs" && !path.is_empty() {
                    return Err(PortableError::Reference);
                }
                for (name, child) in value.as_object().ok_or(PortableError::Schema)? {
                    if key == "$defs" && !definition_name(name) {
                        return Err(PortableError::Reference);
                    }
                    if key == "patternProperties" {
                        pattern(name, patterns)?;
                    }
                    let child_path = format!("{path}/{key}/{}", escape(name));
                    visit(child, &child_path, nodes, patterns)?;
                    children.push(child_path);
                }
            }
            "allOf" | "anyOf" | "oneOf" | "prefixItems" => {
                for (index, child) in value
                    .as_array()
                    .ok_or(PortableError::Schema)?
                    .iter()
                    .enumerate()
                {
                    let child_path = format!("{path}/{key}/{index}");
                    visit(child, &child_path, nodes, patterns)?;
                    children.push(child_path);
                }
            }
            "items"
            | "additionalProperties"
            | "propertyNames"
            | "contains"
            | "not"
            | "if"
            | "then"
            | "else" => {
                let child_path = format!("{path}/{key}");
                visit(value, &child_path, nodes, patterns)?;
                children.push(child_path);
            }
            "$ref" => {
                let value = value.as_str().ok_or(PortableError::Reference)?;
                let name = value
                    .strip_prefix("#/$defs/")
                    .filter(|name| definition_name(name))
                    .ok_or(PortableError::Reference)?;
                reference = Some(format!("/$defs/{name}"));
            }
            "pattern" => pattern(value.as_str().ok_or(PortableError::Pattern)?, patterns)?,
            _ => {}
        }
    }
    nodes.insert(
        path.into(),
        Node {
            children,
            reference,
        },
    );
    Ok(())
}

fn definition_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.'))
}

fn escape(value: &str) -> String {
    value.replace('~', "~0").replace('/', "~1")
}

fn pattern(source: &str, count: &mut usize) -> Result<(), PortableError> {
    *count += 1;
    if *count > 64 || source.len() > 1024 {
        return Err(PortableError::Limit);
    }
    // A small common syntax avoids engine-specific shorthands, flag modes,
    // backreferences, look-around and character-class set operations.
    let mut chars = source.chars().peekable();
    while let Some(character) = chars.next() {
        if character == '\\' {
            let next = chars.next().ok_or(PortableError::Pattern)?;
            if !"\\.^$|?*+()[]{}-/".contains(next) {
                return Err(PortableError::Pattern);
            }
        } else if character == '(' && chars.peek() == Some(&'?') {
            if chars.next() != Some('?') || chars.next() != Some(':') {
                return Err(PortableError::Pattern);
            }
        } else if matches!(character, '&' | '-' | '~') && chars.peek() == Some(&character) {
            return Err(PortableError::Pattern);
        }
    }
    regex::RegexBuilder::new(source)
        .size_limit(1_048_576)
        .dfa_size_limit(1_048_576)
        .build()
        .map_err(|_| PortableError::Pattern)?;
    Ok(())
}

fn expanded(
    path: &str,
    nodes: &BTreeMap<String, Node>,
    active: &mut BTreeSet<String>,
    complete: &mut BTreeMap<String, (usize, usize)>,
) -> Result<(usize, usize), PortableError> {
    if active.len() >= 64 {
        return Err(PortableError::Limit);
    }
    if let Some((cost, height)) = complete.get(path) {
        if active.len() + height > 64 {
            return Err(PortableError::Limit);
        }
        return Ok((*cost, *height));
    }
    if !active.insert(path.into()) {
        return Err(PortableError::Cycle);
    }
    let node = nodes.get(path).ok_or(PortableError::Reference)?;
    let mut cost = 1usize;
    let mut height = 1usize;
    for child in node.children.iter().chain(node.reference.iter()) {
        let (child_cost, child_height) = expanded(child, nodes, active, complete)?;
        height = height.max(child_height + 1);
        cost = cost
            .checked_add(child_cost)
            .filter(|cost| *cost <= 8192)
            .ok_or(PortableError::Limit)?;
    }
    active.remove(path);
    complete.insert(path.into(), (cost, height));
    Ok((cost, height))
}
