/** An acquired resource whose asynchronous cleanup remains owned after cancellation. */
export interface Resource { close(): Promise<void>; }

export class Resources implements Resource {
  readonly #resources: Resource[] = [];
  readonly #acquisitions = new Set<Promise<unknown>>();
  #closing = false;
  #closed: Promise<void> | undefined;
  add(resource: Resource): void {
    if (this.#closing) throw new Cancelled();
    this.#resources.push(resource);
  }
  enter<Value extends Resource>(acquire: () => Promise<Value>, signal: AbortSignal): Promise<Value> {
    if (this.#closing || signal.aborted) return Promise.reject(new Cancelled());
    const task = (async () => {
      const resource = await acquire();
      this.#resources.push(resource);
      if (this.#closing || signal.aborted) throw new Cancelled();
      return resource;
    })();
    this.#acquisitions.add(task);
    void task.then(() => this.#acquisitions.delete(task), () => this.#acquisitions.delete(task));
    return task;
  }
  close(): Promise<void> {
    if (this.#closed !== undefined) return this.#closed;
    this.#closing = true;
    this.#closed = (async () => {
      await Promise.allSettled(this.#acquisitions);
      let failed = false;
      for (let resource = this.#resources.pop(); resource !== undefined; resource = this.#resources.pop()) {
        try { await resource.close(); } catch { failed = true; }
      }
      if (failed) throw new Error('Application resource cleanup failed');
    })();
    return this.#closed;
  }
}
export class Cancelled extends Error { constructor() { super('cancelled'); } }
export class Expired extends Error { constructor() { super('timeout'); } }

export function check(signal: AbortSignal): void {
  if (signal.aborted) throw signal.reason instanceof Expired ? new Expired() : new Cancelled();
}
