# Skulk capability SDK for TypeScript

This is the SDK1 source candidate. The public package uses the issued native
0.7.0rc5 addons without a source-build fallback. Package installation grants no
Fabric authority. Managed installation and complete author journeys require
their own matching host/runtime qualification.

Supported author runtimes are Node 22.18 or later in the Node 22 line, on
macOS ARM64 and glibc 2.34+ Linux x86_64/ARM64. The exact supported addon is
selected and verified before it loads. Unsupported platforms refuse explicitly.
