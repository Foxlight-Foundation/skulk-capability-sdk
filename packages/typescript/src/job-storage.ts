import {constants, closeSync, fstatSync, fsyncSync, lstatSync, mkdirSync, openSync, readSync, renameSync, unlinkSync, writeFileSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
import {join} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
import {performance} from 'node:perf_hooks';
import {native} from './native.js';
import {copyJson, encodeJson, number, object, parseJson, text, utf8, type JsonObject, type JsonValue} from './json.js';
import {jobDigest, JOB_PAGE_SIZE} from './job-contracts.js';

const MAX_DATABASE_BYTES = 128 * 1024 ** 2;
const BINDING = 'CREATE TABLE IF NOT EXISTS native_job_binding (singleton INTEGER PRIMARY KEY CHECK(singleton=1), binding_digest TEXT NOT NULL);';
const ORIGINS = BINDING + `
CREATE TABLE IF NOT EXISTS native_job_origins (
 internal_id TEXT PRIMARY KEY, principal TEXT NOT NULL, original_id TEXT NOT NULL,
 sequence INTEGER NOT NULL UNIQUE, UNIQUE(principal,original_id));
CREATE INDEX IF NOT EXISTS native_job_origins_principal_sequence ON native_job_origins(principal,sequence);`;
type Journal = InstanceType<typeof native.NativeJournal>;
type SqlRow = ReturnType<ReturnType<DatabaseSync['prepare']>['get']>;

function missing(error: unknown): boolean { return error instanceof Error && 'code' in error && error.code === 'ENOENT'; }
function exists(path: string): boolean { try { lstatSync(path); return true; } catch (error) { if (missing(error)) return false; throw error; } }
function directory(path: string): void {
  mkdirSync(path, {recursive: true, mode: 0o700});
  const info = lstatSync(path);
  if (!info.isDirectory() || info.uid !== process.geteuid?.() || (info.mode & 0o077) !== 0) throw new TypeError('Job directory must be private');
}
function privateDescriptor(path: string, maximum: number): number {
  const descriptor = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const info = fstatSync(descriptor);
    if (!info.isFile() || info.uid !== process.geteuid?.() || (info.mode & 0o077) !== 0 || info.size > maximum) {
      throw new TypeError('Job file must be private and bounded');
    }
    return descriptor;
  } catch (error) { closeSync(descriptor); throw error; }
}
function validateFile(path: string): void { const descriptor = privateDescriptor(path, MAX_DATABASE_BYTES); closeSync(descriptor); }
function readMarker(path: string): Buffer {
  const descriptor = privateDescriptor(path, 65536);
  try {
    // Keep the read bounded even if another same-user process grows the file
    // after fstat; ownership fencing does not make arbitrary writers trusted.
    const bytes = Buffer.alloc(65537); let offset = 0;
    while (offset < bytes.length) {
      const count = readSync(descriptor, bytes, offset, bytes.length - offset, null);
      if (count === 0) break;
      offset += count;
    }
    if (offset > 65536) throw new TypeError('Job marker exceeds its bound');
    return bytes.subarray(0, offset);
  } finally { closeSync(descriptor); }
}
function createFile(path: string): void {
  try { const descriptor = openSync(path, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600); closeSync(descriptor); }
  catch (error) { if (!(error instanceof Error && 'code' in error && error.code === 'EEXIST')) throw error; validateFile(path); }
}
function atomicMarker(root: string, path: string, bytes: Buffer): void {
  const temporary = join(root, '.stage-' + randomUUID());
  const descriptor = openSync(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
  try {
    try { writeFileSync(descriptor, bytes); fsyncSync(descriptor); } finally { closeSync(descriptor); }
    renameSync(temporary, path);
    const parent = openSync(root, constants.O_RDONLY | constants.O_DIRECTORY);
    try { fsyncSync(parent); } finally { closeSync(parent); }
  } finally { try { unlinkSync(temporary); } catch (error) { if (!missing(error)) throw error; } }
}
function readonly(path: string, work: (database: DatabaseSync) => void): void {
  validateFile(path);
  const database = new DatabaseSync(path, {readOnly: true, timeout: 2000, allowExtension: false});
  try { work(database); } finally { database.close(); }
}
function transaction<Value>(path: string, work: (database: DatabaseSync) => Value): Value {
  validateFile(path);
  const database = new DatabaseSync(path, {timeout: 2000, allowExtension: false});
  try {
    database.exec('PRAGMA journal_mode=DELETE; PRAGMA synchronous=FULL; BEGIN IMMEDIATE;');
    try { const value = work(database); database.exec('COMMIT'); return value; }
    catch (error) { database.exec('ROLLBACK'); throw error; }
  } finally { database.close(); }
}
function cell(row: SqlRow, key: string): string { const value = row?.[key]; if (typeof value !== 'string') throw new TypeError('Job metadata is damaged'); return value; }
function integer(row: SqlRow, key: string): number { const value = row?.[key]; if (typeof value !== 'number' || !Number.isSafeInteger(value)) throw new TypeError('Job metadata is damaged'); return value; }

/** Helper-process state; never opened from the application's control executor. */
export class JobStorage {
  readonly journal: Journal;
  readonly scope: string;
  readonly #origins: string;
  readonly #quota: number;
  private constructor(journal: Journal, scope: string, origins: string, quota: number) { this.journal = journal; this.scope = scope; this.#origins = origins; this.#quota = quota; }
  /** Validate both existing files before native recovery, under the outer fence. */
  static open(root: string, binding: JsonObject, bounds: JsonObject): JobStorage {
    directory(root);
    const marker = join(root, 'native-jobs.json'), primary = join(root, 'operations.sqlite'), origins = join(root, 'origins.sqlite');
    const canonical = encodeJson(binding), digest = jobDigest(binding), initialized = exists(marker);
    const checkBinding = (database: DatabaseSync): void => {
      if (cell(database.prepare('SELECT binding_digest FROM native_job_binding WHERE singleton=1').get(), 'binding_digest') !== digest) throw new TypeError('Job state binding differs');
    };
    if (initialized) {
      if (!readMarker(marker).equals(canonical)) throw new TypeError('Job state belongs to another definition or node');
      readonly(primary, database => {
        database.prepare('SELECT key,input_digest,reserved_at FROM reservations LIMIT 0').all();
        database.prepare('SELECT key,operation_id,scope,state,submission,sequence FROM operations LIMIT 0').all();
        checkBinding(database);
      });
      readonly(origins, database => { database.prepare('SELECT internal_id,principal,original_id,sequence FROM native_job_origins LIMIT 0').all(); checkBinding(database); });
    } else if ([primary, origins, join(root, 'logs')].some(exists)) throw new TypeError('Unbound job storage requires explicit recovery');
    const journal = new native.NativeJournal(primary, utf8(encodeJson(bounds)), Math.floor(Date.now() / 1000));
    try {
      createFile(origins); transaction(origins, database => database.exec(ORIGINS));
      if (!initialized) {
        // No native worker/connection is live yet. Subsequent primary access is native only.
        transaction(primary, database => { database.exec(BINDING); database.prepare('INSERT INTO native_job_binding VALUES (1,?)').run(digest); });
        transaction(origins, database => database.prepare('INSERT INTO native_job_binding VALUES (1,?)').run(digest));
        atomicMarker(root, marker, canonical);
      }
      const state = new JobStorage(journal, text(binding.scope), origins, number(bounds.reservations));
      state.reclaim(); return state;
    } catch (error) { journal.close(); throw error; }
  }
  key(principal: string, originalId: string): string { return jobDigest({scope: this.scope, principal, operation_id: originalId}); }
  /** Allocate only caller metadata; native reservations remain replay authority. */
  origin(principal: string, originalId: string): string {
    const key = this.key(principal, originalId);
    transaction(this.#origins, database => {
      const page = integer(database.prepare('PRAGMA page_size').get(), 'page_size');
      const pages = Math.floor(MAX_DATABASE_BYTES / page);
      if (!Number.isSafeInteger(pages) || pages < 1 || integer(database.prepare(`PRAGMA max_page_count=${pages}`).get(), 'max_page_count') > pages) throw new TypeError('Job metadata exceeds its bound');
      const prior = database.prepare('SELECT principal,original_id FROM native_job_origins WHERE internal_id=?').get(key);
      if (prior === undefined) {
        if (integer(database.prepare('SELECT count(*) AS count FROM native_job_origins').get(), 'count') >= this.#quota) throw new TypeError('Job identity capacity reached');
        const sequence = integer(database.prepare('SELECT coalesce(max(sequence),0)+1 AS next FROM native_job_origins').get(), 'next');
        database.prepare('INSERT INTO native_job_origins VALUES (?,?,?,?)').run(key, principal, originalId, sequence);
      } else if (cell(prior, 'principal') !== principal || cell(prior, 'original_id') !== originalId) throw new TypeError('Job identity differs');
    });
    return key;
  }
  *#rows(principal: string | null = null, key: string | null = null): Generator<readonly [string, string]> {
    let before: number | null = null;
    while (true) {
      const predicates: string[] = [], parameters: (number | string)[] = [];
      if (principal !== null) { predicates.push('principal=?'); parameters.push(principal); }
      if (key !== null) { predicates.push('internal_id=?'); parameters.push(key); }
      if (before !== null) { predicates.push('sequence<?'); parameters.push(before); }
      const rows = transaction(this.#origins, database => database.prepare('SELECT internal_id,original_id,sequence FROM native_job_origins WHERE ' + (predicates.join(' AND ') || '1=1') + ' ORDER BY sequence DESC LIMIT ?').all(...parameters, JOB_PAGE_SIZE));
      if (rows.length === 0) return;
      before = integer(rows.at(-1), 'sequence');
      for (const row of rows) yield [cell(row, 'internal_id'), cell(row, 'original_id')];
    }
  }
  observe(key: string): JsonObject | null {
    const value = parseJson(this.journal.transact(utf8(encodeJson({method: 'status', scope: this.scope, operation_id: key, now: Math.floor(Date.now() / 1000)}))));
    return value === null ? null : object(value);
  }
  reclaim(key: string | null = null): void {
    const deadline = performance.now() + 2000;
    for (const [internal] of this.#rows(null, key)) {
      if (key === null && performance.now() >= deadline) break;
      if (this.observe(internal) === null) transaction(this.#origins, database => database.prepare('DELETE FROM native_job_origins WHERE internal_id=?').run(internal));
    }
  }
  /** List only this caller's work, withholding shared queue positions and references. */
  page(principal: string, offset: number): JsonObject {
    const deadline = performance.now() + 2000;
    let visible = 0, active = 0, outstanding = 0; const operations: JsonValue[] = [];
    for (const [key, original] of this.#rows(principal)) {
      if (performance.now() >= deadline) throw new TypeError('Job page observation expired');
      const value = this.observe(key);
      if (value !== null) {
        if (value.state === 'queued' || value.state === 'running') active++;
        if (value.state === 'interrupted') outstanding++;
        if (offset <= visible && visible < offset + JOB_PAGE_SIZE) operations.push({...copyJson(value), operation_id: original, backend_reference: null, queue_position: null, input: null, result: null});
        visible++;
      }
    }
    return {operations, active, outstanding, next_offset: visible > offset + JOB_PAGE_SIZE ? offset + JOB_PAGE_SIZE : null};
  }
}
