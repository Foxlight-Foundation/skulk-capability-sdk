//! Thin conversions for the shared parent-bound discovery codec.
use super::{portable_text, stream_error};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyString};
use skulk_capability_kernel::discovery;

/// Identify the independent parent-bound metadata profile.
#[pyfunction]
fn discovery_profile_digest() -> &'static str {
    discovery::profile_digest()
}
/// Derive only the optional sibling of an admitted private startup.
#[pyfunction]
fn discovery_socket(source: &Bound<'_, PyString>) -> PyResult<String> {
    discovery::socket(&portable_text(source)?).map_err(stream_error)
}
/// Admit a response allocation hint before reading its body.
#[pyfunction]
fn discovery_payload_size(source: &Bound<'_, PyString>) -> PyResult<usize> {
    discovery::payload_size(&portable_text(source)?).map_err(stream_error)
}
/// Build a domain-separated startup proof without transmitting its control token.
#[pyfunction]
fn discovery_request<'py>(
    py: Python<'py>,
    startup: &Bound<'_, PyString>,
    context: &Bound<'_, PyString>,
    parent: &Bound<'_, PyString>,
    sequence: u64,
) -> PyResult<Bound<'py, PyBytes>> {
    let (startup, context, parent) = (
        portable_text(startup)?,
        portable_text(context)?,
        portable_text(parent)?,
    );
    let bytes = py
        .detach(|| {
            discovery::request(&startup, &context, &parent, sequence)
                .map(|frame| discovery::encode(&frame))
        })
        .map_err(stream_error)?;
    Ok(PyBytes::new(py, &bytes))
}
/// Admit complete bounded frame bytes without granting caller authority.
#[pyfunction]
fn decode_discovery_frame<'py>(
    py: Python<'py>,
    packet: &Bound<'_, PyBytes>,
) -> PyResult<(Bound<'py, PyBytes>, Bound<'py, PyBytes>)> {
    let bytes = packet.as_bytes();
    let frame = py
        .detach(|| discovery::decode(bytes))
        .map_err(stream_error)?;
    Ok((
        PyBytes::new(py, frame.metadata()),
        PyBytes::new(py, frame.payload()),
    ))
}
/// Verify the actual startup proof and export only parent correlation.
#[pyfunction]
fn admit_discovery_request<'py>(
    py: Python<'py>,
    startup: &Bound<'_, PyString>,
    packet: &Bound<'_, PyBytes>,
) -> PyResult<Bound<'py, PyBytes>> {
    let source = portable_text(startup)?;
    let bytes = packet.as_bytes();
    let admitted = py
        .detach(|| discovery::admit(&source, bytes))
        .map_err(stream_error)?;
    Ok(PyBytes::new(py, &admitted))
}
/// Authenticate exact metadata bytes or a body-free bounded refusal.
#[pyfunction]
fn discovery_reply<'py>(
    py: Python<'py>,
    startup: &Bound<'_, PyString>,
    request: &Bound<'_, PyString>,
    payload: &Bound<'_, PyBytes>,
    error: Option<String>,
) -> PyResult<Bound<'py, PyBytes>> {
    let startup = portable_text(startup)?;
    let source = portable_text(request)?;
    if payload.as_bytes().len() > discovery::MAX_PAYLOAD_BYTES {
        return Err(stream_error(
            skulk_capability_kernel::streams::StreamError::HeaderBound,
        ));
    }
    let body = payload.as_bytes().to_vec();
    let bytes = py
        .detach(|| {
            discovery::reply(&startup, &source, body, error.as_deref())
                .map(|frame| discovery::encode(&frame))
        })
        .map_err(stream_error)?;
    Ok(PyBytes::new(py, &bytes))
}
/// Verify owner proof and every original request binding before exposing bytes.
#[pyfunction]
fn discovery_response<'py>(
    py: Python<'py>,
    startup: &Bound<'_, PyString>,
    request: &Bound<'_, PyString>,
    packet: &Bound<'_, PyBytes>,
) -> PyResult<(Option<String>, Bound<'py, PyBytes>)> {
    let startup = portable_text(startup)?;
    let source = portable_text(request)?;
    let bytes = packet.as_bytes();
    let (error, body) = py
        .detach(|| discovery::response(&startup, &source, bytes))
        .map_err(stream_error)?;
    Ok((error, PyBytes::new(py, &body)))
}
pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(discovery_profile_digest, module)?)?;
    module.add_function(wrap_pyfunction!(discovery_socket, module)?)?;
    module.add_function(wrap_pyfunction!(discovery_payload_size, module)?)?;
    module.add_function(wrap_pyfunction!(discovery_request, module)?)?;
    module.add_function(wrap_pyfunction!(decode_discovery_frame, module)?)?;
    module.add_function(wrap_pyfunction!(admit_discovery_request, module)?)?;
    module.add_function(wrap_pyfunction!(discovery_reply, module)?)?;
    module.add_function(wrap_pyfunction!(discovery_response, module)?)?;
    Ok(())
}
