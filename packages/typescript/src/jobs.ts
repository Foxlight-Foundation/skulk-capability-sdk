import {createHash} from 'node:crypto';
import {resolve, join} from 'node:path';
import {inspect} from 'node:util';
import {native} from './native.js';
import {CapabilityApp, ApplicationStartup, Invocation} from './application.js';
import {check, type Resource} from './resources.js';
import {JobDefinition, jobDigest, jobFieldSource, MAX_JOB_OFFSET} from './job-contracts.js';
import {JobClient} from './job-client.js';
import {parseJobMessage} from './job-channel.js';
import {encodeJson, number, object, parseJson, text, utf8, type JsonObject, type JsonValue} from './json.js';

/** A definite refusal before submit sends any request to the backend. */
export class SubmissionRefused extends Error {
  constructor() { super('Backend refused submission'); }
}
/** An observed backend state; references and shared queue positions stay private. */
export interface JobObservation {
  readonly state: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted';
  readonly progress?: number | null;
  readonly stage?: string | null;
  readonly result?: JsonObject | null;
  readonly error?: string | null;
}
/** Backend effects must observe cancellation and finish real cleanup before return. */
export interface JobAdapter {
  submit(operationId: string, input: JsonObject, signal: AbortSignal): Promise<string>;
  observe(reference: string, signal: AbortSignal): Promise<JobObservation>;
  cancel(reference: string, signal: AbortSignal): Promise<void>;
}
/** Native-validated estimate; planning must not submit backend work. */
export interface JobPlan {
  readonly plan_digest: string;
  readonly summary: string;
  readonly expires_at: number;
  readonly estimated_seconds?: number | null;
  readonly estimated_cost_microunits?: number | null;
  readonly input?: JsonObject | null;
}
/** Durable-job timing bounds and an application-owned, effect-free planner. */
export interface DurableJobsOptions {
  readonly plan: (input: JsonObject, signal: AbortSignal) => Promise<JobPlan>;
  readonly pollSeconds?: number;
  readonly adapterSeconds?: number;
}
type Fence = Awaited<ReturnType<typeof native.openProcessFence>>;
interface Callback {readonly id: number; readonly controller: AbortController; readonly task: Promise<void>;}
interface State {
  readonly nodeId: string;
  readonly scope: string;
  readonly fence: Fence;
  readonly client: JobClient;
  readonly actions: Set<Promise<unknown>>;
  readonly controllers: Set<AbortController>;
  callback: Callback | undefined;
  reserving: Promise<void>;
  closing: boolean;
  failed: boolean;
  closed: Promise<void> | undefined;
}

/** Native durable inference jobs with caller-private reads and shared Python state.
 * Register both declarations in the signed manifest, declare operation bounds,
 * and acquire serve() from CapabilityApp setup. Descriptors grant no authority.
 */
export class DurableJobs {
  readonly #definition: JobDefinition;
  readonly #adapter: JobAdapter;
  readonly #options: DurableJobsOptions;
  readonly #poll: number;
  readonly #seconds: number;
  readonly #result: InstanceType<typeof native.PortableSchema>;
  #state: State | undefined;
  #opening = false;
  constructor(definition: JobDefinition, adapter: JobAdapter, options: DurableJobsOptions) {
    this.#definition = definition; this.#adapter = adapter; this.#options = options;
    this.#poll = options.pollSeconds ?? 1; this.#seconds = options.adapterSeconds ?? 30;
    if (![this.#poll, this.#seconds].every(value => Number.isFinite(value) && value >= 1e-9 && value <= 86400)) throw new TypeError('Job timing exceeds native bounds');
    this.#result = new native.PortableSchema(utf8(encodeJson(definition.resultSchema())));
  }
  /** Cached readiness; performs no journal, process or filesystem I/O. */
  get ready(): boolean { return this.#state !== undefined && !this.#state.closing && !this.#state.failed; }
  /** Bind read and work handlers to their exact declared ID/version pair. */
  register(app: CapabilityApp): void {
    for (const [index, descriptor] of this.#definition.descriptors().entries()) {
      app.unary(text(descriptor.id), invocation => this.#own(invocation, index === 0 ? 'read' : 'work'), {version: text(descriptor.version)});
    }
  }
  /** Acquire shared custody before inspecting state; cleanup retains real effects.
   * Cancellation during acquisition waits for ownership, then closes it. A
   * noncooperative adapter remains contained by the managed owner's final reap.
   */
  async serve(startup: ApplicationStartup, options: {readonly directory: string; readonly signal?: AbortSignal}): Promise<Resource> {
    if (this.#opening) throw new TypeError('Job service already acquired');
    this.#opening = true;
    let fence: Fence | undefined, state: State | undefined;
    try {
      if (options.signal !== undefined) check(options.signal);
      const descriptors = this.#definition.descriptors();
      for (const descriptor of descriptors) {
        const endpoint = startup.endpoint(text(descriptor.id), text(descriptor.version));
        if (!Buffer.from(endpoint.canonicalDescriptor()).equals(encodeJson(descriptor))) throw new TypeError('Job descriptors differ from startup');
      }
      if (startup.operationBounds === null) throw new TypeError('Job bounds must be declared');
      const bounds = object(parseJson(utf8(native.validateContractJson('OperationBounds', utf8(encodeJson(startup.operationBounds))))));
      const root = resolve(options.directory);
      fence = await native.openProcessFence(join(root, 'custody'));
      if (options.signal !== undefined) check(options.signal);
      const scope = jobDigest({node_id: startup.nodeId, descriptors, format: 'skulk.jobs/1', origin_layout: 'separate/1'});
      const client = new JobClient(raw => this.#callbackMessage(raw), () => this.#fail());
      state = {nodeId: startup.nodeId, scope, fence, client, actions: new Set(), controllers: new Set(),
        callback: undefined, reserving: Promise.resolve(), closing: false, failed: false, closed: undefined};
      this.#state = state;
      await client.request('initialize', {directory: root, bounds,
        binding: {format: 'skulk.jobs/1', origin_layout: 'separate/1', node_id: startup.nodeId, scope, bounds},
        poll_seconds: this.#poll, adapter_seconds: this.#seconds, result_schema: utf8(encodeJson(this.#definition.resultSchema()))});
      if (options.signal !== undefined) check(options.signal);
      this.#current();
      const acquired = state;
      return {close: () => this.#close(acquired)};
    } catch (error) {
      if (state !== undefined) await this.#close(state);
      else { fence?.close(); this.#opening = false; }
      throw error;
    }
  }
  #fail(): void {
    const state = this.#state;
    if (state === undefined) return;
    state.failed = true;
    for (const controller of state.controllers) controller.abort();
    state.callback?.controller.abort();
  }
  #close(state: State): Promise<void> {
    state.closed ??= (async () => {
      state.closing = true;
      for (const controller of state.controllers) controller.abort();
      state.callback?.controller.abort();
      const paused = state.client.request('pause').catch(() => {});
      // Acknowledging pause never substitutes for the actual JS cleanup drain.
      await Promise.allSettled(state.actions);
      await state.callback?.task;
      await paused;
      await state.client.close();
      state.fence.close();
      if (this.#state === state) this.#state = undefined;
      this.#opening = false;
    })();
    return state.closed;
  }
  #current(): State {
    const state = this.#state;
    if (state === undefined || state.closing || state.failed) throw new Error('Job service unavailable');
    return state;
  }
  #own(invocation: Invocation, role: 'read' | 'work'): Promise<JsonObject> {
    const state = this.#current();
    const task = this.#handle(state, invocation, role);
    state.actions.add(task);
    void task.then(() => state.actions.delete(task), () => state.actions.delete(task));
    return task;
  }
  #admit(state: State, invocation: Invocation, role: 'read' | 'work'): readonly [string, JsonObject, string] {
    check(invocation.signal);
    const context = invocation.context.export(), executor = object(context.executor);
    const descriptor = this.#definition.descriptors()[role === 'read' ? 0 : 1];
    if (descriptor === undefined || executor.node_id !== state.nodeId || invocation.capabilityId !== descriptor.id || invocation.version !== descriptor.version
      || executor.behavior !== descriptor.behavior || executor.processing_domain !== descriptor.processing_domain
      || executor.descriptor_revision !== native.portableV2ContractDigest('Descriptor', utf8(encodeJson(descriptor)))) throw new Error('Job endpoint differs');
    const principal: Record<string, JsonValue> = Object.create(null) as Record<string, JsonValue>;
    for (const field of ['caller_id', 'caller_kind', 'issuer_node_id']) {
      const value = context[field]; if (value === undefined) throw new Error('Job caller unavailable'); principal[field] = value;
    }
    const raw = utf8(native.validateContractJson('OperationCall', utf8(invocation.rawPayload))), call = object(parseJson(raw));
    if (['status', 'list', 'log'].includes(text(call.op)) !== (role === 'read')) throw new Error('Job verb requires its exact descriptor');
    if (call.offset !== undefined && call.offset !== null && (!Number.isSafeInteger(number(call.offset)) || number(call.offset) > MAX_JOB_OFFSET)) throw new Error('Job offset exceeds its bound');
    return [jobDigest(principal), call, raw];
  }
  #status(value: JsonValue, original: string): JsonObject {
    return {...object(parseJson(text(value))), operation_id: original, backend_reference: null, queue_position: null};
  }
  async #handle(state: State, invocation: Invocation, role: 'read' | 'work'): Promise<JsonObject> {
    const [principal, call, raw] = this.#admit(state, invocation, role), op = text(call.op);
    if (op === 'plan') return await this.#plan(state, object(call.input), invocation.signal);
    if (op === 'list') return object(await state.client.request('page', {principal, offset: call.offset ?? 0}));
    const input = op === 'start' ? jobFieldSource(raw, 'input') : '';
    const original = op === 'start' && (call.operation_id === null || call.operation_id === undefined)
      ? createHash('sha256').update(input).digest('hex').slice(0, 32) : text(call.operation_id);
    const key = jobDigest({scope: state.scope, principal, operation_id: original});
    if (op === 'start') {
      const previous = state.reserving;
      let unlock: (() => void) | undefined;
      state.reserving = new Promise<void>(resolve => { unlock = resolve; });
      await previous;
      try {
        check(invocation.signal); if (this.#current() !== state) throw new Error('Job service changed');
        try {
          await state.client.request('origin', {principal, operation_id: original});
          // Origin allocation can block on SQLite. Cancellation must be observed
          // after it settles, before any native reservation can submit an effect.
          check(invocation.signal); if (this.#current() !== state) throw new Error('Job service changed');
          return this.#status(await state.client.request('reserve', {key, payload: input}), original);
        } finally { await state.client.request('reclaim', {key}); }
      } finally { unlock?.(); }
    }
    if (op === 'status') return this.#status(await state.client.request('status', {key}), original);
    if (op === 'cancel') return this.#status(await state.client.request('cancel', {key}), original);
    if (op === 'log') return {...object(parseJson(text(await state.client.request('log', {key, offset: call.offset ?? 0})))), operation_id: original};
    throw new Error('Unknown job verb');
  }
  async #plan(state: State, input: JsonObject, signal: AbortSignal): Promise<JsonObject> {
    const controller = new AbortController(), abort = (): void => controller.abort();
    state.controllers.add(controller); signal.addEventListener('abort', abort, {once: true});
    const timer = setTimeout(abort, this.#seconds * 1000);
    try {
      check(signal);
      const result = await this.#options.plan(input, controller.signal);
      check(controller.signal);
      return object(parseJson(utf8(native.validateContractJson('OperationPlan', utf8(encodeJson({...result}))))));
    } finally { clearTimeout(timer); signal.removeEventListener('abort', abort); state.controllers.delete(controller); }
  }
  async #callbackMessage(raw: string): Promise<void> {
    const state = this.#state;
    if (state === undefined) throw new Error('Job callback outside service');
    const request = parseJobMessage(raw), id = number(request.id);
    if (request.kind === 'abort') {
      if (state.callback?.id === id) { state.callback.controller.abort(); await state.callback.task; }
      await state.client.send({kind: 'drained', id}); return;
    }
    if (state.callback !== undefined) throw new Error('Native job callbacks overlap');
    if (state.closing || state.failed) { await state.client.send({kind: 'adapter_response', id, value: '{"kind":"failed"}'}); return; }
    const controller = new AbortController();
    const task = this.#callback(state, request, id, controller.signal);
    state.callback = {id, controller, task};
    try { await task; } finally { if (state.callback?.id === id) state.callback = undefined; }
  }
  async #callback(state: State, request: JsonObject, id: number, signal: AbortSignal): Promise<void> {
    let response: JsonObject = {kind: 'failed'};
    try {
      if (request.kind === 'submit') {
        response = {kind: 'accepted', reference: await this.#adapter.submit(text(request.operation_id), object(request.input), signal)};
      } else if (request.kind === 'observe') {
        const observation = await this.#adapter.observe(text(request.reference), signal);
        let {state: status, result = null, error = null} = observation;
        if (status === 'succeeded') {
          let valid = false;
          try { valid = result !== null && this.#result.matches(utf8(encodeJson(result))); } catch { /* Invalid native result is a definite job failure. */ }
          if (!valid) { status = 'failed'; result = null; error = 'job result does not satisfy its native schema'; }
        }
        response = {kind: 'observed', state: status, progress: observation.progress ?? null, stage: observation.stage ?? null, result, error};
      } else if (request.kind === 'cancel') {
        await this.#adapter.cancel(text(request.reference), signal); response = {kind: 'cancel_accepted'};
      }
    } catch (error) { response = error instanceof SubmissionRefused ? {kind: 'refused', reason: 'backend refused submission'} : {kind: 'failed'}; }
    if (signal.aborted) return;
    let value: string;
    try { value = utf8(encodeJson(response)); } catch { value = '{"kind":"failed"}'; }
    // Native respond rejects expired IDs. A late JS completion cannot restore rights.
    try { await state.client.send({kind: 'adapter_response', id, value}); } catch { this.#fail(); }
  }
  toJSON(): string { return 'DurableJobs'; }
  [inspect.custom](): string { return 'DurableJobs'; }
}
