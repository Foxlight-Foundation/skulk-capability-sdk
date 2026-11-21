import {createHash} from 'node:crypto';
import {performance} from 'node:perf_hooks';
import {inspect} from 'node:util';
import {native} from './native.js';
import {copyJson, encodeJson, number, object, parseJson, text, utf8, type JsonObject, type JsonValue} from './json.js';
import {CapabilityEndpoint, type StreamMode} from './contracts.js';
import {Cancelled, Expired, type Resource} from './resources.js';

/** Closed native error codes; backend exception text never crosses this boundary. */
export type CallErrorCode = 'invalid_payload' | 'provider_error' | 'timeout' | 'denied' | 'outcome_unknown' | 'cancelled';
/** A definitive host refusal or uncertainty report, never permission to replay. */
export class CapabilityCallError extends Error {
  readonly code: CallErrorCode;
  constructor(code: CallErrorCode) { super(code); this.code = code; }
}
/** A child reply retaining exact independently owned wire bytes. */
export class CallReply {
  readonly #raw: Buffer;
  constructor(raw: Uint8Array) { this.#raw = Buffer.from(raw); Object.freeze(this); }
  get rawPayload(): Uint8Array { return Buffer.from(this.#raw); }
  get payload(): JsonObject { return object(parseJson(utf8(this.#raw))); }
  toJSON(): string { return 'CallReply'; }
  [inspect.custom](): string { return 'CallReply'; }
}
/** A stream packet copied from native transport; terminals include host finalization. */
export class CallStreamFrame {
  readonly #projection: JsonObject;
  readonly #media: Buffer;
  readonly kind: 'chunk' | 'completed' | 'failed' | 'cancelled';
  constructor(projection: string, media: Uint8Array) {
    this.#projection = object(parseJson(projection)); this.#media = Buffer.from(media);
    const kind = this.#projection.kind;
    if (kind !== 'chunk' && kind !== 'completed' && kind !== 'failed' && kind !== 'cancelled') throw new TypeError('Unexpected child output');
    this.kind = kind; Object.freeze(this);
  }
  get projection(): JsonObject { return copyJson(this.#projection); }
  get payload(): JsonValue { return copyJson(this.#projection.payload ?? null); }
  get media(): Uint8Array { return Buffer.from(this.#media); }
  toJSON(): {kind: string} { return {kind: this.kind}; }
}
/** A requested child ceiling; absent/null inherits the original parent lifetime. */
export interface CallOptions { readonly deadlineUnixMs?: number | null; }
type Control = Awaited<ReturnType<typeof native.openCallV2Control>>;
type Channel = Awaited<ReturnType<typeof native.openTransportV2ClientStream>>;
type Encoder = (kind: string, fields: JsonObject) => string;
interface Reply {readonly body: Buffer; readonly error: CallErrorCode | null;}
class Deferred<Value> {
  readonly #value = Promise.withResolvers<Value>();
  readonly promise = this.#value.promise;
  done = false;
  resolve(value: Value): void { if (!this.done) { this.done = true; this.#value.resolve(value); } }
}
interface Pending {
  readonly id: string;
  readonly mode: StreamMode | null;
  readonly requestedDeadline: number | null;
  readonly reply: Deferred<Reply>;
  readonly started: Deferred<JsonObject | null>;
  startReceived: boolean;
  received: boolean;
  abandoned: boolean;
  stream: CallStream | undefined;
}
function errorCode(value: JsonValue | undefined): CallErrorCode | null {
  if (value === null || value === 'invalid_payload' || value === 'provider_error' || value === 'timeout'
      || value === 'denied' || value === 'outcome_unknown' || value === 'cancelled') return value;
  throw new TypeError('Unknown native call refusal');
}
function nullableNumber(value: JsonValue | undefined): number | null { return value === null ? null : number(value); }
function minimum(values: readonly (number | null)[]): number | null {
  const finite = values.filter((v): v is number => v !== null);
  return finite.length === 0 ? null : Math.min(...finite);
}
function matches(schema: JsonValue | undefined, value: JsonValue): boolean {
  return schema === null || (schema !== undefined && new native.PortableSchema(utf8(encodeJson(schema))).matches(utf8(encodeJson(value))));
}
/** Own a monotonic timer without Node's long-delay overflow or a generation cap. */
export class Deadline implements Resource {
  #timer: NodeJS.Timeout | undefined;
  #closed = false;
  constructor(readonly expiry: number | null, readonly expire: () => void) { this.#schedule(); }
  #schedule(): void {
    if (this.#closed || this.expiry === null) return;
    const remaining = this.expiry - performance.now();
    if (remaining <= 0) { this.expire(); return; }
    this.#timer = setTimeout(() => this.#schedule(), Math.min(remaining, 2_147_483_647));
  }
  async close(): Promise<void> { this.#closed = true; clearTimeout(this.#timer); }
}

/** Internal parent-bound correlation. No root context or caller policy is minted. */
export class Delegations implements Resource {
  readonly #tasks = new Set<Promise<unknown>>();
  #pending: Pending | undefined;
  #sending: Promise<void> | undefined;
  #sequence = 0n;
  active = true;
  constructor(readonly control: Control, readonly frame: Encoder, readonly callId: string,
      readonly context: JsonObject, readonly startup: string, readonly expiry: number | null,
      readonly cancelParent: () => void) {}
  get awaitingReply(): boolean { return this.#pending !== undefined && !this.#pending.received; }
  /** Retain late correlation while immediately removing abandoned child authority. */
  abandon(): void { this.active = false; if (this.#pending !== undefined) this.#pending.abandoned = true; this.cancelParent(); }
  retire(): void {
    this.active = false;
    const pending = this.#pending;
    if (pending !== undefined) {
      if (!pending.received) pending.abandoned = true;
      pending.reply.resolve({body: Buffer.alloc(0), error: 'cancelled'});
      pending.started.resolve(null);
      pending.stream?.retire();
    }
  }
  async close(): Promise<void> {
    this.active = false;
    if (this.#pending !== undefined) { this.abandon(); this.retire(); }
    await Promise.allSettled(this.#tasks);
    await this.#sending;
  }
  /** Admit only the exact next phase of the one live child, including late cleanup. */
  receive(request: JsonObject, body: Buffer): void {
    const pending = this.#pending;
    if (pending === undefined || pending.received || request.request_id !== pending.id) throw new TypeError('Child correlation refused');
    if (request.kind === 'delegate_started') {
      if (pending.mode === null || pending.startReceived || body.length !== 0 || request.mode !== pending.mode) throw new TypeError('Child start phase refused');
      const deadline = nullableNumber(request.deadline_unix_ms);
      for (const ceiling of [nullableNumber(this.context.deadline_unix_ms), pending.requestedDeadline]) {
        if (ceiling !== null && (deadline === null || deadline > ceiling)) throw new TypeError('Child lifetime widened');
      }
      pending.startReceived = true; pending.started.resolve(copyJson(request)); return;
    }
    if (request.kind === 'delegate_finished') {
      if (pending.mode === null || !pending.startReceived || body.length !== 0) throw new TypeError('Child finish phase refused');
    } else if (request.kind !== 'delegate_result' || (pending.mode !== null && (pending.startReceived || request.error === null))) {
      throw new TypeError('Child result phase refused');
    }
    pending.received = true;
    pending.reply.resolve({body: Buffer.from(body), error: errorCode(request.error)});
    pending.started.resolve(null);
    if (pending.abandoned) this.#pending = undefined;
  }
  #begin(destination: CapabilityEndpoint, payload: JsonObject | Uint8Array, options: CallOptions,
      streaming: boolean): {descriptor: JsonObject; pending: Pending} {
    if (!this.active || this.#pending !== undefined) throw new TypeError('Parent delegation is retired or occupied');
    if (this.expiry !== null && performance.now() >= this.expiry) { this.abandon(); throw new Expired(); }
    if (this.#sequence >= (1n << 64n) - 1n) throw new TypeError('Delegation sequence exhausted');
    const declared = destination.descriptor();
    const declaredMode = declared.io_mode;
    let mode: StreamMode | null = null;
    if (streaming) {
      if (declaredMode !== 'server_streaming' && declaredMode !== 'client_streaming' && declaredMode !== 'bidirectional') throw new TypeError('Child stream mode refused');
      mode = declaredMode;
    } else if (declaredMode !== 'unary') throw new TypeError('Unary child requires unary declaration');
    const body = payload instanceof Uint8Array ? Buffer.from(payload) : encodeJson(payload);
    if (!new native.PortableSchema(utf8(encodeJson(declared.input_schema ?? null))).matches(utf8(body))) throw new TypeError('Child input schema refused');
    const id = (this.#sequence + 1n).toString(16).padStart(64, '0');
    const requestedDeadline = options.deadlineUnixMs ?? null;
    const claim: JsonObject = {
      protocol: 3, contract_profile: 'skulk.composition/3', profile_digest: native.compositionV3ProfileDigest(),
      kind: 'delegate', request_id: id, parent_scope_id: this.context.scope_id ?? null,
      parent_request_sha256: this.context.request_sha256 ?? null, request_sha256: createHash('sha256').update(body).digest('hex'),
      destination: {node_id: destination.nodeId, capability_id: declared.id ?? null, version: declared.version ?? null,
        descriptor_revision: destination.descriptorRevision, behavior: declared.behavior ?? null, processing_domain: declared.processing_domain ?? null},
      deadline_unix_ms: requestedDeadline ?? this.context.deadline_unix_ms ?? null,
    };
    const request = object(parseJson(utf8(native.compositionV3ContractJson('DelegateRequest', utf8(encodeJson(claim))))));
    const metadata = this.frame('delegate', {parent_call_id: this.callId, request, payload_size: body.length});
    native.encodeCallV2Frame(metadata, body);
    const pending: Pending = {id, mode, requestedDeadline, reply: new Deferred(), started: new Deferred(),
      startReceived: false, received: false, abandoned: false, stream: undefined};
    this.#pending = pending; this.#sequence += 1n;
    this.#sending = this.control.send(metadata, body);
    void this.#sending.catch(() => this.retire());
    return {descriptor: declared, pending};
  }
  #track<Value>(work: () => Promise<Value>): Promise<Value> {
    const task = work(); this.#tasks.add(task);
    void task.then(() => this.#tasks.delete(task), () => this.#tasks.delete(task)); return task;
  }
  /** Invoke once; uncertainty retires authority rather than authorizing a retry. */
  call(destination: CapabilityEndpoint, payload: JsonObject | Uint8Array, options: CallOptions): Promise<CallReply> {
    return this.#track(async () => {
      const {descriptor, pending} = this.#begin(destination, payload, options, false);
      const sending = this.#sending;
      try {
        await sending;
        const reply = await pending.reply.promise;
        if (pending.abandoned || !this.active) throw new Cancelled();
        if (reply.error !== null) { if (reply.error === 'outcome_unknown') this.retire(); throw new CapabilityCallError(reply.error); }
        const schema = descriptor.output_schema;
        if (schema !== null && (schema === undefined || !new native.PortableSchema(utf8(encodeJson(schema))).matches(utf8(reply.body)))) throw new TypeError('Child result schema refused');
        return new CallReply(reply.body);
      } finally {
        if (pending.received && !pending.abandoned) this.#pending = undefined; else this.abandon();
        await sending;
      }
    });
  }
  /** Own the child media scope for the callback, including uncancellable teardown. */
  stream<Value>(destination: CapabilityEndpoint, payload: JsonObject | Uint8Array,
      work: (stream: CallStream) => Promise<Value>, options: CallOptions): Promise<Value> {
    return this.#track(async () => {
      const {descriptor, pending} = this.#begin(destination, payload, options, true);
      const sending = this.#sending;
      const limits: (number | null)[] = [this.expiry, pending.requestedDeadline === null ? null : performance.now() + pending.requestedDeadline - Date.now()];
      let timer = new Deadline(minimum(limits), () => this.abandon());
      let channel: Channel | undefined, client: CallStream | undefined;
      let failed = false;
      try {
        await sending;
        const started = await pending.started.promise;
        if (pending.abandoned || !this.active) throw new Cancelled();
        if (started === null) {
          const reply = await pending.reply.promise;
          if (reply.error === 'outcome_unknown') this.retire();
          if (reply.error === null) throw new TypeError('Missing child start');
          throw new CapabilityCallError(reply.error);
        }
        const seconds = nullableNumber(started.remaining_seconds), deadline = nullableNumber(started.deadline_unix_ms);
        if ((seconds === null) !== (deadline === null)) throw new TypeError('Child lifetime differs');
        if (seconds !== null && deadline !== null) limits.push(performance.now() + seconds * 1000, performance.now() + deadline - Date.now());
        const expiry = minimum(limits);
        await timer.close(); timer = new Deadline(expiry, () => this.abandon());
        if (expiry !== null && expiry <= performance.now()) throw new Expired();
        if (pending.mode === null) throw new TypeError('Missing child stream mode');
        const mediaId = text(started.media_call_id);
        channel = await native.openTransportV2ClientStream(this.startup, mediaId, pending.mode, expiry === null ? null : (expiry - performance.now()) / 1000);
        client = new CallStream(this, pending, channel, descriptor, mediaId);
        pending.stream = client;
        await client.startInput();
        return await work(client);
      } catch (error: unknown) {
        failed = true; throw error;
      } finally {
        await timer.close();
        const complete = !pending.abandoned && pending.received
          && ((client !== undefined && client.finished) || (client === undefined && !pending.startReceived));
        if (!complete) this.abandon();
        try { if (client !== undefined) await client.close(); else await channel?.close(); }
        finally { await sending; }
        if (complete && this.#pending === pending) this.#pending = undefined;
        if (!complete && !failed) throw new Cancelled();
      }
    });
  }
  check(pending: Pending): void {
    if (!this.active || pending.abandoned || this.#pending !== pending) throw new TypeError('Child stream is retired');
  }
}

/** One scoped child stream; use Invocation.stream's callback to own its lifetime. */
export class CallStream implements AsyncIterable<CallStreamFrame>, Resource {
  #finished = false;
  #closed = false;
  #closing: Promise<void> | undefined;
  #inputDone = false;
  #inputSequence = 0;
  #reading = false;
  #writing = false;
  readonly #owner: Delegations;
  readonly #pending: Pending;
  readonly #channel: Channel;
  readonly #descriptor: JsonObject;
  readonly #mediaId: string;
  constructor(owner: Delegations, pending: Pending, channel: Channel, descriptor: JsonObject, mediaId: string) {
    this.#owner = owner; this.#pending = pending; this.#channel = channel; this.#descriptor = descriptor; this.#mediaId = mediaId;
  }
  get mode(): StreamMode { if (this.#pending.mode === null) throw new TypeError('Not a stream'); return this.#pending.mode; }
  get finished(): boolean { return this.#finished; }
  #check(): void { if (this.#closed) throw new TypeError('Child stream is closed'); this.#owner.check(this.#pending); }
  retire(): void { this.#closed = true; this.#closing ??= this.#channel.close(); void this.#closing.catch(() => undefined); }
  async close(): Promise<void> { if (!this.#finished) this.#owner.abandon(); this.retire(); await this.#closing; }
  async startInput(): Promise<void> { if (this.mode !== 'server_streaming') await this.#send('started', null, null, Buffer.alloc(0)); }
  async #send(kind: 'started' | 'chunk' | 'completed', payload: JsonObject | null, metadata: JsonObject | null, media: Uint8Array): Promise<void> {
    this.#check();
    if (this.mode === 'server_streaming' || this.#inputDone || this.#writing) throw new TypeError('Child input unavailable');
    const projection = utf8(encodeJson({call_id: this.#mediaId, direction: 'caller_to_provider',
      sequence: this.#inputSequence, kind, payload, media: metadata}));
    const binary = Buffer.from(media);
    native.encodeTransportV2Packet(projection, binary);
    if (payload !== null && !matches(this.#descriptor.input_chunk_schema, payload)) throw new TypeError('Child input schema refused');
    this.#writing = true;
    try {
      this.#check(); await this.#channel.sendInput(projection, binary); this.#inputSequence += 1;
      if (kind === 'completed') this.#inputDone = true;
    } catch (error: unknown) { this.#owner.abandon(); throw error; }
    finally { this.#writing = false; }
  }
  /** Send one bounded schema-admitted chunk, preserving separate binary media. */
  async send(payload: JsonObject | null = null, options: {readonly media?: Uint8Array; readonly mediaMetadata?: JsonObject} = {}): Promise<void> {
    await this.#send('chunk', payload, options.mediaMetadata ?? null, options.media ?? Buffer.alloc(0));
  }
  /** Half-close input; provider output remains independently readable. */
  async completeInput(): Promise<void> { await this.#send('completed', null, null, Buffer.alloc(0)); }
  /** Await a terminal's native end and exact host finalization before exposing it. */
  async nextOutput(): Promise<CallStreamFrame | null> {
    this.#check(); if (this.#finished) return null;
    if (this.#reading) throw new TypeError('Child output read already pending');
    this.#reading = true;
    try {
      const packet = await this.#channel.nextOutput();
      if (packet === null) throw new TypeError('Child omitted terminal');
      const value = new CallStreamFrame(packet.projection, packet.media);
      const schema = this.#descriptor[value.kind === 'chunk' ? 'output_chunk_schema' : 'output_schema'];
      if ((value.payload !== null || value.kind === 'completed') && !matches(schema, value.payload)) throw new TypeError('Child output schema refused');
      if (value.kind !== 'chunk') {
        if (await this.#channel.nextOutput() !== null) throw new TypeError('Child omitted stream end');
        await this.#channel.finish();
        const reply = await this.#pending.reply.promise; this.#check();
        if (reply.error !== null) throw new CapabilityCallError(reply.error);
        this.#finished = true;
      }
      this.#check(); return value;
    } catch (error: unknown) { this.#owner.abandon(); throw error; }
    finally { this.#reading = false; }
  }
  async *[Symbol.asyncIterator](): AsyncGenerator<CallStreamFrame> {
    for (let next = await this.nextOutput(); next !== null; next = await this.nextOutput()) yield next;
  }
}
