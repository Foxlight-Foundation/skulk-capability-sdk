import {isIP} from 'node:net';
import {request as httpRequest, type IncomingMessage, type ClientRequest} from 'node:http';
import {request as httpsRequest} from 'node:https';
import {native} from './native.js';
import {CapabilityEndpoint, type Mode} from './contracts.js';
import {array, object, text, utf8, type JsonObject, type JsonValue} from './json.js';

/** Decoded discovery metadata bound, independent of inference output limits. */
export const MAXIMUM_DIRECTORY_BYTES = 8 * 1024 * 1024;
/** Maximum observations accepted atomically from a single directory response. */
export const MAXIMUM_DIRECTORY_OFFERS = 4096;
/** Sanitized service failures never retain response content or credentials. */
export type DiscoveryErrorCode = 'invalid_directory' | 'limit' | 'denied' | 'unsupported' | 'unavailable' | 'timeout' | 'cancelled';
type Domain = 'local' | 'private_site' | 'external';

/** A bounded public error code without a raw HTTP error or response body. */
export class DiscoveryError extends Error {
  constructor(readonly code: DiscoveryErrorCode) { super(`capability discovery ${code}`); }
}
function invalid(): never { throw new DiscoveryError('invalid_directory'); }
function keys(value: JsonObject, expected: readonly string[]): void {
  const observed = Object.keys(value).sort();
  if (observed.length !== expected.length || observed.some((key, i) => key !== [...expected].sort()[i])) invalid();
}
function identifier(value: JsonValue | undefined): string {
  const result = text(value);
  if (result.length < 1 || result.length > 128 || /[^A-Za-z0-9._:@-]/.test(result)) invalid();
  return result;
}
function digest(value: JsonValue | undefined, length: number): string {
  const result = text(value);
  if (result.length !== length || /[^a-f0-9]/.test(result)) invalid();
  return result;
}
function seconds(value: JsonValue | undefined): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) invalid();
  return value;
}
function domain(value: JsonValue | undefined): Domain {
  if (value !== 'local' && value !== 'private_site' && value !== 'external') invalid();
  return value;
}
function mode(value: JsonValue | undefined): Mode {
  if (value !== 'unary' && value !== 'server_streaming' && value !== 'client_streaming' && value !== 'bidirectional') invalid();
  return value;
}

/** An immutable observed generation and exact descriptor; it grants no authority. */
export class CapabilityOffer {
  readonly pluginId: string;
  readonly runtimeDigest: string;
  readonly incarnation: string;
  readonly endpoint: CapabilityEndpoint;
  readonly behavior: 'read' | 'inference';
  readonly processingDomain: Domain;
  readonly ioMode: Mode;
  readonly streamSeconds: number;
  readonly cleanupSeconds: number;
  readonly startupSeconds: number;
  readonly inferenceSeconds: number | null;
  constructor(value: JsonObject) {
    keys(value, ['plugin_id', 'runtime_digest', 'incarnation', 'target', 'descriptor', 'stream_seconds', 'cleanup_seconds', 'startup_seconds', 'inference_seconds']);
    const target = object(value.target);
    keys(target, ['node_id', 'capability_id', 'version', 'descriptor_revision', 'behavior', 'processing_domain']);
    this.pluginId = identifier(value.plugin_id);
    this.runtimeDigest = digest(value.runtime_digest, 64);
    this.incarnation = digest(value.incarnation, 32);
    this.endpoint = new CapabilityEndpoint(identifier(target.node_id), object(value.descriptor));
    const descriptor = this.endpoint.descriptor();
    this.behavior = target.behavior === 'read' || target.behavior === 'inference' ? target.behavior : invalid();
    this.processingDomain = domain(target.processing_domain);
    this.ioMode = mode(descriptor.io_mode);
    if (identifier(target.capability_id) !== this.endpoint.capabilityId || text(target.version) !== this.endpoint.version
        || target.descriptor_revision !== this.endpoint.descriptorRevision
        || descriptor.behavior !== this.behavior || descriptor.processing_domain !== this.processingDomain) invalid();
    digest(target.descriptor_revision, 64);
    this.streamSeconds = seconds(value.stream_seconds);
    this.cleanupSeconds = seconds(value.cleanup_seconds);
    this.startupSeconds = seconds(value.startup_seconds);
    this.inferenceSeconds = value.inference_seconds === null ? null : seconds(value.inference_seconds);
    Object.freeze(this);
  }
}

/** A complete fresh observation, independent of current dispatch authorization. */
export class CapabilityDirectory {
  private constructor(readonly offers: readonly CapabilityOffer[]) { Object.freeze(this); }
  /** Admit the complete response or refuse; malformed entries never become a partial directory. */
  static fromJson(source: Uint8Array): CapabilityDirectory {
    if (source.byteLength > MAXIMUM_DIRECTORY_BYTES) throw new DiscoveryError('limit');
    if (source[0] === 0xef && source[1] === 0xbb && source[2] === 0xbf) throw new DiscoveryError('invalid_directory');
    try {
      // The lossless codec rejects duplicate keys before JSON.parse. Every
      // descriptor separately uses the issued portable/2 contract reader.
      const value: JsonValue = JSON.parse(utf8(native.canonicalJson(utf8(source)))) as JsonValue;
      const values = array(value);
      if (values.length > MAXIMUM_DIRECTORY_OFFERS) throw new DiscoveryError('limit');
      const offers = values.map(value => new CapabilityOffer(object(value)));
      const identities = new Set<string>();
      for (const offer of offers) {
        const identity = JSON.stringify([offer.pluginId, offer.endpoint.nodeId, offer.endpoint.capabilityId, offer.endpoint.version]);
        if (identities.has(identity)) invalid();
        identities.add(identity);
      }
      return new CapabilityDirectory(Object.freeze(offers));
    } catch (error) {
      if (error instanceof DiscoveryError) throw error;
      throw new DiscoveryError('invalid_directory');
    }
  }
  /** Select exact IDs/version; ambiguous destinations require an explicit node ID. */
  select(pluginId: string, capabilityId: string, version: string, nodeId?: string): CapabilityOffer {
    const matches = this.offers.filter(offer => offer.pluginId === pluginId
      && offer.endpoint.capabilityId === capabilityId && offer.endpoint.version === version
      && (nodeId === undefined || offer.endpoint.nodeId === nodeId));
    if (matches.length !== 1 || matches[0] === undefined) throw new TypeError('Discovery requires exactly one matching destination');
    return matches[0];
  }
}

function directoryUrl(baseUrl: string): string {
  const match = /^(https?):\/\/(\[[A-Za-z0-9:.]+\]|[A-Za-z0-9.-]+)(?::([0-9]{1,5}))?\/?$/.exec(baseUrl);
  if (match === null || match[0] !== baseUrl) throw new TypeError('Discovery requires a root HTTP(S) origin');
  const [, scheme, rawHost, rawPort] = match;
  if (rawHost === undefined || scheme === undefined) throw new TypeError('Invalid discovery origin');
  if (rawPort !== undefined && (Number(rawPort) < 1 || Number(rawPort) > 65535)) throw new TypeError('Invalid discovery port');
  const host = rawHost.replace(/^\[|\]$/g, '');
  const ip = isIP(host);
  if (ip === 0 && (rawHost.startsWith('[') || host.length > 253
      || host.replace(/\.$/, '').split('.').some(part => !/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/.test(part)))) {
    throw new TypeError('Invalid discovery host');
  }
  const loopback = ip === 4 ? host.startsWith('127.') : ip === 6 && new URL(`http://[${host}]`).hostname === '[::1]';
  if (scheme === 'http' && !loopback) throw new TypeError('Remote discovery requires HTTPS');
  return baseUrl.replace(/\/$/, '') + '/v1/native-inference';
}

/** Bounds one metadata read; these settings never impose an inference lifetime. */
export interface DiscoveryOptions {
  readonly timeoutSeconds?: number;
  readonly signal?: AbortSignal;
  /** Optional trusted cluster PEM CA; chain and hostname checks stay enabled. */
  readonly certificateAuthority?: string;
}

/** Read caller-authorized offers once, closing response media before returning.
 * Supply an enrolled Skulk client bearer, never a provider credential. Remote
 * origins require HTTPS. Redirects, retries and implicit authorization are absent.
 */
export async function discoverCapabilities(baseUrl: string, bearer: string, options: DiscoveryOptions = {}): Promise<CapabilityDirectory> {
  const url = directoryUrl(baseUrl);
  if (typeof bearer !== 'string' || bearer.length < 1 || bearer.length > 4096 || /[^\x21-\x7e]/.test(bearer)) throw new TypeError('Invalid discovery bearer');
  const timeout = options.timeoutSeconds ?? 30;
  if (!Number.isFinite(timeout) || timeout <= 0 || timeout > 86400) throw new TypeError('Invalid discovery metadata timeout');
  const authority = options.certificateAuthority;
  if (authority !== undefined && (typeof authority !== 'string' || authority.length === 0
      || Buffer.byteLength(authority) > 1048576 || /[^\x00-\x7f]/.test(authority))) throw new TypeError('Invalid discovery certificate authority');
  const controller = new AbortController();
  const cancel = (): void => controller.abort(new DiscoveryError('cancelled'));
  options.signal?.addEventListener('abort', cancel, {once: true});
  if (options.signal?.aborted) cancel();
  const timer = setTimeout(() => controller.abort(new DiscoveryError('timeout')), timeout * 1000);
  timer.unref();
  let request: ClientRequest | undefined;
  let response: IncomingMessage | undefined;
  let closed: Promise<void> | undefined;
  try {
    // A one-off direct agent avoids a process-global fetch dispatcher, proxy
    // environment and idle pooled connection retaining this credentialed read.
    const makeRequest = url.startsWith('https:') ? httpsRequest : httpRequest;
    request = makeRequest(url, {...(authority === undefined ? {} : {ca: authority}),
      method: 'GET', agent: false, signal: controller.signal,
      headers: {authorization: `Bearer ${bearer}`, 'accept-encoding': 'identity'}});
    const pending = request;
    closed = new Promise(resolve => pending.once('close', resolve));
    response = await new Promise<IncomingMessage>((resolve, reject) => {
      pending.once('response', reply => {
        // Status/header refusals close before an iterator owns body errors.
        // An active iterator still observes and reports any read failure.
        reply.on('error', () => {});
        resolve(reply);
      });
      pending.on('error', reject);
      pending.end();
    });
    if (response.statusCode === 401 || response.statusCode === 403) throw new DiscoveryError('denied');
    if (response.statusCode === 404) throw new DiscoveryError('unsupported');
    if (response.statusCode !== 200) throw new DiscoveryError('unavailable');
    if (response.headers['content-type']?.split(';')[0]?.trim().toLowerCase() !== 'application/json') invalid();
    if (response.headers['content-encoding'] !== undefined && response.headers['content-encoding'] !== 'identity') invalid();
    const chunks: Uint8Array[] = [];
    let length = 0;
    for await (const value of response) {
      const chunk: unknown = value;
      if (!Buffer.isBuffer(chunk)) invalid();
      length += chunk.byteLength;
      if (length > MAXIMUM_DIRECTORY_BYTES) throw new DiscoveryError('limit');
      chunks.push(chunk);
    }
    return CapabilityDirectory.fromJson(Buffer.concat(chunks));
  } catch (error) {
    if (controller.signal.aborted) throw controller.signal.reason instanceof DiscoveryError ? controller.signal.reason : new DiscoveryError('cancelled');
    if (error instanceof DiscoveryError) throw error;
    throw new DiscoveryError('unavailable');
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener('abort', cancel);
    controller.abort();
    response?.destroy();
    request?.destroy();
    await closed;
  }
}
