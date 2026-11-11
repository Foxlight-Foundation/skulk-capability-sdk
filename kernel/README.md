# Skulk capability kernel

MIT shared Rust implementation with native Python and Node bindings. This source
includes the `skulk.portable/1` descriptor/envelope candidate, compiled schemas,
canonical JSON, stream primitives and durable-operation primitives.

The portable profile is defined in [portable-v1.json](contracts/portable-v1.json).
Its native authority and complete synthetic outcome vectors are shared across
Rust, Python and TypeScript. The [reference](../docs/native-kernel.md) documents
the profile and its limits.

Build from the repository root with Rust 1.94.1, Node 22 and Python 3.13:

```sh
uv sync --frozen --all-packages --no-editable
uv run --no-sync python tools/check_kernel_export.py
uv run --no-sync python -m tools.qualify_kernel
```

Qualification builds a wheel and Node archive, installs the wheel offline, runs
Rust and binding tests, checks a typed TypeScript consumer, audits both archive
contents and compares all 1,959 portable outcomes through three backends. These
are maintainer artifacts; building them does not select an installed runtime.
The report records `runtime_adoption: false`.

The existing Python SDK retains its released contract. The portable envelope is
not yet the complete managed manifest/startup/installation interface. Historical
fixtures protect known primitive behavior; they do not define acceptance for
the distinct portable profile.

Versioned prerelease package acquisition and its distinct installed-adoption
gates are described in [native candidates](../docs/native-candidates.md).
