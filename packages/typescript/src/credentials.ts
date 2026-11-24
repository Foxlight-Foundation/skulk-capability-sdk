import {createHash} from 'node:crypto';
import {spawn} from 'node:child_process';
import {constants} from 'node:fs';
import {open} from 'node:fs/promises';
import {isAbsolute} from 'node:path';
import {inspect} from 'node:util';
import {credentialCohort} from './credential-cohort.js';
import {encodeJson, object, parseJson, text, utf8, type JsonObject} from './json.js';
import {Cancelled, check, type Resource} from './resources.js';

/** A selected runtime's explicit interpreter; never discovered from PATH/env. */
export interface PythonCredentialHelper {
  readonly executable: string;
  readonly executableSha256: string;
}
/** A declared value could not be read; diagnostics contain no private metadata. */
export class CredentialUnavailable extends Error {
  constructor() { super('Selected credential is unavailable'); }
}
/** Secret bytes require explicit extraction; inspection and JSON stay redacted. */
export class SecretBytes {
  readonly #value: Buffer;
  constructor(value: Uint8Array) { this.#value = Buffer.from(value); Object.freeze(this); }
  getSecretValue(): Uint8Array { return Buffer.from(this.#value); }
  toString(): string { return '[REDACTED]'; }
  toJSON(): string { return '[REDACTED]'; }
  [inspect.custom](): string { return 'SecretBytes([REDACTED])'; }
}
interface RunningRead { readonly stop: () => void; }

/** Read only signed declarations through the owner's exact selected storage.
 * The public Python SDK implements the shared reader inside one bounded child.
 * All reads, including dropped Promises, remain owned until actual child exit.
 */
export class CredentialReader implements Resource {
  readonly #configuration: string | null;
  readonly #declarations: readonly JsonObject[];
  readonly #declared: ReadonlyMap<string, boolean>;
  readonly #helper: PythonCredentialHelper | undefined;
  readonly #tasks = new Set<Promise<unknown>>();
  readonly #children = new Set<RunningRead>();
  #closing = false;
  #closed: Promise<void> | undefined;
  constructor(configuration: string | null, declarations: readonly JsonObject[], helper?: PythonCredentialHelper) {
    this.#configuration = configuration;
    this.#declarations = declarations.map(d => ({credential_id: text(d.credential_id), required: d.required ?? null}));
    this.#declared = new Map(this.#declarations.map(d => {
      if (typeof d.required !== 'boolean') throw new TypeError('Credential declaration requires an explicit required bit');
      return [text(d.credential_id),d.required];
    }));
    if (this.#declared.size !== this.#declarations.length) throw new TypeError('Duplicate credential declaration');
    this.#helper = helper === undefined ? undefined : {executable:helper.executable, executableSha256:helper.executableSha256};
    if (this.#helper !== undefined && (!isAbsolute(this.#helper.executable) || !/^[a-f0-9]{64}$/.test(this.#helper.executableSha256))) {
      throw new TypeError('Credential helper requires an explicit measured runtime');
    }
  }
  /** Observe active aliases afresh, including rotation/retirement; never mutate. */
  read(reference: string, options: {readonly signal?: AbortSignal} = {}): Promise<SecretBytes | null> {
    const task = this.#read(reference, options.signal);
    this.#tasks.add(task);
    void task.then(() => this.#tasks.delete(task), () => this.#tasks.delete(task));
    return task;
  }
  async #read(reference: string, signal: AbortSignal | undefined): Promise<SecretBytes | null> {
    const required = this.#declared.get(reference);
    if (required === undefined) throw new TypeError('Credential reference is not declared');
    if (this.#closing || signal?.aborted) throw new Cancelled();
    if (this.#tasks.size >= 8 || this.#helper === undefined || this.#configuration === null) throw new CredentialUnavailable();
    try {
      // Hash a bounded actual regular file, not an ambient command/shebang.
      // The selected interpreter can be a venv symlink. NONBLOCK lets fstat
      // reject a substituted FIFO without leaving an unowned blocked open.
      const handle = await open(this.#helper.executable, constants.O_RDONLY | constants.O_NONBLOCK);
      try {
        const info = await handle.stat();
        if (!info.isFile() || info.size > 128 * 1024 * 1024 || (info.mode & 0o022) !== 0
            || (info.uid !== 0 && info.uid !== process.getuid?.())) throw new CredentialUnavailable();
        const hash = createHash('sha256'), chunk = Buffer.alloc(1024 * 1024);
        let size = 0;
        while (true) {
          if (signal !== undefined) check(signal);
          if (this.#closing) throw new Cancelled();
          const {bytesRead} = await handle.read(chunk, 0, chunk.length, null);
          if (bytesRead === 0) break;
          size += bytesRead;
          if (size > 128 * 1024 * 1024) throw new CredentialUnavailable();
          hash.update(chunk.subarray(0, bytesRead));
        }
        if (hash.digest('hex') !== this.#helper.executableSha256) throw new CredentialUnavailable();
      } finally { await handle.close(); }
      if (signal !== undefined) check(signal);
      if (this.#closing) throw new Cancelled();
      const request = encodeJson({protocol: 1, configuration: this.#configuration,
        declarations: this.#declarations, reference});
      if (request.length > 32768) throw new CredentialUnavailable();
      return await this.#execute(this.#helper.executable, request, required, signal);
    } catch (error: unknown) {
      if (error instanceof Cancelled) throw error;
      throw new CredentialUnavailable();
    }
  }
  #execute(executable: string, request: Buffer, required: boolean, signal: AbortSignal | undefined): Promise<SecretBytes | null> {
    return new Promise((resolve, reject) => {
      const env: NodeJS.ProcessEnv = {PATH:'/usr/bin:/bin',LANG:'C.UTF-8'};
      if (process.platform === 'linux') env.DBUS_SESSION_BUS_ADDRESS = `unix:path=/run/user/${process.getuid?.()}/bus`;
      const child = spawn(executable, ['-I', '-B', '-m', 'skulk_capability_sdk._credential_bridge'], {
        env, stdio:['pipe','pipe','ignore'], detached:false,
      });
      let buffer: Buffer = Buffer.alloc(0), hello = false, reply: JsonObject | undefined;
      let failed = false, cancelled = false, exited = false;
      let timer: NodeJS.Timeout;
      const kill = (): void => { if (!exited) { try {child.kill('SIGKILL');} catch {failed = true;} } };
      const stop = (): void => { cancelled = true; kill(); };
      const running: RunningRead = {stop}; this.#children.add(running);
      const unavailable = (): void => { failed = true; kill(); };
      timer = setTimeout(unavailable, 5000);
      signal?.addEventListener('abort', stop, {once:true});
      if (signal?.aborted || this.#closing) stop();
      child.once('spawn', () => { if (cancelled || failed || this.#closing) kill(); });
      child.once('error', unavailable);
      child.stdin.on('error', unavailable);
      child.stdout.on('error', unavailable);
      child.stdout.on('data', (chunk: Buffer) => {
        if (failed || cancelled) return;
        try {
          if (buffer.length + chunk.length > 16384) throw new CredentialUnavailable();
          buffer = Buffer.concat([buffer, chunk]);
          for (let offset = buffer.indexOf(10); offset >= 0; offset = buffer.indexOf(10)) {
            const line = object(parseJson(utf8(buffer.subarray(0, offset))));
            buffer = buffer.subarray(offset + 1);
            if (!hello) {
              if (line.protocol !== 1 || line.sdk_version !== credentialCohort.sdkVersion
                  || line.reader_sha256 !== credentialCohort.readerSha256 || Object.keys(line).length !== 3) throw new CredentialUnavailable();
              hello = true; clearTimeout(timer); timer = setTimeout(unavailable, 12000);
              // Selection metadata travels only after the matching cohort hello.
              child.stdin.end(request);
            } else {
              if (reply !== undefined || line.protocol !== 1 || Object.keys(line).length !== 2 || !('value' in line)) throw new CredentialUnavailable();
              reply = line;
            }
          }
        } catch { unavailable(); }
      });
      child.once('close', code => {
        exited = true; clearTimeout(timer); signal?.removeEventListener('abort', stop);
        this.#children.delete(running);
        if (cancelled || this.#closing) { reject(new Cancelled()); return; }
        if (failed || code !== 0 || !hello || reply === undefined || buffer.length !== 0) { reject(new CredentialUnavailable()); return; }
        try {
          if (reply.value === null) {
            if (required) throw new CredentialUnavailable();
            resolve(null); return;
          }
          const encoded = text(reply.value);
          if (encoded.length > 5464 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(encoded)) throw new CredentialUnavailable();
          const value = Buffer.from(encoded, 'base64');
          if (value.length === 0 || value.length > 4096 || value.toString('base64') !== encoded) throw new CredentialUnavailable();
          resolve(new SecretBytes(value));
        } catch { reject(new CredentialUnavailable()); }
      });
    });
  }
  /** Stop all owned reads and await process reaping, including dropped Promises. */
  close(): Promise<void> {
    if (this.#closed !== undefined) return this.#closed;
    this.#closing = true;
    for (const child of this.#children) child.stop();
    this.#closed = (async () => { await Promise.allSettled(this.#tasks); })();
    return this.#closed;
  }
  toJSON(): string { return 'CredentialReader'; }
  [inspect.custom](): string { return 'CredentialReader'; }
}
