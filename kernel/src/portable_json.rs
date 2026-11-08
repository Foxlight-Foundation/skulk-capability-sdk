//! Bounded scalar JSON with one numeric meaning across language bindings.

use std::collections::BTreeMap;

use crate::{codec, portable::PortableError};

const SAFE_INTEGER: i64 = 9_007_199_254_740_991;

/// Normalize exact text before any schema compilation, hashing or signing.
pub(super) fn parse(source: &str, limit: usize) -> Result<codec::Value, PortableError> {
    if source.len() > limit {
        return Err(PortableError::Limit);
    }
    let value = codec::parse_bounded(source, 64).map_err(|error| {
        if matches!(error, codec::CodecError::Nesting) {
            PortableError::Limit
        } else {
            PortableError::Json
        }
    })?;
    let mut remaining = 16_384;
    normalize(value, 0, &mut remaining)
}

fn normalize(
    value: codec::Value,
    depth: usize,
    remaining: &mut usize,
) -> Result<codec::Value, PortableError> {
    if depth > 64 || *remaining == 0 {
        return Err(PortableError::Limit);
    }
    *remaining -= 1;
    Ok(match value {
        codec::Value::Integer(value) => {
            let number = value.parse::<i64>().map_err(|_| PortableError::Number)?;
            if !(-SAFE_INTEGER..=SAFE_INTEGER).contains(&number) {
                return Err(PortableError::Number);
            }
            codec::Value::Integer(number.to_string())
        }
        codec::Value::Float(value) => {
            if !value.is_finite() || value.abs() > SAFE_INTEGER as f64 {
                return Err(PortableError::Number);
            }
            // JSON has one numeric kind. Integral floats and signed zero must
            // not acquire different identities through JSON.parse in Node.
            if value.fract() == 0.0 {
                codec::Value::Integer((value as i64).to_string())
            } else {
                codec::Value::Float(value)
            }
        }
        codec::Value::String(points) => {
            scalar(&points)?;
            codec::Value::String(points)
        }
        codec::Value::Array(values) => codec::Value::Array(
            values
                .into_iter()
                .map(|value| normalize(value, depth + 1, remaining))
                .collect::<Result<_, _>>()?,
        ),
        codec::Value::Object(values) => {
            let mut result = BTreeMap::new();
            for (key, value) in values {
                scalar(&key)?;
                result.insert(key, normalize(value, depth + 1, remaining)?);
            }
            codec::Value::Object(result)
        }
        codec::Value::Null | codec::Value::Bool(_) => value,
        // Constructor-only Python values are not part of this wire contract.
        codec::Value::Tuple(_) | codec::Value::Bytes(_) | codec::Value::Model(_, _) => {
            return Err(PortableError::Json);
        }
    })
}

fn scalar(points: &[u32]) -> Result<(), PortableError> {
    if points.iter().all(|point| char::from_u32(*point).is_some()) {
        Ok(())
    } else {
        Err(PortableError::Unicode)
    }
}

/// Keep the same scalar ordering/ASCII escaping while dropping legacy kinds.
pub(super) fn encode(value: &codec::Value) -> Result<Vec<u8>, PortableError> {
    codec::encode(value).map_err(|_| PortableError::Json)
}

/// The native validator sees exactly the normalized bytes used for identity.
pub(super) fn serde(value: &codec::Value) -> Result<serde_json::Value, PortableError> {
    serde_json::from_slice(&encode(value)?).map_err(|_| PortableError::Json)
}
