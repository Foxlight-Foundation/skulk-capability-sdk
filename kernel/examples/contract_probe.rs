//! Maintainer differential probe, never a capability execution helper.
use std::io::{self, BufRead};

use skulk_capability_kernel::contracts::Registry;

fn main() {
    let registry = Registry::packaged().expect("packaged contract definition");
    for line in io::stdin().lock().lines() {
        let line = line.expect("probe input");
        let (name, source) = line.split_once('\t').expect("contract and JSON input");
        match registry.validate_json(name, source) {
            Ok(bytes) => println!(
                "ok\t{}",
                String::from_utf8(bytes).expect("ASCII projection")
            ),
            Err(error) => println!("error\t{error}"),
        }
    }
}
