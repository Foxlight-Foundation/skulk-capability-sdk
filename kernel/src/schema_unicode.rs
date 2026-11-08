//! Collision-free temporary Unicode projection for a scalar-string validator.
//!
//! Python JSON admits surrogate code points; Rust strings do not. Project them
//! into unused private-use points for this schema/instance pair only. These
//! strings never leave validation and cannot enter canonical bytes or journals.

use std::collections::{BTreeMap, BTreeSet};

use crate::{codec::Value, schema::SchemaError};

#[derive(Clone)]
pub(super) struct Projection {
    forward: BTreeMap<u32, char>,
    reverse: BTreeMap<char, u32>,
}

impl Projection {
    pub(super) fn new(schema: &Value, instance: Option<&Value>) -> Result<Self, SchemaError> {
        if !has_surrogate(schema) && !instance.is_some_and(has_surrogate) {
            return Ok(Self {
                forward: BTreeMap::new(),
                reverse: BTreeMap::new(),
            });
        }
        let mut used = BTreeSet::new();
        collect(schema, &mut used);
        if let Some(instance) = instance {
            collect(instance, &mut used);
        }
        let mut available = (0xf0000..=0xffffd)
            .chain(0x100000..=0x10fffd)
            .filter(|point| !used.contains(point));
        let mut forward = BTreeMap::new();
        let mut reverse = BTreeMap::new();
        for surrogate in 0xd800..=0xdfff {
            let point = available.next().ok_or(SchemaError)?;
            let character = char::from_u32(point).ok_or(SchemaError)?;
            forward.insert(surrogate, character);
            reverse.insert(character, surrogate);
        }
        Ok(Self { forward, reverse })
    }

    pub(super) fn point(&self, point: u32) -> Result<char, SchemaError> {
        self.forward
            .get(&point)
            .copied()
            .or_else(|| char::from_u32(point))
            .ok_or(SchemaError)
    }

    pub(super) fn original(&self, point: char) -> u32 {
        self.reverse
            .get(&point)
            .copied()
            .unwrap_or(u32::from(point))
    }

    pub(super) fn text(&self, points: &[u32]) -> Result<String, SchemaError> {
        points.iter().map(|point| self.point(*point)).collect()
    }

    pub(super) fn range(&self, start: u32, end: u32) -> Result<String, SchemaError> {
        if start > end || end > 0x10ffff {
            return Err(SchemaError);
        }
        let mut parts = Vec::new();
        // Numeric ranges retain their original ordering; mapping a surrogate
        // endpoint must never turn an original range into a private-use range.
        for (low, high) in [(0, 0xd7ff), (0xe000, 0x10ffff)] {
            let low = start.max(low);
            let high = end.min(high);
            if low <= high {
                let mut body = format!("[{}-{}]", literal(low, true)?, literal(high, true)?);
                if self
                    .reverse
                    .keys()
                    .any(|point| (low..=high).contains(&u32::from(*point)))
                {
                    let excluded: String = self
                        .reverse
                        .keys()
                        .filter(|point| (low..=high).contains(&u32::from(**point)))
                        .collect();
                    body = format!("(?![{excluded}]){body}");
                }
                parts.push(body);
            }
        }
        let projected: String = self
            .forward
            .range(start..=end)
            .map(|(_, character)| character)
            .collect();
        if !projected.is_empty() {
            parts.push(format!("[{projected}]"));
        }
        if parts.is_empty() {
            return Err(SchemaError);
        }
        Ok(format!("(?:{})", parts.join("|")))
    }
}

pub(super) fn literal(point: u32, in_class: bool) -> Result<String, SchemaError> {
    let character = char::from_u32(point).ok_or(SchemaError)?;
    // Generated character classes remain literal under either verbose mode.
    // Native regex engines may otherwise ignore spaces or begin a comment
    // inside a class, where Python preserves both characters.
    if character.is_whitespace() || character == '#' {
        return Ok(format!(r"\x{{{point:x}}}"));
    }
    let special = if in_class {
        "\\-[]^"
    } else {
        "\\.^$*+?{}[]()|"
    };
    Ok(if special.contains(character) {
        format!("\\{character}")
    } else {
        character.to_string()
    })
}

pub(super) fn has_surrogate(value: &Value) -> bool {
    match value {
        Value::String(points) => {
            points.iter().any(|point| (0xd800..=0xdfff).contains(point))
                || points
                    .windows(6)
                    .any(|points| escaped_surrogate(points, 'u'))
                || points
                    .windows(10)
                    .any(|points| escaped_surrogate(points, 'U'))
        }
        Value::Object(values) => values.iter().any(|(key, value)| {
            key.iter().any(|point| (0xd800..=0xdfff).contains(point)) || has_surrogate(value)
        }),
        Value::Array(values) => values.iter().any(has_surrogate),
        _ => false,
    }
}

fn escaped_surrogate(points: &[u32], prefix: char) -> bool {
    if points[0] != u32::from('\\') || points[1] != u32::from(prefix) {
        return false;
    }
    let digits: String = points[2..]
        .iter()
        .filter_map(|point| char::from_u32(*point))
        .collect();
    u32::from_str_radix(&digits, 16).is_ok_and(|point| (0xd800..=0xdfff).contains(&point))
}

fn collect(value: &Value, used: &mut BTreeSet<u32>) {
    fn string(points: &[u32], used: &mut BTreeSet<u32>) {
        used.extend(points.iter().copied());
        // Reserve scalar literals written as regex Unicode escapes as well.
        for index in 0..points.len().saturating_sub(1) {
            if points[index] != u32::from('\\') {
                continue;
            }
            let size = match points[index + 1] {
                0x75 => 4,
                0x55 => 8,
                _ => continue,
            };
            if let Some(digits) = points.get(index + 2..index + 2 + size) {
                let text: String = digits
                    .iter()
                    .filter_map(|point| char::from_u32(*point))
                    .collect();
                if let Ok(point) = u32::from_str_radix(&text, 16) {
                    used.insert(point);
                }
            }
        }
    }
    match value {
        Value::String(points) => string(points, used),
        Value::Object(values) => {
            for (key, value) in values {
                string(key, used);
                collect(value, used);
            }
        }
        Value::Array(values) => {
            for value in values {
                collect(value, used);
            }
        }
        _ => (),
    }
}
