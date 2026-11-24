import {createHash} from 'node:crypto';
import {inspect} from 'node:util';
import type {Invocation} from './application.js';
import type {CallReply, CallOptions} from './calls.js';
import {CapabilityEndpoint, type Mode} from './contracts.js';
import {native, canonicalValue} from './native.js';
import {encodeJson, object, parseJson, text, utf8, type JsonObject} from './json.js';

/** The declaration's effect; proposal metadata never authorizes execution. */
export type CapabilityBehavior = 'read' | 'inference' | 'proposal' | 'execute';
/** Declaration only: processing permission is independently admitted by the host. */
export type CapabilityDomain = 'local' | 'private_site' | 'external';
/** Sanitized interface refusals contain neither arguments nor caller metadata. */
export type InterfaceErrorCode = 'invalid_descriptor' | 'invalid_payload' | 'unavailable' | 'not_read' | 'unsupported_mode';
/** An interface failure without retaining the failed declaration or arguments. */
export class CapabilityInterfaceError extends Error {
  constructor(readonly code: InterfaceErrorCode) { super(`capability interface ${code}`); }
}

/** An owned native declaration, not current permission or signed custody. */
export class CapabilityDescription {
  private constructor(readonly endpoint: CapabilityEndpoint) { Object.freeze(this); }
  /** Read an exact declaration without contacting a host or claiming authority. */
  static fromEndpoint(destination: CapabilityEndpoint): CapabilityDescription {
    try {
      if (typeof destination.nodeId !== 'string'
          || /^[A-Za-z0-9._:@-]{1,128}$/.exec(destination.nodeId)?.[0] !== destination.nodeId) throw new TypeError('Invalid identifier');
      return new CapabilityDescription(new CapabilityEndpoint(destination.nodeId, destination.descriptor()));
    } catch { throw new CapabilityInterfaceError('invalid_descriptor'); }
  }
  /** Copy the complete already native-validated declaration. */
  descriptor(): JsonObject { return this.endpoint.descriptor(); }
  /** Preserve the native effect classification without reinterpreting text. */
  get behavior(): CapabilityBehavior {
    const value = this.descriptor().behavior;
    if (value === 'read' || value === 'inference' || value === 'proposal' || value === 'execute') return value;
    throw new CapabilityInterfaceError('invalid_descriptor');
  }
  /** Preserve the complete signed I/O mode. */
  get ioMode(): Mode {
    const value = this.descriptor().io_mode;
    if (value === 'unary' || value === 'server_streaming' || value === 'client_streaming' || value === 'bidirectional') return value;
    throw new CapabilityInterfaceError('invalid_descriptor');
  }
  /** Describe a processing boundary without claiming caller consent. */
  get processingDomain(): CapabilityDomain {
    const value = this.descriptor().processing_domain;
    if (value === 'local' || value === 'private_site' || value === 'external') return value;
    throw new CapabilityInterfaceError('invalid_descriptor');
  }
  /** Descriptive tool text is never used as an authority classification. */
  get summary(): string { return text(this.descriptor().description); }
  toJSON(): string { return 'CapabilityDescription'; }
  [inspect.custom](): string { return 'CapabilityDescription'; }
}

/** Immutable review data with no caller credential, approval or execute method. */
export class CapabilityProposal {
  readonly #canonical: Buffer;
  readonly #request: Buffer;
  private constructor(canonical: Uint8Array, request: Uint8Array) {
    this.#canonical = Buffer.from(canonical); this.#request = Buffer.from(request); Object.freeze(this);
  }
  /** Pure construction: validate and copy an inert unary/server-output JSON request.
   * Input-media streams require more than a JSON object and are refused. A governed
   * declaration can be represented here without adding any governed dispatch path. */
  static prepare(destination: CapabilityEndpoint, payload: JsonObject): CapabilityProposal {
    const description = CapabilityDescription.fromEndpoint(destination);
    if (description.ioMode !== 'unary' && description.ioMode !== 'server_streaming') throw new CapabilityInterfaceError('unsupported_mode');
    try {
      const request = encodeJson(payload), argumentsValue = object(parseJson(utf8(request)));
      const descriptor = description.descriptor();
      if (!new native.PortableSchema(utf8(canonicalValue(descriptor.input_schema))).matches(utf8(request))) throw new TypeError('Invalid input');
      const endpoint = description.endpoint;
      const intent: JsonObject = {format: 1, kind: 'capability_request_proposal',
        destination: {node_id: endpoint.nodeId, capability_id: endpoint.capabilityId, version: endpoint.version,
          descriptor_revision: endpoint.descriptorRevision}, descriptor, payload: argumentsValue,
        request_sha256: createHash('sha256').update(request).digest('hex')};
      // This is an explicit SDK data export, not an issued approval/execution
      // contract. The same native codec constructs both language projections.
      return new CapabilityProposal(canonicalValue(intent), request);
    } catch { throw new CapabilityInterfaceError('invalid_payload'); }
  }
  /** Explicitly export exact intent bytes; they grant no authority. */
  canonical(): Uint8Array { return Buffer.from(this.#canonical); }
  /** Copy arguments without exposing internal mutable storage. */
  get payload(): JsonObject { return object(parseJson(utf8(this.#request))); }
  /** Explicit export of exact canonical request bytes bound by this intent. */
  get requestBytes(): Uint8Array { return Buffer.from(this.#request); }
  /** Exact comparison identity, never an approval credential. */
  get digest(): string { return createHash('sha256').update(this.#canonical).digest('hex'); }
  toJSON(): string { return 'CapabilityProposal'; }
  [inspect.custom](): string { return 'CapabilityProposal'; }
}

/** Common interfaces retaining exactly one invocation's original caller. */
export class CapabilityClient {
  readonly #invocation: Invocation;
  constructor(invocation: Invocation) { this.#invocation = invocation; Object.freeze(this); }
  /** Freshly observe one exact permitted descriptor; never cache authority. */
  async describe(destination: CapabilityEndpoint): Promise<CapabilityDescription> {
    const expected = CapabilityDescription.fromEndpoint(destination).endpoint;
    const directory = await this.#invocation.discover();
    const matches = directory.offers.filter(offer => offer.endpoint.nodeId === expected.nodeId
      && Buffer.from(offer.endpoint.canonicalDescriptor()).equals(Buffer.from(expected.canonicalDescriptor())));
    if (matches.length !== 1 || matches[0] === undefined) throw new CapabilityInterfaceError('unavailable');
    return CapabilityDescription.fromEndpoint(matches[0].endpoint);
  }
  /** Perform a unary read with original-caller native dispatch and fresh host checks.
   * Inference/proposal/execute declarations refuse before transport. Existing
   * cancellation, revocation and uncertainty custody remains with the invocation.
   * Configured reads do not require the optional discovery channel. */
  async read(destination: CapabilityEndpoint, payload: JsonObject | Uint8Array, options: CallOptions = {}): Promise<CallReply> {
    const description = CapabilityDescription.fromEndpoint(destination);
    if (description.behavior !== 'read') throw new CapabilityInterfaceError('not_read');
    if (description.ioMode !== 'unary') throw new CapabilityInterfaceError('unsupported_mode');
    return this.#invocation.call(description.endpoint, payload, options);
  }
  /** Observe current caller permission, then construct data without dispatch.
   * No child-effect sequence is consumed. Unsupported governed destinations stay
   * refused; no service identity or approval can replace the original caller. */
  async propose(destination: CapabilityEndpoint, payload: JsonObject): Promise<CapabilityProposal> {
    const current = await this.describe(destination);
    return CapabilityProposal.prepare(current.endpoint, payload);
  }
  toJSON(): string { return 'CapabilityClient'; }
  [inspect.custom](): string { return 'CapabilityClient'; }
}
