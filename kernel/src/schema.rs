//! Offline generic payload validation shared by execution and qualification.

use std::{cell::RefCell, collections::HashMap, str::FromStr};

use jsonschema::{Keyword, ValidationError};
use serde_json::{Number, Value};
use thiserror::Error;

use crate::codec;
use crate::schema_unicode::{Projection, has_surrogate};

const MAX_JSON_BYTES: usize = 65_536;

thread_local! {
    // Scoped to one projected definition. Repeated branch predicates share the
    // compiled native nodes instead of expanding nested coverage exponentially.
    static FRAGMENTS: RefCell<Option<HashMap<String, jsonschema::Validator>>> = const { RefCell::new(None) };
}

struct Compilation(Option<HashMap<String, jsonschema::Validator>>);

impl Compilation {
    fn enter() -> Self {
        Self(FRAGMENTS.with(|cache| cache.replace(Some(HashMap::new()))))
    }
}

impl Drop for Compilation {
    fn drop(&mut self) {
        FRAGMENTS.with(|cache| cache.replace(self.0.take()));
    }
}

/// Safe schema failures never include schema, payload or credential contents.
#[derive(Debug, Error)]
#[error("invalid payload schema")]
pub struct SchemaError;

/// Compiled result schema; format remains annotation as in the Python SDK.
pub struct Schema {
    validator: Compiled,
    definition: codec::Value,
    order: codec::ObjectOrders,
    projected: bool,
}

impl Schema {
    /// Compile without permitting file or network reference retrieval.
    pub fn new(source: &str) -> Result<Self, SchemaError> {
        Self::from_source(source, false)
    }

    /// Compile the installed owner's explicit Draft 2020-12 root contract.
    ///
    /// The source contract and its canonical revision remain untouched. Only
    /// this validator's private definition uses the fixed root dialect; nested
    /// resources keep their own declared dialects. Metadata has a separate
    /// envelope from the ordinary payload/wire limit.
    pub fn installed(source: &str) -> Result<Self, SchemaError> {
        Self::from_source(source, true)
    }

    fn from_source(source: &str, installed: bool) -> Result<Self, SchemaError> {
        let limit = if installed { 262_144 } else { MAX_JSON_BYTES };
        if source.len() > limit {
            return Err(SchemaError);
        }
        let (mut definition, order) = codec::parse_with_order(source).map_err(|_| SchemaError)?;
        if installed && let codec::Value::Object(fields) = &mut definition {
            let keyword = codec::points("$schema");
            // Replacing an ill-typed declaration would conceal a meta-schema
            // failure. The root selector is fixed by the owner, not the author.
            if fields
                .get(&keyword)
                .is_some_and(|value| !matches!(value, codec::Value::String(_)))
            {
                return Err(SchemaError);
            }
            fields.insert(
                keyword,
                codec::Value::String(codec::points(
                    "https://json-schema.org/draft/2020-12/schema",
                )),
            );
        }
        let projection = Projection::new(&definition, None)?;
        let validator = compile(&definition, &projection, &order)?;
        // An unavoidable missing root reference cannot validate any result.
        // Refuse it before a runner acquires ownership or submits backend work;
        // unused definitions and guarded branches remain lazy.
        if let codec::Value::Object(fields) = &definition
            && ["$ref", "$recursiveRef", "$dynamicRef"]
                .iter()
                .any(|key| fields.contains_key(&codec::points(key)))
            && crate::schema_reference::unavailable(&validator.main, &Value::Null)
        {
            return Err(SchemaError);
        }
        let projected = has_surrogate(&definition);
        Ok(Self {
            validator,
            definition,
            order,
            projected,
        })
    }

    /// Test one finite JSON payload without invoking any external retriever.
    pub fn matches(&self, source: &str) -> bool {
        if source.len() > MAX_JSON_BYTES {
            return false;
        }
        let Ok(value) = codec::parse(source) else {
            return false;
        };
        let Ok(projection) = Projection::new(&self.definition, Some(&value)) else {
            return false;
        };
        let Ok(instance) = json_value(&value, &projection, 0) else {
            return false;
        };
        if self.projected || has_surrogate(&value) {
            compile(&self.definition, &projection, &self.order)
                .is_ok_and(|validator| crate::schema_reference::matches(&validator.main, &instance))
        } else {
            crate::schema_reference::matches(&self.validator.main, &instance)
        }
    }
}

pub(super) struct Compiled {
    pub(super) main: jsonschema::Validator,
    // Keywords keep only weak references to their shared validator table.
    // This owner prevents both premature release and a validator/keyword cycle.
    pub(super) _resources: Option<std::sync::Arc<crate::schema_scoped::Scope>>,
    pub(super) _graph: Option<std::sync::Arc<crate::schema_graph::Scope>>,
}

fn compile(
    definition: &codec::Value,
    projection: &Projection,
    order: &codec::ObjectOrders,
) -> Result<Compiled, SchemaError> {
    let schema = json_value(definition, projection, 0)?;
    if let Some(compiled) = crate::schema_scoped::try_compile(&schema, projection)? {
        return Ok(compiled);
    }
    if let Some(compiled) = crate::schema_graph::try_compile(&schema, projection, order)? {
        return Ok(compiled);
    }
    let _compilation = Compilation::enter();
    Ok(Compiled {
        main: compile_fragment(&schema, projection, false)?,
        _resources: None,
        _graph: None,
    })
}

/// Compile an already projected fragment with the same native compatibility
/// rules. Legacy coverage predicates inherit their containing resource's draft.
pub(super) fn compile_fragment(
    definition: &Value,
    projection: &Projection,
    legacy: bool,
) -> Result<jsonschema::Validator, SchemaError> {
    let key = format!(
        "{legacy}:{}",
        serde_json::to_string(definition).map_err(|_| SchemaError)?
    );
    if let Some(validator) = FRAGMENTS.with(|cache| {
        cache
            .borrow()
            .as_ref()
            .and_then(|cache| cache.get(&key).cloned())
    }) {
        return Ok(validator);
    }
    let mut schema = definition.clone();
    if legacy && let Some(object) = schema.as_object_mut() {
        object.entry("$schema").or_insert_with(|| {
            Value::String("https://json-schema.org/draft/2019-09/schema".into())
        });
    }
    let legacy_keyword = crate::schema_legacy::keyword_name(&schema);
    crate::schema_legacy::prepare(&mut schema, false, &legacy_keyword);
    let original = schema.clone();
    crate::schema_pattern::prepare(&mut schema, projection)?;
    crate::schema_alias::rewrite(&original, &mut schema, projection);
    let legacy_projection = projection.clone();
    let validator = options(&schema)
        .with_keyword(legacy_keyword, move |_, value, _| {
            crate::schema_legacy::Coverage::new(value, &legacy_projection)
                .map(|coverage| Box::new(coverage) as Box<dyn for<'i> Keyword<'i>>)
                .map_err(|_| ValidationError::schema("invalid legacy property coverage"))
        })
        .build(&schema)
        .map_err(|_| SchemaError)?;
    FRAGMENTS.with(|cache| {
        if let Some(cache) = cache.borrow_mut().as_mut() {
            cache.insert(key, validator.clone());
        }
    });
    Ok(validator)
}

/// Every scope and standalone predicate uses the same numeric/regex policy.
pub(super) fn options(schema: &Value) -> jsonschema::ValidationOptions<'static> {
    crate::schema_reference::options(schema)
        .should_validate_formats(false)
        .with_pattern_options(jsonschema::PatternOptions::fancy_regex().backtrack_limit(100_000))
        .with_keyword("multipleOf", |_, divisor, _| {
            let validator = if let Some(number) = divisor.as_number()
                && number.is_f64()
            {
                Multiple::Float(
                    number
                        .as_f64()
                        .filter(|number| number.is_finite() && *number > 0.0)
                        .ok_or_else(|| ValidationError::schema("invalid numeric divisor"))?,
                )
            } else {
                Multiple::Integer(
                    jsonschema::options()
                        .offline()
                        .build(&serde_json::json!({"multipleOf": divisor}))?,
                )
            };
            Ok(Box::new(validator))
        })
}

/// Preserve the authoring envelope's 200-level limit without serde's 128-level
/// parser ceiling. The lossless codec already checks finite numbers and keys.
fn json_value(
    value: &codec::Value,
    projection: &Projection,
    depth: usize,
) -> Result<Value, SchemaError> {
    if depth > 200 {
        return Err(SchemaError);
    }
    Ok(match value {
        codec::Value::Null => Value::Null,
        codec::Value::Bool(value) => Value::Bool(*value),
        codec::Value::Integer(value) => {
            Value::Number(Number::from_str(value).map_err(|_| SchemaError)?)
        }
        codec::Value::Float(value) => Value::Number(Number::from_f64(*value).ok_or(SchemaError)?),
        codec::Value::String(value) => Value::String(projection.text(value)?),
        codec::Value::Array(values) => Value::Array(
            values
                .iter()
                .map(|v| json_value(v, projection, depth + 1))
                .collect::<Result<_, _>>()?,
        ),
        codec::Value::Object(values) => Value::Object(
            values
                .iter()
                .map(|(k, v)| Ok((projection.text(k)?, json_value(v, projection, depth + 1)?)))
                .collect::<Result<_, SchemaError>>()?,
        ),
        _ => return Err(SchemaError),
    })
}

enum Multiple {
    Float(f64),
    Integer(jsonschema::Validator),
}

impl<'i> Keyword<'i> for Multiple {
    fn validate(&self, instance: &'i Value) -> Result<(), ValidationError<'i>> {
        if self.is_valid(instance) {
            Ok(())
        } else {
            Err(ValidationError::custom("numeric multiple refused"))
        }
    }

    fn is_valid(&self, instance: &'i Value) -> bool {
        if !instance.is_number() {
            return true;
        }
        match self {
            Self::Integer(validator) => validator.is_valid(instance),
            Self::Float(divisor) => instance.as_f64().is_some_and(|number| {
                let quotient = number / divisor;
                if quotient.is_finite() {
                    quotient.fract() == 0.0
                } else {
                    number.is_finite() && number % divisor == 0.0
                }
            }),
        }
    }
}

#[cfg(test)]
mod scoped_ownership_tests {
    use super::Schema;
    use std::sync::Arc;

    #[test]
    fn dropping_the_schema_releases_the_shared_resource_table() {
        let schema = Schema::new(r##"{"$schema":"https://json-schema.org/draft/2019-09/schema","$defs":{"v":{"unevaluatedProperties":{"type":"integer"}}},"$ref":"#/$defs/v","unevaluatedProperties":false}"##).unwrap();
        let scope = Arc::downgrade(schema.validator._resources.as_ref().unwrap());
        assert!(schema.matches(r#"{"type":"s"}"#));
        drop(schema);
        assert!(scope.upgrade().is_none());
    }

    #[test]
    fn a_flat_reference_chain_compiles_only_actual_predicates() {
        let mut definitions = serde_json::Map::new();
        for index in 0..64 {
            let value = if index == 63 {
                serde_json::json!({"additionalProperties":{"type":"integer"}})
            } else {
                serde_json::json!({"$ref":format!("#/$defs/v{}", index + 1)})
            };
            definitions.insert(format!("v{index}"), value);
        }
        let source = serde_json::json!({"$schema":"https://json-schema.org/draft/2019-09/schema",
            "$defs":definitions,"$ref":"#/$defs/v0","unevaluatedProperties":false});
        let schema = Schema::new(&source.to_string()).unwrap();
        assert!(schema.matches(r#"{"type":1}"#));
        assert!(!schema.matches(r#"{"x":1}"#));
        let scope = schema.validator._resources.as_ref().unwrap();
        assert_eq!(scope.predicate_count(), 3);
    }

    #[test]
    fn dropping_a_recursive_schema_releases_its_graph() {
        let schema = Schema::new(r##"{"$schema":"https://json-schema.org/draft/2019-09/schema","$recursiveAnchor":true,"properties":{"child":{"$recursiveRef":"#"}},"unevaluatedProperties":false}"##).unwrap();
        let graph = Arc::downgrade(schema.validator._graph.as_ref().unwrap());
        assert!(schema.matches(r#"{"child":{"child":{}}}"#));
        drop(schema);
        assert!(graph.upgrade().is_none());
    }
}
