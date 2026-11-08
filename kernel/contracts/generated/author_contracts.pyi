"""Generated author-v1 declarations; native implementation is private."""

from typing import Literal

from pydantic import JsonValue

class ApprovedEffect:
    authority_id: str
    campaign_policy_digest: str | None
    capability_id: str
    capability_node_id: str
    capability_version: str
    currency: Literal["USD"]
    descriptor_revision: str
    effect: Literal["acquire", "release"]
    expires_at_unix_seconds: int
    host_id: str
    idempotency_key: str
    key_id: str
    maximum_lifetime_seconds: int
    maximum_total_cost_microunits: int
    operator_id: str
    plan_digest: str
    policy_revision: str
    proposal_digest: str | None
    provider_selection: str
    transport_node_id: str

class BlobMedia:
    blob_id: str
    kind: Literal["blob"]
    media_type: str
    sha256: str
    size_bytes: int

class Bounds:
    backoff_seconds: float
    call_seconds: float
    concurrency: Literal[1]
    health_seconds: float
    log_bytes: Literal[0]
    max_children: int
    restart_budget: int
    shutdown_seconds: float
    startup_seconds: float
    stream_seconds: float

class CredentialInput:
    credential_id: str
    description: str
    required: bool
    title: str

class Descriptor:
    annotations: dict[str, str] | None
    description: str
    id: str
    input_chunk_schema: dict[str, JsonValue] | None
    input_schema: dict[str, JsonValue]
    io_mode: Literal["unary", "server_streaming", "client_streaming", "bidirectional"]
    output_chunk_schema: dict[str, JsonValue] | None
    output_schema: dict[str, JsonValue] | None
    title: str
    version: str

class Health:
    kind: Literal["health"]
    nonce: str
    protocol: int
    ready: bool
    surfaces: tuple[SurfaceReport, ...]

class Hello:
    bundle_id: str
    bundle_version: str
    capability_node_id: str
    host_id: str
    kind: Literal["hello"]
    manifest_sha256: str
    protocol: int
    token: str
    transport_node_id: str

class InlineMedia:
    channels: int | None
    codec: str | None
    data: bytes
    duration_seconds: float | None
    kind: Literal["inline"]
    media_type: str
    sample_rate: int | None
    timestamp_seconds: float | None

class Invoke:
    call_id: str
    capability_id: str
    descriptor_revision: str
    kind: Literal["invoke"]
    payload: dict[str, JsonValue]
    protocol: int
    remaining_seconds: float
    version: str

class Manifest:
    arguments: tuple[str, ...]
    bounds: Bounds
    bundle_id: str
    bundle_version: str
    configuration_schema: dict[str, JsonValue] | None
    credential_inputs: tuple[CredentialInput, ...]
    descriptors: tuple[Descriptor, ...]
    effect: Literal["read", "governed"]
    executable: str
    executable_sha256: str
    operations: OperationBounds | None
    preflight_profile: Literal["local", "runpod-native"] | None
    protocol: int
    runtime_dependencies: tuple[str, ...]
    skulk_requires: str
    steward: tuple[StewardExposure, ...]
    surfaces: tuple[Surface, ...]
    title: str | None

class OperationBounds:
    log_bytes: int
    max_queued: int
    max_runtime_seconds: int
    reservations: int
    retain: int
    stale_after_seconds: int

class OperationCall:
    input: dict[str, JsonValue] | None
    offset: int | None
    op: Literal["start", "status", "cancel", "list", "log", "plan"]
    operation_id: str | None

class OperationLog:
    next_offset: int
    offset: int
    operation_id: str
    text: str
    truncated: bool

class OperationPage:
    active: int
    next_offset: int | None
    operations: tuple[OperationStatus, ...]
    outstanding: int

class OperationPlan:
    estimated_cost_microunits: int | None
    estimated_seconds: int | None
    expires_at: int
    input: dict[str, JsonValue] | None
    plan_digest: str
    summary: str

class OperationStatus:
    backend_reference: str | None
    error: str | None
    finished_at: int | None
    input: dict[str, JsonValue] | None
    input_digest: str
    operation_id: str
    progress: float | None
    queue_position: int | None
    result: dict[str, JsonValue] | None
    stage: str | None
    stale: bool
    state: Literal[
        "queued", "running", "succeeded", "failed", "cancelled", "interrupted"
    ]
    submission: Literal["pending", "uncertain", "accepted"]
    submitted_at: int
    updated_at: int

class Result:
    call_id: str
    error: Literal["invalid_payload", "provider_error", "timeout"] | None
    kind: Literal["result"]
    payload: dict[str, JsonValue] | None
    protocol: int

class Shutdown:
    kind: Literal["shutdown"]
    protocol: int

class Startup:
    configuration: dict[str, JsonValue] | None
    credential_config: str | None
    host_id: str
    manifest: Manifest
    node_id: str
    serve_host: str | None
    socket: str
    stream_socket: str | None
    token: str
    transport_id: str

class StewardExposure:
    behavior: Literal["read", "lifecycle", "operation"]
    provider_selection: (
        Literal["runpod-simulator", "runpod-witness-experimental"] | None
    )
    qualified_id: str
    risk: Literal["observation", "billable", "effect"]
    summary: str | None

class StreamEnd:
    call_id: str
    kind: Literal["end"]

class StreamError:
    code: Literal[
        "provider_error",
        "transport_error",
        "unreachable",
        "cancelled",
        "timeout",
        "invalid_frame",
    ]
    message: str

class StreamFrame:
    call_id: str
    direction: Literal["caller_to_provider", "provider_to_caller"]
    error: StreamError | None
    kind: Literal["started", "chunk", "completed", "failed", "cancelled"]
    media: BlobMedia | InlineMedia | None
    payload: dict[str, JsonValue] | None
    sequence: int
    synthetic: Literal[False]

class StreamHello:
    call_id: str
    capability_node_id: str
    kind: Literal["stream_hello"]
    manifest_sha256: str
    protocol: int
    token: str

class StreamInvoke:
    call_id: str
    capability_id: str
    descriptor_revision: str
    kind: Literal["stream_invoke"]
    payload: dict[str, JsonValue]
    protocol: int
    remaining_seconds: float
    version: str

class Surface:
    entry: str
    kind: Literal["proxied", "link"]
    surface_id: str
    title: str
    websocket: bool

class SurfaceReport:
    port: int | None
    ready: bool
    surface_id: str
    url: str | None

type StreamPacket = StreamFrame | StreamEnd
type WireMessage = (
    Health | Hello | Invoke | Result | Shutdown | StreamHello | StreamInvoke
)
