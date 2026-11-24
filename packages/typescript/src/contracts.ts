import {native, canonicalValue} from './native.js';
import {copyJson, object, parseJson, text, utf8, type JsonObject, type JsonValue} from './json.js';

/** Explicit shared I/O modes; null lifetime remains uncapped inference. */
export type Mode = 'unary' | 'server_streaming' | 'client_streaming' | 'bidirectional';
/** Media directions are owned by the compiled transport, not application framing. */
export type StreamMode = Exclude<Mode, 'unary'>;
/** Metadata about the host-admitted original caller; exporting grants no rights. */
export class CallContext {
  readonly #canonical: string;
  constructor(source: string) {
    this.#canonical = utf8(native.compositionV3ContractJson('Context', source));
    Object.freeze(this);
  }
  /** Copy already admitted caller metadata without exposing its internal storage. */
  export(): JsonObject { return object(parseJson(this.#canonical)); }
  toJSON(): string { return 'CallContext'; }
}

/** Exact discovery/startup endpoint metadata; not an independently usable grant. */
export class CapabilityEndpoint {
  readonly #descriptor: string;
  readonly nodeId: string;
  readonly capabilityId: string;
  readonly version: string;
  readonly descriptorRevision: string;
  constructor(nodeId: string, descriptor: JsonObject) {
    this.#descriptor = utf8(native.portableV2ContractJson('Descriptor', utf8(canonicalValue(descriptor))));
    const admitted = admittedDescriptor(this.#descriptor);
    this.nodeId = nodeId;
    this.capabilityId = text(admitted.id);
    this.version = text(admitted.version);
    this.descriptorRevision = native.portableV2ContractDigest('Descriptor', this.#descriptor);
    Object.freeze(this);
  }
  /** Return an owned copy of the exact normalized descriptor. */
  descriptor(): JsonObject { return admittedDescriptor(this.#descriptor); }
  /** Return independent canonical bytes for exact target binding. */
  canonicalDescriptor(): Uint8Array { return Buffer.from(this.#descriptor); }
}

function admittedDescriptor(canonical: string): JsonObject {
  // Compiled portable/2 already validated these exact private bytes. Descriptor
  // records have a larger bound than the separate 64 KiB invocation payload.
  return object(JSON.parse(canonical) as JsonValue);
}

/** One native-admitted input packet, with binary media kept separate from JSON. */
export class InputFrame {
  readonly #projection: JsonObject;
  readonly #media: Uint8Array;
  constructor(projection: JsonObject, media: Uint8Array) {
    this.#projection = copyJson(projection); this.#media = Uint8Array.from(media); Object.freeze(this);
  }
  get projection(): JsonObject { return copyJson(this.#projection); }
  get payload(): JsonValue { return copyJson(this.#projection.payload ?? null); }
  get media(): Uint8Array { return Uint8Array.from(this.#media); }
}

/** A provider output; terminals are withheld until iterator/resource cleanup ends. */
export class OutputFrame {
  readonly kind: 'chunk' | 'completed' | 'failed' | 'cancelled';
  readonly #payload: JsonValue;
  readonly #media: Uint8Array;
  readonly #metadata: JsonObject | null;
  readonly error: string | null;
  constructor(kind: OutputFrame['kind'], payload: JsonValue = null,
      options: {readonly media?: Uint8Array; readonly mediaMetadata?: JsonObject; readonly error?: string} = {}) {
    this.kind = kind; this.#payload = copyJson(payload);
    this.#media = Uint8Array.from(options.media ?? []);
    this.#metadata = options.mediaMetadata === undefined ? null : copyJson(options.mediaMetadata);
    this.error = options.error ?? null; Object.freeze(this);
  }
  get payload(): JsonValue { return copyJson(this.#payload); }
  get media(): Uint8Array { return Uint8Array.from(this.#media); }
  get mediaMetadata(): JsonObject | null { return this.#metadata === null ? null : copyJson(this.#metadata); }
}
