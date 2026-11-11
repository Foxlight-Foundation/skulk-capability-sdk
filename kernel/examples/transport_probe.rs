//! Bounded synthetic protocol-five full-wire probe, never a host authority.
use serde_json::{Value, json};
use skulk_capability_kernel::transport;
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
    let mut out = io::BufWriter::new(io::stdout().lock());
    for line in io::stdin().lock().lines() {
        let result = line
            .ok()
            .filter(|line| line.len() <= 262_144)
            .and_then(|line| serde_json::from_str::<Value>(&line).ok())
            .and_then(|request| {
                let source = request["source"].as_str()?;
                if request["action"] == "control" {
                    transport::Control::from_json(source)
                        .ok()
                        .map(|c| transport::encode_control(&c))
                } else {
                    let media = unhex(request["media_hex"].as_str()?)?;
                    transport::packet(source, media)
                        .and_then(|p| transport::encode_packet(&p))
                        .ok()
                }
            });
        let response = match result {
            Some(wire) => {
                json!({"canonical":wire.iter().map(|b|format!("{b:02x}")).collect::<String>(),"error":null})
            }
            None => json!({"canonical":null,"error":"transport_invalid"}),
        };
        writeln!(out, "{response}").expect("probe output");
        out.flush().expect("probe flush");
    }
}
