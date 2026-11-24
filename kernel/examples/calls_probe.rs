//! Independent synthetic call codec observations, never a host authority.
use serde_json::{Value, json};
use skulk_capability_kernel::{calls, calls_v2};
use std::io::{self, BufRead, Write};
fn unhex(raw: &str) -> Option<Vec<u8>> {
    if !raw.len().is_multiple_of(2) {
        return None;
    }
    raw.as_bytes()
        .chunks_exact(2)
        .map(|p| u8::from_str_radix(std::str::from_utf8(p).ok()?, 16).ok())
        .collect()
}
fn main() {
    let mut output = io::BufWriter::new(io::stdout().lock());
    for line in io::stdin().lock().lines() {
        let result = line
            .ok()
            .filter(|s| s.len() <= 1_048_576)
            .and_then(|s| serde_json::from_str::<Value>(&s).ok())
            .and_then(|input| {
                if input["profile"] == "skulk.calls/2" {
                    let frame = if input["action"] == "encode" {
                        calls_v2::Frame::from_json(
                            input["source"].as_str()?,
                            unhex(input["payload_hex"].as_str()?)?,
                        )
                        .ok()?
                    } else {
                        calls_v2::decode(&unhex(input["packet_hex"].as_str()?)?).ok()?
                    };
                    Some(calls_v2::encode(&frame))
                } else if input["profile"].is_null() {
                    let frame = if input["action"] == "encode" {
                        calls::Frame::from_json(
                            input["source"].as_str()?,
                            unhex(input["payload_hex"].as_str()?)?,
                        )
                        .ok()?
                    } else {
                        calls::decode(&unhex(input["packet_hex"].as_str()?)?).ok()?
                    };
                    Some(calls::encode(&frame))
                } else {
                    None
                }
            });
        let result = match result {
            Some(wire) => {
                json!({"canonical":wire.iter().map(|b|format!("{b:02x}")).collect::<String>(),"error":null})
            }
            None => json!({"canonical":null,"error":"calls_invalid"}),
        };
        writeln!(output, "{result}").expect("probe output");
        output.flush().expect("probe flush");
    }
}
