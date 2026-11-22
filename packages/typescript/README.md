# Skulk capability SDK for TypeScript

This is the SDK1 source candidate. The public package uses the issued native
0.7.0rc5 addons without a source-build fallback. Package installation grants no
Fabric authority. Managed installation and complete author journeys require
their own matching host/runtime qualification.

Supported author runtimes are Node 22.18 or later in the Node 22 line, on
macOS ARM64 and glibc 2.34+ Linux x86_64/ARM64. The exact supported addon is
selected and verified before it loads. Unsupported platforms refuse explicitly.

Declared credential reads require an explicitly selected matching public Python
SDK helper. Values are redacted, reads observe rotation/retirement, and owned
helpers are reaped on cancellation. No ambient interpreter or backend fallback
is selected. Signed Node/runtime and native OS-session qualification remain
separate from source and clean-package checks.
