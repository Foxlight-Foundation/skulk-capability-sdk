# Discover caller-authorized capabilities

SDK 0.5.14rc1 adds typed discovery in Python and TypeScript over Core's existing
`GET /v1/native-inference` API. Supply a Skulk client key enrolled in **Settings →
Inference access**. Core returns only the ready destinations permitted to that
client. An enrolled client with no permitted destinations receives an empty
directory. A revoked or invalid key is refused.

This is an SDK1 candidate component. It requires the matching native SDK reader
and a compatible Core host; the [installed author setup](native-authoring.md#install-and-enable-on-a-compatible-host)
explains host and caller prerequisites. Provider account tokens stay in their
capability. A directory observation grants no authority and does not replace
Core's current-policy checks when work is dispatched.
This is an external client API using that client's own enrolled identity.
Application invocations retain their original caller through the separate
`Invocation.call`/stream composition interfaces; a service bearer cannot replace
an invocation's parent scope. Generic governed and Steward exposure remains a
separate SDK1 acceptance item.

## Discovery inside an active invocation

The [SDK 0.5.15rc1 prerelease](https://github.com/Foxlight-Foundation/skulk-capability-sdk/releases/tag/v0.5.15-rc.1)
adds `await invocation.discover()` in both
Python and TypeScript. It takes no client key or caller argument. Core resolves
the actual active parent on its protected owner connection and intersects fresh
observations with that parent's original caller permissions, exact allowed
destinations, processing domains and ancestry. It excludes the executor and
ancestors across version aliases. Discovering an offer never grants access.

```python
async def handle(invocation: Invocation) -> dict[str, JsonValue]:
    directory = await invocation.discover()
    offer = directory.select("configured.application", "configured.read", "1.0.0")
    reply = await invocation.call(offer.endpoint, {"input": "application task"})
    return reply.payload
```

```ts
const directory = await invocation.discover();
const offer = directory.select('configured.application', 'configured.read', '1.0.0');
const reply = await invocation.call(offer.endpoint, {input: 'application task'});
```

Only one query or child call may be pending per invocation. Await discovery
before calling a child or completing the handler. Cancellation owns and joins
the socket cleanup; interrupted or invalid observations retire the parent. A
separate monotonic metadata sequence never consumes a child-effect sequence.
The host rechecks caller credentials, policy, parent and owner before delivery.
There is no service bearer, permission cache, retry, automatic selection or route.

This optional feature uses the separately fingerprinted `skulk.discovery/1`
companion channel in native 0.7.0rc7 and requires a matching owner/Core bridge.
Issued calls/2 and startup bytes retain their meaning. Older cohorts or hosts
report `DiscoveryError("unsupported")`; they never try a different endpoint.
The published SDK15 Python and TypeScript packages select the
[issued native7 cohort](https://github.com/Foxlight-Foundation/skulk-capability-sdk/releases/tag/native-v0.7.0-rc.7).
Package availability, matching owner/Core receipt adoption and normally installed
qualification are separate gates. Installing the SDK alone does not upgrade the
host or enroll a caller. This is a component prerelease; stable SDK1 and a
compatible Skulk release require their coordinated acceptance. Earlier native5/6
release assets and profile meanings are preserved.
This invocation read uses the local companion channel and needs no HTTPX extra.
The examples below describe the distinct external enrolled-client interface.

## Python

From a reviewed SDK checkout, prepare the public packages and issued native wheel:

```sh
uv sync --frozen --all-packages --no-editable --group native-runtime \
  --extra native-discovery --reinstall-package skulk-capability-sdk \
  --reinstall-package skulk-capability-tools
```

The `native-discovery` extra supplies HTTPX. It adds no provider SDK or account
integration. Set `SKULK_CLIENT_TOKEN` only in the caller's runtime environment;
keep it out of source, manifests, URLs and logs.

```python
import asyncio
import os

from skulk_capability_sdk.discovery import discover_capabilities


async def main() -> None:
    directory = await discover_capabilities(
        os.environ.get("SKULK_API_URL", "http://127.0.0.1:52415"),
        os.environ["SKULK_CLIENT_TOKEN"],
    )
    for offer in directory.offers:
        print(offer.plugin_id, offer.endpoint.capability_id,
              offer.endpoint.version, offer.io_mode)


asyncio.run(main())
```

The immutable `CapabilityOffer` retains `plugin_id`, `runtime_digest`,
`incarnation`, the compiled `endpoint`, `behavior`, `processing_domain`,
`io_mode`, and the signed resource settings `stream_seconds`, `cleanup_seconds`,
`startup_seconds` and `inference_seconds`. A null inference ceiling remains null.
Calling `endpoint.descriptor()` returns an owned copy.

## TypeScript

Build/install the audited public package as described in
[TypeScript authoring](typescript-authoring.md). Its existing Node 22/native
requirements also apply to discovery; no Python credential helper is used for
this HTTP read.

```ts
import {discoverCapabilities} from '@foxlight/skulk-capability-sdk';

const token = process.env.SKULK_CLIENT_TOKEN;
if (token === undefined) throw new Error('A Skulk client key is required');
const directory = await discoverCapabilities(
  process.env.SKULK_API_URL ?? 'http://127.0.0.1:52415', token,
);
for (const offer of directory.offers) {
  console.log(offer.pluginId, offer.endpoint.capabilityId,
    offer.endpoint.version, offer.ioMode);
}
```

The TypeScript offer has the equivalent immutable `pluginId`, `runtimeDigest`,
`incarnation`, `endpoint`, `behavior`, `processingDomain`, `ioMode`,
`streamSeconds`, `cleanupSeconds`, `startupSeconds` and `inferenceSeconds`.
`discoverCapabilities` accepts an optional `signal` for caller cancellation.

## Exact selection and lifetime

`directory.select(plugin_id, capability_id, version, node_id=...)` in Python,
or `directory.select(pluginId, capabilityId, version, nodeId)` in TypeScript,
requires exactly one match. Supply the node ID when the same version exists at
multiple destinations. Missing and ambiguous matches refuse. There is no best
match, default model selection, classifier or fallback route.

Each read is fresh. The SDK does not cache grants or automatically retry a
refusal, timeout or unavailable host. An offer's runtime and incarnation describe
the generation observed; later dispatch still requires independently current
caller authorization and destination custody. Read-only discovery exposes only
the currently supported `read` and `inference` behaviors. Governed proposal and
execution surfaces require their separate acceptance.

Remote origins require HTTPS with normal certificate verification. Plain HTTP
accepts only literal loopback addresses, such as `127.0.0.1` or `[::1]`; use a
literal address rather than `localhost`. Origins must contain no user information,
path prefix, query or fragment. Redirects and environment proxy configuration
are disabled for these credential-bearing reads. Node uses an owned one-off
direct HTTP(S) connection outside the global fetch pool, with TLS verification
explicitly enabled even when the containing process disables it globally. Both
languages request identity encoding and refuse other encodings at the headers
before any response decompression.
For a private cluster CA, pass its trusted PEM certificate as Python
`certificate_authority=...` or TypeScript `certificateAuthority: ...`. Certificate
chain and hostname verification stay enabled; there is no insecure TLS option.

Decoded directory metadata is bounded at 8 MiB and 4,096 offers. Admission is atomic:
duplicate JSON keys or destinations, unknown fields, malformed records, invalid
native descriptors, and descriptor/target identity or effect/domain mismatches
refuse the complete observation. Descriptor metadata retains the native 262,144-byte
contract bound, separately from 65,536-byte invocation payloads.

The optional `timeout_seconds`/`timeoutSeconds` defaults to 30 seconds and bounds
this metadata read only. Response media and pending HTTP work are closed before
timeout or cancellation returns. It adds no inference duration or output cap.
Errors expose bounded codes through Python `DiscoveryError.code` or TypeScript
`DiscoveryError.code`; raw responses, URLs and credentials are excluded.
