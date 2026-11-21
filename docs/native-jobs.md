# Native durable inference jobs

The SDK1 source candidate provides `skulk_capability_sdk.jobs`. It wraps the
published native journal and scheduler; Python does not implement a second
queue or recovery policy. Use the separately installed qualified native kernel
and a compatible managed/2 host, as described in [native applications](native-application.md).
This is candidate authoring support, not a stable SDK1 release or a completed
signed independent-author installation journey.

A durable job differs from a streaming request: accepting a job persists its
identity and returns while the backend continues. Cancelling or losing the
acceptance RPC does not automatically cancel an accepted job. Reconnect with
the same operation ID to inspect it; generating another ID can create another
job. Job runtime and queue limits come from the explicitly declared manifest
`operations` bounds, independently of ordinary inference request lifetimes.

## Declare and register the service

Build a definition from portable object input/result schemas:

```python
from skulk_capability_sdk.jobs import DurableJobs, JobDefinition

spec = JobDefinition.create(
    id="studio.render",
    version="1.0.0",
    title="Render jobs",
    description="Create and observe inference render jobs",
    input_schema={
        "type": "object",
        "properties": {"prompt": {"type": "string"}},
        "required": ["prompt"],
        "additionalProperties": False,
    },
    result_schema={
        "type": "object",
        "properties": {"asset_id": {"type": "string"}},
        "required": ["asset_id"],
        "additionalProperties": False,
    },
    processing_domain="local",
)
```

Put both objects returned by `spec.descriptors()` into the signed managed/2
manifest's contract envelope. Set its effect to `inference` and declare the
`operations` bounds. The compiled managed validator refuses operation
annotations without those bounds. Nothing here constructs a legacy descriptor
or turns a declaration into caller authorization.

| Endpoint | Behavior / domain | Input verbs |
| --- | --- | --- |
| `studio.render.read` | `read` / `local` | `status`, `list`, `log` |
| `studio.render.work` | `inference` / the declared backend domain | `start`, `cancel`, `plan` |

The application setup owns the service lifetime:

```python
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from skulk_capability_sdk.application import ApplicationStartup, CapabilityApp

# backend implements JobAdapter; estimate returns an OperationPlan.
jobs = DurableJobs(spec, backend, plan=estimate)

@asynccontextmanager
async def setup(startup: ApplicationStartup) -> AsyncGenerator[None]:
    # Normal managed owners select a stable private process working directory.
    # Choose storage here, never from an invocation's input.
    async with jobs.serve(startup, directory=Path.cwd() / "render-jobs-v1"):
        yield

app = CapabilityApp(setup=setup)
jobs.register(app)
# The application entrypoint passes its protected startup descriptor to app.serve.
```

`ApplicationStartup.operation_bounds` and `endpoint(id, version)` expose only
metadata already admitted from the actual native startup. Opening the job
service verifies its exact descriptors against those endpoints. The host still
independently authorizes the original caller, processing domain and signed
implementation at dispatch. `read` cannot submit jobs. This facade supports
inference jobs; it does not enable governed provisioning, proposal or arbitrary
write execution, which the current composition cohort refuses.

## Backend adapter

Implement the public `JobAdapter` protocol from `skulk_capability_sdk.operations`:

| Method | Responsibility |
| --- | --- |
| `submit(operation_id, input)` | Submit once and return an opaque backend reference. The supplied ID is a stable scoped backend idempotency key, not the caller's display ID. |
| `observe(reference)` | Return state, progress, safe stage, result and safe error for that exact backend job. |
| `cancel(reference)` | Request cancellation; acceptance alone does not prove the backend stopped. |

Return a terminal state only after observing it. Use asynchronous I/O and
cancellation-safe cleanup. The native scheduler bounds adapter waits and rejects
late replies. Generic adapter exceptions produce sanitized failures. A
`SubmissionRefused` is valid only when no submission occurred. Never put provider
bodies, secrets or sensitive URLs into adapter stage/error text. Native result
validation runs before persistence, including portable numeric/Unicode rules.

An accepted job continues under its durable acceptance and declared runtime
limit. A start RPC's completion, deadline or client revocation does not itself
retract that acceptance. Explicit cancellation is a separate authorized request.
The job service retains no live invocation or caller context and cannot use an
expired context to delegate later. Adapters needing new capability calls must
use a separately supported host authority path; exporting a context is not one.

## Caller isolation and recovery

Within an installed node and exact job definition, each original admitted caller
has its own operation IDs, status, list, logs and cancellation namespace. The
principal includes its issuer and caller kind. A different caller cannot inspect
or cancel another caller's job by guessing its display ID. Backend references
and shared queue positions are withheld from responses. List counts and pagination describe only that
caller's visible jobs.

Idempotency compares portable canonical input, so `1` and `1.0` have the same
native meaning. Reusing an ID with different input refuses. Display pruning
never removes the durable reservation; a pruned ID cannot start again.
Rejected starts release their caller-index allocation after the native
reservation attempt has finished. This never deletes a native replay fence.
Cancellation before reservation drains that allocation without submitting later;
cancellation after reservation starts can still leave durable accepted work.

The versioned `skulk.jobs/1` state binding records the installed node, exact
signed descriptor identities and admitted operation bounds. A restart or update
cannot silently increase an already accepted job's runtime limit. Changing these
bounds requires an explicit qualified migration that preserves existing work
and replay fences. Both its native journal and separate `origins.sqlite` caller
index contain matching bindings. All live journal observations and writes go
through the native API; Python SQLite owns only the separate caller index.
This avoids competing SQLite implementations accessing one live database.
Each database retains its own 128MiB ceiling; exhausted storage refuses without
resetting accepted work. This is a new facade namespace around the shared existing
journal format; legacy hashes, old journals and issued profiles are unchanged.
A changed definition/node, missing marker or missing/corrupt/truncated initialized
journal requires explicit recovery. The service never relabels an old journal,
resets its reservations or automatically adopts another node's jobs. Keep the
whole state directory together in backups. List observations and restart index
cleanup are bounded; an expired list observation refuses instead of reporting
partial counts. Index cleanup never removes uncertain work or native replay
fences. Consumer migration/restore must be
qualified before a release changes these identities.

After restart, queued/pending jobs that never reached submission become failed
with an interruption reason instead of being automatically executed. Accepted
references reconcile by observation. An uncertain
submission with no reference stays interrupted and outstanding; it is not
resubmitted. Application/backend-specific reconciliation may be needed. The
private custody lock remains held until native scheduling and actual adapter
cleanup finish, including repeated shutdown cancellation. A compatible owner can
forcefully reap the whole application after its signed shutdown grace; remote
backend work still requires the retained original job reference or explicit
uncertainty reconciliation.

The public [job tests](../tests_native/test_jobs.py) exercise these boundaries
through compiled native application calls. The [separate process fixture](../tests_native/fixtures/job_application.py)
uses a synthetic file-backed acceptance signal: SIGKILL after that effect leaves
an uncertain job, and restarting/repeating its ID causes no second submission.
These account-free process/socket tests do not replace signed Core/owner
installed qualification or claim provider/hardware support.
