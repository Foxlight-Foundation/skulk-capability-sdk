//! Maintainer-only JSON-lines adapter; never part of either runtime archive.

use std::io::{self, BufRead};

use serde_json::{Value, json};
use skulk_capability_kernel::{handshake, managed, portable};

fn evaluate(request: &Value) -> Result<Value, portable::PortableError> {
    let source = request["source"].as_str().expect("probe source");
    match request["operation"].as_str().expect("probe operation") {
        "canonical" => Ok(
            json!({"canonical": String::from_utf8(portable::canonical_json(source)?).expect("ASCII JSON")}),
        ),
        "schema" => Ok(
            json!({"valid": portable::Schema::new(request["definition"].as_str().expect("probe definition"))?.matches(source)?}),
        ),
        "managed" => {
            let name = request["name"].as_str().expect("probe root");
            Ok(
                json!({"canonical":String::from_utf8(managed::validate_contract_json(name,source)?).expect("ASCII JSON"),"digest":managed::contract_digest(name,source)?}),
            )
        }
        "handshake" => {
            let record = if request["action"] == "parse" {
                handshake::Record::from_json(request["name"].as_str().expect("probe name"), source)?
            } else {
                let startup = managed::Record::from_json(
                    "Startup",
                    request["startup"].as_str().expect("probe startup"),
                )?;
                if request["action"] == "create" {
                    handshake::from_startup(&startup, request["call_id"].as_str())?
                } else {
                    handshake::admit(&startup, source, request["call_id"].as_str())?
                }
            };
            Ok(json!({"canonical":std::str::from_utf8(record.canonical()).expect("ASCII JSON")}))
        }
        "contract" => {
            let name = request["name"].as_str().expect("probe root");
            Ok(
                json!({"canonical":String::from_utf8(portable::validate_contract_json(name,source)?).expect("ASCII JSON"),"digest":portable::contract_digest(name,source)?}),
            )
        }
        _ => panic!("unknown maintainer probe operation"),
    }
}

fn main() {
    for line in io::stdin().lock().lines() {
        let request: Value = serde_json::from_str(&line.expect("probe input")).expect("probe JSON");
        let response = evaluate(&request).unwrap_or_else(
            |error| json!({"error":error.to_string().split(':').next().expect("error code")}),
        );
        println!("{response}");
    }
}
