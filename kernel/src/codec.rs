//! Legacy finite, sorted, compact, ASCII-escaped JSON and scoped input digests.
//!
//! A string is code points, rather than Rust UTF-8, because the existing Python
//! encoder accepts lone surrogates. Integers keep arbitrary decimal precision;
//! floats retain their distinct kind and use Python's exponent/decimal profile.
//! Incoming JSON rejects duplicate decoded keys before any identity is computed.

use std::collections::BTreeMap;
use std::fmt::Write;

use sha2::{Digest, Sha256};
use thiserror::Error;

/// Safe failures that never include the input, a key, or a credential value.
#[derive(Debug, Error, PartialEq, Eq)]
pub enum CodecError {
    #[error("invalid JSON")]
    Syntax,
    #[error("duplicate JSON key")]
    DuplicateKey,
    #[error("non-finite JSON value")]
    NonFinite,
    #[error("JSON nesting exceeds the bound")]
    Nesting,
    #[error("invalid Unicode code point")]
    CodePoint,
    #[error("invalid integer representation")]
    Integer,
    #[error("operation input must be an object")]
    InputShape,
}

/// Lossless value used by both language bindings before legacy serialization.
#[derive(Clone, Debug, PartialEq)]
pub enum Value {
    Null,
    Bool(bool),
    Integer(String),
    Float(f64),
    String(Vec<u32>),
    Array(Vec<Value>),
    /// Constructor-only kinds; never produced by the JSON parser.
    Tuple(Vec<Value>),
    Bytes(Vec<u8>),
    Model(String, Box<Value>),
    Object(BTreeMap<Vec<u32>, Value>),
}

const MAX_DEPTH: usize = 1024;
const MAX_INTEGER_DIGITS: usize = 4300;

/// Decode one finite JSON document while retaining integer versus float kinds.
pub fn parse(input: &str) -> Result<Value, CodecError> {
    parse_document(input, false, MAX_DEPTH).map(|(value, _)| value)
}

/// A stricter consumer can refuse nesting during parsing, before allocation.
pub(crate) fn parse_bounded(input: &str, max_depth: usize) -> Result<Value, CodecError> {
    parse_document(input, false, max_depth).map(|(value, _)| value)
}

/// Validation-only source order; canonical values and digests stay sorted.
pub(crate) type ObjectOrders = BTreeMap<Vec<Vec<u32>>, Vec<Vec<u32>>>;

pub(crate) fn parse_with_order(input: &str) -> Result<(Value, ObjectOrders), CodecError> {
    parse_document(input, true, MAX_DEPTH)
}

fn parse_document(
    input: &str,
    ordered: bool,
    max_depth: usize,
) -> Result<(Value, ObjectOrders), CodecError> {
    let mut parser = Parser {
        input,
        at: 0,
        ordered,
        max_depth,
        path: Vec::new(),
        orders: BTreeMap::new(),
    };
    let value = parser.value(0)?;
    parser.whitespace();
    if parser.at != input.len() {
        return Err(CodecError::Syntax);
    }
    Ok((value, parser.orders))
}

/// Encode a structured value exactly as the legacy Python canonical encoder.
pub fn encode(value: &Value) -> Result<Vec<u8>, CodecError> {
    let mut output = String::new();
    write_value(value, &mut output, 0)?;
    Ok(output.into_bytes())
}

/// Canonicalize a document; bindings can instead construct values losslessly.
pub fn canonical(input: &str) -> Result<Vec<u8>, CodecError> {
    encode(&parse(input)?)
}

/// SHA-256 of the exact legacy canonical document, used for signed identities.
pub fn document_digest(input: &str) -> Result<String, CodecError> {
    Ok(sha256(&canonical(input)?))
}

/// Bind an operation's input identity to its installed descriptor scope.
pub fn input_digest(scope: &str, input: &str) -> Result<String, CodecError> {
    let payload = parse(input)?;
    scoped_digest(&points(scope), payload)
}

/// Preserve a binding's decoded scope, including Python surrogate code points.
pub fn scoped_digest(scope: &[u32], payload: Value) -> Result<String, CodecError> {
    if !matches!(payload, Value::Object(_)) {
        return Err(CodecError::InputShape);
    }
    let value = Value::Object(BTreeMap::from([
        (points("input"), payload),
        (points("scope"), Value::String(scope.to_vec())),
    ]));
    Ok(sha256(&encode(&value)?))
}

/// Encode one already-decoded string, including surrogate code points.
pub fn string(value: &[u32]) -> Result<String, CodecError> {
    let mut output = String::new();
    write_string(value, &mut output)?;
    Ok(output)
}

/// Convert an ordinary UTF-8 string to the kernel's code point representation.
pub fn points(value: &str) -> Vec<u32> {
    value.chars().map(u32::from).collect()
}

pub(crate) fn sha256(value: &[u8]) -> String {
    let mut output = String::with_capacity(64);
    for byte in Sha256::digest(value) {
        write!(output, "{byte:02x}").expect("writing to String");
    }
    output
}

fn write_value(value: &Value, output: &mut String, depth: usize) -> Result<(), CodecError> {
    if depth > MAX_DEPTH {
        return Err(CodecError::Nesting);
    }
    match value {
        Value::Null => output.push_str("null"),
        Value::Bool(value) => output.push_str(if *value { "true" } else { "false" }),
        Value::Integer(value) => {
            if !valid_integer(value) {
                return Err(CodecError::Integer);
            }
            output.push_str(if value == "-0" { "0" } else { value });
        }
        Value::Float(value) => output.push_str(&float(*value)?),
        Value::String(value) => write_string(value, output)?,
        Value::Array(values) | Value::Tuple(values) => {
            output.push('[');
            for (index, value) in values.iter().enumerate() {
                if index != 0 {
                    output.push(',');
                }
                write_value(value, output, depth + 1)?;
            }
            output.push(']');
        }
        Value::Object(values) => {
            output.push('{');
            for (index, (key, value)) in values.iter().enumerate() {
                if index != 0 {
                    output.push(',');
                }
                write_string(key, output)?;
                output.push(':');
                write_value(value, output, depth + 1)?;
            }
            output.push('}');
        }
        Value::Bytes(_) | Value::Model(_, _) => return Err(CodecError::Syntax),
    }
    Ok(())
}

fn write_string(value: &[u32], output: &mut String) -> Result<(), CodecError> {
    output.push('"');
    for point in value {
        match *point {
            8 => output.push_str("\\b"),
            9 => output.push_str("\\t"),
            10 => output.push_str("\\n"),
            12 => output.push_str("\\f"),
            13 => output.push_str("\\r"),
            34 => output.push_str("\\\""),
            92 => output.push_str("\\\\"),
            32..=126 => output.push(char::from_u32(*point).ok_or(CodecError::CodePoint)?),
            0..=0xffff => write!(output, "\\u{point:04x}").expect("writing to String"),
            0x10000..=0x10ffff => {
                let offset = point - 0x10000;
                let high = 0xd800 + (offset >> 10);
                let low = 0xdc00 + (offset & 0x3ff);
                write!(output, "\\u{high:04x}\\u{low:04x}").expect("writing to String");
            }
            _ => return Err(CodecError::CodePoint),
        }
    }
    output.push('"');
    Ok(())
}

fn valid_integer(value: &str) -> bool {
    let digits = value.strip_prefix('-').unwrap_or(value);
    !digits.is_empty()
        && digits.len() <= MAX_INTEGER_DIGITS
        && digits.bytes().all(|byte| byte.is_ascii_digit())
        && (digits.len() == 1 || !digits.starts_with('0'))
}

/// Ryu provides shortest round-trip digits; this formats them as Python repr.
fn float(value: f64) -> Result<String, CodecError> {
    if !value.is_finite() {
        return Err(CodecError::NonFinite);
    }
    if value == 0.0 {
        return Ok(if value.is_sign_negative() {
            "-0.0"
        } else {
            "0.0"
        }
        .into());
    }
    let negative = value.is_sign_negative();
    let mut buffer = ryu::Buffer::new();
    let shortest = buffer.format_finite(value.abs());
    let (mantissa, exponent) = match shortest.split_once('e') {
        Some((mantissa, exponent)) => (
            mantissa,
            exponent.parse::<i32>().map_err(|_| CodecError::Syntax)?,
        ),
        None => (shortest, 0),
    };
    let point = mantissa.find('.').unwrap_or(mantissa.len()) as i32;
    let raw: String = mantissa.chars().filter(|value| *value != '.').collect();
    let leading = raw.bytes().take_while(|value| *value == b'0').count();
    let digits = raw[leading..].trim_end_matches('0');
    let exponent = exponent + point - leading as i32 - 1;
    let mut output = if negative { "-".into() } else { String::new() };
    if !(-4..16).contains(&exponent) {
        output.push_str(&digits[..1]);
        if digits.len() > 1 {
            output.push('.');
            output.push_str(&digits[1..]);
        }
        write!(
            output,
            "e{}{magnitude:02}",
            if exponent < 0 { '-' } else { '+' },
            magnitude = exponent.unsigned_abs()
        )
        .expect("writing to String");
    } else if exponent < 0 {
        output.push_str("0.");
        output.extend(std::iter::repeat_n('0', (-exponent - 1) as usize));
        output.push_str(digits);
    } else {
        let before = exponent as usize + 1;
        if digits.len() <= before {
            output.push_str(digits);
            output.extend(std::iter::repeat_n('0', before - digits.len()));
            output.push_str(".0");
        } else {
            output.push_str(&digits[..before]);
            output.push('.');
            output.push_str(&digits[before..]);
        }
    }
    Ok(output)
}

struct Parser<'a> {
    input: &'a str,
    at: usize,
    ordered: bool,
    max_depth: usize,
    path: Vec<Vec<u32>>,
    orders: ObjectOrders,
}

impl Parser<'_> {
    fn whitespace(&mut self) {
        while self
            .peek()
            .is_some_and(|value| matches!(value, b' ' | b'\n' | b'\r' | b'\t'))
        {
            self.at += 1;
        }
    }

    fn peek(&self) -> Option<u8> {
        self.input.as_bytes().get(self.at).copied()
    }

    fn take(&mut self, byte: u8) -> Result<(), CodecError> {
        if self.peek() != Some(byte) {
            return Err(CodecError::Syntax);
        }
        self.at += 1;
        Ok(())
    }

    fn value(&mut self, depth: usize) -> Result<Value, CodecError> {
        if depth > self.max_depth {
            return Err(CodecError::Nesting);
        }
        self.whitespace();
        match self.peek() {
            Some(b'n') => self.literal("null", Value::Null),
            Some(b't') => self.literal("true", Value::Bool(true)),
            Some(b'f') => self.literal("false", Value::Bool(false)),
            Some(b'"') => self.text().map(Value::String),
            Some(b'[') => {
                self.at += 1;
                self.whitespace();
                let mut values = Vec::new();
                if self.peek() != Some(b']') {
                    loop {
                        if self.ordered {
                            self.path.push(points(&values.len().to_string()));
                        }
                        values.push(self.value(depth + 1)?);
                        if self.ordered {
                            self.path.pop();
                        }
                        self.whitespace();
                        if self.peek() != Some(b',') {
                            break;
                        }
                        self.at += 1;
                    }
                }
                self.take(b']')?;
                Ok(Value::Array(values))
            }
            Some(b'{') => {
                self.at += 1;
                self.whitespace();
                let mut values = BTreeMap::new();
                let mut keys = Vec::new();
                if self.peek() != Some(b'}') {
                    loop {
                        self.whitespace();
                        let key = self.text()?;
                        self.whitespace();
                        self.take(b':')?;
                        if self.ordered {
                            self.path.push(key.clone());
                            keys.push(key.clone());
                        }
                        let value = self.value(depth + 1)?;
                        if self.ordered {
                            self.path.pop();
                        }
                        if values.insert(key, value).is_some() {
                            return Err(CodecError::DuplicateKey);
                        }
                        self.whitespace();
                        if self.peek() != Some(b',') {
                            break;
                        }
                        self.at += 1;
                    }
                }
                self.take(b'}')?;
                if self.ordered {
                    self.orders.insert(self.path.clone(), keys);
                }
                Ok(Value::Object(values))
            }
            Some(b'-' | b'0'..=b'9') => self.number(),
            _ => Err(CodecError::Syntax),
        }
    }

    fn literal(&mut self, literal: &str, value: Value) -> Result<Value, CodecError> {
        if !self.input[self.at..].starts_with(literal) {
            return Err(CodecError::Syntax);
        }
        self.at += literal.len();
        Ok(value)
    }

    fn text(&mut self) -> Result<Vec<u32>, CodecError> {
        self.take(b'"')?;
        let mut output = Vec::new();
        loop {
            match self.peek() {
                None | Some(0..=31) => return Err(CodecError::Syntax),
                Some(b'"') => {
                    self.at += 1;
                    return Ok(output);
                }
                Some(b'\\') => {
                    self.at += 1;
                    let escape = self.peek().ok_or(CodecError::Syntax)?;
                    self.at += 1;
                    let point = match escape {
                        b'"' => 34,
                        b'\\' => 92,
                        b'/' => 47,
                        b'b' => 8,
                        b'f' => 12,
                        b'n' => 10,
                        b'r' => 13,
                        b't' => 9,
                        b'u' => {
                            let high = self.hex()?;
                            if (0xd800..=0xdbff).contains(&high)
                                && self.input[self.at..].starts_with("\\u")
                            {
                                let saved = self.at;
                                self.at += 2;
                                let low = self.hex()?;
                                if (0xdc00..=0xdfff).contains(&low) {
                                    0x10000 + ((high - 0xd800) << 10) + low - 0xdc00
                                } else {
                                    self.at = saved;
                                    high
                                }
                            } else {
                                high
                            }
                        }
                        _ => return Err(CodecError::Syntax),
                    };
                    output.push(point);
                }
                Some(_) => {
                    let character = self.input[self.at..]
                        .chars()
                        .next()
                        .ok_or(CodecError::Syntax)?;
                    self.at += character.len_utf8();
                    output.push(u32::from(character));
                }
            }
        }
    }

    fn hex(&mut self) -> Result<u32, CodecError> {
        let raw = self
            .input
            .as_bytes()
            .get(self.at..self.at + 4)
            .ok_or(CodecError::Syntax)?;
        let mut value = 0;
        for byte in raw {
            let digit = match byte {
                b'0'..=b'9' => u32::from(byte - b'0'),
                b'a'..=b'f' => u32::from(byte - b'a') + 10,
                b'A'..=b'F' => u32::from(byte - b'A') + 10,
                _ => return Err(CodecError::Syntax),
            };
            value = (value << 4) + digit;
        }
        self.at += 4;
        Ok(value)
    }

    fn number(&mut self) -> Result<Value, CodecError> {
        let start = self.at;
        if self.peek() == Some(b'-') {
            self.at += 1;
        }
        match self.peek() {
            Some(b'0') => self.at += 1,
            Some(b'1'..=b'9') => {
                while self.peek().is_some_and(|value| value.is_ascii_digit()) {
                    self.at += 1;
                }
            }
            _ => return Err(CodecError::Syntax),
        }
        let mut floating = false;
        if self.peek() == Some(b'.') {
            floating = true;
            self.at += 1;
            self.digits()?;
        }
        if self
            .peek()
            .is_some_and(|value| matches!(value, b'e' | b'E'))
        {
            floating = true;
            self.at += 1;
            if self
                .peek()
                .is_some_and(|value| matches!(value, b'+' | b'-'))
            {
                self.at += 1;
            }
            self.digits()?;
        }
        let raw = &self.input[start..self.at];
        if floating {
            let value: f64 = raw.parse().map_err(|_| CodecError::Syntax)?;
            if !value.is_finite() {
                return Err(CodecError::NonFinite);
            }
            Ok(Value::Float(value))
        } else if valid_integer(raw) {
            Ok(Value::Integer(if raw == "-0" {
                "0".into()
            } else {
                raw.into()
            }))
        } else {
            Err(CodecError::Integer)
        }
    }

    fn digits(&mut self) -> Result<(), CodecError> {
        let start = self.at;
        while self.peek().is_some_and(|value| value.is_ascii_digit()) {
            self.at += 1;
        }
        if self.at == start {
            return Err(CodecError::Syntax);
        }
        Ok(())
    }
}
