//! CPython-native conversion and journal ownership; no helper process.
use std::collections::BTreeMap;
use std::path::Path;
use std::sync::{Arc, Mutex};

use pyo3::exceptions::{PyRuntimeError, PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};
use skulk_capability_kernel::{
    binding, calls, codec, composition, composition_v2, contracts, handshake, handshake_v2,
    journal, managed, managed_v2, operation_logs, operations, portable, portable_v2, schema,
    streams, transport,
};

/// Private qualification surface; source text preserves wide JSON numbers.
#[pyfunction]
fn schema_matches(py: Python<'_>, definition: String, instance: String) -> PyResult<bool> {
    py.detach(|| {
        schema::Schema::new(&definition)
            .map(|schema| schema.matches(&instance))
            .map_err(|_| PyValueError::new_err("invalid payload schema"))
    })
}

/// Installed descriptor validation uses the owner's fixed root dialect.
#[pyfunction]
fn schema_matches_installed(
    py: Python<'_>,
    definition: String,
    instance: String,
) -> PyResult<bool> {
    py.detach(|| {
        schema::Schema::installed(&definition)
            .map(|schema| schema.matches(&instance))
            .map_err(|_| PyValueError::new_err("invalid payload schema"))
    })
}

/// Inference-capable managed declaration fingerprint; this grants no permission.
#[pyfunction]
fn managed_v2_profile_digest() -> &'static str {
    managed_v2::profile_digest()
}

/// Protocol-six startup matching fingerprint; hosts still own authentication.
#[pyfunction]
fn handshake_v2_profile_digest() -> &'static str {
    handshake_v2::profile_digest()
}

/// Exact managed installation-profile fingerprint; no authorization effects.
#[pyfunction]
fn managed_profile_digest() -> &'static str {
    managed::profile_digest()
}

/// The independent composition fingerprint grants no caller authority.
#[pyfunction]
fn composition_profile_digest() -> &'static str {
    composition::profile_digest()
}

/// Immutable caller metadata, without diagnostic serialization or content digest.
#[pyclass(frozen, module = "_skulk_capability_kernel")]
struct CompositionContract {
    record: composition::Record,
}

#[pymethods]
impl CompositionContract {
    /// Parse structurally; the host must establish active-scope provenance.
    #[staticmethod]
    fn from_json(py: Python<'_>, name: &str, source: &Bound<'_, PyString>) -> PyResult<Self> {
        let source = portable_text(source)?;
        let record = py
            .detach(|| composition::Record::from_json(name, &source))
            .map_err(|error| PyValueError::new_err(error.to_string()))?;
        Ok(Self { record })
    }

    /// Public kind only, without caller, scope or request identity.
    #[getter]
    fn name(&self) -> &'static str {
        self.record.name()
    }

    /// Explicitly copy sensitive canonical bytes for protected host IPC.
    fn canonical<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        PyBytes::new(py, self.record.canonical())
    }

    /// Attenuate with host observations; no registry lookup, replay or dispatch.
    // Separate arguments make host observations explicit at the FFI boundary.
    #[allow(clippy::too_many_arguments)]
    fn admit(
        &self,
        py: Python<'_>,
        request: &Self,
        policy: &Self,
        executor: &Bound<'_, PyString>,
        request_bytes: Vec<u8>,
        now_unix_ms: u64,
        scope_id: &str,
    ) -> PyResult<Self> {
        let executor = portable_text(executor)?;
        let record = py
            .detach(|| {
                composition::admit(
                    &self.record,
                    &request.record,
                    &policy.record,
                    &executor,
                    &request_bytes,
                    now_unix_ms,
                    scope_id,
                )
            })
            .map_err(|error| PyValueError::new_err(error.to_string()))?;
        Ok(Self { record })
    }

    fn __repr__(&self) -> String {
        format!("CompositionContract({})", self.record.name())
    }
}

/// The inference-aware composition fingerprint grants no caller authority.
#[pyfunction]
fn composition_v2_profile_digest() -> &'static str {
    composition_v2::profile_digest()
}

/// Immutable caller metadata, without diagnostic serialization or content digest.
#[pyclass(frozen, module = "_skulk_capability_kernel")]
struct CompositionV2Contract {
    record: composition_v2::Record,
}

#[pymethods]
impl CompositionV2Contract {
    /// Parse structurally; the host must establish active-scope provenance.
    #[staticmethod]
    fn from_json(py: Python<'_>, name: &str, source: &Bound<'_, PyString>) -> PyResult<Self> {
        let source = portable_text(source)?;
        let record = py
            .detach(|| composition_v2::Record::from_json(name, &source))
            .map_err(|error| PyValueError::new_err(error.to_string()))?;
        Ok(Self { record })
    }

    /// Public kind only, without caller, scope or request identity.
    #[getter]
    fn name(&self) -> &'static str {
        self.record.name()
    }

    /// Explicitly copy sensitive canonical bytes for protected host IPC.
    fn canonical<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        PyBytes::new(py, self.record.canonical())
    }

    /// Attenuate with host observations; no registry lookup, replay or dispatch.
    // Separate arguments make host observations explicit at the FFI boundary.
    #[allow(clippy::too_many_arguments)]
    fn admit(
        &self,
        py: Python<'_>,
        request: &Self,
        policy: &Self,
        executor: &Bound<'_, PyString>,
        request_bytes: Vec<u8>,
        now_unix_ms: u64,
        scope_id: &str,
    ) -> PyResult<Self> {
        let executor = portable_text(executor)?;
        let record = py
            .detach(|| {
                composition_v2::admit(
                    &self.record,
                    &request.record,
                    &policy.record,
                    &executor,
                    &request_bytes,
                    now_unix_ms,
                    scope_id,
                )
            })
            .map_err(|error| PyValueError::new_err(error.to_string()))?;
        Ok(Self { record })
    }

    fn __repr__(&self) -> String {
        format!("CompositionV2Contract({})", self.record.name())
    }
}

/// Native admission of a managed manifest or private inherited startup record.
#[pyfunction]
fn managed_contract_json<'py>(
    py: Python<'py>,
    name: &str,
    source: &Bound<'py, PyString>,
) -> PyResult<Bound<'py, PyBytes>> {
    let source = portable_text(source)?;
    let bytes = py
        .detach(|| managed::validate_contract_json(name, &source))
        .map_err(portable_error)?;
    Ok(PyBytes::new(py, &bytes))
}

/// Full managed identity; never truncate or treat a digest as caller authority.
#[pyfunction]
fn managed_contract_digest(
    py: Python<'_>,
    name: &str,
    source: &Bound<'_, PyString>,
) -> PyResult<String> {
    let source = portable_text(source)?;
    py.detach(|| managed::contract_digest(name, &source))
        .map_err(portable_error)
}

/// SDK 1.0 candidate canonical JSON; no legacy numeric or surrogate kinds.
#[pyfunction]
fn portable_canonical_json<'py>(
    py: Python<'py>,
    source: &Bound<'py, PyString>,
) -> PyResult<Bound<'py, PyBytes>> {
    let source = portable_text(source)?;
    let bytes = py
        .detach(|| portable::canonical_json(&source))
        .map_err(portable_error)?;
    Ok(PyBytes::new(py, &bytes))
}

/// Normalize a profile-bound descriptor or protocol-five contract envelope.
#[pyfunction]
fn portable_contract_json<'py>(
    py: Python<'py>,
    name: &str,
    source: &Bound<'py, PyString>,
) -> PyResult<Bound<'py, PyBytes>> {
    let source = portable_text(source)?;
    let bytes = py
        .detach(|| portable::validate_contract_json(name, &source))
        .map_err(portable_error)?;
    Ok(PyBytes::new(py, &bytes))
}

/// Bind new-model identity to the explicit profile and canonical contract.
#[pyfunction]
fn portable_contract_digest(
    py: Python<'_>,
    name: &str,
    source: &Bound<'_, PyString>,
) -> PyResult<String> {
    let source = portable_text(source)?;
    py.detach(|| portable::contract_digest(name, &source))
        .map_err(portable_error)
}

/// Normalize a profile-bound descriptor or protocol-six contract envelope.
#[pyfunction]
fn portable_v2_contract_json<'py>(
    py: Python<'py>,
    name: &str,
    source: &Bound<'py, PyString>,
) -> PyResult<Bound<'py, PyBytes>> {
    let source = portable_text(source)?;
    let bytes = py
        .detach(|| portable_v2::validate_contract_json(name, &source))
        .map_err(portable_error)?;
    Ok(PyBytes::new(py, &bytes))
}

/// Bind new-model identity to the explicit profile and canonical contract.
#[pyfunction]
fn portable_v2_contract_digest(
    py: Python<'_>,
    name: &str,
    source: &Bound<'_, PyString>,
) -> PyResult<String> {
    let source = portable_text(source)?;
    py.detach(|| portable_v2::contract_digest(name, &source))
        .map_err(portable_error)
}

/// Qualify the fixed portable schema profile through the installed native ABI.
#[pyfunction]
fn portable_schema_matches(
    py: Python<'_>,
    definition: &Bound<'_, PyString>,
    instance: &Bound<'_, PyString>,
) -> PyResult<bool> {
    let definition = portable_text(definition)?;
    let instance = portable_text(instance)?;
    py.detach(|| portable::Schema::new(&definition).and_then(|schema| schema.matches(&instance)))
        .map_err(portable_error)
}

fn portable_error(error: portable::PortableError) -> PyErr {
    PyValueError::new_err(error.to_string())
}

fn portable_text(source: &Bound<'_, PyString>) -> PyResult<String> {
    let text = source
        .to_str()
        .map_err(|_| portable_error(portable::PortableError::Unicode))?;
    if text.len() > 262_144 {
        return Err(portable_error(portable::PortableError::Limit));
    }
    Ok(text.into())
}

/// The profile fingerprint belongs to the kernel, never to a caller annotation.
#[pyfunction]
fn portable_profile_digest() -> &'static str {
    portable::profile_digest()
}

/// The profile fingerprint belongs to the kernel, never to a caller annotation.
#[pyfunction]
fn portable_v2_profile_digest() -> &'static str {
    portable_v2::profile_digest()
}

/// Exact fingerprint of the explicit protocol-five handshake specification.
#[pyfunction]
fn handshake_profile_digest() -> &'static str {
    handshake::profile_digest()
}

/// Immutable native-owned manifest/startup handle; repr never exposes contents.
#[pyclass(frozen, module = "_skulk_capability_kernel")]
struct ManagedContract {
    record: managed::Record,
}

#[pymethods]
impl ManagedContract {
    /// Admit profile-bound author bytes without a language-validator fallback.
    #[staticmethod]
    fn from_json(
        py: Python<'_>,
        name: &Bound<'_, PyString>,
        source: &Bound<'_, PyString>,
    ) -> PyResult<Self> {
        let name = portable_text(name)?;
        let source = portable_text(source)?;
        let record = py
            .detach(|| managed::Record::from_json(&name, &source))
            .map_err(portable_error)?;
        Ok(Self { record })
    }

    /// Public model kind; no startup credentials are projected.
    #[getter]
    fn name(&self) -> &'static str {
        self.record.name()
    }

    /// Full profile/model-domain identity; never a legacy truncated revision.
    #[getter]
    fn digest(&self) -> &str {
        self.record.digest()
    }

    /// Explicitly export an owned copy for protected IPC or signed metadata.
    fn canonical<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        PyBytes::new(py, self.record.canonical())
    }

    /// Derive control hello, or a stream hello for a separately admitted call.
    #[pyo3(signature = (call_id=None))]
    fn hello(
        &self,
        py: Python<'_>,
        call_id: Option<&Bound<'_, PyString>>,
    ) -> PyResult<HandshakeContract> {
        let call_id = call_id.map(portable_text).transpose()?;
        let record = py
            .detach(|| handshake::from_startup(&self.record, call_id.as_deref()))
            .map_err(portable_error)?;
        Ok(HandshakeContract { record })
    }

    /// Bind an incoming hello to this expected startup and optional call.
    #[pyo3(signature = (source, call_id=None))]
    fn admit_hello(
        &self,
        py: Python<'_>,
        source: &Bound<'_, PyString>,
        call_id: Option<&Bound<'_, PyString>>,
    ) -> PyResult<HandshakeContract> {
        let source = portable_text(source)?;
        let call_id = call_id.map(portable_text).transpose()?;
        let record = py
            .detach(|| handshake::admit(&self.record, &source, call_id.as_deref()))
            .map_err(portable_error)?;
        Ok(HandshakeContract { record })
    }

    fn __repr__(&self) -> String {
        format!("ManagedContract({})", self.record.name())
    }
}

/// Immutable handshake handle; structural admission alone grants no authority.
#[pyclass(frozen, module = "_skulk_capability_kernel")]
struct HandshakeContract {
    record: handshake::Record,
}

#[pymethods]
impl HandshakeContract {
    /// Parse an explicit protocol-five hello; startup binding is a separate step.
    #[staticmethod]
    fn from_json(
        py: Python<'_>,
        name: &Bound<'_, PyString>,
        source: &Bound<'_, PyString>,
    ) -> PyResult<Self> {
        let name = portable_text(name)?;
        let source = portable_text(source)?;
        let record = py
            .detach(|| handshake::Record::from_json(&name, &source))
            .map_err(portable_error)?;
        Ok(Self { record })
    }

    /// Public record kind with no credential-bearing values.
    #[getter]
    fn name(&self) -> &'static str {
        self.record.name()
    }

    /// Explicitly copy credential-bearing wire bytes for the protected channel.
    fn canonical<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        PyBytes::new(py, self.record.canonical())
    }

    fn __repr__(&self) -> String {
        format!("HandshakeContract({})", self.record.name())
    }
}

/// Immutable native-owned manifest/startup handle; repr never exposes contents.
#[pyclass(frozen, module = "_skulk_capability_kernel")]
struct ManagedV2Contract {
    record: managed_v2::Record,
}

#[pymethods]
impl ManagedV2Contract {
    /// Admit profile-bound author bytes without a language-validator fallback.
    #[staticmethod]
    fn from_json(
        py: Python<'_>,
        name: &Bound<'_, PyString>,
        source: &Bound<'_, PyString>,
    ) -> PyResult<Self> {
        let name = portable_text(name)?;
        let source = portable_text(source)?;
        let record = py
            .detach(|| managed_v2::Record::from_json(&name, &source))
            .map_err(portable_error)?;
        Ok(Self { record })
    }

    /// Public model kind; no startup credentials are projected.
    #[getter]
    fn name(&self) -> &'static str {
        self.record.name()
    }

    /// Full profile/model-domain identity; never a legacy truncated revision.
    #[getter]
    fn digest(&self) -> &str {
        self.record.digest()
    }

    /// Explicitly export an owned copy for protected IPC or signed metadata.
    fn canonical<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        PyBytes::new(py, self.record.canonical())
    }

    /// Derive control hello, or a stream hello for a separately admitted call.
    #[pyo3(signature = (call_id=None))]
    fn hello(
        &self,
        py: Python<'_>,
        call_id: Option<&Bound<'_, PyString>>,
    ) -> PyResult<HandshakeV2Contract> {
        let call_id = call_id.map(portable_text).transpose()?;
        let record = py
            .detach(|| handshake_v2::from_startup(&self.record, call_id.as_deref()))
            .map_err(portable_error)?;
        Ok(HandshakeV2Contract { record })
    }

    /// Bind an incoming hello to this expected startup and optional call.
    #[pyo3(signature = (source, call_id=None))]
    fn admit_hello(
        &self,
        py: Python<'_>,
        source: &Bound<'_, PyString>,
        call_id: Option<&Bound<'_, PyString>>,
    ) -> PyResult<HandshakeV2Contract> {
        let source = portable_text(source)?;
        let call_id = call_id.map(portable_text).transpose()?;
        let record = py
            .detach(|| handshake_v2::admit(&self.record, &source, call_id.as_deref()))
            .map_err(portable_error)?;
        Ok(HandshakeV2Contract { record })
    }

    fn __repr__(&self) -> String {
        format!("ManagedV2Contract({})", self.record.name())
    }
}

/// Immutable handshake handle; structural admission alone grants no authority.
#[pyclass(frozen, module = "_skulk_capability_kernel")]
struct HandshakeV2Contract {
    record: handshake_v2::Record,
}

#[pymethods]
impl HandshakeV2Contract {
    /// Parse an explicit protocol-six hello; startup binding is a separate step.
    #[staticmethod]
    fn from_json(
        py: Python<'_>,
        name: &Bound<'_, PyString>,
        source: &Bound<'_, PyString>,
    ) -> PyResult<Self> {
        let name = portable_text(name)?;
        let source = portable_text(source)?;
        let record = py
            .detach(|| handshake_v2::Record::from_json(&name, &source))
            .map_err(portable_error)?;
        Ok(Self { record })
    }

    /// Public record kind with no credential-bearing values.
    #[getter]
    fn name(&self) -> &'static str {
        self.record.name()
    }

    /// Explicitly copy credential-bearing wire bytes for the protected channel.
    fn canonical<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        PyBytes::new(py, self.record.canonical())
    }

    fn __repr__(&self) -> String {
        format!("HandshakeV2Contract({})", self.record.name())
    }
}

/// Reusable immutable compiled schema shared safely by concurrent handlers.
#[pyclass(frozen)]
struct PortableSchema {
    schema: portable::Schema,
}

#[pymethods]
impl PortableSchema {
    /// Eagerly compile the fixed profile and retain its native-owned validator.
    #[new]
    fn new(py: Python<'_>, source: &Bound<'_, PyString>) -> PyResult<Self> {
        let source = portable_text(source)?;
        let schema = py
            .detach(|| portable::Schema::new(&source))
            .map_err(portable_error)?;
        Ok(Self { schema })
    }

    /// Match bounded scalar JSON without retaining any per-call payload state.
    fn matches(&self, py: Python<'_>, source: &Bound<'_, PyString>) -> PyResult<bool> {
        let source = portable_text(source)?;
        py.detach(|| self.schema.matches(&source))
            .map_err(portable_error)
    }
}

fn points(value: &Bound<'_, PyAny>) -> PyResult<Vec<u32>> {
    let encoded = value
        .py()
        .get_type::<PyString>()
        .getattr("encode")?
        .call1((value, "utf-32-le", "surrogatepass"))?;
    let bytes = encoded.cast::<PyBytes>()?.as_bytes();
    Ok(bytes
        .chunks_exact(4)
        .map(|chunk| u32::from_le_bytes(chunk.try_into().expect("four bytes")))
        .collect())
}

fn value_from_python(value: &Bound<'_, PyAny>, depth: usize) -> PyResult<codec::Value> {
    if depth > 256 {
        return Err(PyValueError::new_err("JSON nesting exceeds the bound"));
    }
    if value.is_none() {
        return Ok(codec::Value::Null);
    }
    if let Ok(value) = value.cast::<PyBool>() {
        return Ok(codec::Value::Bool(value.is_true()));
    }
    if value.is_instance_of::<PyInt>() {
        // Call the builtin representation, not a user subclass's __str__.
        let digits = value
            .py()
            .get_type::<PyInt>()
            .getattr("__repr__")?
            .call1((value,))?
            .extract::<String>()?;
        return Ok(codec::Value::Integer(digits));
    }
    if value.is_instance_of::<PyFloat>() {
        return Ok(codec::Value::Float(value.extract()?));
    }
    if value.is_instance_of::<PyString>() {
        return Ok(codec::Value::String(points(value)?));
    }
    if let Ok(values) = value.cast::<PyDict>() {
        let mut result = BTreeMap::new();
        for (key, value) in values.iter() {
            if !key.is_instance_of::<PyString>() {
                return Err(PyTypeError::new_err("JSON keys must be strings"));
            }
            result.insert(points(&key)?, value_from_python(&value, depth + 1)?);
        }
        return Ok(codec::Value::Object(result));
    }
    if let Ok(values) = value.cast::<PyList>() {
        return Ok(codec::Value::Array(
            values
                .iter()
                .map(|value| value_from_python(&value, depth + 1))
                .collect::<PyResult<_>>()?,
        ));
    }
    if let Ok(values) = value.cast::<PyTuple>() {
        return Ok(codec::Value::Array(
            values
                .iter()
                .map(|value| value_from_python(&value, depth + 1))
                .collect::<PyResult<_>>()?,
        ));
    }
    Err(PyTypeError::new_err("unsupported JSON value"))
}

fn constructor_from_python(
    value: &Bound<'_, PyAny>,
    depth: usize,
    remaining: &mut usize,
) -> PyResult<codec::Value> {
    if depth > 200 {
        return Err(PyValueError::new_err("constructor nesting exceeds bound"));
    }
    *remaining = remaining
        .checked_sub(16)
        .ok_or_else(|| PyValueError::new_err("constructor exceeds bound"))?;
    if let Ok(record) = value.extract::<PyRef<'_, NativeAuthorContract>>() {
        // Native objects are frozen and cannot be constructed without validation.
        return record
            .record
            .constructor_input(remaining)
            .map_err(|_| PyValueError::new_err("constructor exceeds bound"));
    }
    if let Ok(bytes) = value.cast::<PyBytes>() {
        *remaining = remaining
            .checked_sub(bytes.as_bytes().len())
            .ok_or_else(|| PyValueError::new_err("constructor exceeds bound"))?;
        return Ok(codec::Value::Bytes(bytes.as_bytes().to_vec()));
    }
    if let Ok(values) = value.cast::<PyTuple>() {
        return Ok(codec::Value::Tuple(
            values
                .iter()
                .map(|value| constructor_from_python(&value, depth + 1, remaining))
                .collect::<PyResult<_>>()?,
        ));
    }
    if let Ok(values) = value.cast::<PyList>() {
        return Ok(codec::Value::Array(
            values
                .iter()
                .map(|value| constructor_from_python(&value, depth + 1, remaining))
                .collect::<PyResult<_>>()?,
        ));
    }
    if let Ok(values) = value.cast::<PyDict>() {
        let mut result = BTreeMap::new();
        for (key, value) in values.iter() {
            let Ok(key) = key.cast::<PyString>() else {
                return Err(PyTypeError::new_err("constructor keys must be strings"));
            };
            let size = key
                .len()?
                .checked_mul(4)
                .ok_or_else(|| PyValueError::new_err("constructor exceeds bound"))?;
            *remaining = remaining
                .checked_sub(size)
                .ok_or_else(|| PyValueError::new_err("constructor exceeds bound"))?;
            result.insert(
                points(key.as_any())?,
                constructor_from_python(&value, depth + 1, remaining)?,
            );
        }
        return Ok(codec::Value::Object(result));
    }
    if let Ok(text) = value.cast::<PyString>() {
        let size = text
            .len()?
            .checked_mul(4)
            .ok_or_else(|| PyValueError::new_err("constructor exceeds bound"))?;
        *remaining = remaining
            .checked_sub(size)
            .ok_or_else(|| PyValueError::new_err("constructor exceeds bound"))?;
    }
    if value.is_instance_of::<PyInt>() && !value.is_instance_of::<PyBool>() {
        let bits = value
            .py()
            .get_type::<PyInt>()
            .getattr("bit_length")?
            .call1((value,))?
            .extract::<usize>()?;
        let estimate = bits
            .checked_mul(30103)
            .and_then(|bits| bits.checked_div(100000))
            .and_then(|digits| digits.checked_add(2))
            .ok_or_else(|| PyValueError::new_err("constructor exceeds bound"))?;
        *remaining = remaining
            .checked_sub(estimate)
            .ok_or_else(|| PyValueError::new_err("constructor exceeds bound"))?;
    }
    value_from_python(value, depth)
}

/// Opaque native constructor result; no Python policy or mutable model storage.
#[pyclass(frozen, module = "_skulk_capability_kernel")]
struct NativeAuthorContract {
    record: contracts::ValidatedContract,
}
#[pymethods]
impl NativeAuthorContract {
    /// The versioned root or model whose rules normalized this object.
    #[getter]
    fn name(&self) -> &str {
        self.record.name()
    }
    /// Canonical projection may refuse values that are valid before serialization.
    fn canonical<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        let bytes = py
            .detach(|| {
                contracts::Registry::packaged()
                    .and_then(|registry| registry.project_value(&self.record))
            })
            .map_err(|_| PyValueError::new_err("contract cannot be serialized"))?;
        Ok(PyBytes::new(py, &bytes))
    }
    /// Serialize raw inline media separately from its validated JSON header.
    fn stream_packet<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        let packet = py
            .detach(|| {
                contracts::Registry::packaged()
                    .and_then(|registry| registry.stream_packet(&self.record))
            })
            .map_err(|_| PyValueError::new_err("invalid stream contract"))?;
        let bytes = py
            .detach(|| streams::encode_packet(&packet))
            .map_err(stream_error)?;
        Ok(PyBytes::new(py, &bytes))
    }
}
/// Validate structured constructor data while retaining Python's tuple/byte kinds.
#[pyfunction]
fn validate_contract_value(name: &str, value: &Bound<'_, PyAny>) -> PyResult<NativeAuthorContract> {
    let mut remaining = 16 * 1024 * 1024;
    let value = constructor_from_python(value, 0, &mut remaining)?;
    let record = contracts::Registry::packaged()
        .and_then(|registry| {
            registry.validate_value(name, &value, contracts::ConstructorForm::Python)
        })
        .map_err(|_| PyValueError::new_err("invalid constructor value"))?;
    Ok(NativeAuthorContract { record })
}
fn codec_error(error: codec::CodecError) -> PyErr {
    PyValueError::new_err(error.to_string())
}
fn journal_error(error: journal::JournalError) -> PyErr {
    PyValueError::new_err((binding::error_code(&error), error.to_string()))
}

/// Encode ordinary Python JSON values without losing integer/float distinction.
#[pyfunction]
fn canonical<'py>(py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyBytes>> {
    let value = value_from_python(value, 0)?;
    let bytes = py.detach(|| codec::encode(&value)).map_err(codec_error)?;
    Ok(PyBytes::new(py, &bytes))
}

/// Hash input under its scope using the shared kernel's legacy profile.
#[pyfunction]
fn input_digest(
    py: Python<'_>,
    scope: &Bound<'_, PyString>,
    value: &Bound<'_, PyAny>,
) -> PyResult<String> {
    let scope = points(scope.as_any())?;
    let value = value_from_python(value, 0)?;
    py.detach(|| codec::scoped_digest(&scope, value))
        .map_err(codec_error)
}

/// Parse exact JSON text before canonical encoding; duplicate keys are refused.
#[pyfunction]
fn canonical_json<'py>(py: Python<'py>, input: &str) -> PyResult<Bound<'py, PyBytes>> {
    let bytes = py.detach(|| codec::canonical(input)).map_err(codec_error)?;
    Ok(PyBytes::new(py, &bytes))
}

/// Shared native validation/defaults/projection; no Python policy fallback.
#[pyfunction]
fn validate_contract_json<'py>(
    py: Python<'py>,
    name: &str,
    input: &str,
) -> PyResult<Bound<'py, PyBytes>> {
    let bytes = py
        .detach(|| {
            contracts::Registry::packaged().and_then(|registry| registry.validate_json(name, input))
        })
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    Ok(PyBytes::new(py, &bytes))
}

/// Experimental native journal handle. Explicit close releases reconciliation
/// ownership; every transaction releases the GIL before bounded storage I/O.
#[pyclass(module = "_skulk_capability_kernel")]
struct NativeJournal {
    journal: operations::SharedJournal,
}
#[pymethods]
impl NativeJournal {
    #[new]
    #[pyo3(signature=(path,bounds_json,now))]
    fn new(py: Python<'_>, path: &str, bounds_json: &str, now: i64) -> PyResult<Self> {
        let journal = py
            .detach(|| binding::open(Path::new(path), bounds_json, now))
            .map_err(journal_error)?;
        Ok(Self {
            journal: operations::JournalGroup::new(journal),
        })
    }
    /// Apply the shared request envelope and return exact canonical response JSON.
    fn transact(&self, py: Python<'_>, request: &str) -> PyResult<String> {
        let bytes = py
            .detach(|| {
                let mut guard = self
                    .journal
                    .data
                    .lock()
                    .map_err(|_| journal::JournalError::Busy)?;
                let journal = guard.as_mut().ok_or(journal::JournalError::Invalid)?;
                binding::transact(journal, request)
            })
            .map_err(journal_error)?;
        String::from_utf8(bytes).map_err(|_| PyRuntimeError::new_err("invalid native encoding"))
    }
    /// Acquire the original supervisor.lock fence before reconciliation.
    fn acquire_ownership(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| self.journal.acquire_ownership())
            .map_err(journal_error)
    }
    /// Release the fence only after all native scope workers have stopped.
    fn release_ownership(&self) -> PyResult<()> {
        self.journal.release_ownership().map_err(journal_error)
    }
    /// Refuse closing shared state while an operation worker is live.
    fn close(&self) -> PyResult<()> {
        self.journal.close().map_err(journal_error)
    }
}

#[pymodule]
fn _skulk_capability_kernel(module: &Bound<'_, PyModule>) -> PyResult<()> {
    pyo3_async_runtimes::tokio::init(streams::runtime_builder());
    module.add_function(wrap_pyfunction!(canonical, module)?)?;
    module.add_function(wrap_pyfunction!(canonical_json, module)?)?;
    module.add_function(wrap_pyfunction!(validate_contract_json, module)?)?;
    module.add_function(wrap_pyfunction!(validate_contract_value, module)?)?;
    module.add_function(wrap_pyfunction!(schema_matches, module)?)?;
    module.add_function(wrap_pyfunction!(schema_matches_installed, module)?)?;
    module.add_function(wrap_pyfunction!(portable_canonical_json, module)?)?;
    module.add_function(wrap_pyfunction!(portable_contract_json, module)?)?;
    module.add_function(wrap_pyfunction!(portable_contract_digest, module)?)?;
    module.add_function(wrap_pyfunction!(portable_schema_matches, module)?)?;
    module.add_function(wrap_pyfunction!(portable_profile_digest, module)?)?;
    module.add_function(wrap_pyfunction!(managed_profile_digest, module)?)?;
    module.add_function(wrap_pyfunction!(composition_profile_digest, module)?)?;
    module.add_class::<CompositionContract>()?;
    module.add_function(wrap_pyfunction!(composition_v2_profile_digest, module)?)?;
    module.add_class::<CompositionV2Contract>()?;
    module.add_function(wrap_pyfunction!(portable_v2_profile_digest, module)?)?;
    module.add_function(wrap_pyfunction!(portable_v2_contract_json, module)?)?;
    module.add_function(wrap_pyfunction!(portable_v2_contract_digest, module)?)?;
    module.add_function(wrap_pyfunction!(handshake_profile_digest, module)?)?;
    module.add_class::<ManagedContract>()?;
    module.add_class::<ManagedV2Contract>()?;
    module.add_class::<HandshakeV2Contract>()?;
    module.add_function(wrap_pyfunction!(managed_v2_profile_digest, module)?)?;
    module.add_function(wrap_pyfunction!(handshake_v2_profile_digest, module)?)?;
    module.add_class::<HandshakeContract>()?;
    module.add_function(wrap_pyfunction!(managed_contract_json, module)?)?;
    module.add_function(wrap_pyfunction!(managed_contract_digest, module)?)?;
    module.add_class::<PortableSchema>()?;
    module.add_class::<NativeAuthorContract>()?;
    module.add_function(wrap_pyfunction!(input_digest, module)?)?;
    module.add_class::<NativeJournal>()?;
    module.add_class::<NativeStream>()?;
    module.add_class::<NativeOperationRunner>()?;
    module.add_function(wrap_pyfunction!(operation_runner, module)?)?;
    module.add_function(wrap_pyfunction!(open_stream, module)?)?;
    module.add_function(wrap_pyfunction!(transport_profile_digest, module)?)?;
    module.add_function(wrap_pyfunction!(encode_portable_control, module)?)?;
    module.add_function(wrap_pyfunction!(decode_portable_control, module)?)?;
    module.add_function(wrap_pyfunction!(encode_portable_packet, module)?)?;
    module.add_function(wrap_pyfunction!(decode_portable_packet, module)?)?;
    module.add_function(wrap_pyfunction!(open_portable_stream, module)?)?;
    module.add_function(wrap_pyfunction!(open_portable_control, module)?)?;
    module.add_class::<NativeControl>()?;
    module.add_function(wrap_pyfunction!(calls_profile_digest, module)?)?;
    module.add_function(wrap_pyfunction!(call_payload_size, module)?)?;
    module.add_function(wrap_pyfunction!(encode_call_frame, module)?)?;
    module.add_function(wrap_pyfunction!(decode_call_frame, module)?)?;
    module.add_function(wrap_pyfunction!(open_call_control, module)?)?;
    module.add_class::<NativeCallControl>()?;
    module.add_function(wrap_pyfunction!(encode_stream_packet, module)?)?;
    module.add_function(wrap_pyfunction!(decode_stream_packet, module)?)?;
    Ok(())
}

fn stream_error(error: streams::StreamError) -> PyErr {
    PyValueError::new_err(error.to_string())
}

/// Validate/frame a JSON projection plus a separate capped raw media buffer.
#[pyfunction]
fn encode_stream_packet<'py>(
    py: Python<'py>,
    projection: &str,
    media: &Bound<'py, PyBytes>,
) -> PyResult<Bound<'py, PyBytes>> {
    if media.as_bytes().len() > streams::MAX_MEDIA_BYTES {
        return Err(stream_error(streams::StreamError::MediaBound));
    }
    let packet = streams::Packet::from_projection(projection, media.as_bytes().to_vec())
        .map_err(stream_error)?;
    let bytes = py
        .detach(|| streams::encode_packet(&packet))
        .map_err(stream_error)?;
    Ok(PyBytes::new(py, &bytes))
}

/// Decode a complete bounded packet while keeping arbitrary media out of JSON.
#[pyfunction]
fn decode_stream_packet<'py>(
    py: Python<'py>,
    bytes: &Bound<'py, PyBytes>,
) -> PyResult<(String, Bound<'py, PyBytes>)> {
    let packet = streams::decode_packet(bytes.as_bytes()).map_err(stream_error)?;
    Ok((
        packet.projection_json().map_err(stream_error)?,
        PyBytes::new(py, &packet.media),
    ))
}

/// Direct native async stream owner; the private facade acknowledges iterator
/// cleanup before finish, and always awaits close from its finally path.
#[pyclass(module = "_skulk_capability_kernel")]
struct NativeStream {
    inner: Arc<streams::Session>,
}
#[pymethods]
impl NativeStream {
    /// Await one validated ingress packet; completed input then returns None.
    fn next_input<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let session = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let packet = session.next_input().await.map_err(stream_error)?;
            match packet {
                Some(packet) => Ok(Some((
                    session.projection(&packet).map_err(stream_error)?,
                    Python::attach(|py| PyBytes::new(py, &packet.media).unbind()),
                ))),
                None => Ok(None),
            }
        })
    }
    /// Send/hold callback output in the shared sequence and cleanup engine.
    fn send_output<'py>(
        &self,
        py: Python<'py>,
        projection: &str,
        media: &Bound<'py, PyBytes>,
    ) -> PyResult<Bound<'py, PyAny>> {
        if media.as_bytes().len() > streams::MAX_MEDIA_BYTES {
            return Err(stream_error(streams::StreamError::MediaBound));
        }
        let packet = self
            .inner
            .packet(projection, media.as_bytes().to_vec())
            .map_err(stream_error)?;
        let session = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            session.send_output(packet).await.map_err(stream_error)
        })
    }
    /// Emit output terminal/end only after the language iterator has closed.
    fn finish<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let session = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            session.finish().await.map_err(stream_error)
        })
    }
    /// Await cancellation of all owned transport tasks and socket shutdown.
    fn close<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let session = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            session.close().await;
            Ok(())
        })
    }
}

/// Open one authenticated protocol-4 local stream, sharing the Rust reactor.
#[pyfunction]
fn open_stream(
    py: Python<'_>,
    socket: String,
    hello: String,
    call_id: String,
    mode: String,
    seconds: f64,
) -> PyResult<Bound<'_, PyAny>> {
    let mode = streams::Mode::parse(&mode).map_err(stream_error)?;
    pyo3_async_runtimes::tokio::future_into_py(py, async move {
        let session = streams::Session::open(&socket, &hello, &call_id, mode, seconds)
            .await
            .map_err(stream_error)?;
        Ok(NativeStream {
            inner: Arc::new(session),
        })
    })
}

fn operation_error(error: operations::OperationError) -> PyErr {
    PyValueError::new_err(error.to_string())
}

/// Native scoped operation worker. Its small language dispatcher serves bridge
/// requests; reservation, ownership, observation policy and clocks stay native.
#[pyclass(module = "_skulk_capability_kernel")]
struct NativeOperationRunner {
    inner: Arc<operations::Runner>,
    _ownership: Arc<Mutex<Option<journal::Ownership>>>,
}
impl Drop for NativeOperationRunner {
    fn drop(&mut self) {
        self.inner.request_close();
    }
}
#[pymethods]
impl NativeOperationRunner {
    /// Start a single native scope worker, without submitting unreserved jobs.
    fn start<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let runner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            runner.start().await.map_err(operation_error)
        })
    }
    /// Interrupt adapter waits and await persisted ownership before returning.
    fn stop<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let runner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            runner.stop().await;
            Ok(())
        })
    }
    /// Canonical scoped reservation and legacy default ID; returns exact status JSON.
    fn reserve<'py>(
        &self,
        py: Python<'py>,
        operation_id: Option<String>,
        payload: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let runner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            binding::status_json(
                runner
                    .reserve(operation_id, payload)
                    .await
                    .map_err(operation_error)?,
            )
            .map_err(journal_error)
        })
    }
    /// Observe local state, never issuing a backend callback.
    fn status<'py>(&self, py: Python<'py>, operation_id: String) -> PyResult<Bound<'py, PyAny>> {
        let runner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            runner
                .status(operation_id)
                .await
                .map_err(operation_error)?
                .map(binding::status_json)
                .transpose()
                .map_err(journal_error)
        })
    }
    /// Persist cancellation intent; only backend terminal observation stops accepted jobs.
    fn cancel<'py>(&self, py: Python<'py>, operation_id: String) -> PyResult<Bound<'py, PyAny>> {
        let runner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            binding::status_json(runner.cancel(operation_id).await.map_err(operation_error)?)
                .map_err(journal_error)
        })
    }
    /// Read bounded byte-offset log evidence without invoking its adapter.
    fn log<'py>(
        &self,
        py: Python<'py>,
        operation_id: String,
        offset: u64,
    ) -> PyResult<Bound<'py, PyAny>> {
        let runner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            runner
                .log_page(operation_id, offset)
                .await
                .map_err(operation_error)
        })
    }
    /// Await one current adapter request, cancellation advisory or runner stop.
    fn next_request<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let bridge = self.inner.bridge.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            bridge
                .next()
                .await
                .map_err(operation_error)?
                .map(|command| command.json())
                .transpose()
                .map_err(operation_error)
        })
    }
    /// Refuse late/duplicate replies; a response cannot revive an expired callback.
    fn respond(&self, id: u64, response: &str) -> PyResult<bool> {
        self.inner
            .bridge
            .respond(id, response)
            .map_err(operation_error)
    }
}

/// Build the native worker around the existing shared journal and private logs.
#[pyfunction]
fn operation_runner(
    py: Python<'_>,
    journal: PyRef<'_, NativeJournal>,
    logs: String,
    scope: String,
    poll_seconds: f64,
    adapter_seconds: f64,
    result_schema: String,
) -> PyResult<NativeOperationRunner> {
    let handle = journal.journal.clone();
    let ownership = journal.journal.ownership.clone();
    let inner = py
        .detach(|| {
            let bounds = handle
                .data
                .lock()
                .map_err(|_| operations::OperationError::Stopped)?
                .as_ref()
                .ok_or(operations::OperationError::Stopped)?
                .bounds();
            let logs = operation_logs::Logs::new(Path::new(&logs), bounds.log_bytes as u64)?;
            operations::Runner::new(
                handle,
                logs,
                operations::Config {
                    scope,
                    poll_seconds,
                    adapter_seconds,
                },
                &result_schema,
                None,
            )
        })
        .map_err(operation_error)?;
    Ok(NativeOperationRunner {
        inner,
        _ownership: ownership,
    })
}

/// Independent fingerprint for protocol-five control and binary framing.
#[pyfunction]
fn transport_profile_digest() -> &'static str {
    transport::profile_digest()
}
/// Encode one closed control record; host execution admission remains separate.
#[pyfunction]
fn encode_portable_control<'py>(
    py: Python<'py>,
    source: &Bound<'_, PyString>,
) -> PyResult<Bound<'py, PyBytes>> {
    let source = portable_text(source)?;
    let bytes = py
        .detach(|| {
            transport::Control::from_json(&source)
                .map(|control| transport::encode_control(&control))
        })
        .map_err(stream_error)?;
    Ok(PyBytes::new(py, &bytes))
}
/// Return an owned canonical copy after complete length/profile validation.
#[pyfunction]
fn decode_portable_control<'py>(
    py: Python<'py>,
    bytes: &Bound<'_, PyBytes>,
) -> PyResult<Bound<'py, PyBytes>> {
    let control = transport::decode_control(bytes.as_bytes()).map_err(stream_error)?;
    Ok(PyBytes::new(py, control.canonical()))
}
/// Admit portable projection text before framing a separate bounded binary buffer.
#[pyfunction]
fn encode_portable_packet<'py>(
    py: Python<'py>,
    projection: &Bound<'_, PyString>,
    media: &Bound<'_, PyBytes>,
) -> PyResult<Bound<'py, PyBytes>> {
    if media.as_bytes().len() > streams::MAX_MEDIA_BYTES {
        return Err(stream_error(streams::StreamError::MediaBound));
    }
    let projection = portable_text(projection)?;
    let packet = transport::packet(&projection, media.as_bytes().to_vec()).map_err(stream_error)?;
    let bytes = py
        .detach(|| transport::encode_packet(&packet))
        .map_err(stream_error)?;
    Ok(PyBytes::new(py, &bytes))
}
/// Decode only protocol-five bytes; legacy readers are never a fallback.
#[pyfunction]
fn decode_portable_packet<'py>(
    py: Python<'py>,
    bytes: &Bound<'_, PyBytes>,
) -> PyResult<(String, Bound<'py, PyBytes>)> {
    let packet = transport::decode_packet(bytes.as_bytes()).map_err(stream_error)?;
    Ok((
        transport::projection(&packet).map_err(stream_error)?,
        PyBytes::new(py, &packet.media),
    ))
}
/// Open the startup-bound stream endpoint, deriving its hello in native code.
#[pyfunction]
fn open_portable_stream<'py>(
    py: Python<'py>,
    startup: &ManagedContract,
    call_id: String,
    mode: String,
    seconds: f64,
) -> PyResult<Bound<'py, PyAny>> {
    let record = managed::Record::from_json(
        startup.record.name(),
        std::str::from_utf8(startup.record.canonical())
            .map_err(|_| stream_error(streams::StreamError::InvalidFrame))?,
    )
    .map_err(portable_error)?;
    let mode = streams::Mode::parse(&mode).map_err(stream_error)?;
    pyo3_async_runtimes::tokio::future_into_py(py, async move {
        let session = streams::Session::open_portable(&record, &call_id, mode, seconds)
            .await
            .map_err(stream_error)?;
        Ok(NativeStream {
            inner: Arc::new(session),
        })
    })
}
/// Protected native control channel with poisoned-channel cancellation semantics.
#[pyclass(module = "_skulk_capability_kernel")]
struct NativeControl {
    inner: Arc<transport::Channel>,
}
#[pymethods]
impl NativeControl {
    /// Receive admitted canonical bytes; interrupted reads close this channel.
    fn receive<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let channel = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let record = channel.receive().await.map_err(stream_error)?;
            Ok(Python::attach(|py| {
                PyBytes::new(py, record.canonical()).unbind()
            }))
        })
    }
    /// Validate and send a control; no inference authority is created by sending.
    fn send<'py>(
        &self,
        py: Python<'py>,
        source: &Bound<'_, PyString>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let source = portable_text(source)?;
        let control = transport::Control::from_json(&source).map_err(stream_error)?;
        let channel = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            channel.send(&control).await.map_err(stream_error)
        })
    }
    /// Await native timer/socket teardown from the language-owned finally path.
    fn close<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let channel = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            channel.close().await;
            Ok(())
        })
    }
}
/// Derive the endpoint/hello from an admitted startup; host trust remains separate.
#[pyfunction]
fn open_portable_control<'py>(
    py: Python<'py>,
    startup: &ManagedContract,
    seconds: f64,
) -> PyResult<Bound<'py, PyAny>> {
    let record = managed::Record::from_json(
        startup.record.name(),
        std::str::from_utf8(startup.record.canonical())
            .map_err(|_| stream_error(streams::StreamError::InvalidFrame))?,
    )
    .map_err(portable_error)?;
    pyo3_async_runtimes::tokio::future_into_py(py, async move {
        let channel = transport::Channel::connect(&record, seconds)
            .await
            .map_err(stream_error)?;
        Ok(NativeControl {
            inner: Arc::new(channel),
        })
    })
}

/// Explicit composed-call fingerprint; parsing never authenticates a caller.
#[pyfunction]
fn calls_profile_digest() -> &'static str {
    calls::profile_digest()
}

/// Admit bounded metadata before a host reads a body; not full frame admission.
#[pyfunction]
fn call_payload_size(py: Python<'_>, source: &Bound<'_, PyString>) -> PyResult<usize> {
    let source = portable_text(source)?;
    py.detach(|| calls::payload_size(&source))
        .map_err(stream_error)
}

/// Preserve exact raw payload bytes while admitting closed composition metadata.
#[pyfunction]
fn encode_call_frame<'py>(
    py: Python<'py>,
    source: &Bound<'_, PyString>,
    payload: &Bound<'_, PyBytes>,
) -> PyResult<Bound<'py, PyBytes>> {
    let source = portable_text(source)?;
    if payload.as_bytes().len() > calls::MAX_PAYLOAD_BYTES {
        return Err(stream_error(streams::StreamError::HeaderBound));
    }
    let payload = payload.as_bytes().to_vec();
    let bytes = py
        .detach(|| calls::Frame::from_json(&source, payload).map(|frame| calls::encode(&frame)))
        .map_err(stream_error)?;
    Ok(PyBytes::new(py, &bytes))
}

/// Return defensive metadata/body copies from one complete admitted frame.
#[pyfunction]
fn decode_call_frame<'py>(
    py: Python<'py>,
    packet: &Bound<'_, PyBytes>,
) -> PyResult<(Bound<'py, PyBytes>, Bound<'py, PyBytes>)> {
    let bytes = packet.as_bytes();
    let frame = py.detach(|| calls::decode(bytes)).map_err(stream_error)?;
    Ok((
        PyBytes::new(py, frame.metadata()),
        PyBytes::new(py, frame.payload()),
    ))
}

/// One startup-bound composed-call channel; no legacy profile guessing/fallback.
#[pyclass(module = "_skulk_capability_kernel")]
struct NativeCallControl {
    inner: Arc<transport::Channel>,
}

#[pymethods]
impl NativeCallControl {
    /// Receive one compiled-admitted metadata/body pair under native I/O fences.
    fn receive<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let channel = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let frame = channel.receive_call().await.map_err(stream_error)?;
            Ok(Python::attach(|py| {
                (
                    PyBytes::new(py, frame.metadata()).unbind(),
                    PyBytes::new(py, frame.payload()).unbind(),
                )
            }))
        })
    }
    /// Admit exact body binding, then await bounded backpressure without retries.
    fn send<'py>(
        &self,
        py: Python<'py>,
        source: &Bound<'_, PyString>,
        payload: &Bound<'_, PyBytes>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let source = portable_text(source)?;
        let bytes = payload.as_bytes();
        if bytes.len() > calls::MAX_PAYLOAD_BYTES {
            return Err(stream_error(streams::StreamError::HeaderBound));
        }
        let frame = py
            .detach(|| calls::Frame::from_json(&source, bytes.to_vec()))
            .map_err(stream_error)?;
        let channel = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            channel.send_call(&frame).await.map_err(stream_error)
        })
    }
    /// Fence pending operations and await native socket/timer cleanup.
    fn close<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let channel = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            channel.close().await;
            Ok(())
        })
    }
}

/// Derive protected endpoint/hello natively; the host still admits current trust.
#[pyfunction]
fn open_call_control<'py>(
    py: Python<'py>,
    startup: &ManagedContract,
    seconds: f64,
) -> PyResult<Bound<'py, PyAny>> {
    let source = std::str::from_utf8(startup.record.canonical())
        .map_err(|_| stream_error(streams::StreamError::InvalidFrame))?;
    let record =
        managed::Record::from_json(startup.record.name(), source).map_err(portable_error)?;
    pyo3_async_runtimes::tokio::future_into_py(py, async move {
        let channel = transport::Channel::connect(&record, seconds)
            .await
            .map_err(stream_error)?;
        Ok(NativeCallControl {
            inner: Arc::new(channel),
        })
    })
}
