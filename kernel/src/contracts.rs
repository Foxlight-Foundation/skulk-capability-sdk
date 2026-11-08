//! Private, versioned author-contract validation and projection.
//!
//! Type/default rules come from one inert definition. Python/Node must not
//! independently coerce numbers, apply defaults or compute signed projections.
//! Generic JSON uses the lossless codec, while declared strings follow the
//! Python model's Unicode/type contract. No network or Python execution occurs.

use std::collections::{BTreeMap, HashMap};
use std::sync::OnceLock;

use regex::Regex;
use serde_json::Value as Spec;
use thiserror::Error;
use unicode_normalization::UnicodeNormalization;

use crate::codec::{self, Value};

const MAX_INPUT: usize = 196608;
const MAX_DEPTH: usize = 128;
const MAX_CONSTRUCTOR_BYTES: usize = 16 * 1024 * 1024;

/// Source language distinctions remain explicit until native normalization.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ConstructorForm {
    Python,
    JavaScript,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Form {
    Json,
    Python,
    JavaScript,
    Normalized,
}

/// A validated value retains raw media until its actual ownership boundary.
#[derive(Clone, Debug)]
pub struct ValidatedContract {
    name: String,
    model_name: String,
    value: Value,
}
impl ValidatedContract {
    /// The reviewed contract or model name used for validation.
    pub fn name(&self) -> &str {
        &self.name
    }
    /// Immutable normalized data, retaining binary media until serialization.
    pub fn value(&self) -> &Value {
        &self.value
    }
    /// Reuse only this immutable validated model, charging before copying it.
    pub fn constructor_input(&self, remaining: &mut usize) -> Result<Value> {
        constructor_budget(&self.value, remaining, 0)?;
        *remaining = remaining
            .checked_sub(self.model_name.len())
            .ok_or(ContractError::Bound)?;
        Ok(Value::Model(
            self.model_name.clone(),
            Box::new(self.value.clone()),
        ))
    }
}
const DEFINITION: &str = include_str!("../contracts/author-v1.json");

/// Sanitized contract failures never echo settings, credentials or payloads.
#[derive(Debug, Error)]
pub enum ContractError {
    #[error("unknown author contract")]
    Unknown,
    #[error("invalid author contract value")]
    Invalid,
    #[error("author contract bound exceeded")]
    Bound,
    #[error("unsupported contract definition")]
    Definition,
    #[error("operation requires effect authority")]
    Authority,
}
type Result<T> = std::result::Result<T, ContractError>;

/// Compiled versioned rules shared by every language binding.
pub struct Registry {
    spec: Spec,
    patterns: HashMap<String, Regex>,
}

impl Registry {
    /// Compile the packaged inert source without resolving any external resource.
    pub fn packaged() -> Result<&'static Self> {
        static REGISTRY: OnceLock<std::result::Result<Registry, ()>> = OnceLock::new();
        REGISTRY
            .get_or_init(|| Self::compile(DEFINITION).map_err(|_| ()))
            .as_ref()
            .map_err(|_| ContractError::Definition)
    }

    /// Compile a maintainer definition; malformed shapes fail closed.
    pub fn compile(source: &str) -> Result<Self> {
        let spec: Spec = serde_json::from_str(source).map_err(|_| ContractError::Definition)?;
        if spec.get("format").and_then(Spec::as_u64) != Some(1)
            || spec.get("profile").and_then(Spec::as_str) != Some("author-v1")
            || spec.get("models").and_then(Spec::as_object).is_none()
            || spec.get("roots").and_then(Spec::as_object).is_none()
        {
            return Err(ContractError::Definition);
        }
        let mut patterns = HashMap::new();
        collect_patterns(&spec, &mut patterns)?;
        Ok(Self { spec, patterns })
    }

    /// Normalize one JSON contract and return its legacy canonical projection.
    /// This never hashes the caller's unvalidated or default-incomplete document.
    pub fn validate_json(&self, name: &str, source: &str) -> Result<Vec<u8>> {
        if source.len() > MAX_INPUT {
            return Err(ContractError::Bound);
        }
        let value = codec::parse(source).map_err(|_| ContractError::Invalid)?;
        json_input(&value, 0)?;
        let schema = self
            .spec
            .get("roots")
            .and_then(|roots| roots.get(name))
            .ok_or(ContractError::Unknown)?;
        let normalized = self.normalize(schema, Some(&value), 0, Form::Json)?;
        let projected = self.project(schema, &normalized, 0)?;
        let bytes = codec::encode(&projected).map_err(|_| ContractError::Invalid)?;
        let bound = match name {
            "Manifest" => self.limit("manifest")?,
            "Startup" => self.limit("startup")?,
            "WireMessage" => self.limit("frame")?,
            _ => MAX_INPUT,
        };
        if bytes.len() > bound {
            return Err(ContractError::Bound);
        }
        Ok(bytes)
    }

    /// Validate Python/JavaScript constructor values without first erasing tuple,
    /// byte or Unicode kinds through JSON. Arbitrary binary media remains valid
    /// even when a caller cannot serialize it as a JSON document.
    pub fn validate_value(
        &self,
        name: &str,
        value: &Value,
        form: ConstructorForm,
    ) -> Result<ValidatedContract> {
        let mut remaining = MAX_CONSTRUCTOR_BYTES;
        constructor_budget(value, &mut remaining, 0)?;
        let schema = self.constructor_schema(name)?;
        let form = match form {
            ConstructorForm::Python => Form::Python,
            ConstructorForm::JavaScript => Form::JavaScript,
        };
        let value = self.normalize(&schema, Some(value), 0, form)?;
        let model_name = self.selected_model(&schema, &value)?;
        Ok(ValidatedContract {
            name: name.into(),
            model_name,
            value,
        })
    }

    fn selected_model(&self, schema: &Spec, value: &Value) -> Result<String> {
        match text(schema, "type")? {
            "model" => Ok(text(schema, "name")?.into()),
            "checked" | "default" | "nullable" => self.selected_model(child(schema)?, value),
            "tagged" => {
                let tag = value_text(member(value, text(schema, "field")?)?)?;
                let choice = schema
                    .get("choices")
                    .and_then(|choices| choices.get(&tag))
                    .ok_or(ContractError::Definition)?;
                self.selected_model(choice, value)
            }
            "union" => {
                for choice in schema
                    .get("choices")
                    .and_then(Spec::as_array)
                    .ok_or(ContractError::Definition)?
                {
                    if self
                        .normalize(choice, Some(value), 0, Form::Normalized)
                        .is_ok()
                    {
                        return self.selected_model(choice, value);
                    }
                }
                Err(ContractError::Invalid)
            }
            _ => Err(ContractError::Definition),
        }
    }

    /// Serialize one validated contract using the shared legacy projection.
    pub fn project_value(&self, value: &ValidatedContract) -> Result<Vec<u8>> {
        let schema = self.constructor_schema(&value.name)?;
        let value = self.project(&schema, &value.value, 0)?;
        codec::encode(&value).map_err(|_| ContractError::Invalid)
    }

    /// Produce the validated stream projection plus its separate raw attachment.
    /// Binary bytes are omitted before JSON projection, as in Python write_packet.
    pub fn stream_packet(&self, value: &ValidatedContract) -> Result<crate::streams::Packet> {
        if !["StreamFrame", "StreamEnd", "StreamPacket"].contains(&value.name.as_str()) {
            return Err(ContractError::Invalid);
        }
        let mut frame = value.value.clone();
        let mut raw = Vec::new();
        if let Value::Object(fields) = &mut frame
            && let Some(Value::Object(media)) = fields.get_mut(&codec::points("media"))
            && media.get(&codec::points("kind")) == Some(&Value::String(codec::points("inline")))
        {
            let Some(Value::Bytes(bytes)) = media.get(&codec::points("data")) else {
                return Err(ContractError::Invalid);
            };
            raw = bytes.clone();
            media.insert(codec::points("data"), Value::Bytes(Vec::new()));
        }
        let schema = self.constructor_schema(&value.name)?;
        let mut projection = self.project(&schema, &frame, 0)?;
        if let Value::Object(fields) = &mut projection
            && let Some(Value::Object(media)) = fields.get_mut(&codec::points("media"))
        {
            media.remove(&codec::points("data"));
        }
        let source =
            String::from_utf8(codec::encode(&projection).map_err(|_| ContractError::Invalid)?)
                .map_err(|_| ContractError::Invalid)?;
        crate::streams::Packet::from_projection(&source, raw).map_err(|_| ContractError::Invalid)
    }

    fn constructor_schema(&self, name: &str) -> Result<Spec> {
        if let Some(schema) = self.spec.get("roots").and_then(|roots| roots.get(name)) {
            return Ok(schema.clone());
        }
        if self
            .spec
            .get("models")
            .and_then(|models| models.get(name))
            .is_some()
        {
            for section in ["roots", "models"] {
                if let Some(contract) = find_model_contract(
                    self.spec.get(section).ok_or(ContractError::Definition)?,
                    name,
                ) {
                    return Ok(contract);
                }
            }
            return Ok(serde_json::json!({"type":"model","name":name}));
        }
        Err(ContractError::Unknown)
    }

    /// Validate authority before a dispatcher may touch its journal/backend.
    /// The caller derives this explicit grant from its authenticated owner; SDK
    /// package accounts are not fabric authority and cannot mint that grant.
    pub fn authorize_operation_json(&self, source: &str, authority: &str) -> Result<Vec<u8>> {
        if !["read", "effect"].contains(&authority) {
            return Err(ContractError::Authority);
        }
        let bytes = self.validate_json("OperationCall", source)?;
        let normalized =
            codec::parse(std::str::from_utf8(&bytes).map_err(|_| ContractError::Invalid)?)
                .map_err(|_| ContractError::Invalid)?;
        let op = value_text(member(&normalized, "op")?)?;
        if ["start", "cancel", "plan"].contains(&op.as_str()) && authority != "effect" {
            return Err(ContractError::Authority);
        }
        Ok(bytes)
    }

    fn limit(&self, name: &str) -> Result<usize> {
        self.spec
            .get("limits")
            .and_then(|limits| limits.get(name))
            .and_then(Spec::as_u64)
            .and_then(|value| usize::try_from(value).ok())
            .ok_or(ContractError::Definition)
    }

    fn model(&self, name: &str) -> Result<&serde_json::Map<String, Spec>> {
        self.spec
            .get("models")
            .and_then(|models| models.get(name))
            .and_then(|model| model.get("fields"))
            .and_then(Spec::as_object)
            .ok_or(ContractError::Definition)
    }

    fn normalize(
        &self,
        schema: &Spec,
        input: Option<&Value>,
        depth: usize,
        form: Form,
    ) -> Result<Value> {
        if depth > MAX_DEPTH {
            return Err(ContractError::Bound);
        }
        let kind = text(schema, "type")?;
        if kind == "default" {
            let default;
            let value = match input {
                Some(value) => value,
                None => {
                    default = spec_value(schema.get("value").ok_or(ContractError::Definition)?)?;
                    &default
                }
            };
            return self.normalize(
                child(schema)?,
                Some(value),
                depth + 1,
                if input.is_none() {
                    Form::Normalized
                } else {
                    form
                },
            );
        }
        let input = input.ok_or(ContractError::Invalid)?;
        match kind {
            "checked" => {
                let value = self.normalize(child(schema)?, Some(input), depth + 1, form)?;
                self.semantic(text(schema, "rule")?, &value)?;
                Ok(value)
            }
            "nullable" if matches!(input, Value::Null) => Ok(Value::Null),
            "nullable" => self.normalize(child(schema)?, Some(input), depth + 1, form),
            "json" => {
                structured_json(input, 0)?;
                Ok(input.clone())
            }
            "model" => {
                let (input, form) = if let Value::Model(name, value) = input {
                    if name != text(schema, "name")? {
                        return Err(ContractError::Invalid);
                    }
                    (value.as_ref(), Form::Normalized)
                } else {
                    (input, form)
                };
                let Value::Object(values) = input else {
                    return Err(ContractError::Invalid);
                };
                let fields = self.model(text(schema, "name")?)?;
                if values.keys().any(|key| {
                    decoded(key)
                        .ok()
                        .is_none_or(|key| !fields.contains_key(&key))
                }) {
                    return Err(ContractError::Invalid);
                }
                let mut output = BTreeMap::new();
                for (name, field) in fields {
                    let key = codec::points(name);
                    let value = self.normalize(child(field)?, values.get(&key), depth + 1, form)?;
                    output.insert(key, value);
                }
                Ok(Value::Object(output))
            }
            "object" => {
                let Value::Object(values) = input else {
                    return Err(ContractError::Invalid);
                };
                let mut output = BTreeMap::new();
                for (key, value) in values {
                    output.insert(
                        key.clone(),
                        self.normalize(
                            schema.get("values").ok_or(ContractError::Definition)?,
                            Some(value),
                            depth + 1,
                            form,
                        )?,
                    );
                }
                Ok(Value::Object(output))
            }
            "array" => {
                let values = match (form, input) {
                    (Form::Python, Value::Tuple(values)) => values,
                    (Form::Json | Form::JavaScript | Form::Normalized, Value::Array(values)) => {
                        values
                    }
                    _ => return Err(ContractError::Invalid),
                };
                length(schema, values.len())?;
                let item = schema.get("items").ok_or(ContractError::Definition)?;
                Ok(Value::Array(
                    values
                        .iter()
                        .map(|value| self.normalize(item, Some(value), depth + 1, form))
                        .collect::<Result<_>>()?,
                ))
            }
            "tagged" => {
                let tag = member(input, text(schema, "field")?)?;
                let tag = value_text(tag)?;
                let choice = schema
                    .get("choices")
                    .and_then(|choices| choices.get(&tag))
                    .ok_or(ContractError::Invalid)?;
                self.normalize(choice, Some(input), depth + 1, form)
            }
            "union" => {
                for choice in schema
                    .get("choices")
                    .and_then(Spec::as_array)
                    .ok_or(ContractError::Definition)?
                {
                    if let Ok(value) = self.normalize(choice, Some(input), depth + 1, form) {
                        return Ok(value);
                    }
                }
                Err(ContractError::Invalid)
            }
            "literal" => {
                for expected in schema
                    .get("values")
                    .and_then(Spec::as_array)
                    .ok_or(ContractError::Definition)?
                {
                    let expected = spec_value(expected)?;
                    if literal_equal(input, &expected) {
                        return Ok(expected);
                    }
                }
                Err(ContractError::Invalid)
            }
            "str" => {
                let Value::String(points) = input else {
                    return Err(ContractError::Invalid);
                };
                // Unconstrained Python strings remain Python objects in the
                // reference validator; constrained strings cross its Rust UTF-8
                // boundary and refuse lone surrogates. Preserve that distinction.
                if form != Form::Json
                    && ["pattern", "min_length", "max_length"]
                        .iter()
                        .all(|key| schema.get(*key).is_none())
                {
                    return Ok(input.clone());
                }
                let string = decoded(points)?;
                length(schema, points.len())?;
                if let Some(pattern) = schema.get("pattern").and_then(Spec::as_str)
                    && !self
                        .patterns
                        .get(pattern)
                        .ok_or(ContractError::Definition)?
                        .is_match(&string)
                {
                    return Err(ContractError::Invalid);
                }
                Ok(input.clone())
            }
            "bool" if matches!(input, Value::Bool(_)) => Ok(input.clone()),
            "int" => {
                let Value::Integer(integer) = input else {
                    return Err(ContractError::Invalid);
                };
                integer_bounds(schema, integer)?;
                Ok(input.clone())
            }
            "float" => {
                let number = match input {
                    Value::Float(number) => *number,
                    Value::Integer(integer) => {
                        integer.parse::<f64>().map_err(|_| ContractError::Invalid)?
                    }
                    _ => return Err(ContractError::Invalid),
                };
                if !number.is_finite() {
                    return Err(ContractError::Invalid);
                }
                float_bounds(schema, number)?;
                Ok(Value::Float(number))
            }
            // Pydantic's JSON form for bytes is UTF-8 text, not base64. Raw
            // arbitrary media still uses the separate transport entry point.
            "bytes" => {
                let bytes = match (form, input) {
                    (Form::Python | Form::JavaScript | Form::Normalized, Value::Bytes(bytes)) => {
                        bytes.clone()
                    }
                    (Form::Json | Form::Normalized, Value::String(_)) => {
                        value_text(input)?.into_bytes()
                    }
                    _ => return Err(ContractError::Invalid),
                };
                length(schema, bytes.len())?;
                Ok(Value::Bytes(bytes))
            }
            _ => Err(ContractError::Invalid),
        }
    }

    fn project(&self, schema: &Spec, value: &Value, depth: usize) -> Result<Value> {
        if depth > MAX_DEPTH {
            return Err(ContractError::Bound);
        }
        match text(schema, "type")? {
            "checked" | "default" => self.project(child(schema)?, value, depth + 1),
            "nullable" if matches!(value, Value::Null) => Ok(Value::Null),
            "nullable" => self.project(child(schema)?, value, depth + 1),
            "model" => {
                let fields = self.model(text(schema, "name")?)?;
                let mut output = BTreeMap::new();
                for (name, field) in fields {
                    let key = codec::points(name);
                    let item = member(value, name)?;
                    if let Some(omit) = field.get("omit_equal")
                        && literal_equal(item, &spec_value(omit)?)
                    {
                        continue;
                    }
                    output.insert(key, self.project(child(field)?, item, depth + 1)?);
                }
                Ok(Value::Object(output))
            }
            "object" => {
                let Value::Object(values) = value else {
                    return Err(ContractError::Invalid);
                };
                let mut output = BTreeMap::new();
                for (key, value) in values {
                    output.insert(
                        project_key(key),
                        self.project(
                            schema.get("values").ok_or(ContractError::Definition)?,
                            value,
                            depth + 1,
                        )?,
                    );
                }
                Ok(Value::Object(output))
            }
            "array" => {
                let Value::Array(values) = value else {
                    return Err(ContractError::Invalid);
                };
                Ok(Value::Array(
                    values
                        .iter()
                        .map(|value| {
                            self.project(
                                schema.get("items").ok_or(ContractError::Definition)?,
                                value,
                                depth + 1,
                            )
                        })
                        .collect::<Result<_>>()?,
                ))
            }
            "tagged" => {
                let tag = value_text(member(value, text(schema, "field")?)?)?;
                let choice = schema
                    .get("choices")
                    .and_then(|choices| choices.get(&tag))
                    .ok_or(ContractError::Definition)?;
                self.project(choice, value, depth + 1)
            }
            "union" => {
                for choice in schema
                    .get("choices")
                    .and_then(Spec::as_array)
                    .ok_or(ContractError::Definition)?
                {
                    if self
                        .normalize(choice, Some(value), depth + 1, Form::Normalized)
                        .is_ok()
                    {
                        return self.project(choice, value, depth + 1);
                    }
                }
                Err(ContractError::Invalid)
            }
            "json" => Ok(project_json(value)),
            "bytes" => {
                let Value::Bytes(bytes) = value else {
                    return Err(ContractError::Invalid);
                };
                let text = std::str::from_utf8(bytes).map_err(|_| ContractError::Invalid)?;
                Ok(Value::String(codec::points(text)))
            }
            _ => Ok(value.clone()),
        }
    }

    fn semantic(&self, rule: &str, value: &Value) -> Result<()> {
        match rule {
            "accepted_protocol" => {
                if self
                    .spec
                    .get("protocols")
                    .and_then(Spec::as_array)
                    .ok_or(ContractError::Definition)?
                    .iter()
                    .any(|v| spec_value(v).ok().as_ref() == Some(value))
                {
                    Ok(())
                } else {
                    Err(ContractError::Invalid)
                }
            }
            "descriptor_modes" => {
                let mode = value_text(member(value, "io_mode")?)?;
                for (name, required) in [
                    (
                        "input_chunk_schema",
                        mode == "client_streaming" || mode == "bidirectional",
                    ),
                    (
                        "output_chunk_schema",
                        mode == "server_streaming" || mode == "bidirectional",
                    ),
                ] {
                    if (!matches!(member(value, name)?, Value::Null)) != required {
                        return Err(ContractError::Invalid);
                    }
                }
                if let Value::Object(annotations) = member(value, "annotations")?
                    && let Some(admission) = annotations.get(&codec::points("skulk.admission"))
                {
                    // An extra metadata slot is only a declared cached read,
                    // never an alternate lane for streaming or input-bearing work.
                    if admission != &Value::String(codec::points("metadata")) || mode != "unary" {
                        return Err(ContractError::Invalid);
                    }
                    let Value::Object(schema) = member(value, "input_schema")? else {
                        return Err(ContractError::Invalid);
                    };
                    if schema.len() != 2
                        || schema.get(&codec::points("type"))
                            != Some(&Value::String(codec::points("object")))
                        || schema.get(&codec::points("additionalProperties"))
                            != Some(&Value::Bool(false))
                    {
                        return Err(ContractError::Invalid);
                    }
                }
                Ok(())
            }
            "surface_kind" => {
                let entry = value_text(member(value, "entry")?)?;
                let link = value_text(member(value, "kind")?)? == "link";
                if (link && (entry != "/" || member(value, "websocket")? != &Value::Bool(false)))
                    || (!link && (!entry.starts_with('/') || entry.contains("..")))
                {
                    Err(ContractError::Invalid)
                } else {
                    Ok(())
                }
            }
            "operation_verb" => {
                let op = value_text(member(value, "op")?)?;
                let id = member(value, "operation_id")?;
                let input = member(value, "input")?;
                if ((op == "start" || op == "plan") && matches!(input, Value::Null))
                    || (["status", "cancel", "log"].contains(&op.as_str())
                        && matches!(id, Value::Null))
                    || (op == "list" && (truthy(id) || truthy(input)))
                    || (!["log", "list"].contains(&op.as_str())
                        && !matches!(member(value, "offset")?, Value::Null))
                {
                    Err(ContractError::Invalid)
                } else {
                    Ok(())
                }
            }
            "result_outcome" => {
                if matches!(member(value, "payload")?, Value::Null)
                    == matches!(member(value, "error")?, Value::Null)
                {
                    Err(ContractError::Invalid)
                } else {
                    Ok(())
                }
            }
            "public_surface_link" => {
                if !matches!(member(value, "url")?, Value::Null) {
                    public_link(&value_text(member(value, "url")?)?)?;
                }
                Ok(())
            }
            "serve_address" => unicast_address(&value_text(value)?),
            "startup_protocol" => {
                let protocol = member(member(value, "manifest")?, "protocol")?;
                let Value::Integer(protocol) = protocol else {
                    return Err(ContractError::Invalid);
                };
                if (protocol != "4" && !matches!(member(value, "stream_socket")?, Value::Null))
                    || (protocol != "3"
                        && protocol != "4"
                        && !matches!(member(value, "serve_host")?, Value::Null))
                {
                    return Err(ContractError::Invalid);
                }
                Ok(())
            }
            "unique_surface_reports" => unique(values(value, "surfaces")?, "surface_id"),
            "stream_lifecycle" => stream_lifecycle(value),
            "manifest_contracts" => self.manifest(value),
            // Unknown semantic rules are never equivalent to shape validation.
            _ => Err(ContractError::Definition),
        }
    }

    fn manifest(&self, value: &Value) -> Result<()> {
        let descriptors = values(value, "descriptors")?;
        let surfaces = values(value, "surfaces")?;
        let credentials = values(value, "credential_inputs")?;
        let steward = values(value, "steward")?;
        let dependencies = values(value, "runtime_dependencies")?;
        for (index, dependency) in dependencies.iter().enumerate() {
            if dependencies[..index].contains(dependency)
                || dependency == &Value::String(codec::points("pip"))
            {
                return Err(ContractError::Invalid);
            }
        }
        unique(surfaces, "surface_id")?;
        unique(credentials, "credential_id")?;
        unique(steward, "qualified_id")?;
        let mut ids = std::collections::HashSet::new();
        let mut operations = std::collections::HashSet::new();
        for descriptor in descriptors {
            let id = format!(
                "{}@{}",
                value_text(member(descriptor, "id")?)?,
                value_text(member(descriptor, "version")?)?
            );
            if !ids.insert(id.clone()) {
                return Err(ContractError::Invalid);
            }
            if matches!(member(descriptor, "annotations")?, Value::Object(annotations) if annotations.get(&codec::points("skulk.operation"))==Some(&Value::String(codec::points("1"))))
            {
                operations.insert(id);
            }
            if member(value, "protocol")? != &Value::Integer("4".into())
                && value_text(member(descriptor, "io_mode")?)? != "unary"
            {
                return Err(ContractError::Invalid);
            }
        }
        if member(value, "protocol")? != &Value::Integer("4".into())
            && member(member(value, "bounds")?, "stream_seconds")? != &Value::Float(300.0)
        {
            return Err(ContractError::Invalid);
        }
        let preflight = member(value, "preflight_profile")?;
        if !matches!(preflight, Value::Null)
            && matches!(member(value, "configuration_schema")?, Value::Null)
        {
            return Err(ContractError::Invalid);
        }
        let effect = value_text(member(value, "effect")?)?;
        if preflight == &Value::String(codec::points("runpod-native")) {
            let required = credentials
                .iter()
                .filter(|v| member(v, "required").ok() == Some(&Value::Bool(true)))
                .map(|v| value_text(member(v, "credential_id")?))
                .collect::<Result<std::collections::HashSet<_>>>()?;
            if effect != "governed"
                || descriptors.len() != 1
                || [
                    "witness-rpc",
                    "authority-rpc",
                    "cleanup-ssh-key",
                    "bootstrap-client-key",
                ]
                .iter()
                .any(|id| !required.contains(*id))
            {
                return Err(ContractError::Invalid);
            }
        }
        if !operations.is_empty() && matches!(member(value, "operations")?, Value::Null) {
            return Err(ContractError::Invalid);
        }
        for exposure in steward {
            let id = value_text(member(exposure, "qualified_id")?)?;
            let behavior = value_text(member(exposure, "behavior")?)?;
            let risk = value_text(member(exposure, "risk")?)?;
            let provider = member(exposure, "provider_selection")?;
            if !ids.contains(&id)
                || (behavior == "read" && (effect != "read" || risk != "observation"))
                || (behavior == "lifecycle"
                    && (effect != "governed"
                        || risk != "billable"
                        || matches!(provider, Value::Null)))
                || (behavior == "operation"
                    && (effect != "governed"
                        || risk == "observation"
                        || !operations.contains(&id)
                        || !matches!(provider, Value::Null)))
            {
                return Err(ContractError::Invalid);
            }
        }
        let requirements = value_text(member(value, "skulk_requires")?)?;
        specifier_set(&requirements)?;
        // Nested startup manifests must satisfy the same canonical bound as a
        // top-level manifest; raw/default-complete size is not the projection.
        let schema = self
            .spec
            .get("roots")
            .and_then(|r| r.get("Manifest"))
            .ok_or(ContractError::Definition)?;
        let projected = self.project(schema, value, 0)?;
        let size = codec::encode(&projected)
            .map_err(|_| ContractError::Invalid)?
            .len();
        if size > self.limit("manifest")? {
            return Err(ContractError::Bound);
        }
        // Expanded declarations remain incompatible with older installed
        // readers even though this native qualification profile understands them.
        if (descriptors.len() > 8 || size > 131072) && !extended_reader(&requirements)? {
            return Err(ContractError::Invalid);
        }
        Ok(())
    }
}

fn values<'a>(value: &'a Value, key: &str) -> Result<&'a [Value]> {
    let Value::Array(v) = member(value, key)? else {
        return Err(ContractError::Invalid);
    };
    Ok(v)
}
fn unique(values: &[Value], key: &str) -> Result<()> {
    let mut seen = std::collections::HashSet::new();
    for value in values {
        if !seen.insert(value_text(member(value, key)?)?) {
            return Err(ContractError::Invalid);
        }
    }
    Ok(())
}
fn json_input(value: &Value, depth: usize) -> Result<()> {
    if depth > 200 {
        return Err(ContractError::Bound);
    }
    match value {
        Value::Tuple(_) | Value::Bytes(_) | Value::Model(_, _) => Err(ContractError::Invalid),
        Value::String(v) if v.iter().any(|c| (0xd800..=0xdfff).contains(c)) => {
            Err(ContractError::Invalid)
        }
        Value::Array(v) => {
            for child in v {
                json_input(child, depth + 1)?;
            }
            Ok(())
        }
        Value::Object(v) => {
            for (key, child) in v {
                if key.iter().any(|c| (0xd800..=0xdfff).contains(c)) {
                    return Err(ContractError::Invalid);
                }
                json_input(child, depth + 1)?;
            }
            Ok(())
        }
        _ => Ok(()),
    }
}
fn stream_lifecycle(value: &Value) -> Result<()> {
    let kind = value_text(member(value, "kind")?)?;
    let zero = member(value, "sequence")? == &Value::Integer("0".into());
    let payload = member(value, "payload")?;
    let media = member(value, "media")?;
    let error = member(value, "error")?;
    let content = !matches!(payload, Value::Null) || !matches!(media, Value::Null);
    if (zero && kind != "started")
        || (kind == "started" && (!zero || content || !matches!(error, Value::Null)))
        || (kind == "chunk" && (!content || !matches!(error, Value::Null)))
        || (kind == "completed" && !matches!(error, Value::Null))
        || (["failed", "cancelled"].contains(&kind.as_str())
            && (matches!(error, Value::Null)
                || content
                || (value_text(member(error, "code")?)? == "cancelled") != (kind == "cancelled")))
    {
        Err(ContractError::Invalid)
    } else {
        Ok(())
    }
}
fn scoped_address(value: &str) -> Result<std::net::IpAddr> {
    let address = if let Some((ip, zone)) = value.split_once('%') {
        if zone.is_empty() || zone.contains(['%', '/']) || !ip.contains(':') {
            return Err(ContractError::Invalid);
        }
        ip
    } else {
        value
    };
    address.parse().map_err(|_| ContractError::Invalid)
}
fn unicast_address(value: &str) -> Result<()> {
    // Python 3.13 propagates mapped IPv4 properties. Classify the embedded IP
    // without changing the original serve-address text in canonical projection.
    let ip = scoped_address(value)?.to_canonical();
    if ip.is_loopback() || ip.is_unspecified() || ip.is_multicast() {
        Err(ContractError::Invalid)
    } else {
        Ok(())
    }
}
fn specifier_set(value: &str) -> Result<()> {
    // Python's arbitrary equality admits a non-version token. Keep it separate
    // from the PEP 440 version parser rather than silently narrowing that API.
    let mut regular = Vec::new();
    for part in value.split(',').map(str::trim).filter(|p| !p.is_empty()) {
        if let Some(token) = part.strip_prefix("===") {
            if token
                .trim()
                .chars()
                .any(|char| char.is_whitespace() || matches!(char, ';' | ')'))
            {
                return Err(ContractError::Invalid);
            }
        } else {
            regular.push(part);
        }
    }
    regular
        .join(",")
        .parse::<pep440_rs::VersionSpecifiers>()
        .map(|_| ())
        .map_err(|_| ContractError::Invalid)
}

fn extended_reader(value: &str) -> Result<bool> {
    use pep440_rs::{Operator, Version, VersionSpecifier};
    let floor = "2.0.3"
        .parse::<Version>()
        .map_err(|_| ContractError::Definition)?;
    // Prove an explicit bound instead of sampling a few excluded releases.
    // Arbitrary equality may be non-version text; it then proves no floor.
    for part in value.split(',').map(str::trim).filter(|p| !p.is_empty()) {
        if let Some(exact) = part.strip_prefix("===") {
            if exact.trim().parse::<Version>().is_ok_and(|v| v >= floor) {
                return Ok(true);
            }
            continue;
        }
        let requirement = part
            .parse::<VersionSpecifier>()
            .map_err(|_| ContractError::Invalid)?;
        if matches!(
            requirement.operator(),
            Operator::GreaterThanEqual
                | Operator::GreaterThan
                | Operator::TildeEqual
                | Operator::Equal
                | Operator::EqualStar
        ) && requirement.version() >= &floor
        {
            return Ok(true);
        }
    }
    Ok(false)
}
fn public_link(value: &str) -> Result<()> {
    let cleaned = value
        .chars()
        .filter(|c| !matches!(c, '\t' | '\n' | '\r'))
        .collect::<String>();
    let cleaned = cleaned.trim_start_matches(|c: char| c <= ' ');
    let Some((scheme, rest)) = cleaned.split_once(':') else {
        return Err(ContractError::Invalid);
    };
    if !["http", "https"].contains(&scheme.to_ascii_lowercase().as_str()) {
        return Err(ContractError::Invalid);
    }
    let Some(rest) = rest.strip_prefix("//") else {
        return Err(ContractError::Invalid);
    };
    let end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
    let netloc = &rest[..end];
    let tail = &rest[end..];
    if netloc.is_empty()
        || netloc.contains('@')
        || tail.contains(['?', '#'])
        || value.trim_end().ends_with(['?', '#'])
    {
        return Err(ContractError::Invalid);
    }
    let normalized = netloc
        .chars()
        .filter(|c| !matches!(c, ':' | '?' | '#' | '@'))
        .collect::<String>();
    if normalized
        .nfkc()
        .any(|c| matches!(c, '/' | '?' | '#' | '@' | ':'))
    {
        return Err(ContractError::Invalid);
    }
    if netloc.contains('[') || netloc.contains(']') {
        let Some(host) = netloc.strip_prefix('[') else {
            return Err(ContractError::Invalid);
        };
        let Some((host, port)) = host.split_once(']') else {
            return Err(ContractError::Invalid);
        };
        if !port.is_empty() && !port.starts_with(':') {
            return Err(ContractError::Invalid);
        }
        if let Some(future) = host.strip_prefix('v') {
            let Some((hex, end)) = future.split_once('.') else {
                return Err(ContractError::Invalid);
            };
            if hex.is_empty() || !hex.chars().all(|c| c.is_ascii_hexdigit()) || end.is_empty() {
                return Err(ContractError::Invalid);
            }
        } else {
            if !matches!(scoped_address(host)?, std::net::IpAddr::V6(_)) {
                return Err(ContractError::Invalid);
            }
        }
    }
    Ok(())
}

fn text<'a>(value: &'a Spec, key: &str) -> Result<&'a str> {
    value
        .get(key)
        .and_then(Spec::as_str)
        .ok_or(ContractError::Definition)
}
fn child(value: &Spec) -> Result<&Spec> {
    value.get("schema").ok_or(ContractError::Definition)
}
fn spec_value(value: &Spec) -> Result<Value> {
    codec::parse(&value.to_string()).map_err(|_| ContractError::Definition)
}
fn member<'a>(value: &'a Value, key: &str) -> Result<&'a Value> {
    if let Value::Model(_, value) = value {
        return member(value, key);
    }
    let Value::Object(values) = value else {
        return Err(ContractError::Invalid);
    };
    values
        .get(&codec::points(key))
        .ok_or(ContractError::Invalid)
}
fn value_text(value: &Value) -> Result<String> {
    let Value::String(points) = value else {
        return Err(ContractError::Invalid);
    };
    decoded(points)
}
fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(v) => *v,
        Value::Integer(v) => v != "0",
        Value::Float(v) => *v != 0.0,
        Value::String(v) => !v.is_empty(),
        Value::Array(v) | Value::Tuple(v) => !v.is_empty(),
        Value::Bytes(v) => !v.is_empty(),
        Value::Model(_, value) => truthy(value),
        Value::Object(v) => !v.is_empty(),
    }
}
fn literal_equal(a: &Value, b: &Value) -> bool {
    if a == b {
        return true;
    }
    let number = |v: &Value| match v {
        Value::Bool(v) => Some(if *v { 1.0 } else { 0.0 }),
        Value::Integer(v) => v.parse::<f64>().ok(),
        Value::Float(v) => Some(*v),
        _ => None,
    };
    matches!((number(a),number(b)),(Some(a),Some(b)) if a==b)
}
fn length(schema: &Spec, value: usize) -> Result<()> {
    if schema
        .get("min_length")
        .and_then(Spec::as_u64)
        .is_some_and(|bound| (value as u64) < bound)
        || schema
            .get("max_length")
            .and_then(Spec::as_u64)
            .is_some_and(|bound| (value as u64) > bound)
    {
        Err(ContractError::Invalid)
    } else {
        Ok(())
    }
}
fn decimal_cmp(a: &str, b: &str) -> std::cmp::Ordering {
    let an = a.starts_with('-');
    let bn = b.starts_with('-');
    if an != bn {
        return if an {
            std::cmp::Ordering::Less
        } else {
            std::cmp::Ordering::Greater
        };
    }
    let aa = a.trim_start_matches('-');
    let bb = b.trim_start_matches('-');
    let cmp = aa.len().cmp(&bb.len()).then_with(|| aa.cmp(bb));
    if an { cmp.reverse() } else { cmp }
}
fn integer_bounds(schema: &Spec, value: &str) -> Result<()> {
    for key in ["ge", "gt", "le", "lt"] {
        if let Some(bound) = schema.get(key) {
            let bound = bound.to_string();
            let cmp = decimal_cmp(value, &bound);
            let valid = match key {
                "ge" => cmp.is_ge(),
                "gt" => cmp.is_gt(),
                "le" => cmp.is_le(),
                _ => cmp.is_lt(),
            };
            if !valid {
                return Err(ContractError::Invalid);
            }
        }
    }
    Ok(())
}
fn float_bounds(schema: &Spec, value: f64) -> Result<()> {
    for key in ["ge", "gt", "le", "lt"] {
        if let Some(bound) = schema.get(key).and_then(Spec::as_f64) {
            let valid = match key {
                "ge" => value >= bound,
                "gt" => value > bound,
                "le" => value <= bound,
                _ => value < bound,
            };
            if !valid {
                return Err(ContractError::Invalid);
            }
        }
    }
    Ok(())
}
fn collect_patterns(value: &Spec, patterns: &mut HashMap<String, Regex>) -> Result<()> {
    match value {
        Spec::Object(values) => {
            if let Some(pattern) = values.get("pattern").and_then(Spec::as_str) {
                patterns.insert(
                    pattern.into(),
                    Regex::new(pattern).map_err(|_| ContractError::Definition)?,
                );
            }
            for child in values.values() {
                collect_patterns(child, patterns)?;
            }
        }
        Spec::Array(values) => {
            for child in values {
                collect_patterns(child, patterns)?;
            }
        }
        _ => {}
    }
    Ok(())
}
fn project_key(key: &[u32]) -> Vec<u32> {
    key.iter()
        .flat_map(|point| {
            if (0xd800..=0xdfff).contains(point) {
                vec![0xfffd; 3]
            } else {
                vec![*point]
            }
        })
        .collect()
}
fn project_json(value: &Value) -> Value {
    match value {
        Value::Object(values) => Value::Object(
            values
                .iter()
                .map(|(key, value)| (project_key(key), project_json(value)))
                .collect(),
        ),
        Value::Array(values) => Value::Array(values.iter().map(project_json).collect()),
        _ => value.clone(),
    }
}

fn decoded(points: &[u32]) -> Result<String> {
    points
        .iter()
        .copied()
        .map(char::from_u32)
        .collect::<Option<String>>()
        .ok_or(ContractError::Invalid)
}

fn constructor_budget(value: &Value, remaining: &mut usize, depth: usize) -> Result<()> {
    if depth > 200 {
        return Err(ContractError::Bound);
    }
    let cost = match value {
        Value::String(value) => value.len().checked_mul(4).ok_or(ContractError::Bound)?,
        Value::Integer(value) => value.len(),
        Value::Bytes(value) => value.len(),
        _ => 16,
    };
    *remaining = remaining.checked_sub(cost).ok_or(ContractError::Bound)?;
    if let Value::Integer(_) = value {
        codec::encode(value).map_err(|_| ContractError::Invalid)?;
    }
    if let Value::String(points) = value
        && points.iter().any(|point| *point > 0x10ffff)
    {
        return Err(ContractError::Invalid);
    }
    match value {
        Value::Tuple(values) | Value::Array(values) => {
            for value in values {
                constructor_budget(value, remaining, depth + 1)?;
            }
        }
        Value::Object(values) => {
            for (key, value) in values {
                if key.iter().any(|point| *point > 0x10ffff) {
                    return Err(ContractError::Invalid);
                }
                *remaining = remaining
                    .checked_sub(key.len().checked_mul(4).ok_or(ContractError::Bound)?)
                    .ok_or(ContractError::Bound)?;
                constructor_budget(value, remaining, depth + 1)?;
            }
        }
        Value::Model(name, value) => {
            *remaining = remaining
                .checked_sub(name.len())
                .ok_or(ContractError::Bound)?;
            constructor_budget(value, remaining, depth + 1)?;
        }
        _ => {}
    }
    Ok(())
}
fn structured_json(value: &Value, depth: usize) -> Result<()> {
    if depth > 200 {
        return Err(ContractError::Bound);
    }
    match value {
        Value::Tuple(_) | Value::Bytes(_) | Value::Model(_, _) => Err(ContractError::Invalid),
        Value::Array(values) => {
            for value in values {
                structured_json(value, depth + 1)?;
            }
            Ok(())
        }
        Value::Object(values) => {
            for value in values.values() {
                structured_json(value, depth + 1)?;
            }
            Ok(())
        }
        _ => Ok(()),
    }
}

fn find_model_contract(node: &Spec, name: &str) -> Option<Spec> {
    let mut inner = node;
    while inner.get("type").and_then(Spec::as_str) == Some("checked") {
        inner = inner.get("schema")?;
    }
    if inner.get("type").and_then(Spec::as_str) == Some("model")
        && inner.get("name").and_then(Spec::as_str) == Some(name)
    {
        return Some(node.clone());
    }
    match node {
        Spec::Object(values) => values
            .values()
            .find_map(|child| find_model_contract(child, name)),
        Spec::Array(values) => values
            .iter()
            .find_map(|child| find_model_contract(child, name)),
        _ => None,
    }
}
