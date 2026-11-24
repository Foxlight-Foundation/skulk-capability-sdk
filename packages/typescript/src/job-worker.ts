/** Private owned helper entry point. It contains no provider or caller authority. */
import {join} from 'node:path';
import {native} from './native.js';
import {jobFrames, parseJobMessage, sendJobFrame} from './job-channel.js';
import {JobStorage} from './job-storage.js';
import {number, object, text, type JsonObject, type JsonValue} from './json.js';

type Runner = ReturnType<typeof native.operationRunner>;
let storage: JobStorage | undefined, runner: Runner | undefined, pump: Promise<void> | undefined;
let stopping = false, parentGone = false;
let paused: Promise<void> | undefined;
let drain: {readonly id: number; readonly resolve: () => void} | undefined;
const pending = new Map<Promise<void>, string>();

async function send(value: JsonObject): Promise<void> { await sendJobFrame(process.stdout, value); }
async function callbacks(active: Runner): Promise<void> {
  for (let raw = await active.nextRequest(); raw !== null; raw = await active.nextRequest()) {
    const request = parseJobMessage(raw);
    if (request.kind === 'abort') {
      if (parentGone) continue;
      // Do not request a replacement callback before the real adapter drains.
      let complete: (() => void) | undefined;
      const waited = new Promise<void>(resolve => { complete = resolve; });
      if (complete === undefined) throw new TypeError('Job callback drain unavailable');
      drain = {id: number(request.id), resolve: complete};
      await send({kind: 'adapter', value: raw});
      await waited; drain = undefined;
    } else await send({kind: 'adapter', value: raw});
  }
}

async function pause(): Promise<void> {
  stopping = true;
  paused ??= (async () => { await runner?.stop(); await pump; })();
  await paused;
}

async function dispatch(method: string, value: JsonObject): Promise<JsonValue> {
  const state = storage, active = runner;
  if (state === undefined || active === undefined) throw new TypeError('Job helper unavailable');
  if (method === 'pause') { await pause(); return null; }
  if (method === 'origin') {
    if (stopping) throw new TypeError('Job helper is stopping');
    return state.origin(text(value.principal), text(value.operation_id));
  }
  if (method === 'reclaim') { state.reclaim(text(value.key)); return null; }
  if (method === 'page') return state.page(text(value.principal), number(value.offset));
  const key = text(value.key);
  if (method === 'reserve') {
    if (stopping) throw new TypeError('Job helper is stopping');
    return await active.reserve(key, text(value.payload));
  }
  if (method === 'status') return await active.status(key);
  if (method === 'cancel') {
    if (await active.status(key) === null) throw new TypeError('Unknown job');
    return await active.cancel(key);
  }
  if (method === 'log') {
    if (await active.status(key) === null) throw new TypeError('Unknown job');
    return await active.log(key, number(value.offset));
  }
  throw new TypeError('Unknown job helper method');
}

async function reply(sequence: number, method: string, value: JsonObject): Promise<void> {
  try { await send({kind: 'reply', sequence, ok: true, value: await dispatch(method, value)}); }
  catch { await send({kind: 'reply', sequence, ok: false, value: null}); }
}

async function main(): Promise<void> {
  let initialized = false;
  try {
    for await (const message of jobFrames(process.stdin)) {
      if (message.kind === 'drained') {
        const active = drain;
        if (active === undefined || active.id !== message.id) throw new TypeError('Job callback drain differs');
        active.resolve(); continue;
      }
      if (message.kind === 'adapter_response') {
        if (runner === undefined) throw new TypeError('Job helper unavailable');
        const id = number(message.id);
        try { runner.respond(id, text(message.value)); }
        catch { runner.respond(id, '{"kind":"failed"}'); }
        continue;
      }
      if (message.kind !== 'request') throw new TypeError('Job helper direction refused');
      const sequence = number(message.sequence), method = text(message.method), value = object(message.value);
      if (!Number.isSafeInteger(sequence) || sequence < 0) throw new TypeError('Invalid job helper sequence');
      if (!initialized) {
        if (method !== 'initialize' || sequence !== 0) throw new TypeError('Job helper initialization required');
        const directory = text(value.directory), binding = object(value.binding), bounds = object(value.bounds);
        storage = JobStorage.open(directory, binding, bounds);
        runner = native.operationRunner(storage.journal, join(directory, 'logs'), storage.scope,
          number(value.poll_seconds), number(value.adapter_seconds), text(value.result_schema));
        await runner.start(); initialized = true;
        pump = callbacks(runner);
        // A failed pump is terminal. The parent retains outer process custody.
        void pump.catch(() => { process.stdin.destroy(new Error('Job callback pump failed')); });
        await send({kind: 'reply', sequence, ok: true, value: null}); continue;
      }
      if (method === 'close') {
        await pause(); await Promise.allSettled(pending.keys());
        storage?.journal.close(); storage = undefined;
        await send({kind: 'reply', sequence, ok: true, value: null}); break;
      }
      // Cleanup has its own bounded slot: saturated reads cannot strand an
      // origin allocation or prevent the worker's shutdown handshake.
      const maintenance = ['pause', 'reclaim'], methods = [...pending.values()];
      if (sequence === 0 || (maintenance.includes(method) ? methods.includes(method)
        : methods.filter(value => !maintenance.includes(value)).length >= 8)) throw new TypeError('Job helper capacity exceeded');
      const task = reply(sequence, method, value); pending.set(task, method);
      void task.then(() => pending.delete(task), () => { pending.delete(task); process.stdin.destroy(new Error('Job helper output failed')); });
    }
  } catch {
    // No backend diagnostics, paths or inputs cross a failed private channel.
    try { await send({kind: 'fatal'}); } catch { /* Parent may already be gone. */ }
    process.exitCode = 1;
  } finally {
    // EOF means no parent callback acknowledgment can still arrive. Its main
    // process retains the outer fence until its own adapter cleanup completes.
    parentGone = true; drain?.resolve();
    try { await pause(); await Promise.allSettled(pending.keys()); }
    finally { storage?.journal.close(); process.stdin.destroy(); process.stdout.end(); }
  }
}

await main();
