# TypeScript native application candidate

The public TypeScript source package is `@foxlight/skulk-capability-sdk`
**0.5.13-rc.1**. It is an SDK1 source candidate, not a stable SDK1 publication.
It supplies managed application setup, cached readiness/surfaces, exact-version
unary and all three streaming handlers, invocation-owned resources, and unary
and streaming child calls. It uses the already published native **0.7.0rc5**
cohort; it contains no owner, provider integration or router.

## Acquire and build

From a reviewed checkout of this public repository:

```sh
npm ci --ignore-scripts --prefix packages/typescript
npm run build --prefix packages/typescript
npm run check --prefix packages/typescript
npm test --prefix packages/typescript
mkdir -p .runtime
npm pack ./packages/typescript --pack-destination .runtime
uv run python tools/check_typescript_export.py .runtime/foxlight-skulk-capability-sdk-0.5.13-rc.1.tgz
```

Install that audited tarball into an author's own project with
`npm install --ignore-scripts /path/to/foxlight-skulk-capability-sdk-0.5.13-rc.1.tgz`.
The package contains JavaScript and declarations, with exact public native asset
URLs and integrity in its shrinkwrap. Installing/using it needs neither a native
compiler nor a Foxlight publishing account. TypeScript is needed only to compile
an author's TypeScript source. Supported runtimes are Node 22.18+ in the Node 22
line, on macOS ARM64 and glibc 2.34+ Linux x86_64/ARM64. Other platforms refuse;
there is no source-build or JavaScript-validator fallback. The loader measures
the selected native binary and conversion/loader bytes before loading them.

## Register application behavior

```ts
import {CapabilityApp, OutputFrame} from '@foxlight/skulk-capability-sdk';

const app = new CapabilityApp({
  async setup(startup, signal) {
    // Configuration is an owned copy. Open application resources here.
    const settings = startup.configuration;
    return {async close() {/* drain real application cleanup */}};
  },
  ready: () => true, // cached, synchronous, no I/O
  surfaces: () => [],
});

app.unary('example.echo', async call => ({text: call.payload.text ?? null}),
  {version: '1.0.0'});

app.stream('example.transform', async function* (call, inputs) {
  for await (const frame of inputs) {
    if (call.signal.aborted) return;
    yield new OutputFrame('chunk', frame.payload, {
      media: frame.media,
      mediaMetadata: {kind:'inline', media_type:'audio/pcm'},
    });
  }
  yield new OutputFrame('completed', {});
}, {version:'1.0.0'});

// The compatible managed launcher supplies this inherited private FD.
export async function serve(configurationDescriptor: number): Promise<void> {
  await app.serve(configurationDescriptor);
}
```

The manifest must declare exactly the registered IDs, versions, processing
domains, behavior, schemas and I/O modes. Version-less registration works only
for a unique signed ID. `serve` consumes and closes its startup descriptor once;
it refuses undeclared, ambiguous or wrong-mode registrations before setup and
connection. Configuration is ordinary application settings. Private startup
tokens and socket locations are excluded from `ApplicationStartup`.

The compiled managed/2, portable/2, composition/3, calls/2 and transport/2 readers
own validation/framing. The application additionally checks exact destination,
descriptor revision, original raw request digest and schemas before behavior.
`context.export()` is metadata, not caller authority. A compatible owner/Core
still authenticates the original caller and authorizes each actual dispatch.

## Read declared credentials

`startup.credentials` supplies a read-only `CredentialReader`. Its references
come from the actual signed `credential_inputs`; callers cannot select a vault,
backend, namespace or helper through a request. Selection stays in protected
startup and is omitted from public startup inspection and ordinary settings.

The application receives an explicit `PythonCredentialHelper` through
`CapabilityApp({credentialHelper: selectedHelper, ...})`. It names an absolute
selected interpreter and its SHA256, not a program looked up on PATH. That
interpreter must be a regular file owned by root or the application user and
must not be group/world writable. The selected runtime must contain the matching
public Python SDK0.5.13rc1 reader; native
backends additionally need its `native-credentials` dependencies and OS service.
The generated, signed Node runtime/launcher journey is still being completed.
For source development, supply this measured runtime explicitly. A missing
helper refuses reads, including optional reads; it never chooses ambient Python
or a different backend.

The three-platform source tests explicitly create their Python environment from
a uv-managed interpreter and check these custody preconditions. A preinstalled
system interpreter is not implicitly qualified as the selected helper.

```ts
// Retain startup.credentials during setup, then read inside an owned handler.
const secret = await credentials.read('api-token', {signal: call.signal});
if (secret === null) {
  // A valid selected store has no active value for an optional declaration.
} else {
  const bytes = secret.getSecretValue(); // an independent copy for your client
  // Never log or return the value. Secret inspection/JSON stays redacted.
}
```

Each read observes the current alias, including rotation and retirement. An
undeclared reference raises `TypeError` before helper/storage access. A required
absence, missing selection, damaged storage, wrong helper identity or unavailable
selected backend raises sanitized `CredentialUnavailable`. The helper uses the
same public Python reader and selected file/Keychain/Secret Service semantics.
The bridge receives a small credential-access projection after native startup
admission; it does not reinterpret the managed startup through a legacy model.
There is no credential mutation API, backend discovery, anonymous fallback or
creation of missing storage.

Interpreter acquisition is measured and bounded; helper startup has a five-second
budget and the complete read has a twelve-second budget. Selection metadata goes
over private pipes only after a matching reader-cohort hello. The cohort digest
checks compatibility; runtime attestation and the complete dependency closure
remain installer responsibilities. Up to eight reads may be in flight. Abort
or application shutdown kills and reaps each owned reader before its Promise
finishes. Dropped read Promises remain owned by the application. The contained
reader starts no nested helper, so cancellation cannot orphan a grandchild.
These are local credential-I/O budgets, independent of inference lifetime.

## Call another capability

Obtain the exact descriptor and node from a trusted application discovery or
configuration surface, then construct a `CapabilityEndpoint`. It is a copied
declaration, not a grant. Inside a handler:

```ts
const reply = await call.call(endpoint, {text:'hello'});
const exactReplyBytes = reply.rawPayload;

await call.stream(streamEndpoint, {text:'hello'}, async stream => {
  if (stream.mode !== 'server_streaming') {
    await stream.send({input:'audio'}, {
      media: new Uint8Array([0, 255]),
      mediaMetadata: {kind:'inline', media_type:'audio/pcm'},
    });
    await stream.completeInput(); // half-close; output stays open
  }
  for await (const frame of stream) {
    // Binary media stays separate. Terminal exposure waits for native end
    // and the exact host delegate_finished, not merely a terminal packet.
  }
});
```

Raw `Uint8Array` requests retain their exact wire bytes. Object requests go
through the issued conversion bridge and native portable canonicalization;
getters, cycles, unsafe integers and lossy JSON values refuse before sending.
One child may be pending per parent. A definite refusal can leave that parent
usable. Uncertainty, abandoned media or a dropped call retires delegation;
there is no automatic retry or replacement effect. Stream callbacks give
TypeScript a lexical ownership scope equivalent to Python's `async with`.

## Cancellation and cleanup

Use `call.signal` with asynchronous backend operations. Cancellation is
cooperative in JavaScript; a non-yielding handler cannot be preempted by an
AbortSignal. The owner retains its signed process-reap deadline. Finite caller
deadlines attenuate children. A null inference lifetime remains uncapped;
startup/health liveness is independent of model output.

Use `await call.enterResource(acquire)` to retain acquisition and LIFO cleanup
even when cancellation races acquisition. Cleanup drains before the control
result; repeated cancellation cannot interrupt it. A handler that returns with
unjoined child work retires its delegation. Stream success requires native
terminal/end plus control completion; input half-close does not end output.

## Qualification and remaining author journey

The three-platform CI job packs this package, audits the positive member/hash
inventory, installs it into a fresh project without lifecycle scripts, compiles
an independent public declaration consumer and runs real Node processes against
independent compiled Python control/media peers. It checks all four provider
and caller modes, raw bytes, media, schemas, cancellation, late correlation,
resource cleanup and inference beyond thirty seconds with ordinary health.
Credential cases additionally cover actual installed-wheel file reads,
rotation/retirement, required/optional absence, redaction, no fallback,
measured-helper mismatch, private hello ordering, malformed/bounded replies,
repeated cancellation, inherited process-group custody and dropped-read cleanup.
Native backend dispatch is tested with synthetic backend implementations; this
does not claim an OS account/session qualification. These synthetic host
observations establish author-side behavior only.

Full TypeScript parity still requires native durable inference jobs. The public
CLI/templates, successful native OS-session credential qualification and normal signed
language-specific runtime acquisition/install/update/rollback/remove journey
also remain release gates. The current Python runtime closure must not be
presented as a Node runtime, and ambient `node` availability is not an installed
runtime qualification. See [native publishing](native-publishing.md) and
[native jobs](native-jobs.md) for their existing, separately scoped contracts.
