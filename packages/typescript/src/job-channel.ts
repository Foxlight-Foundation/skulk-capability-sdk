import type {Readable, Writable} from 'node:stream';
import {canonicalValue, native} from './native.js';
import {object, utf8, type JsonObject, type JsonValue} from './json.js';

export const JOB_FRAME_BYTES = 262144;

/** Decode a bounded private envelope with the compiled duplicate-key reader.
 * Nested payloads are canonical strings already admitted by the public native
 * contracts. Their escaping can exceed the public 64-KiB scalar JSON ceiling.
 */
export function parseJobMessage(source: string): JsonObject {
  if (Buffer.byteLength(source) > JOB_FRAME_BYTES) throw new TypeError('Job frame exceeds its bound');
  const value: unknown = JSON.parse(utf8(native.canonicalJson(source)));
  return object(value as JsonValue);
}

/** Bounded private helper framing; the native reader still owns JSON policy. */
export async function* jobFrames(input: Readable): AsyncGenerator<JsonObject> {
  let buffered = Buffer.alloc(0);
  for await (const value of input) {
    if (!Buffer.isBuffer(value)) throw new TypeError('Job channel requires bytes');
    buffered = Buffer.concat([buffered, value]);
    while (buffered.length >= 4) {
      const size = buffered.readUInt32BE(0);
      if (size === 0 || size > JOB_FRAME_BYTES) throw new TypeError('Job frame exceeds its bound');
      if (buffered.length < size + 4) break;
      const body = buffered.subarray(4, size + 4);
      buffered = buffered.subarray(size + 4);
      yield parseJobMessage(utf8(body));
    }
    if (buffered.length > JOB_FRAME_BYTES + 4) throw new TypeError('Job channel exceeds its bound');
  }
  if (buffered.length !== 0) throw new TypeError('Job channel ended inside a frame');
}

/** Own one complete bounded write through its actual stream completion. */
export async function sendJobFrame(output: Writable, value: JsonObject): Promise<void> {
  const body = canonicalValue(value);
  if (body.length === 0 || body.length > JOB_FRAME_BYTES) throw new TypeError('Job frame exceeds its bound');
  const header = Buffer.alloc(4); header.writeUInt32BE(body.length);
  await new Promise<void>((resolve, reject) => output.write(Buffer.concat([header, body]), error => error ? reject(error) : resolve()));
}
