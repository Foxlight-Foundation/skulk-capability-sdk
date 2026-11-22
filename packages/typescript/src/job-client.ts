import {spawn, type ChildProcessWithoutNullStreams} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {jobFrames, sendJobFrame} from './job-channel.js';
import {number, text, type JsonObject, type JsonValue} from './json.js';

interface Pending {
  readonly method: string;
  readonly resolve: (value: JsonValue) => void;
  readonly reject: (reason: Error) => void;
}

/** Own the SDK-selected helper through its actual process exit, never PATH lookup. */
export class JobClient {
  readonly #child: ChildProcessWithoutNullStreams;
  readonly #exit: Promise<void>;
  readonly #pending = new Map<number, Pending>();
  readonly #adapter: (raw: string) => Promise<void>;
  readonly #failed: () => void;
  #sequence = 0;
  #failure = false;
  #closing = false;
  #closed = false;
  #reader: Promise<void>;
  constructor(adapter: (raw: string) => Promise<void>, failed: () => void) {
    this.#adapter = adapter; this.#failed = failed;
    // No parent provider credentials or NODE_OPTIONS enter the storage helper.
    this.#child = spawn(process.execPath, [fileURLToPath(new URL('./job-worker.js', import.meta.url))], {
      detached: false, stdio: ['pipe', 'pipe', 'pipe'], env: {LANG: 'C.UTF-8'},
    });
    this.#exit = new Promise(resolve => {
      this.#child.once('error', () => this.#fail());
      this.#child.once('close', () => {
        this.#closed = true;
        if (!this.#closing || this.#pending.size !== 0) this.#fail();
        resolve();
      });
    });
    // A broken channel is terminal; diagnostic contents never escape the helper.
    this.#child.stdin.on('error', () => this.#fail());
    let stderr = 0;
    this.#child.stderr.on('data', (bytes: Buffer) => { stderr += bytes.length; if (stderr > 65536) this.#fail(); });
    this.#reader = this.#read();
    void this.#reader.catch(() => this.#fail());
  }
  #fail(): void {
    if (this.#failure) return;
    this.#failure = true;
    for (const pending of this.#pending.values()) pending.reject(new Error('Job helper unavailable'));
    this.#pending.clear(); this.#failed();
    if (!this.#closed) this.#child.kill('SIGKILL');
  }
  async #read(): Promise<void> {
    for await (const message of jobFrames(this.#child.stdout)) {
      if (message.kind === 'adapter') {
        // Adapter work is independently owned; the reader must still receive abort.
        void this.#adapter(text(message.value)).catch(() => this.#fail());
      } else if (message.kind === 'reply') {
        const sequence = number(message.sequence), pending = this.#pending.get(sequence);
        if (pending === undefined || typeof message.ok !== 'boolean' || message.value === undefined) throw new TypeError('Job helper reply differs');
        this.#pending.delete(sequence);
        if (message.ok) pending.resolve(message.value);
        else pending.reject(new Error('Job operation refused'));
      } else throw new TypeError('Job helper direction refused');
    }
    if (!this.#closing || this.#pending.size !== 0) this.#fail();
  }
  /** Keep the RPC owned even when its originating application call is cancelled. */
  async request(method: string, value: JsonObject = {}): Promise<JsonValue> {
    if (this.#failure || this.#closed || (this.#closing && method !== 'close')) throw new Error('Job helper unavailable');
    const maintenance = ['pause', 'close', 'reclaim'];
    const pending = [...this.#pending.values()];
    if (maintenance.includes(method) ? pending.some(item => item.method === method)
      : pending.filter(item => !maintenance.includes(item.method)).length >= 8) throw new Error('Job helper capacity reached');
    const sequence = this.#sequence++;
    if (!Number.isSafeInteger(sequence)) { this.#fail(); throw new Error('Job helper sequence exhausted'); }
    const result = new Promise<JsonValue>((resolve, reject) => this.#pending.set(sequence, {method, resolve, reject}));
    // Install a rejection observer before writing: an early process exit may
    // reject while the stream completion is still in flight.
    void result.catch(() => {});
    try { await sendJobFrame(this.#child.stdin, {kind: 'request', sequence, method, value}); }
    catch { this.#fail(); }
    return await result;
  }
  async send(value: JsonObject): Promise<void> {
    if (this.#failure || this.#closed) throw new Error('Job helper unavailable');
    try { await sendJobFrame(this.#child.stdin, value); }
    catch { this.#fail(); throw new Error('Job helper unavailable'); }
  }
  /** No fence may be released until this returns and application effects drain. */
  async close(): Promise<void> {
    if (!this.#closing) {
      this.#closing = true;
      if (!this.#failure && !this.#closed) {
        try { await this.request('close'); this.#child.stdin.end(); }
        catch { this.#fail(); }
      }
    }
    await this.#exit; await this.#reader.catch(() => {});
  }
}
