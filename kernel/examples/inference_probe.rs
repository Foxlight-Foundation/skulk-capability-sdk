//! Bounded synthetic inference-authority probe; no active-call registry or host effects.
use serde_json::{Value, json};
use skulk_capability_kernel::{
    composition_v2::{self as composition, CompositionError, Record},
    portable_v2,
};
use std::io::{self, BufRead, Write};

fn evaluate(request: &Value) -> Result<Record, CompositionError> {
    let text = |key: &str| request[key].as_str().ok_or(CompositionError::Invalid);
    let record = Record::from_json(text("name")?, text("source")?)?;
    if request["action"] == "parse" {
        return Ok(record);
    }
    let parent = Record::from_json("Context", text("parent")?)?;
    let policy = Record::from_json("Policy", text("policy")?)?;
    let hex = text("request_hex")?;
    if !hex.len().is_multiple_of(2) {
        return Err(CompositionError::Invalid);
    }
    let bytes = hex
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            u8::from_str_radix(
                std::str::from_utf8(pair).map_err(|_| CompositionError::Invalid)?,
                16,
            )
            .map_err(|_| CompositionError::Invalid)
        })
        .collect::<Result<Vec<u8>, _>>()?;
    composition::admit(
        &parent,
        &record,
        &policy,
        text("executor")?,
        &bytes,
        request["now_unix_ms"]
            .as_u64()
            .ok_or(CompositionError::Invalid)?,
        text("scope_id")?,
    )
}
fn main() {
    let mut output = io::BufWriter::new(io::stdout().lock());
    for line in io::stdin().lock().lines() {
        let request = line
            .ok()
            .filter(|line| line.len() <= 1_048_576)
            .and_then(|line| serde_json::from_str::<Value>(&line).ok());
        if let Some(request) = request.as_ref().filter(|value| value["action"] == "author") {
            let result = (|| {
                let name = request["name"]
                    .as_str()
                    .ok_or(skulk_capability_kernel::portable::PortableError::Contract)?;
                let source = request["source"]
                    .as_str()
                    .ok_or(skulk_capability_kernel::portable::PortableError::Contract)?;
                let bytes = portable_v2::validate_contract_json(name, source)?;
                let digest = portable_v2::contract_digest(name, source)?;
                Ok::<_, skulk_capability_kernel::portable::PortableError>((bytes, digest))
            })();
            let response = match result {
                Ok((bytes, digest)) => {
                    json!({"canonical":std::str::from_utf8(&bytes).expect("scalar JSON"),"error":null,"digest":digest})
                }
                Err(error) => {
                    json!({"canonical":null,"error":error.to_string().split(':').next().expect("bounded error"),"digest":null})
                }
            };
            writeln!(output, "{response}").expect("probe output");
            output.flush().expect("probe flush");
            continue;
        }
        let result = request
            .ok_or(CompositionError::Invalid)
            .and_then(|value| evaluate(&value));
        let response = match result {
            Ok(record) => {
                json!({"canonical":std::str::from_utf8(record.canonical()).expect("scalar JSON"),"error":null,"digest":null})
            }
            Err(error) => {
                json!({"canonical":null,"error":error.to_string().split(':').next().expect("bounded error"),"digest":null})
            }
        };
        writeln!(output, "{response}").expect("probe output");
        output.flush().expect("probe flush");
    }
}
