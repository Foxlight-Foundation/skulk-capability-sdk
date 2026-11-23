//! Independent synthetic wire observations; no host permissions are established.
use serde_json::{Value, json};
use skulk_capability_kernel::discovery;
use std::io::{self, BufRead, Write};
fn unhex(source: &str) -> Option<Vec<u8>> {
    if !source.len().is_multiple_of(2) {
        return None;
    }
    source
        .as_bytes()
        .chunks_exact(2)
        .map(|p| u8::from_str_radix(std::str::from_utf8(p).ok()?, 16).ok())
        .collect()
}
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
fn observe(v: Value) -> Option<String> {
    let source = v["source"].as_str()?;
    match v["action"].as_str()? {
        "request" => Some(hex(&discovery::encode(
            &discovery::request(
                v["startup"].as_str()?,
                v["context"].as_str()?,
                v["parent"].as_str()?,
                v["sequence"].as_str()?.parse().ok()?,
            )
            .ok()?,
        ))),
        "admit" => String::from_utf8(
            discovery::admit(v["startup"].as_str()?, &unhex(v["packet_hex"].as_str()?)?).ok()?,
        )
        .ok(),
        "reply" => Some(hex(&discovery::encode(
            &discovery::reply(
                v["startup"].as_str()?,
                source,
                unhex(v["payload_hex"].as_str()?)?,
                v["code"].as_str(),
            )
            .ok()?,
        ))),
        "response" => {
            let (code, body) = discovery::response(
                v["startup"].as_str()?,
                source,
                &unhex(v["packet_hex"].as_str()?)?,
            )
            .ok()?;
            Some(json!({"code":code,"payload_hex":hex(&body)}).to_string())
        }
        "hint" => Some(discovery::payload_size(source).ok()?.to_string()),
        _ => None,
    }
}
fn main() {
    let mut output = io::BufWriter::new(io::stdout().lock());
    for line in io::stdin().lock().lines() {
        let result = line
            .ok()
            .filter(|v| v.len() <= 1_048_576)
            .and_then(|v| serde_json::from_str(&v).ok())
            .and_then(observe);
        let result = match result {
            Some(value) => json!({"canonical":value,"error":null}),
            None => json!({"canonical":null,"error":"discovery_invalid"}),
        };
        writeln!(output, "{result}").expect("probe output");
        output.flush().expect("probe flush");
    }
}
