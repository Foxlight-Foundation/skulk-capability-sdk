// Generated author-v1 declarations; native implementation is private.
export type JsonValue = null | boolean | number | string | ReadonlyArray<JsonValue> | {readonly [key: string]: JsonValue};

export interface ApprovedEffect {
  readonly authority_id: string;
  readonly campaign_policy_digest?: string | null;
  readonly capability_id: string;
  readonly capability_node_id: string;
  readonly capability_version: string;
  readonly currency?: "USD";
  readonly descriptor_revision: string;
  readonly effect: "acquire" | "release";
  readonly expires_at_unix_seconds: number;
  readonly host_id: string;
  readonly idempotency_key: string;
  readonly key_id: string;
  readonly maximum_lifetime_seconds: number;
  readonly maximum_total_cost_microunits: number;
  readonly operator_id: string;
  readonly plan_digest: string;
  readonly policy_revision: string;
  readonly proposal_digest?: string | null;
  readonly provider_selection: string;
  readonly transport_node_id: string;
}

export interface BlobMedia {
  readonly blob_id: string;
  readonly kind?: "blob";
  readonly media_type: string;
  readonly sha256: string;
  readonly size_bytes: number;
}

export interface Bounds {
  readonly backoff_seconds?: number;
  readonly call_seconds?: number;
  readonly concurrency?: 1;
  readonly health_seconds?: number;
  readonly log_bytes?: 0;
  readonly max_children?: number;
  readonly restart_budget?: number;
  readonly shutdown_seconds?: number;
  readonly startup_seconds?: number;
  readonly stream_seconds?: number;
}

export interface CredentialInput {
  readonly credential_id: string;
  readonly description: string;
  readonly required?: boolean;
  readonly title: string;
}

export interface Descriptor {
  readonly annotations?: Readonly<Record<string, string>> | null;
  readonly description: string;
  readonly id: string;
  readonly input_chunk_schema?: Readonly<Record<string, JsonValue>> | null;
  readonly input_schema: Readonly<Record<string, JsonValue>>;
  readonly io_mode?: "unary" | "server_streaming" | "client_streaming" | "bidirectional";
  readonly output_chunk_schema?: Readonly<Record<string, JsonValue>> | null;
  readonly output_schema?: Readonly<Record<string, JsonValue>> | null;
  readonly title: string;
  readonly version: string;
}

export interface Health {
  readonly kind?: "health";
  readonly nonce: string;
  readonly protocol?: number;
  readonly ready: boolean;
  readonly surfaces?: ReadonlyArray<SurfaceReport>;
}

export interface Hello {
  readonly bundle_id: string;
  readonly bundle_version: string;
  readonly capability_node_id: string;
  readonly host_id: string;
  readonly kind?: "hello";
  readonly manifest_sha256: string;
  readonly protocol?: number;
  readonly token: string;
  readonly transport_node_id: string;
}

export interface InlineMedia {
  readonly channels?: number | null;
  readonly codec?: string | null;
  readonly data: Uint8Array;
  readonly duration_seconds?: number | null;
  readonly kind?: "inline";
  readonly media_type: string;
  readonly sample_rate?: number | null;
  readonly timestamp_seconds?: number | null;
}

export interface Invoke {
  readonly call_id: string;
  readonly capability_id: string;
  readonly descriptor_revision: string;
  readonly kind?: "invoke";
  readonly payload: Readonly<Record<string, JsonValue>>;
  readonly protocol?: number;
  readonly remaining_seconds: number;
  readonly version: string;
}

export interface Manifest {
  readonly arguments: ReadonlyArray<string>;
  readonly bounds?: Bounds;
  readonly bundle_id: string;
  readonly bundle_version: string;
  readonly configuration_schema?: Readonly<Record<string, JsonValue>> | null;
  readonly credential_inputs?: ReadonlyArray<CredentialInput>;
  readonly descriptors: ReadonlyArray<Descriptor>;
  readonly effect?: "read" | "governed";
  readonly executable: string;
  readonly executable_sha256: string;
  readonly operations?: OperationBounds | null;
  readonly preflight_profile?: "local" | "runpod-native" | null;
  readonly protocol?: number;
  readonly runtime_dependencies?: ReadonlyArray<string>;
  readonly skulk_requires?: string;
  readonly steward?: ReadonlyArray<StewardExposure>;
  readonly surfaces?: ReadonlyArray<Surface>;
  readonly title?: string | null;
}

export interface OperationBounds {
  readonly log_bytes?: number;
  readonly max_queued?: number;
  readonly max_runtime_seconds?: number;
  readonly reservations?: number;
  readonly retain?: number;
  readonly stale_after_seconds?: number;
}

export interface OperationCall {
  readonly input?: Readonly<Record<string, JsonValue>> | null;
  readonly offset?: number | null;
  readonly op: "start" | "status" | "cancel" | "list" | "log" | "plan";
  readonly operation_id?: string | null;
}

export interface OperationLog {
  readonly next_offset: number;
  readonly offset: number;
  readonly operation_id: string;
  readonly text: string;
  readonly truncated: boolean;
}

export interface OperationPage {
  readonly active: number;
  readonly next_offset?: number | null;
  readonly operations: ReadonlyArray<OperationStatus>;
  readonly outstanding?: number;
}

export interface OperationPlan {
  readonly estimated_cost_microunits?: number | null;
  readonly estimated_seconds?: number | null;
  readonly expires_at: number;
  readonly input?: Readonly<Record<string, JsonValue>> | null;
  readonly plan_digest: string;
  readonly summary: string;
}

export interface OperationStatus {
  readonly backend_reference?: string | null;
  readonly error?: string | null;
  readonly finished_at?: number | null;
  readonly input?: Readonly<Record<string, JsonValue>> | null;
  readonly input_digest: string;
  readonly operation_id: string;
  readonly progress?: number | null;
  readonly queue_position?: number | null;
  readonly result?: Readonly<Record<string, JsonValue>> | null;
  readonly stage?: string | null;
  readonly stale: boolean;
  readonly state: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted";
  readonly submission: "pending" | "uncertain" | "accepted";
  readonly submitted_at: number;
  readonly updated_at: number;
}

export interface Result {
  readonly call_id: string;
  readonly error: "invalid_payload" | "provider_error" | "timeout" | null;
  readonly kind?: "result";
  readonly payload: Readonly<Record<string, JsonValue>> | null;
  readonly protocol?: number;
}

export interface Shutdown {
  readonly kind?: "shutdown";
  readonly protocol?: number;
}

export interface Startup {
  readonly configuration?: Readonly<Record<string, JsonValue>> | null;
  readonly credential_config?: string | null;
  readonly host_id: string;
  readonly manifest: Manifest;
  readonly node_id: string;
  readonly serve_host?: string | null;
  readonly socket: string;
  readonly stream_socket?: string | null;
  readonly token: string;
  readonly transport_id: string;
}

export interface StewardExposure {
  readonly behavior: "read" | "lifecycle" | "operation";
  readonly provider_selection?: "runpod-simulator" | "runpod-witness-experimental" | null;
  readonly qualified_id: string;
  readonly risk: "observation" | "billable" | "effect";
  readonly summary?: string | null;
}

export interface StreamEnd {
  readonly call_id: string;
  readonly kind?: "end";
}

export interface StreamError {
  readonly code: "provider_error" | "transport_error" | "unreachable" | "cancelled" | "timeout" | "invalid_frame";
  readonly message: string;
}

export interface StreamFrame {
  readonly call_id: string;
  readonly direction: "caller_to_provider" | "provider_to_caller";
  readonly error?: StreamError | null;
  readonly kind: "started" | "chunk" | "completed" | "failed" | "cancelled";
  readonly media?: BlobMedia | InlineMedia | null;
  readonly payload?: Readonly<Record<string, JsonValue>> | null;
  readonly sequence: number;
  readonly synthetic?: false;
}

export interface StreamHello {
  readonly call_id: string;
  readonly capability_node_id: string;
  readonly kind?: "stream_hello";
  readonly manifest_sha256: string;
  readonly protocol?: number;
  readonly token: string;
}

export interface StreamInvoke {
  readonly call_id: string;
  readonly capability_id: string;
  readonly descriptor_revision: string;
  readonly kind?: "stream_invoke";
  readonly payload: Readonly<Record<string, JsonValue>>;
  readonly protocol?: number;
  readonly remaining_seconds: number;
  readonly version: string;
}

export interface Surface {
  readonly entry?: string;
  readonly kind: "proxied" | "link";
  readonly surface_id: string;
  readonly title: string;
  readonly websocket?: boolean;
}

export interface SurfaceReport {
  readonly port?: number | null;
  readonly ready: boolean;
  readonly surface_id: string;
  readonly url?: string | null;
}

export type StreamPacket = StreamFrame | StreamEnd;
export type WireMessage = Health | Hello | Invoke | Result | Shutdown | StreamHello | StreamInvoke;
