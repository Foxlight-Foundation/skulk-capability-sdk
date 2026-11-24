# SDK Native Reference

The current source is reference0.2.1 for SDK1.0.1/native0.7.0. See the
[SDK1 cohort](../../docs/sdk1-cohort.md) for matching runtime qualification.
The earlier reference0.1.0 and signed0.2.0 inputs retain their original bytes.
This is an optional developer example; normal Skulk and Integrator use do not
require installing it.

A model-free managed capability that demonstrates the public Python SDK and
supplies a small reproducible native runtime for SDK1 author qualification.
The source is MIT, like the SDK. The separately supplied owner runtime keeps
its own license and distribution; it is not included in this source directory.

| Capability | Mode | Behavior |
| --- | --- | --- |
| `foxlight.sdk-reference.echo@1.0.0` | Unary | Return input text |
| `foxlight.sdk-reference.stream@1.0.0` | Server streaming | Emit text and one completion |
| `foxlight.sdk-reference.collect@1.0.0` | Client streaming | Join input text after half-close |
| `foxlight.sdk-reference.duplex@1.0.0` | Bidirectional | Echo each text/media frame, then complete |

This example uses no model, provider account, network client or external
processing. Combined input text is limited to the declared final schema's4096
characters, and the SDK owns framing, cancellation and child lifetime.
Handlers consume host-provided invocations; they do not create caller authority.

Use the [public package quickstart](../../docs/native-public-quickstart.md) to
prepare an isolated author environment. From this directory, with `AUTHOR` set
to that environment's absolute `skulk-cap` executable:

```sh
"$AUTHOR" run --native
```

In a second terminal in this directory:

```sh
"$AUTHOR" debug --native
"$AUTHOR" invoke --native --capability foxlight.sdk-reference.echo@1.0.0 \
  --input '{"text":"Hello"}'
"$AUTHOR" invoke --native --capability foxlight.sdk-reference.collect@1.0.0 \
  --input-chunk '{"text":"one"}' --input-chunk '{"text":"two"}'
```

Stop with Ctrl-C. See the [native publishing guide](../../docs/native-publishing.md)
for exact wheelhouse, independent rebuild, signing and feed inputs. Building this
source does not register a publisher or grant permission to an installed app.
A signed preview requires a compatible SDK1 Core/owner candidate; the version
range alone does not prove a host implements the native contracts. Normal
configuration enable and exact client enrollment still apply.

Reference Store delivery and the complete independent author acceptance remain
separate qualification gates. Source presence is not a published runtime recipe.
