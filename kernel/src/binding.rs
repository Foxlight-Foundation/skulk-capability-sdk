//! Shared binding boundary. Language shims convert values; this module owns
//! canonical bytes, journal dispatch and safe errors for both native addons.

use std::collections::BTreeMap;
use std::path::Path;

use crate::codec::{self, CodecError, Value};
use crate::journal::{Bounds, Journal, JournalError, Reservation, Status, Update};

/// Binding-owned tagged values preserve kinds that JavaScript cannot infer.
/// The transport is not canonical document JSON and is never signed or stored.
pub fn tagged(input: &str) -> Result<Value, CodecError> {
    decode_tag(&codec::parse(input)?, 0, false)
}

fn decode_tag(value: &Value, depth: usize, constructor: bool) -> Result<Value, CodecError> {
    if depth > 256 {
        return Err(CodecError::Nesting);
    }
    let Value::Array(items) = value else {
        return Err(CodecError::Syntax);
    };
    let Some(Value::String(kind)) = items.first() else {
        return Err(CodecError::Syntax);
    };
    let kind = text(kind)?;
    let payload = items.get(1);
    match (kind.as_str(), items.len(), payload) {
        ("null", 1, _) => Ok(Value::Null),
        ("bool", 2, Some(Value::Bool(value))) => Ok(Value::Bool(*value)),
        ("integer", 2, Some(Value::String(value))) => {
            let result = Value::Integer(text(value)?);
            codec::encode(&result)?;
            Ok(result)
        }
        // Hex bits preserve negative zero and avoid another JSON-number round trip.
        ("float", 2, Some(Value::String(value))) => {
            let value = text(value)?;
            if value.len() != 16 {
                return Err(CodecError::Syntax);
            }
            let value =
                f64::from_bits(u64::from_str_radix(&value, 16).map_err(|_| CodecError::Syntax)?);
            if !constructor && !value.is_finite() {
                return Err(CodecError::NonFinite);
            }
            Ok(Value::Float(value))
        }
        ("string", 2, Some(value)) => Ok(Value::String(point_array(value)?)),
        ("bytes", 2, Some(Value::String(hex))) if constructor => {
            let hex = text(hex)?;
            if hex.len() % 2 != 0 {
                return Err(CodecError::Syntax);
            }
            Ok(Value::Bytes(
                hex.as_bytes()
                    .chunks_exact(2)
                    .map(|pair| {
                        let pair = std::str::from_utf8(pair).map_err(|_| CodecError::Syntax)?;
                        u8::from_str_radix(pair, 16).map_err(|_| CodecError::Syntax)
                    })
                    .collect::<Result<_, _>>()?,
            ))
        }
        ("tuple", 2, Some(Value::Array(values))) if constructor => Ok(Value::Tuple(
            values
                .iter()
                .map(|value| decode_tag(value, depth + 1, constructor))
                .collect::<Result<_, _>>()?,
        )),
        ("array", 2, Some(Value::Array(values))) => Ok(Value::Array(
            values
                .iter()
                .map(|value| decode_tag(value, depth + 1, constructor))
                .collect::<Result<_, _>>()?,
        )),
        ("object", 2, Some(Value::Array(entries))) => {
            let mut values = BTreeMap::new();
            for entry in entries {
                let Value::Array(pair) = entry else {
                    return Err(CodecError::Syntax);
                };
                if pair.len() != 2 {
                    return Err(CodecError::Syntax);
                }
                let key = point_array(&pair[0])?;
                if values
                    .insert(key, decode_tag(&pair[1], depth + 1, constructor)?)
                    .is_some()
                {
                    return Err(CodecError::DuplicateKey);
                }
            }
            Ok(Value::Object(values))
        }
        _ => Err(CodecError::Syntax),
    }
}

fn point_array(value: &Value) -> Result<Vec<u32>, CodecError> {
    let Value::Array(values) = value else {
        return Err(CodecError::Syntax);
    };
    values
        .iter()
        .map(|value| {
            let Value::Integer(value) = value else {
                return Err(CodecError::CodePoint);
            };
            let point = value.parse::<u32>().map_err(|_| CodecError::CodePoint)?;
            if point > 0x10ffff {
                return Err(CodecError::CodePoint);
            }
            Ok(point)
        })
        .collect()
}

fn text(points: &[u32]) -> Result<String, CodecError> {
    points
        .iter()
        .map(|point| char::from_u32(*point).ok_or(CodecError::CodePoint))
        .collect()
}

/// Small structured refusals allow facades to map domain exceptions safely.
pub fn error_code(error: &JournalError) -> &'static str {
    match error {
        JournalError::Conflict => "conflict",
        JournalError::Retired => "retired",
        JournalError::QueueFull => "queue_full",
        JournalError::FenceFull => "fence_full",
        JournalError::Unknown => "unknown",
        JournalError::Busy => "busy",
        JournalError::PrivateFile | JournalError::PrivateDirectory => "private_storage",
        JournalError::Invalid | JournalError::FileSize | JournalError::Json(_) => "invalid",
        JournalError::Storage(_) | JournalError::Io(_) => "storage",
    }
}

/// Open through the complete six-field bounds contract, refusing unknown fields.
pub fn open(path: &Path, bounds: &str, now: i64) -> Result<Journal, JournalError> {
    if now < 0 {
        return Err(JournalError::Invalid);
    }
    let bounds = codec::parse(bounds)?;
    let Value::Object(fields) = &bounds else {
        return Err(JournalError::Invalid);
    };
    let mut result = Bounds::default();
    for (key, value) in fields {
        let Value::Integer(value) = value else {
            return Err(JournalError::Invalid);
        };
        let value = value.parse::<i64>().map_err(|_| JournalError::Invalid)?;
        match text(key)?.as_str() {
            "max_queued" => result.max_queued = value,
            "max_runtime_seconds" => result.max_runtime_seconds = value,
            "stale_after_seconds" => result.stale_after_seconds = value,
            "retain" => result.retain = value,
            "log_bytes" => result.log_bytes = value,
            "reservations" => result.reservations = value,
            _ => return Err(JournalError::Invalid),
        }
    }
    Journal::open(path, result, now)
}

/// Execute one synchronous journal transaction. No callback or backend request
/// can occur here. Both languages receive the same canonical response bytes.
pub fn transact(journal: &mut Journal, request: &str) -> Result<Vec<u8>, JournalError> {
    let request = codec::parse(request)?;
    let Value::Object(fields) = &request else {
        return Err(JournalError::Invalid);
    };
    let method = required_text(fields, "method")?;
    let extra: &[&str] = match method.as_str() {
        "reserve" => &["operation_id", "input_digest", "input"],
        "update" => &[
            "operation_id",
            "state",
            "submission",
            "progress",
            "stage",
            "backend_reference",
            "result",
            "error",
        ],
        "status" | "payload" | "started_at" | "request_cancel" | "cancel_requested" => {
            &["operation_id"]
        }
        "page" => &["offset"],
        "next_queued" | "interrupted_accepted" | "drain_retired" => &[],
        _ => return Err(JournalError::Invalid),
    };
    for key in fields.keys() {
        let key = text(key)?;
        if !["method", "scope", "now"].contains(&key.as_str()) && !extra.contains(&key.as_str()) {
            return Err(JournalError::Invalid);
        }
    }
    let scope = optional_text(fields, "scope")?.unwrap_or_default();
    let id = if extra.contains(&"operation_id") {
        required_text(fields, "operation_id")?
    } else {
        String::new()
    };
    let now = integer_field(fields, "now")?;
    let now =
        if ["reserve", "update", "status", "page", "request_cancel"].contains(&method.as_str()) {
            now.ok_or(JournalError::Invalid)?
        } else {
            now.unwrap_or(0)
        };
    if now < 0 {
        return Err(JournalError::Invalid);
    }
    let response = match method.as_str() {
        "reserve" => {
            let digest = required_text(fields, "input_digest")?;
            let payload = fields
                .get(&codec::points("input"))
                .ok_or(JournalError::Invalid)?;
            let payload =
                String::from_utf8(codec::encode(payload)?).map_err(|_| JournalError::Invalid)?;
            let (reservation, status) = journal.reserve(&id, &digest, &payload, &scope, now)?;
            object([
                ("created", Value::Bool(reservation == Reservation::Created)),
                ("status", status_value(status)?),
            ])
        }
        "update" => {
            let result = fields
                .get(&codec::points("result"))
                .filter(|value| !matches!(value, Value::Null));
            let result_json = result
                .map(codec::encode)
                .transpose()?
                .map(String::from_utf8)
                .transpose()
                .map_err(|_| JournalError::Invalid)?;
            let progress = match fields.get(&codec::points("progress")) {
                None | Some(Value::Null) => None,
                Some(Value::Float(value)) => Some(*value),
                Some(Value::Integer(value)) => {
                    Some(value.parse().map_err(|_| JournalError::Invalid)?)
                }
                _ => return Err(JournalError::Invalid),
            };
            status_value(journal.update(
                &id,
                &scope,
                Update {
                    state: optional_text(fields, "state")?,
                    submission: optional_text(fields, "submission")?,
                    progress,
                    stage: optional_text(fields, "stage")?,
                    backend_reference: optional_text(fields, "backend_reference")?,
                    result_json,
                    error: optional_text(fields, "error")?,
                },
                now,
            )?)?
        }
        "status" => journal
            .status(&id, &scope, now)?
            .map(status_value)
            .transpose()?
            .unwrap_or(Value::Null),
        "payload" => journal
            .payload(&id, &scope)?
            .map(|value| codec::parse(&value))
            .transpose()?
            .unwrap_or(Value::Null),
        "started_at" => optional_integer(journal.started_at(&id, &scope)?),
        "request_cancel" => {
            journal.request_cancel(&id, &scope, now)?;
            Value::Null
        }
        "cancel_requested" => Value::Bool(journal.cancel_requested(&id, &scope)?),
        "next_queued" => optional_string(journal.next_queued(&scope)?),
        "interrupted_accepted" => pairs(journal.interrupted_accepted(&scope)?),
        "drain_retired" => pairs(journal.drain_retired()),
        "page" => {
            let page = journal.page(&scope, integer_field(fields, "offset")?.unwrap_or(0), now)?;
            object([
                (
                    "operations",
                    Value::Array(
                        page.operations
                            .into_iter()
                            .map(status_value)
                            .collect::<Result<_, _>>()?,
                    ),
                ),
                ("next_offset", optional_integer(page.next_offset)),
                ("active", integer(page.active)),
                ("outstanding", integer(page.outstanding)),
            ])
        }
        _ => return Err(JournalError::Invalid),
    };
    Ok(codec::encode(&response)?)
}

fn required_text(fields: &BTreeMap<Vec<u32>, Value>, key: &str) -> Result<String, JournalError> {
    optional_text(fields, key)?.ok_or(JournalError::Invalid)
}
fn optional_text(
    fields: &BTreeMap<Vec<u32>, Value>,
    key: &str,
) -> Result<Option<String>, JournalError> {
    match fields.get(&codec::points(key)) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(value)) => Ok(Some(text(value)?)),
        _ => Err(JournalError::Invalid),
    }
}
fn integer_field(
    fields: &BTreeMap<Vec<u32>, Value>,
    key: &str,
) -> Result<Option<i64>, JournalError> {
    match fields.get(&codec::points(key)) {
        None => Ok(None),
        Some(Value::Integer(value)) => Ok(Some(value.parse().map_err(|_| JournalError::Invalid)?)),
        _ => Err(JournalError::Invalid),
    }
}
fn integer(value: i64) -> Value {
    Value::Integer(value.to_string())
}
fn optional_integer(value: Option<i64>) -> Value {
    value.map(integer).unwrap_or(Value::Null)
}
fn optional_string(value: Option<String>) -> Value {
    value
        .map(|value| Value::String(codec::points(&value)))
        .unwrap_or(Value::Null)
}
fn object<const N: usize>(values: [(&str, Value); N]) -> Value {
    Value::Object(
        values
            .into_iter()
            .map(|(key, value)| (codec::points(key), value))
            .collect(),
    )
}
fn pairs(values: Vec<(String, String)>) -> Value {
    Value::Array(
        values
            .into_iter()
            .map(|(a, b)| Value::Array(vec![optional_string(Some(a)), optional_string(Some(b))]))
            .collect(),
    )
}
fn status_value(status: Status) -> Result<Value, JournalError> {
    Ok(object([
        ("operation_id", optional_string(Some(status.operation_id))),
        ("state", optional_string(Some(status.state))),
        ("submission", optional_string(Some(status.submission))),
        (
            "progress",
            status.progress.map(Value::Float).unwrap_or(Value::Null),
        ),
        ("stage", optional_string(status.stage)),
        ("queue_position", optional_integer(status.queue_position)),
        ("submitted_at", integer(status.submitted_at)),
        ("updated_at", integer(status.updated_at)),
        ("finished_at", optional_integer(status.finished_at)),
        ("stale", Value::Bool(status.stale)),
        ("input_digest", optional_string(Some(status.input_digest))),
        (
            "backend_reference",
            optional_string(status.backend_reference),
        ),
        (
            "input",
            status
                .input_json
                .map(|value| codec::parse(&value))
                .transpose()?
                .unwrap_or(Value::Null),
        ),
        (
            "result",
            status
                .result_json
                .map(|value| codec::parse(&value))
                .transpose()?
                .unwrap_or(Value::Null),
        ),
        ("error", optional_string(status.error)),
    ]))
}

/// Encode a runner's status using the same canonical legacy observation envelope.
pub fn status_json(status: Status) -> Result<String, JournalError> {
    String::from_utf8(codec::encode(&status_value(status)?)?).map_err(|_| JournalError::Invalid)
}

/// Constructor-only tagged transport preserves raw bytes and nonfinite numbers.
/// Ordinary JSON transport keeps its existing stricter finite/kind rules.
pub fn constructor_tagged(input: &str) -> Result<Value, CodecError> {
    if input.len() > 16 * 1024 * 1024 {
        return Err(CodecError::Syntax);
    }
    decode_tag(&codec::parse(input)?, 0, true)
}
/// Convert normalized constructor data for a language facade without JSON loss.
fn constructor_tree(value: &Value, depth: usize) -> Result<Value, CodecError> {
    if depth > 200 {
        return Err(CodecError::Nesting);
    }
    let tag =
        |kind: &str, value: Value| Value::Array(vec![Value::String(codec::points(kind)), value]);
    let encoded = match value {
        Value::Null => Value::Array(vec![Value::String(codec::points("null"))]),
        Value::Bool(value) => tag("bool", Value::Bool(*value)),
        Value::Integer(value) => tag("integer", Value::String(codec::points(value))),
        Value::Float(value) => tag(
            "float",
            Value::String(codec::points(&format!("{:016x}", value.to_bits()))),
        ),
        Value::String(value) => tag(
            "string",
            Value::Array(
                value
                    .iter()
                    .map(|point| Value::Integer(point.to_string()))
                    .collect(),
            ),
        ),
        Value::Bytes(value) => tag(
            "bytes",
            Value::String(codec::points(
                &value
                    .iter()
                    .map(|byte| format!("{byte:02x}"))
                    .collect::<String>(),
            )),
        ),
        Value::Array(values) | Value::Tuple(values) => tag(
            "array",
            Value::Array(
                values
                    .iter()
                    .map(|value| constructor_tree(value, depth + 1))
                    .collect::<Result<_, _>>()?,
            ),
        ),
        Value::Object(values) => tag(
            "object",
            Value::Array(
                values
                    .iter()
                    .map(|(key, value)| {
                        Ok(Value::Array(vec![
                            Value::Array(
                                key.iter()
                                    .map(|point| Value::Integer(point.to_string()))
                                    .collect(),
                            ),
                            constructor_tree(value, depth + 1)?,
                        ]))
                    })
                    .collect::<Result<_, CodecError>>()?,
            ),
        ),
        Value::Model(_, value) => return constructor_tree(value, depth + 1),
    };
    Ok(encoded)
}
/// Emit constructor conversion data once, with bounded output and no re-parsing.
pub fn constructor_transport(value: &Value) -> Result<String, CodecError> {
    let encoded = codec::encode(&constructor_tree(value, 0)?)?;
    if encoded.len() > 16 * 1024 * 1024 {
        return Err(CodecError::Syntax);
    }
    String::from_utf8(encoded).map_err(|_| CodecError::Syntax)
}
