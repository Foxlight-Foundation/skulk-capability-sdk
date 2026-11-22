import {createHash} from 'node:crypto';
import {performance} from 'node:perf_hooks';
import {closeSync, fstatSync, readSync} from 'node:fs';
import {inspect} from 'node:util';
import {native} from './native.js';
import {array, copyJson, encodeJson, number, object, parseJson, text, utf8, type JsonObject} from './json.js';
import {CallContext, CapabilityEndpoint, InputFrame, OutputFrame, type Mode, type StreamMode} from './contracts.js';
import {Cancelled, Expired, Resources, check, type Resource} from './resources.js';
import {CallReply, CallStream, CapabilityCallError, Deadline, Delegations, type CallOptions} from './calls.js';
import {CredentialReader, type PythonCredentialHelper} from './credentials.js';

/** Private-startup projection containing only admitted application metadata. */
export class ApplicationStartup {
  readonly #value: JsonObject;
  readonly #credentials: CredentialReader;
  constructor(value: JsonObject, credentials: CredentialReader = new CredentialReader(null, [])) {
    this.#value = copyJson(value); this.#credentials = credentials; Object.freeze(this);
  }
  get credentials(): CredentialReader { return this.#credentials; }
  get nodeId(): string { return text(this.#value.node_id); }
  get hostId(): string { return text(this.#value.host_id); }
  get configuration(): JsonObject | null {
    return this.#value.configuration === null ? null : copyJson(object(this.#value.configuration));
  }
  get serveHost(): string | null { return this.#value.serve_host === null ? null : text(this.#value.serve_host); }
  get operationBounds(): JsonObject | null { return this.#value.operations === null ? null : copyJson(object(this.#value.operations)); }
  /** Copy one exact declared endpoint. A descriptor does not grant caller rights. */
  endpoint(capabilityId: string, version: string): CapabilityEndpoint {
    const matches = array(this.#value.descriptors).map(object).filter(d => d.id === capabilityId && d.version === version);
    if (matches.length !== 1 || matches[0] === undefined) throw new TypeError('Endpoint is not declared');
    return new CapabilityEndpoint(this.nodeId, matches[0]);
  }
  toJSON(): string { return 'ApplicationStartup'; }
  [inspect.custom](): string { return 'ApplicationStartup'; }
}

/** An exact host-admitted invocation, with cooperative cancellation and owned resources. */
export class Invocation {
  readonly #payload: JsonObject;
  readonly #raw: Buffer;
  readonly #resources: Resources;
  readonly #calls: Delegations;
  readonly callId: string;
  readonly capabilityId: string;
  readonly version: string;
  readonly context: CallContext;
  readonly signal: AbortSignal;
  constructor(callId: string, capabilityId: string, version: string, body: Buffer,
      context: CallContext, signal: AbortSignal, resources: Resources, calls: Delegations) {
    this.callId = callId; this.capabilityId = capabilityId; this.version = version;
    this.#raw = Buffer.from(body); this.#payload = object(parseJson(utf8(body)));
    this.context = context; this.signal = signal; this.#resources = resources; this.#calls = calls; Object.freeze(this);
  }
  get payload(): JsonObject { return copyJson(this.#payload); }
  get rawPayload(): Uint8Array { return Buffer.from(this.#raw); }
  /** Retain acquisition and LIFO cleanup even if cancellation races either one. */
  enterResource<Value extends Resource>(acquire: () => Promise<Value>): Promise<Value> {
    return this.#resources.enter(acquire, this.signal);
  }
  /** Delegate once using the original live caller; no replacement effect is replayed. */
  call(destination: CapabilityEndpoint, payload: JsonObject | Uint8Array, options: CallOptions = {}): Promise<CallReply> {
    return this.#calls.call(destination, payload, options);
  }
  /** Own one child stream for a callback, including media/end and host finalization. */
  stream<Value>(destination: CapabilityEndpoint, payload: JsonObject | Uint8Array,
      work: (stream: CallStream) => Promise<Value>, options: CallOptions = {}): Promise<Value> {
    return this.#calls.stream(destination, payload, work, options);
  }
  toJSON(): string { return 'Invocation'; }
  [inspect.custom](): string { return 'Invocation'; }
}

/** A unary handler returns a portable object, never an unvalidated backend body. */
export type UnaryHandler = (call: Invocation) => Promise<JsonObject>;
/** Stream handlers observe AbortSignal and retain cleanup in iterator finally blocks. */
export type StreamHandler = (call: Invocation, inputs: AsyncIterable<InputFrame>) => AsyncIterable<OutputFrame>;
/** Setup may acquire one resource; its cleanup outlives cancelled application startup. */
export type ApplicationSetup = (startup: ApplicationStartup, signal: AbortSignal) => Promise<Resource | void>;
/** Cached health observations must be synchronous and perform no I/O. */
export interface ApplicationOptions {
  readonly credentialHelper?: PythonCredentialHelper;
  readonly setup?: ApplicationSetup;
  readonly ready?: () => boolean;
  readonly surfaces?: () => readonly JsonObject[];
}
interface Job {
  readonly controller: AbortController;
  done: boolean;
  task: Promise<void>;
  calls: Delegations | undefined;
}
type Control = Awaited<ReturnType<typeof native.openCallV2Control>>;
type Stream = Awaited<ReturnType<typeof native.openTransportV2Stream>>;
const key = (id: string, version: string | null): string => JSON.stringify([id, version]);

function readStartup(descriptor: number): string {
  try {
    const metadata = fstatSync(descriptor);
    if (!metadata.isFile() || metadata.uid !== process.geteuid?.() || (metadata.mode & 0o077) !== 0) {
      throw new TypeError('Application startup requires a private regular descriptor');
    }
    const bytes = Buffer.alloc(262145);
    let offset = 0;
    while (offset < bytes.length) {
      const count = readSync(descriptor, bytes, offset, bytes.length - offset, null);
      if (count === 0) break;
      offset += count;
    }
    if (offset > 262144) throw new TypeError('Application startup exceeds its bound');
    return utf8(bytes.subarray(0, offset));
  } finally { closeSync(descriptor); }
}
function schemaMatches(schema: JsonObject[keyof JsonObject] | undefined, value: string): boolean {
  return schema === null || (schema !== undefined && new native.PortableSchema(utf8(encodeJson(schema))).matches(value));
}
function frame(kind: string, fields: JsonObject): string {
  return utf8(encodeJson({protocol: 2, contract_profile: 'skulk.calls/2',
    profile_digest: native.callsV2ProfileDigest(), kind, payload_size: 0, ...fields}));
}
function mode(value: unknown): Mode {
  if (value === 'unary' || value === 'server_streaming' || value === 'client_streaming' || value === 'bidirectional') return value;
  throw new TypeError('Unsupported admitted mode');
}

/** Explicit native application lifecycle; the host independently owns all admission. */
export class CapabilityApp {
  readonly #unary = new Map<string, UnaryHandler>();
  readonly #streams = new Map<string, StreamHandler>();
  readonly #options: ApplicationOptions;
  #serving = false;
  constructor(options: ApplicationOptions = {}) { this.#options = options; }
  /** Bind one unary ID/version before serving; ambiguous shorthand refuses. */
  unary(id: string, handler: UnaryHandler, options: {readonly version?: string} = {}): void {
    this.#register(id, options.version ?? null); this.#unary.set(key(id, options.version ?? null), handler);
  }
  /** Bind server, client or bidirectional behavior to its actual signed declaration. */
  stream(id: string, handler: StreamHandler, options: {readonly version?: string} = {}): void {
    this.#register(id, options.version ?? null); this.#streams.set(key(id, options.version ?? null), handler);
  }
  #register(id: string, version: string | null): void {
    if (this.#serving || this.#unary.has(key(id, version)) || this.#streams.has(key(id, version))) {
      throw new TypeError('Application handler is already bound');
    }
  }
  #bind<Handler>(handlers: ReadonlyMap<string, Handler>, descriptors: readonly JsonObject[]): Map<string, Handler> {
    const result = new Map<string, Handler>();
    for (const [registration, handler] of handlers) {
      const identity: unknown = JSON.parse(registration);
      if (!Array.isArray(identity) || identity.length !== 2) throw new TypeError('Invalid registration');
      const [id, version]: readonly unknown[] = identity;
      const matches = descriptors.filter(d => d.id === id && (version === null || d.version === version));
      if (matches.length !== 1 || matches[0] === undefined) throw new TypeError('Ambiguous or undeclared handler');
      const target = key(text(matches[0].id), text(matches[0].version));
      if (result.has(target)) throw new TypeError('Duplicate handler version');
      result.set(target, handler);
    }
    return result;
  }
  /** Consume protected startup once and drain real cleanup before serving returns.
   * AbortSignal is cooperative: handlers must pass it to their own asynchronous
   * effects. The managed owner retains the final signed process-reap deadline.
   */
  async serve(descriptor: number, options: {readonly signal?: AbortSignal} = {}): Promise<void> {
    if (this.#serving) { closeSync(descriptor); throw new TypeError('Application can serve only once'); }
    this.#serving = true;
    const startup = utf8(native.managedV2ContractJson('Startup', readStartup(descriptor)));
    const value = object(parseJson(startup));
    const manifest = object(value.manifest), contracts = object(manifest.contracts);
    const descriptors = array(contracts.descriptors).map(object);
    const unary = this.#bind(this.#unary, descriptors), streams = this.#bind(this.#streams, descriptors);
    if (unary.size + streams.size !== descriptors.length) throw new TypeError('Handlers differ from the signed manifest');
    for (const declared of descriptors) {
      const identity = key(text(declared.id), text(declared.version));
      if ((declared.io_mode === 'unary') !== unary.has(identity) || unary.has(identity) === streams.has(identity)) {
        throw new TypeError('Handler mode differs from the signed manifest');
      }
    }
    const credentials = new CredentialReader(value.credential_config === null ? null : text(value.credential_config),
      array(manifest.credential_inputs).map(object), this.#options.credentialHelper);
    const publicStartup = new ApplicationStartup({node_id: value.node_id ?? null, host_id: value.host_id ?? null,
      configuration: value.configuration ?? null, serve_host: value.serve_host ?? null,
      operations: manifest.operations ?? null, descriptors}, credentials);
    const controller = new AbortController();
    const abort = (): void => controller.abort(new Cancelled());
    options.signal?.addEventListener('abort', abort, {once: true});
    if (options.signal?.aborted) abort();
    let resource: Resource | void = undefined;
    try {
      check(controller.signal);
      resource = await this.#options.setup?.(publicStartup, controller.signal);
      check(controller.signal);
      await this.#control(startup, value, manifest, descriptors, unary, streams, controller.signal);
    } finally {
      options.signal?.removeEventListener('abort', abort);
      try { await resource?.close(); } finally { await credentials.close(); }
    }
  }
  async #control(startup: string, value: JsonObject, manifest: JsonObject, descriptors: readonly JsonObject[],
      unary: ReadonlyMap<string, UnaryHandler>, streams: ReadonlyMap<string, StreamHandler>, signal: AbortSignal): Promise<void> {
    const bounds = object(manifest.bounds);
    const control = await native.openCallV2Control(startup, Math.max(number(bounds.startup_seconds), 2 * number(bounds.health_seconds)));
    const jobs = new Map<string, Job>();
    const abort = (): void => { void control.close().catch(() => undefined); };
    signal.addEventListener('abort', abort, {once: true});
    try {
      check(signal);
      while (true) {
        const packet = await control.receive();
        const request = object(parseJson(utf8(packet.metadata)));
        if (request.kind === 'shutdown') return;
        if (request.kind === 'health') {
          await control.send(frame('health', {nonce: request.nonce ?? null,
            ready: this.#options.ready?.() ?? true, surfaces: this.#options.surfaces?.() ?? []}), Buffer.alloc(0));
        } else if (request.kind === 'invoke') {
          for (const [identity, job] of jobs) if (job.done && !job.calls?.awaitingReply) jobs.delete(identity);
          const identity = text(request.call_id);
          if (jobs.has(identity) || jobs.size >= number(bounds.concurrency)) throw new TypeError('Application workload capacity exceeded');
          const job: Job = {controller: new AbortController(), task: Promise.resolve(), done: false, calls: undefined};
          jobs.set(identity, job);
          job.task = this.#execute(control, startup, value, descriptors, unary, streams, request, packet.payload, job);
          void job.task.then(() => { job.done = true; }, () => { job.done = true; void control.close().catch(() => undefined); });
        } else if (request.kind === 'cancel') {
          jobs.get(text(request.call_id))?.controller.abort(new Cancelled());
        } else if (request.kind === 'delegate_result' || request.kind === 'delegate_started' || request.kind === 'delegate_finished') {
          const parent = jobs.get(text(request.parent_call_id));
          if (parent?.calls === undefined) throw new TypeError('Delegated parent correlation refused');
          parent.calls.receive(request, packet.payload);
        } else {
          throw new TypeError('Application control direction refused');
        }
      }
    } finally {
      signal.removeEventListener('abort', abort);
      for (const job of jobs.values()) job.controller.abort(new Cancelled());
      await Promise.allSettled([...jobs.values()].map(job => job.task));
      await control.close();
    }
  }
  async #execute(control: Control, startup: string, value: JsonObject, descriptors: readonly JsonObject[],
      unary: ReadonlyMap<string, UnaryHandler>, streams: ReadonlyMap<string, StreamHandler>, request: JsonObject,
      body: Buffer, job: Job): Promise<void> {
    let error: string | null = null;
    let encoded: Buffer = Buffer.alloc(0);
    let timer: Deadline | undefined;
    const resources = new Resources();
    const retire = (): void => job.calls?.retire();
    try {
      check(job.controller.signal);
      const context = new CallContext(utf8(encodeJson(request.context ?? null)));
      const fields = context.export(), target = object(fields.executor);
      const id = text(target.capability_id), version = text(target.version);
      const declared = descriptors.find(d => d.id === id && d.version === version);
      if (declared === undefined || target.node_id !== value.node_id || target.behavior !== declared.behavior
          || target.processing_domain !== declared.processing_domain || request.mode !== declared.io_mode
          || target.descriptor_revision !== native.portableV2ContractDigest('Descriptor', utf8(encodeJson(declared)))
          || fields.request_sha256 !== createHash('sha256').update(body).digest('hex')
          || !schemaMatches(declared.input_schema, utf8(body))) throw new TypeError('Application request binding differs');
      const declaredMode = mode(declared.io_mode);
      let seconds: number | null = request.remaining_seconds === null ? null : number(request.remaining_seconds);
      const deadline = fields.deadline_unix_ms === null ? null : number(fields.deadline_unix_ms);
      if ((seconds === null) !== (deadline === null)) throw new TypeError('Application lifetime binding differs');
      if (seconds !== null && deadline !== null) {
        seconds = Math.min(seconds, (deadline - Date.now()) / 1000);
        if (seconds <= 0) throw new Expired();
        timer = new Deadline(performance.now() + seconds * 1000, () => job.controller.abort(new Expired()));
      }
      const calls = new Delegations(control, frame, text(request.call_id), fields, startup,
        seconds === null ? null : performance.now() + seconds * 1000, () => job.controller.abort(new Cancelled()));
      job.calls = calls; resources.add(calls);
      job.controller.signal.addEventListener('abort', retire, {once: true});
      if (job.controller.signal.aborted) retire();
      check(job.controller.signal);
      const invocation = new Invocation(text(request.call_id), id, version, body, context, job.controller.signal, resources, calls);
      if (declaredMode === 'unary') {
        const handler = unary.get(key(id, version));
        if (handler === undefined) throw new TypeError('Unary handler unavailable');
        const result = object(await handler(invocation));
        encoded = encodeJson(result);
        if (!schemaMatches(declared.output_schema, utf8(encoded))) throw new TypeError('Application result schema refused');
      } else {
        const handler = streams.get(key(id, version));
        if (handler === undefined) throw new TypeError('Stream handler unavailable');
        await this.#streamWork(startup, invocation, declared, declaredMode, seconds, handler, resources, () => {
          void timer?.close();
        });
        encoded = encodeJson({});
      }
    } catch (failure: unknown) {
      error = job.controller.signal.aborted ? (job.controller.signal.reason instanceof Expired ? 'timeout' : 'cancelled')
        : failure instanceof Cancelled ? 'cancelled' : failure instanceof Expired ? 'timeout'
        : failure instanceof CapabilityCallError ? failure.code
        : failure instanceof TypeError ? 'invalid_payload' : 'provider_error';
    } finally {
      // The generation timer cannot interrupt cleanup. Cancellation remains
      // retired, and host process custody lasts until actual cleanup finishes.
      await timer?.close();
      try { await resources.close(); } catch { error = 'provider_error'; }
      job.controller.signal.removeEventListener('abort', retire);
      if (job.controller.signal.aborted && error === null) error = job.controller.signal.reason instanceof Expired ? 'timeout' : 'cancelled';
      if (error !== null) encoded = Buffer.alloc(0);
      await control.send(frame('result', {call_id: request.call_id ?? null, error, payload_size: encoded.length}), encoded);
    }
  }
  async #streamWork(startup: string, call: Invocation, declared: JsonObject, mode: StreamMode,
      seconds: number | null, handler: StreamHandler, resources: Resources, disarm: () => void): Promise<void> {
    const stream: Stream = await native.openTransportV2Stream(startup, call.callId, mode, seconds);
    let aborted: Promise<void> | undefined;
    const abort = (): void => { aborted ??= stream.close(); void aborted.catch(() => undefined); };
    call.signal.addEventListener('abort', abort, {once: true});
    async function* inputs(): AsyncGenerator<InputFrame> {
      for (let packet = await stream.nextInput(); packet !== null; packet = await stream.nextInput()) {
        check(call.signal);
        const projection = object(parseJson(packet.projection));
        if (projection.kind === 'chunk' && projection.payload !== null
            && !schemaMatches(declared.input_chunk_schema, utf8(encodeJson(projection.payload ?? null)))) {
          throw new TypeError('Application input chunk schema refused');
        }
        yield new InputFrame(projection, packet.media);
      }
    }
    const ingress = inputs();
    let outputs: AsyncIterator<OutputFrame> | undefined;
    try {
      check(call.signal);
      outputs = handler(call, ingress)[Symbol.asyncIterator]();
      let sequence = 1;
      try {
        while (true) {
          const next = await outputs.next();
          check(call.signal);
          if (next.done) break;
          const output = next.value;
          if (!(output instanceof OutputFrame)) throw new TypeError('Expected a typed output frame');
          if (output.kind === 'chunk' && output.payload !== null
              && !schemaMatches(declared.output_chunk_schema, utf8(encodeJson(output.payload)))) throw new TypeError('Output chunk schema refused');
          if (output.kind === 'completed' && !schemaMatches(declared.output_schema, utf8(encodeJson(output.payload)))) throw new TypeError('Terminal schema refused');
          await stream.sendOutput(utf8(encodeJson({call_id: call.callId, direction: 'provider_to_caller',
            sequence, kind: output.kind, payload: output.payload, media: output.mediaMetadata,
            error: output.error === null ? null : {code: output.error, message: ''}})), Buffer.from(output.media));
          sequence += 1;
          if (output.kind !== 'chunk') break;
        }
      } finally {
        disarm();
        try { await outputs.return?.(); } finally { await ingress.return(undefined); }
        await resources.close();
      }
      check(call.signal);
      await stream.finish();
    } finally {
      call.signal.removeEventListener('abort', abort);
      await stream.close();
      await aborted;
    }
  }
}
