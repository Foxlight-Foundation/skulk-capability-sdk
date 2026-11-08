//! Runtime reference scopes for the producer's recursive property coverage.
//!
//! Native validators handle ordinary predicates. References enter another
//! immutable node through this owner, carrying the real resolver rather than
//! compiling a detached fragment with an invented dynamic scope.

use std::{
    cell::RefCell,
    collections::{BTreeMap, BTreeSet, HashMap, HashSet},
    sync::{Arc, OnceLock, Weak},
};

use jsonschema::{Keyword, ValidationError, Validator};
use referencing::{Draft, Resolver, Uri};
use serde_json::Value;

use crate::{
    schema::{Compiled, SchemaError},
    schema_alias, schema_legacy, schema_pattern, schema_reference,
    schema_scoped::{Node, children, has_coverage_cycle, tag, token},
    schema_unicode::Projection,
};

#[derive(Clone)]
struct Context {
    base: String,
    // Nearest to outermost, exactly as the referencing resolver exposes it.
    previous: Vec<String>,
}

impl Context {
    fn capture(resolver: &Resolver<'_>) -> Self {
        Self {
            base: resolver.base_uri().as_str().to_owned(),
            previous: resolver
                .dynamic_scope()
                .iter()
                .map(|uri| uri.as_str().to_owned())
                .collect(),
        }
    }

    fn resolver<'a>(&self, scope: &'a Scope) -> Result<Resolver<'a>, SchemaError> {
        let mut path = self.previous.iter().rev();
        let base = path.next().unwrap_or(&self.base);
        let mut resolver = scope
            .registry
            .resolver(Uri::parse(base.clone()).map_err(|_| SchemaError)?);
        for uri in path.chain((!self.previous.is_empty()).then_some(&self.base)) {
            resolver = resolver
                .lookup(uri)
                .map_err(|_| SchemaError)?
                .resolver()
                .clone();
        }
        Ok(resolver)
    }
}

#[derive(Clone)]
struct Frame {
    owner: usize,
    node: String,
    context: Context,
}

/// Only effective scope bindings affect references, not repeated URI visits.
#[derive(Hash, PartialEq, Eq, Clone)]
struct Bindings {
    base: String,
    dynamic: BTreeMap<String, String>,
    recursive: Option<String>,
}

#[derive(Hash, PartialEq, Eq, Clone)]
struct Visit {
    owner: usize,
    node: String,
    instance: usize,
    scalar: Option<String>,
    collect_errors: bool,
    bindings: Bindings,
}

#[derive(Default)]
struct State {
    frames: Vec<Frame>,
    active: HashSet<Visit>,
    results: HashMap<Visit, bool>,
}

thread_local! {
    static STATE: RefCell<State> = RefCell::new(State::default());
}

pub(super) struct Evaluation(State);

impl Evaluation {
    pub(super) fn enter() -> Self {
        Self(STATE.with(|state| state.replace(State::default())))
    }
}

impl Drop for Evaluation {
    fn drop(&mut self) {
        STATE.with(|state| state.replace(std::mem::take(&mut self.0)));
    }
}

struct Resource {
    dynamic: BTreeMap<String, String>,
    recursive: bool,
}

pub(super) struct Scope {
    keyword: String,
    root: Context,
    nodes: HashMap<String, Node>,
    resources: HashMap<String, Resource>,
    registry: referencing::Registry<'static>,
    validators: OnceLock<HashMap<String, HashMap<String, Validator>>>,
}

struct Entry(Weak<Scope>);
struct Proxy {
    scope: Weak<Scope>,
    node: String,
    enter: bool,
}

struct Noop;
impl<'i> Keyword<'i> for Noop {
    fn validate(&self, _: &'i Value) -> Result<(), ValidationError<'i>> {
        Ok(())
    }
    fn is_valid(&self, _: &'i Value) -> bool {
        true
    }
}

fn modern(value: &Value, inherited: Draft, references: &mut bool) -> bool {
    let draft = inherited.detect(value);
    if !matches!(draft, Draft::Draft201909 | Draft::Draft202012) {
        return false;
    }
    if let Some(object) = value.as_object() {
        *references |= ["$ref", "$dynamicRef", "$recursiveRef"]
            .iter()
            .any(|key| object.contains_key(*key));
    }
    let mut accepted = true;
    children(value, |_, child| {
        accepted &= modern(child, draft, references);
    });
    accepted
}

/// Compile modern graphs that were not selected by the qualified static path.
pub(super) fn try_compile(
    definition: &Value,
    projection: &Projection,
    order: &crate::codec::ObjectOrders,
) -> Result<Option<Compiled>, SchemaError> {
    let mut references = false;
    if !modern(definition, Draft::Draft202012, &mut references) || !references {
        return Ok(None);
    }
    let keyword = schema_legacy::keyword_name(definition);
    let base = format!("json-schema:///{keyword}/");
    let mut schema = definition.clone();
    schema_pattern::prepare(&mut schema, projection)?;
    schema_alias::rewrite(definition, &mut schema, projection);
    jsonschema::meta::validate(&schema).map_err(|_| SchemaError)?;
    // Record source keyword order on schema nodes before regex pointer names
    // are lowered. This private annotation is replaced by the ordinary tag.
    let mut ordered = definition.clone();
    install_order(&mut ordered, order, projection, &keyword)?;
    schema_pattern::prepare(&mut ordered, projection)?;
    schema_alias::rewrite(definition, &mut ordered, projection);
    schema = ordered;
    let mut nodes = HashMap::new();
    tag(&mut schema, "#", Draft::Draft202012, &keyword, &mut nodes)?;
    let registry = schema_reference::registry(&schema, &base)?;
    let draft = Draft::Draft202012.detect(&schema);
    let resolver = registry
        .resolver(Uri::parse(base).map_err(|_| SchemaError)?)
        .in_subresource(draft.create_resource_ref(&schema))
        .map_err(|_| SchemaError)?;
    let root = Context::capture(&resolver);
    let mut resources = HashMap::<String, Resource>::new();
    let mut ids = HashSet::new();
    let mut anchors = HashSet::new();
    let mut edges = HashMap::new();
    let mut properties = false;
    let mut runtime_references = false;
    for (id, node) in &nodes {
        // Pointer traversal retains the root's lexical scope for this scan.
        let resolved = resolver.lookup(&encode(id)).map_err(|_| SchemaError)?;
        let uri = resolved.resolver().base_uri().as_str().to_owned();
        properties |= node.schema.get("unevaluatedProperties").is_some()
            || node.schema.get("unevaluatedItems").is_some();
        for kind in ["$ref", "$dynamicRef", "$recursiveRef"] {
            if (kind == "$dynamicRef" && node.draft == Draft::Draft201909)
                || (kind == "$recursiveRef" && node.draft == Draft::Draft202012)
            {
                continue;
            }
            if let Some(reference) = node.schema.get(kind).and_then(Value::as_str) {
                runtime_references |= kind == "$dynamicRef" || kind == "$recursiveRef";
                let target = if kind == "$recursiveRef" {
                    resolved.resolver().lookup_recursive_ref()
                } else {
                    resolved.resolver().lookup(reference)
                };
                if let Ok(target) = target {
                    let identity = target.contents().get(&keyword).and_then(Value::as_str);
                    edges
                        .entry(id.clone())
                        .or_insert_with(Vec::new)
                        .extend(identity.map(str::to_owned));
                }
            }
        }
        if node.schema.get("$id").is_some() && !ids.insert(uri.clone()) {
            return Ok(None);
        }
        let recursive = resolved
            .resolver()
            .lookup(&uri)
            .map_err(|_| SchemaError)?
            .contents()
            .get("$recursiveAnchor")
            == Some(&Value::Bool(true));
        let resource = resources.entry(uri.clone()).or_insert_with(|| Resource {
            dynamic: BTreeMap::new(),
            recursive,
        });
        for name in ["$anchor", "$dynamicAnchor"] {
            if let Some(anchor) = node.schema.get(name).and_then(Value::as_str) {
                if !anchors.insert((uri.clone(), name, anchor.to_owned())) {
                    return Ok(None);
                }
                if name == "$dynamicAnchor" && node.draft == Draft::Draft202012 {
                    resource.dynamic.insert(anchor.to_owned(), id.clone());
                }
            }
        }
    }
    // Retain native annotation propagation for reference-only graphs without
    // property/item coverage or a nonproductive coverage cycle.
    if !properties && !runtime_references && !has_coverage_cycle(&nodes, &edges) {
        return Ok(None);
    }
    let scope = Arc::new(Scope {
        keyword: keyword.clone(),
        root,
        nodes,
        resources,
        registry,
        validators: OnceLock::new(),
    });
    let mut validators = HashMap::new();
    for (id, node) in &scope.nodes {
        let mut predicates = HashMap::new();
        for key in &node.order {
            if special(key, node.draft) {
                continue;
            }
            let (component, siblings) = component(id, node, key, &keyword)?;
            let weak = Arc::downgrade(&scope);
            let mut options = crate::schema::options(&component)
                .with_draft(node.draft)
                .with_keyword(keyword.clone(), move |_, value, _| {
                    Ok(Box::new(Proxy {
                        scope: weak.clone(),
                        node: value
                            .get("node")
                            .and_then(Value::as_str)
                            .ok_or_else(|| ValidationError::schema("invalid predicate identity"))?
                            .to_owned(),
                        enter: value.get("enter").and_then(Value::as_bool).unwrap_or(true),
                    }))
                });
            for sibling in siblings {
                options = options.with_keyword(sibling, |_, _, _| Ok(Box::new(Noop)));
            }
            predicates.insert(
                key.clone(),
                options.build(&component).map_err(|_| SchemaError)?,
            );
        }
        validators.insert(id.clone(), predicates);
    }
    scope.validators.set(validators).map_err(|_| SchemaError)?;
    let weak = Arc::downgrade(&scope);
    let main = jsonschema::options()
        .with_keyword(keyword.clone(), move |_, _, _| {
            Ok(Box::new(Entry(weak.clone())))
        })
        .build(&serde_json::json!({keyword: true}))
        .map_err(|_| SchemaError)?;
    Ok(Some(Compiled {
        main,
        _resources: None,
        _graph: Some(scope),
    }))
}

// The producer checks the complete definition against its root meta-schema,
// then evolves validators without re-checking a target resource's meta-schema.
// Reserved annotations from another draft must not be reinterpreted by the
// standalone predicate compiler. The registry and coverage keep the original.
fn special(key: &str, draft: Draft) -> bool {
    key.starts_with('$')
        || matches!(
            key,
            "unevaluatedProperties" | "unevaluatedItems" | "then" | "else" | "oneOf" | "anyOf"
        )
        || (draft == Draft::Draft201909 && key == "prefixItems")
}

fn install_order(
    schema: &mut Value,
    order: &crate::codec::ObjectOrders,
    projection: &Projection,
    keyword: &str,
) -> Result<(), SchemaError> {
    let mut projected = HashMap::new();
    for (path, keys) in order {
        let mut pointer = String::new();
        for part in path {
            pointer.push('/');
            pointer.push_str(&token(&projection.text(part)?));
        }
        projected.insert(
            pointer,
            Value::Array(
                keys.iter()
                    .map(|key| projection.text(key).map(Value::String))
                    .collect::<Result<_, _>>()?,
            ),
        );
    }
    fn walk(value: &mut Value, path: &str, keyword: &str, order: &HashMap<String, Value>) {
        let mut paths = Vec::new();
        children(value, |path, _| paths.push(path));
        if let Some(object) = value.as_object_mut() {
            object.insert(
                keyword.to_owned(),
                order
                    .get(path)
                    .cloned()
                    .unwrap_or_else(|| serde_json::json!([])),
            );
        }
        for child in paths {
            if let Some(value) = value.pointer_mut(&child) {
                walk(value, &format!("{path}{child}"), keyword, order);
            }
        }
    }
    walk(schema, "", keyword, &projected);
    Ok(())
}

fn component(
    id: &str,
    node: &Node,
    key: &str,
    keyword: &str,
) -> Result<(Value, Vec<&'static str>), SchemaError> {
    let mut whole = node.schema.clone();
    let mut paths = Vec::new();
    children(&whole, |path, _| paths.push(path));
    for path in paths {
        let enter = !matches!(path.as_str(), "/if" | "/contains" | "/not");
        *whole.pointer_mut(&path).ok_or(SchemaError)? =
            serde_json::json!({keyword: {"node":format!("{id}{path}"), "enter":enter}});
    }
    let mut fields = serde_json::Map::new();
    fields.insert(key.to_owned(), whole.get(key).ok_or(SchemaError)?.clone());
    let (context, suppressed): (&[&str], &[&'static str]) = match key {
        "additionalProperties" => (
            &["properties", "patternProperties"],
            &["properties", "patternProperties"],
        ),
        "items" if node.draft == Draft::Draft202012 => (&["prefixItems"], &["prefixItems"]),
        "additionalItems" => (&["items"], &["items"]),
        "if" => (&["then", "else"], &[]),
        "contains" => (&["minContains", "maxContains"], &[]),
        _ => (&[], &[]),
    };
    for sibling in context {
        if let Some(value) = whole.get(*sibling) {
            fields.insert((*sibling).to_owned(), value.clone());
        }
    }
    Ok((Value::Object(fields), suppressed.to_vec()))
}

fn encode(id: &str) -> String {
    let encoded: String = id
        .strip_prefix('#')
        .unwrap_or(id)
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

impl Scope {
    fn owner(&self) -> usize {
        std::ptr::from_ref(self) as usize
    }

    fn bindings(&self, context: &Context) -> Bindings {
        let mut dynamic = BTreeMap::new();
        for uri in std::iter::once(&context.base).chain(&context.previous) {
            if let Some(resource) = self.resources.get(uri) {
                dynamic.extend(resource.dynamic.clone());
            }
        }
        let mut recursive = None;
        for uri in &context.previous {
            if !self.resources.get(uri).is_some_and(|r| r.recursive) {
                break;
            }
            recursive = Some(uri.clone());
        }
        Bindings {
            base: context.base.clone(),
            dynamic,
            recursive,
        }
    }

    fn visit(&self, id: &str, instance: &Value, context: &Context) -> Visit {
        Visit {
            owner: self.owner(),
            node: id.to_owned(),
            instance: std::ptr::from_ref(instance) as usize,
            // propertyNames validators lend successive temporary strings from
            // the same address. Scalar contents must distinguish those visits.
            scalar: (!instance.is_object() && !instance.is_array()).then(|| instance.to_string()),
            collect_errors: false,
            bindings: self.bindings(context),
        }
    }

    fn valid(&self, id: &str, instance: &Value, context: &Context) -> Result<bool, SchemaError> {
        self.valid_mode(id, instance, context, false)
    }

    fn valid_mode(
        &self,
        id: &str,
        instance: &Value,
        context: &Context,
        collect_errors: bool,
    ) -> Result<bool, SchemaError> {
        let mut key = self.visit(id, instance, context);
        key.collect_errors = collect_errors;
        if let Some(valid) = STATE.with(|state| state.borrow().results.get(&key).copied()) {
            return Ok(valid);
        }
        if !STATE.with(|state| state.borrow_mut().active.insert(key.clone())) {
            return Err(SchemaError);
        }
        STATE.with(|state| {
            state.borrow_mut().frames.push(Frame {
                owner: self.owner(),
                node: id.to_owned(),
                context: context.clone(),
            });
        });
        let valid = self.run(id, instance, context, collect_errors);
        STATE.with(|state| {
            let mut state = state.borrow_mut();
            state.frames.pop();
            state.active.remove(&key);
            if let Ok(value) = valid {
                state.results.insert(key, value);
            }
        });
        valid
    }

    fn run(
        &self,
        id: &str,
        instance: &Value,
        context: &Context,
        collect_errors: bool,
    ) -> Result<bool, SchemaError> {
        let node = self.nodes.get(id).ok_or(SchemaError)?;
        if let Some(value) = node.schema.as_bool() {
            return Ok(value);
        }
        let validators = self
            .validators
            .get()
            .and_then(|table| table.get(id))
            .ok_or(SchemaError)?;
        let mut accepted = true;
        for field in &node.order {
            let valid = match field.as_str() {
                "$ref" | "$dynamicRef" | "$recursiveRef"
                    if !(field == "$dynamicRef" && node.draft == Draft::Draft201909
                        || field == "$recursiveRef" && node.draft == Draft::Draft202012) =>
                {
                    let reference = node
                        .schema
                        .get(field)
                        .and_then(Value::as_str)
                        .ok_or(SchemaError)?;
                    let (target, valid, context) = self.resolve(field, reference, context)?;
                    if let Some(target) = target {
                        self.valid_mode(&target, instance, &context, collect_errors)?
                    } else {
                        valid
                    }
                }
                "unevaluatedProperties" => self.accepts(id, instance)?,
                "unevaluatedItems" => {
                    self.items(id, node.draft == Draft::Draft201909, instance, context)?
                }
                "oneOf" => {
                    let mut found = 0;
                    let branches = node
                        .schema
                        .get(field)
                        .and_then(Value::as_array)
                        .ok_or(SchemaError)?;
                    for index in 0..branches.len() {
                        let child = format!("{id}/oneOf/{index}");
                        // After the first match, Python tests remaining branches
                        // with evolve, preserving the parent resolver.
                        let child_context = if found > 0 {
                            context.clone()
                        } else {
                            self.descend(&child, context)?
                        };
                        if self.valid_mode(&child, instance, &child_context, found == 0)? {
                            found += 1;
                        }
                    }
                    found == 1
                }
                "anyOf" => {
                    let branches = node
                        .schema
                        .get(field)
                        .and_then(Value::as_array)
                        .ok_or(SchemaError)?;
                    let mut found = false;
                    for index in 0..branches.len() {
                        let child = format!("{id}/anyOf/{index}");
                        if self.valid_mode(
                            &child,
                            instance,
                            &self.descend(&child, context)?,
                            true,
                        )? {
                            found = true;
                            break;
                        }
                    }
                    found
                }
                _ if special(field, node.draft) => true,
                _ => {
                    let validator = validators.get(field).ok_or(SchemaError)?;
                    if collect_errors {
                        validator.iter_errors(instance).count() == 0
                    } else {
                        validator.is_valid(instance)
                    }
                }
            };
            if !valid {
                if !collect_errors {
                    return Ok(false);
                }
                accepted = false;
            }
        }
        Ok(accepted)
    }

    fn inline_context(&self, id: &str) -> Result<Context, SchemaError> {
        let frame = STATE
            .with(|state| state.borrow().frames.last().cloned())
            .filter(|frame| frame.owner == self.owner())
            .ok_or(SchemaError)?;
        let mut resolver = frame.context.resolver(self)?;
        let mut ancestors: Vec<_> = self
            .nodes
            .iter()
            .filter(|(path, _)| {
                path.len() > frame.node.len()
                    && path.starts_with(&format!("{}/", frame.node))
                    && (id == path.as_str() || id.starts_with(&format!("{path}/")))
            })
            .collect();
        ancestors.sort_by_key(|(path, _)| path.len());
        for (path, node) in ancestors {
            // The producer evaluates if/contains/not via evolve rather than
            // descend, retaining the parent resolver despite a local $id.
            if path.ends_with("/if") || path.ends_with("/contains") || path.ends_with("/not") {
                continue;
            }
            resolver = resolver
                .in_subresource(node.draft.create_resource_ref(&node.schema))
                .map_err(|_| SchemaError)?;
        }
        Ok(Context::capture(&resolver))
    }

    fn descend(&self, id: &str, context: &Context) -> Result<Context, SchemaError> {
        let node = self.nodes.get(id).ok_or(SchemaError)?;
        let resolver = context
            .resolver(self)?
            .in_subresource(node.draft.create_resource_ref(&node.schema))
            .map_err(|_| SchemaError)?;
        Ok(Context::capture(&resolver))
    }

    fn resolve(
        &self,
        kind: &str,
        reference: &str,
        context: &Context,
    ) -> Result<(Option<String>, bool, Context), SchemaError> {
        let resolver = context.resolver(self)?;
        let resolved = if kind == "$recursiveRef" {
            resolver.lookup_recursive_ref()
        } else {
            resolver.lookup(reference)
        }
        .map_err(|_| SchemaError)?;
        let context = Context::capture(resolved.resolver());
        match resolved.contents() {
            Value::Bool(value) => Ok((None, *value, context)),
            Value::Object(object) => {
                let id = object
                    .get(&self.keyword)
                    .and_then(Value::as_str)
                    .filter(|id| self.nodes.contains_key(*id))
                    .ok_or(SchemaError)?;
                Ok((Some(id.to_owned()), true, context))
            }
            _ => Err(SchemaError),
        }
    }

    fn collect(
        &self,
        root: &str,
        legacy: bool,
        instance: &Value,
        context: &Context,
        names: &mut BTreeSet<String>,
    ) -> Result<(), SchemaError> {
        let Some(object) = instance.as_object() else {
            return Ok(());
        };
        let mut pending = vec![(root.to_owned(), context.clone(), false)];
        let mut active = HashSet::new();
        while let Some((id, context, exiting)) = pending.pop() {
            let key = self.visit(&id, instance, &context);
            if exiting {
                active.remove(&key);
                continue;
            }
            if !active.insert(key) {
                return Err(SchemaError);
            }
            pending.push((id.clone(), context.clone(), true));
            let node = self.nodes.get(&id).ok_or(SchemaError)?;
            let Some(schema) = node.schema.as_object() else {
                continue;
            };
            for kind in [
                "$ref",
                if legacy {
                    "$recursiveRef"
                } else {
                    "$dynamicRef"
                },
            ] {
                if let Some(reference) = schema.get(kind).and_then(Value::as_str) {
                    let (target, _, resolved) = self.resolve(kind, reference, &context)?;
                    if let Some(target) = target {
                        pending.push((target, resolved, false));
                    }
                }
            }
            if let Some(properties) = schema.get("properties").and_then(Value::as_object) {
                names.extend(
                    properties
                        .keys()
                        .filter(|key| object.contains_key(*key))
                        .cloned(),
                );
            }
            for field in ["additionalProperties", "unevaluatedProperties"] {
                if let Some(subschema) = schema.get(field) {
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
                        let child = format!("{id}/{field}");
                        let descended = self.descend(&child, &context)?;
                        for (key, value) in object {
                            if self.valid(&child, value, &descended)? {
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
                        pending.push((
                            format!("{id}/dependentSchemas/{}", token(key)),
                            context.clone(),
                            false,
                        ));
                    }
                }
            }
            for field in ["allOf", "anyOf", "oneOf"] {
                if let Some(children) = schema.get(field).and_then(Value::as_array) {
                    for index in 0..children.len() {
                        let child = format!("{id}/{field}/{index}");
                        if self.valid(&child, instance, &self.descend(&child, &context)?)? {
                            // The pinned helper gathers with its parent resolver,
                            // although branch validation itself enters the child.
                            pending.push((child, context.clone(), false));
                        }
                    }
                }
            }
            if schema.contains_key("if") {
                let tested = format!("{id}/if");
                if self.valid(&tested, instance, &context)? {
                    pending.push((tested, context.clone(), false));
                    if schema.contains_key("then") {
                        pending.push((format!("{id}/then"), context.clone(), false));
                    }
                } else if schema.contains_key("else") {
                    pending.push((format!("{id}/else"), context.clone(), false));
                }
            }
        }
        Ok(())
    }

    fn items(
        &self,
        root: &str,
        legacy: bool,
        instance: &Value,
        context: &Context,
    ) -> Result<bool, SchemaError> {
        let Some(values) = instance.as_array() else {
            return Ok(true);
        };
        let mut indexes = BTreeSet::new();
        let mut pending = vec![(root.to_owned(), context.clone(), false)];
        let mut active = HashSet::new();
        while let Some((id, context, exiting)) = pending.pop() {
            let key = self.visit(&id, instance, &context);
            if exiting {
                active.remove(&key);
                continue;
            }
            if !active.insert(key) {
                return Err(SchemaError);
            }
            pending.push((id.clone(), context.clone(), true));
            let node = self.nodes.get(&id).ok_or(SchemaError)?;
            let Some(schema) = node.schema.as_object() else {
                continue;
            };
            // The modern helper stops immediately at items, before references.
            if !legacy && schema.contains_key("items") {
                indexes.extend(0..values.len());
                continue;
            }
            for kind in [
                "$ref",
                if legacy {
                    "$recursiveRef"
                } else {
                    "$dynamicRef"
                },
            ] {
                if let Some(reference) = schema.get(kind).and_then(Value::as_str) {
                    let (target, _, resolved) = self.resolve(kind, reference, &context)?;
                    if let Some(target) = target {
                        pending.push((target, resolved, false));
                    }
                }
            }
            if legacy {
                if let Some(items) = schema.get("items") {
                    if schema.contains_key("additionalItems") || items.is_object() {
                        indexes.extend(0..values.len());
                        continue;
                    }
                    // The pinned helper calls len(items), so boolean items
                    // causes a producer exception when this helper is reached.
                    indexes.extend(0..items.as_array().ok_or(SchemaError)?.len());
                }
            } else if let Some(prefix) = schema.get("prefixItems").and_then(Value::as_array) {
                indexes.extend(0..prefix.len());
            }
            for field in ["contains", "unevaluatedItems"] {
                if schema.contains_key(field) {
                    let child = format!("{id}/{field}");
                    // evolve(schema=...), unlike descend, does not enter $id.
                    for (index, value) in values.iter().enumerate() {
                        if self.valid(&child, value, &context)? {
                            indexes.insert(index);
                        }
                    }
                }
            }
            for field in ["allOf", "anyOf", "oneOf"] {
                if let Some(children) = schema.get(field).and_then(Value::as_array) {
                    for index in 0..children.len() {
                        let child = format!("{id}/{field}/{index}");
                        if self.valid(&child, instance, &self.descend(&child, &context)?)? {
                            pending.push((child, context.clone(), false));
                        }
                    }
                }
            }
            if schema.contains_key("if") {
                let tested = format!("{id}/if");
                if self.valid(&tested, instance, &context)? {
                    pending.push((tested, context.clone(), false));
                    if schema.contains_key("then") {
                        pending.push((format!("{id}/then"), context.clone(), false));
                    }
                } else if schema.contains_key("else") {
                    pending.push((format!("{id}/else"), context.clone(), false));
                }
            }
        }
        Ok((0..values.len()).all(|index| indexes.contains(&index)))
    }

    fn accepts(&self, id: &str, instance: &Value) -> Result<bool, SchemaError> {
        let node = self.nodes.get(id).ok_or(SchemaError)?;
        if node.schema.get("unevaluatedProperties").is_none() {
            return Ok(true);
        }
        let Some(object) = instance.as_object() else {
            return Ok(true);
        };
        let context = self.inline_context(id)?;
        let mut names = BTreeSet::new();
        self.collect(
            id,
            node.draft == Draft::Draft201909,
            instance,
            &context,
            &mut names,
        )?;
        let remaining = format!("{id}/unevaluatedProperties");
        let context = self.descend(&remaining, &context)?;
        for (key, value) in object {
            if !names.contains(key) && !self.valid(&remaining, value, &context)? {
                return Ok(false);
            }
        }
        Ok(true)
    }
}

fn checked(result: Result<bool, SchemaError>) -> bool {
    match result {
        Ok(valid) => valid,
        Err(_) => {
            schema_reference::refuse();
            false
        }
    }
}

impl<'i> Keyword<'i> for Entry {
    fn validate(&self, instance: &'i Value) -> Result<(), ValidationError<'i>> {
        if self.is_valid(instance) {
            Ok(())
        } else {
            Err(ValidationError::custom("schema refused"))
        }
    }
    fn is_valid(&self, instance: &'i Value) -> bool {
        checked(
            self.0
                .upgrade()
                .ok_or(SchemaError)
                .and_then(|scope| scope.valid("#", instance, &scope.root)),
        )
    }
}

impl<'i> Keyword<'i> for Proxy {
    fn validate(&self, instance: &'i Value) -> Result<(), ValidationError<'i>> {
        if self.evaluate(instance, true) {
            Ok(())
        } else {
            Err(ValidationError::custom("unevaluated property refused"))
        }
    }
    fn is_valid(&self, instance: &'i Value) -> bool {
        self.evaluate(instance, false)
    }
}

impl Proxy {
    fn evaluate(&self, instance: &Value, collect_errors: bool) -> bool {
        checked(self.scope.upgrade().ok_or(SchemaError).and_then(|scope| {
            let frame = STATE
                .with(|state| state.borrow().frames.last().cloned())
                .filter(|frame| frame.owner == scope.owner())
                .ok_or(SchemaError)?;
            let context = if self.enter {
                scope.descend(&self.node, &frame.context)?
            } else {
                frame.context
            };
            scope.valid_mode(&self.node, instance, &context, collect_errors)
        }))
    }
}
