//! Resource-scoped static coverage with the pinned producer's helper rules.
//!
//! Predicate validators enter through a pointer in the complete registered
//! document. A detached subschema does not retain its enclosing base URI.

use std::{
    cell::RefCell,
    collections::{BTreeSet, HashMap, HashSet},
    sync::{Arc, OnceLock, Weak},
};

use jsonschema::{Keyword, ValidationError, Validator};
use referencing::{Draft, Registry, Retrieve, Uri};
use serde_json::Value;

use crate::{
    schema::{Compiled, SchemaError},
    schema_alias, schema_legacy, schema_pattern, schema_reference,
    schema_unicode::Projection,
};

type Results = HashMap<(String, usize), bool>;

thread_local! {
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

pub(super) struct Node {
    pub(super) schema: Value,
    pub(super) draft: Draft,
    pub(super) patterns: Vec<Validator>,
    pub(super) order: Vec<String>,
}

/// Owns the immutable registry and validators; keywords hold weak links back.
pub(super) struct Scope {
    keyword: String,
    base: String,
    nodes: HashMap<String, Node>,
    references: HashMap<String, Option<String>>,
    registry: Registry<'static>,
    validators: OnceLock<HashMap<String, Validator>>,
}

struct Coverage {
    scope: Weak<Scope>,
    node: String,
}

struct Noop;

impl<'i> Keyword<'i> for Noop {
    fn validate(&self, _instance: &'i Value) -> Result<(), ValidationError<'i>> {
        Ok(())
    }
    fn is_valid(&self, _instance: &'i Value) -> bool {
        true
    }
}

struct NeverRetrieve;

impl Retrieve for NeverRetrieve {
    fn retrieve(
        &self,
        _uri: &Uri<String>,
    ) -> Result<Value, Box<dyn std::error::Error + Send + Sync>> {
        Err(std::io::Error::new(std::io::ErrorKind::NotFound, "unavailable schema resource").into())
    }
}

#[derive(Default)]
struct Scan {
    legacy: bool,
    scoped: bool,
    unsupported: bool,
}

/// Select closed static modern-resource graphs. Other graphs retain the
/// previous permanently offline path until their separate qualification.
pub(super) fn try_compile(
    definition: &Value,
    projection: &Projection,
) -> Result<Option<Compiled>, SchemaError> {
    let mut scan = Scan::default();
    walk_scan(definition, Draft::Draft202012, &mut scan);
    if !scan.legacy || !scan.scoped || scan.unsupported {
        return Ok(None);
    }

    let keyword = schema_legacy::keyword_name(definition);
    let base = format!("json-schema:///{keyword}/");
    let wrapper_base = format!("json-schema:///{keyword}.predicate");
    let mut schema = definition.clone();
    let original = schema.clone();
    schema_pattern::prepare(&mut schema, projection)?;
    schema_alias::rewrite(&original, &mut schema, projection);
    // Wrapping a reference as an entry point validates only that wrapper's
    // meta-schema. Check the complete producer definition before compiling it.
    jsonschema::meta::validate(&schema).map_err(|_| SchemaError)?;
    let mut nodes = HashMap::new();
    tag(&mut schema, "#", Draft::Draft202012, &keyword, &mut nodes)?;
    let draft = Draft::Draft202012.detect(&schema);
    let Ok(registry) = Registry::new()
        .draft(draft)
        .retriever(NeverRetrieve)
        .add(&base, draft.create_resource(schema.clone()))
        .and_then(|builder| builder.prepare())
    else {
        return Ok(None);
    };

    let resolver = registry.resolver(Uri::parse(base.clone()).map_err(|_| SchemaError)?);
    // The registration URI aliases the document; its own root ID establishes
    // the producer's actual reference scope before any pointer traversal.
    let resolver = resolver
        .in_subresource(draft.create_resource_ref(&schema))
        .map_err(|_| SchemaError)?;
    let mut references = HashMap::new();
    let mut ids = HashSet::new();
    let mut anchors = HashSet::new();
    for (id, node) in &nodes {
        let resolved = resolver.lookup(&encode(id)).map_err(|_| SchemaError)?;
        let uri = resolved.resolver().base_uri().as_str().to_owned();
        if node.schema.get("$id").is_some() && !ids.insert(uri.clone()) {
            return Ok(None);
        }
        if let Some(anchor) = node.schema.get("$anchor").and_then(Value::as_str)
            && !anchors.insert((uri, anchor.to_owned()))
        {
            return Ok(None);
        }
        if let Some(reference) = node.schema.get("$ref").and_then(Value::as_str) {
            let Ok(target) = resolved.resolver().lookup(reference) else {
                return Ok(None);
            };
            let target = match target.contents() {
                Value::Bool(_) => None,
                Value::Object(object) => {
                    let Some(target) = object.get(&keyword).and_then(Value::as_str) else {
                        return Ok(None);
                    };
                    if !nodes.contains_key(target) {
                        return Ok(None);
                    }
                    Some(target.to_owned())
                }
                _ => return Ok(None),
            };
            references.insert(id.clone(), target);
        }
    }
    let edges = references
        .iter()
        .map(|(id, target)| (id.clone(), target.iter().cloned().collect()))
        .collect();
    if has_coverage_cycle(&nodes, &edges) {
        return Ok(None);
    }

    let entry_base = resolver.base_uri().as_str().to_owned();

    let scope = Arc::new(Scope {
        keyword: keyword.clone(),
        base: entry_base,
        nodes,
        references,
        registry,
        validators: OnceLock::new(),
    });
    let weak = Arc::downgrade(&scope);
    let options = crate::schema::options(&schema)
        .with_registry(&scope.registry)
        .with_base_uri(wrapper_base)
        // Keep the original field so its identity, children, metadata and
        // references remain real schemas. The private keyword performs the
        // producer's coverage check for every modern parent in this graph.
        .with_keyword("unevaluatedProperties", |_, _, _| Ok(Box::new(Noop)))
        .with_keyword(keyword, move |_, value, _| {
            let node = value
                .as_str()
                .ok_or_else(|| ValidationError::schema("invalid coverage identity"))?;
            Ok(Box::new(Coverage {
                scope: weak.clone(),
                node: node.to_owned(),
            }))
        });
    let mut validators = HashMap::new();
    // Only coverage predicates need entry-point validators. Compiling every
    // intermediate reference node repeats long chains quadratically.
    for id in predicate_ids(&scope.nodes) {
        let reference = format!("{}{}", scope.base, encode(&id));
        let validator = options
            .build(&serde_json::json!({"$ref":reference}))
            .map_err(|_| SchemaError)?;
        validators.insert(id, validator);
    }
    let main = validators.get("#").ok_or(SchemaError)?.clone();
    scope.validators.set(validators).map_err(|_| SchemaError)?;
    Ok(Some(Compiled {
        main,
        _resources: Some(scope),
        _graph: None,
    }))
}

fn predicate_ids(nodes: &HashMap<String, Node>) -> HashSet<String> {
    let mut ids = HashSet::from(["#".to_owned()]);
    for (id, node) in nodes {
        children(&node.schema, |path, _| {
            if matches!(
                path.as_str(),
                "/additionalProperties" | "/unevaluatedProperties" | "/if"
            ) || ["/allOf/", "/anyOf/", "/oneOf/"]
                .iter()
                .any(|prefix| path.starts_with(prefix))
            {
                ids.insert(format!("{id}{path}"));
            }
        });
    }
    ids
}

fn walk_scan(value: &Value, inherited: Draft, scan: &mut Scan) {
    let draft = inherited.detect(value);
    if !matches!(draft, Draft::Draft201909 | Draft::Draft202012) {
        scan.unsupported = true;
    }
    if let Some(object) = value.as_object() {
        scan.legacy |= draft == Draft::Draft201909 && object.contains_key("unevaluatedProperties");
        scan.scoped |= object.contains_key("$ref") || object.contains_key("$id");
        // A later oneOf candidate is evaluated with the producer's parent
        // resolver after the first match; the runtime graph owns that phase.
        scan.unsupported |= object
            .get("oneOf")
            .and_then(Value::as_array)
            .is_some_and(|branches| branches.iter().any(|branch| branch.get("$id").is_some()));
        scan.unsupported |= [
            "$recursiveRef",
            "$dynamicRef",
            "$recursiveAnchor",
            "$dynamicAnchor",
        ]
        .iter()
        .any(|key| object.contains_key(*key));
    }
    children(value, |_, child| walk_scan(child, draft, scan));
}

pub(super) fn token(value: &str) -> String {
    value.replace('~', "~0").replace('/', "~1")
}

fn encode(value: &str) -> String {
    let pointer = value.strip_prefix('#').unwrap_or(value);
    let encoded: String = pointer
        .bytes()
        .map(|byte| {
            if byte.is_ascii_alphanumeric() || b"-._~/".contains(&byte) {
                char::from(byte).to_string()
            } else {
                format!("%{byte:02X}")
            }
        })
        .collect();
    format!("#{encoded}")
}

pub(super) fn children(value: &Value, mut visit: impl FnMut(String, &Value)) {
    for key in [
        "$defs",
        "definitions",
        "properties",
        "patternProperties",
        "dependentSchemas",
        "dependencies",
    ] {
        if let Some(children) = value.get(key).and_then(Value::as_object) {
            for (name, child) in children {
                if child.is_object() || child.is_boolean() {
                    visit(format!("/{key}/{}", token(name)), child);
                }
            }
        }
    }
    for key in ["allOf", "anyOf", "oneOf", "prefixItems"] {
        if let Some(children) = value.get(key).and_then(Value::as_array) {
            for (index, child) in children.iter().enumerate() {
                visit(format!("/{key}/{index}"), child);
            }
        }
    }
    for key in [
        "additionalProperties",
        "unevaluatedProperties",
        "items",
        "additionalItems",
        "unevaluatedItems",
        "contains",
        "propertyNames",
        "not",
        "if",
        "then",
        "else",
    ] {
        if let Some(child) = value.get(key) {
            if let Some(children) = child.as_array() {
                for (index, child) in children.iter().enumerate() {
                    visit(format!("/{key}/{index}"), child);
                }
            } else if child.is_object() || child.is_boolean() {
                visit(format!("/{key}"), child);
            }
        }
    }
}

pub(super) fn tag(
    value: &mut Value,
    id: &str,
    inherited: Draft,
    keyword: &str,
    nodes: &mut HashMap<String, Node>,
) -> Result<(), SchemaError> {
    let draft = inherited.detect(value);
    let order = value
        .get(keyword)
        .and_then(Value::as_array)
        .map(|keys| {
            keys.iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default();
    let mut paths = Vec::new();
    children(value, |path, _| paths.push(path));
    if let Some(object) = value.as_object_mut() {
        object.insert(keyword.into(), Value::String(id.into()));
    }
    for path in paths {
        let child = value.pointer_mut(&path).ok_or(SchemaError)?;
        tag(child, &format!("{id}{path}"), draft, keyword, nodes)?;
    }
    let mut patterns = Vec::new();
    if let Some(fields) = value.get("patternProperties").and_then(Value::as_object) {
        for pattern in fields.keys() {
            patterns.push(
                jsonschema::options()
                    .offline()
                    .should_validate_formats(false)
                    .with_pattern_options(
                        jsonschema::PatternOptions::fancy_regex().backtrack_limit(100_000),
                    )
                    .build(&serde_json::json!({"pattern":pattern}))
                    .map_err(|_| SchemaError)?,
            );
        }
    }
    nodes.insert(
        id.into(),
        Node {
            schema: value.clone(),
            draft,
            patterns,
            order,
        },
    );
    Ok(())
}

fn coverage_edges(id: &str, node: &Node, references: &HashMap<String, Vec<String>>) -> Vec<String> {
    let mut result = Vec::new();
    if let Some(targets) = references.get(id) {
        result.extend(targets.iter().cloned());
    }
    children(&node.schema, |path, _| {
        if ["/allOf/", "/anyOf/", "/oneOf/", "/dependentSchemas/"]
            .iter()
            .any(|prefix| path.starts_with(prefix))
            || matches!(path.as_str(), "/if" | "/then" | "/else" | "/not")
        {
            result.push(format!("{id}{path}"));
        }
    });
    result
}

pub(super) fn has_coverage_cycle(
    nodes: &HashMap<String, Node>,
    references: &HashMap<String, Vec<String>>,
) -> bool {
    let mut done = HashSet::new();
    for root in nodes.keys() {
        let mut active = HashSet::new();
        let mut pending = vec![(root.clone(), false)];
        while let Some((id, exiting)) = pending.pop() {
            if exiting {
                active.remove(&id);
                done.insert(id);
                continue;
            }
            if done.contains(&id) {
                continue;
            }
            if !active.insert(id.clone()) {
                return true;
            }
            pending.push((id.clone(), true));
            if let Some(node) = nodes.get(&id) {
                for child in coverage_edges(&id, node, references) {
                    pending.push((child, false));
                }
            }
        }
    }
    false
}

impl Scope {
    #[cfg(test)]
    pub(super) fn predicate_count(&self) -> usize {
        self.validators.get().unwrap().len()
    }

    fn valid(&self, id: &str, instance: &Value) -> Result<bool, SchemaError> {
        let key = (id.to_owned(), std::ptr::from_ref(instance) as usize);
        if let Some(valid) = RESULTS.with(|cache| {
            cache
                .borrow()
                .as_ref()
                .and_then(|cache| cache.get(&key).copied())
        }) {
            return Ok(valid);
        }
        let validator = self
            .validators
            .get()
            .and_then(|validators| validators.get(id))
            .ok_or(SchemaError)?;
        let valid = validator.is_valid(instance);
        RESULTS.with(|cache| {
            if let Some(cache) = cache.borrow_mut().as_mut() {
                cache.insert(key, valid);
            }
        });
        Ok(valid)
    }

    fn collect(
        &self,
        root: &str,
        legacy: bool,
        instance: &Value,
        names: &mut BTreeSet<String>,
    ) -> Result<(), SchemaError> {
        let Some(object) = instance.as_object() else {
            return Ok(());
        };
        let mut pending = vec![root.to_owned()];
        let mut seen = HashSet::new();
        while let Some(id) = pending.pop() {
            if !seen.insert(id.clone()) {
                continue;
            }
            let node = self.nodes.get(&id).ok_or(SchemaError)?;
            let Some(schema) = node.schema.as_object() else {
                continue;
            };
            if let Some(Some(target)) = self.references.get(&id) {
                pending.push(target.clone());
            }
            if let Some(properties) = schema.get("properties").and_then(Value::as_object) {
                names.extend(
                    properties
                        .keys()
                        .filter(|key| object.contains_key(*key))
                        .cloned(),
                );
            }
            for key in ["additionalProperties", "unevaluatedProperties"] {
                if let Some(subschema) = schema.get(key) {
                    if legacy {
                        if subschema == &Value::Bool(true) {
                            names.extend(object.keys().cloned());
                        } else if let Some(fields) = subschema.as_object() {
                            names.extend(
                                fields
                                    .keys()
                                    .filter(|key| {
                                        *key != &self.keyword && object.contains_key(*key)
                                    })
                                    .cloned(),
                            );
                        }
                    } else {
                        let child = format!("{id}/{key}");
                        for (key, value) in object {
                            if self.valid(&child, value)? {
                                names.insert(key.clone());
                            }
                        }
                    }
                }
            }
            for key in object.keys() {
                if node
                    .patterns
                    .iter()
                    .any(|pattern| pattern.is_valid(&Value::String(key.clone())))
                {
                    names.insert(key.clone());
                }
            }
            if let Some(dependencies) = schema.get("dependentSchemas").and_then(Value::as_object) {
                for key in dependencies.keys() {
                    if object.contains_key(key) {
                        pending.push(format!("{id}/dependentSchemas/{}", token(key)));
                    }
                }
            }
            for key in ["allOf", "anyOf", "oneOf"] {
                if let Some(children) = schema.get(key).and_then(Value::as_array) {
                    for index in 0..children.len() {
                        let child = format!("{id}/{key}/{index}");
                        if self.valid(&child, instance)? {
                            pending.push(child);
                        }
                    }
                }
            }
            if schema.contains_key("if") {
                let tested = format!("{id}/if");
                if self.valid(&tested, instance)? {
                    pending.push(tested);
                    if schema.contains_key("then") {
                        pending.push(format!("{id}/then"));
                    }
                } else if schema.contains_key("else") {
                    pending.push(format!("{id}/else"));
                }
            }
        }
        Ok(())
    }

    fn accepts(&self, id: &str, instance: &Value) -> Result<bool, SchemaError> {
        let node = self.nodes.get(id).ok_or(SchemaError)?;
        if node.schema.get("unevaluatedProperties").is_none() {
            return Ok(true);
        }
        let Some(object) = instance.as_object() else {
            return Ok(true);
        };
        let mut names = BTreeSet::new();
        self.collect(id, node.draft == Draft::Draft201909, instance, &mut names)?;
        for (key, value) in object {
            if !names.contains(key) && !self.valid(&format!("{id}/unevaluatedProperties"), value)? {
                return Ok(false);
            }
        }
        Ok(true)
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
        match self
            .scope
            .upgrade()
            .ok_or(SchemaError)
            .and_then(|scope| scope.accepts(&self.node, instance))
        {
            Ok(valid) => valid,
            Err(_) => {
                schema_reference::refuse();
                false
            }
        }
    }
}
