//! Maintainer-only generic schema probe; never distributed as a runtime helper.

use std::io::{self, BufRead};

use skulk_capability_kernel::schema::Schema;

fn main() {
    for line in io::stdin().lock().lines() {
        let line = line.expect("probe input");
        let (schema, payload) = line.split_once('\t').expect("schema and payload");
        let compiled = if std::env::args().any(|argument| argument == "--installed") {
            Schema::installed(schema)
        } else {
            Schema::new(schema)
        };
        match compiled {
            Err(_) => println!("schema-error"),
            Ok(schema) if schema.matches(payload) => println!("valid"),
            Ok(_) => println!("invalid"),
        }
    }
}
