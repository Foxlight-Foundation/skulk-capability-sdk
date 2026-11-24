import {once} from 'node:events';
import {createConnection} from 'node:net';
import {performance} from 'node:perf_hooks';
import {native} from './native.js';
import {encodeJson, utf8, type JsonObject} from './json.js';
import {CapabilityDirectory, DiscoveryError, MAXIMUM_DIRECTORY_BYTES} from './discovery.js';

/** Own one private parent-bound metadata exchange, without bearer substitution. */
export async function queryInvocation(startup: string, context: JsonObject, parent: string,
    sequence: bigint, expiry: number | null, signal: AbortSignal,
    check: () => void): Promise<CapabilityDirectory> {
  if (typeof native.discoveryRequest !== 'function') throw new DiscoveryError('unsupported');
  const packet = native.discoveryRequest(startup, utf8(encodeJson(context)), parent, String(sequence));
  const request = utf8(native.decodeDiscoveryFrame(packet).metadata);
  const duration = Math.max(0, Math.min(30_000, expiry === null ? 30_000 : expiry - performance.now()));
  const timer = AbortSignal.timeout(Math.ceil(duration));
  const combined = AbortSignal.any([signal, timer]);
  check();
  const socket = createConnection({path: native.discoverySocket(startup), signal: combined});
  try {
    await once(socket, 'connect', {signal: combined});
    check(); socket.end(packet);
    const chunks: Buffer[] = []; let size = 0;
    for await (const part of socket) {
      if (!Buffer.isBuffer(part)) throw new DiscoveryError('invalid_directory');
      size += part.length;
      if (size > MAXIMUM_DIRECTORY_BYTES + 4100) throw new DiscoveryError('limit');
      chunks.push(Buffer.from(part));
    }
    check();
    const bytes = Buffer.concat(chunks);
    let response: ReturnType<typeof native.discoveryResponse>;
    try { response = native.discoveryResponse(startup, request, bytes); }
    catch { throw new DiscoveryError('invalid_directory'); }
    if (response.error != null) {
      if (response.error !== 'denied' && response.error !== 'unavailable' && response.error !== 'limit') {
        throw new DiscoveryError('invalid_directory');
      }
      throw new DiscoveryError(response.error);
    }
    const result = CapabilityDirectory.fromJson(response.payload);
    check(); return result;
  } catch (error) {
    if (error instanceof DiscoveryError) throw error;
    if (timer.aborted) throw new DiscoveryError('timeout');
    if (signal.aborted) throw new DiscoveryError('cancelled');
    if (error instanceof Error && 'code' in error && error.code === 'ENOENT') throw new DiscoveryError('unsupported');
    throw new DiscoveryError('unavailable');
  } finally {
    // A closed query cannot leave an unowned socket or metadata read behind.
    await new Promise<void>(resolve => {
      if (socket.closed) { resolve(); return; }
      socket.once('close', () => resolve()); socket.destroy();
    });
  }
}
