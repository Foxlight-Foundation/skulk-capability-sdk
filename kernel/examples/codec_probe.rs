//! Maintainer differential probe; never a capability runtime helper process.

use std::io::{self, BufRead, Write};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut output = io::BufWriter::new(io::stdout().lock());
    for line in io::stdin().lock().lines() {
        let line = line?;
        let encoded = skulk_capability_kernel::codec::canonical(&line)?;
        output.write_all(&encoded)?;
        output.write_all(b"\n")?;
    }
    Ok(())
}
