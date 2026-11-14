/** SDK 1.0 candidate facade. The kernel owns normalization and validation. */
import native from './loader.cjs';
import {Buffer} from 'node:buffer';
import {inspect} from 'node:util';

/** Explicit profile ID; its normative fingerprint is supplied by the kernel. */
export const PORTABLE_PROFILE = 'skulk.portable/1' as const;
/** Legacy protocol-three/four readers must refuse this new envelope. */
export const PORTABLE_PROTOCOL = 5 as const;
/** The contract root participates in its domain-separated identity. */
export type PortableContractName = 'Descriptor' | 'ContractEnvelope';

/** Return the fingerprint to bind into author contracts and negotiation. */
export function portableProfileDigest(): string { return native.portableProfileDigest(); }

/** Canonicalize exact JSON text; the returned bytes have no retained ownership. */
export function canonicalPortableJson(source: string): Uint8Array {
  return native.portableCanonicalJson(source);
}

/** Immutable admitted contract. Reading its bytes cannot mutate its identity. */
export class PortableContract {
  readonly #canonical: Uint8Array;
  readonly digest: string;
  readonly name: PortableContractName;

  private constructor(name: PortableContractName, source: string) {
    this.name = name;
    this.#canonical = native.portableContractJson(name, source);
    this.digest = native.portableContractDigest(name, source);
    Object.freeze(this);
  }

  /** Validate profile, defaults, I/O modes, schemas and envelope negotiation. */
  static fromJson(name: PortableContractName, source: string): PortableContract {
    return new PortableContract(name, source);
  }

  /** Return a copy so callers cannot modify the admitted canonical bytes. */
  canonical(): Uint8Array { return Uint8Array.from(this.#canonical); }
}

/** Native-owned compiled schema; no JavaScript regex or validator fallback. */
export class PortableSchema {
  readonly #compiled: InstanceType<typeof native.PortableSchema>;

  /** Eagerly refuse unsupported constructs and unsafe resource expansion. */
  constructor(source: string) {
    this.#compiled = new native.PortableSchema(source);
    Object.freeze(this);
  }

  /** Return a normal match/mismatch; malformed or over-budget JSON throws. */
  matches(source: string): boolean { return this.#compiled.matches(source); }
}

/** Managed records have a separate fingerprint; portable descriptor identity stays frozen. */
export const MANAGED_PROFILE = 'skulk.managed/1' as const;
/** Managed record roots participate in the full identity domain. */
export type ManagedContractName = 'Manifest' | 'Startup';
/** Return the exact managed installation/startup profile fingerprint. */
export function managedProfileDigest(): string { return native.managedProfileDigest(); }

/** Immutable native-admitted managed record. Startup bytes may contain secrets.
 * Keep startup records on private inherited IPC; never log or publish them.
 * Admission validates structure, not signatures, trust or execution authority.
 */
export class ManagedContract {
  readonly #canonical: Uint8Array;
  readonly digest: string;
  readonly name: ManagedContractName;

  private constructor(name: ManagedContractName, source: string) {
    this.name = name;
    this.#canonical = native.managedContractJson(name, source);
    this.digest = native.managedContractDigest(name, source);
    Object.freeze(this);
  }

  /** Validate defaults, full identity binding, configuration and startup constraints. */
  static fromJson(name: ManagedContractName, source: string): ManagedContract {
    return new ManagedContract(name, source);
  }

  /** Derive the explicit hello for this startup and an optional admitted call. */
  hello(callId?: string): HandshakeContract {
    const bytes = native.managedHelloJson(this.name, Buffer.from(this.#canonical).toString('utf8'), callId);
    return HandshakeContract.fromJson(callId === undefined ? 'Hello' : 'StreamHello', bytes.toString('utf8'));
  }

  /** Bind an incoming hello using the shared native implementation. No I/O occurs. */
  admitHello(source: string, callId?: string): HandshakeContract {
    const bytes = native.managedAdmitHelloJson(this.name, Buffer.from(this.#canonical).toString('utf8'), source, callId);
    return HandshakeContract.fromJson(callId === undefined ? 'Hello' : 'StreamHello', bytes.toString('utf8'));
  }

  /** Avoid implicit diagnostic serialization of startup content or its identity. */
  toJSON(): {name: ManagedContractName} { return {name: this.name}; }
  /** Keep routine Node inspection free of credential-bearing values. */
  [inspect.custom](): string { return `ManagedContract(${this.name})`; }

  /** Copy bytes without permitting mutation of the native-admitted identity. */
  canonical(): Uint8Array { return Uint8Array.from(this.#canonical); }
}


/** Protocol-five handshakes have an independent immutable fingerprint. */
export const HANDSHAKE_PROFILE = 'skulk.handshake/1' as const;
/** Closed handshake kinds, separate from legacy control/media transports. */
export type HandshakeContractName = 'Hello' | 'StreamHello';
/** Return the native normative handshake fingerprint. */
export function handshakeProfileDigest(): string { return native.handshakeProfileDigest(); }

/** Immutable credential-bearing handshake; parsing is distinct from binding. */
export class HandshakeContract {
  readonly #canonical: Uint8Array;
  readonly name: HandshakeContractName;

  private constructor(name: HandshakeContractName, source: string) {
    this.name = name;
    this.#canonical = native.handshakeContractJson(name, source);
    Object.freeze(this);
  }

  /** Validate the explicit profile/kind/protocol; startup admission is separate. */
  static fromJson(name: HandshakeContractName, source: string): HandshakeContract {
    return new HandshakeContract(name, source);
  }

  /** Explicitly copy secret-bearing bytes for protected IPC. */
  canonical(): Uint8Array { return Uint8Array.from(this.#canonical); }
  /** Serialize only the public kind during incidental diagnostic encoding. */
  toJSON(): {name: HandshakeContractName} { return {name: this.name}; }
  /** Native token and identity fields never appear in routine Node inspection. */
  [inspect.custom](): string { return `HandshakeContract(${this.name})`; }
}

/** Managed records have a separate fingerprint; portable descriptor identity stays frozen. */
export const MANAGED_V2_PROFILE = 'skulk.managed/2' as const;
/** Return the exact managed installation/startup profile fingerprint. */
export function managedV2ProfileDigest(): string { return native.managedV2ProfileDigest(); }

/** Immutable native-admitted managed record. Startup bytes may contain secrets.
 * Keep startup records on private inherited IPC; never log or publish them.
 * Admission validates structure, not signatures, trust or execution authority.
 */
export class ManagedV2Contract {
  readonly #canonical: Uint8Array;
  readonly digest: string;
  readonly name: ManagedContractName;

  private constructor(name: ManagedContractName, source: string) {
    this.name = name;
    this.#canonical = native.managedV2ContractJson(name, source);
    this.digest = native.managedV2ContractDigest(name, source);
    Object.freeze(this);
  }

  /** Validate defaults, full identity binding, configuration and startup constraints. */
  static fromJson(name: ManagedContractName, source: string): ManagedV2Contract {
    return new ManagedV2Contract(name, source);
  }

  /** Derive the explicit hello for this startup and an optional admitted call. */
  hello(callId?: string): HandshakeV2Contract {
    const bytes = native.managedV2HelloJson(this.name, Buffer.from(this.#canonical).toString('utf8'), callId);
    return HandshakeV2Contract.fromJson(callId === undefined ? 'Hello' : 'StreamHello', bytes.toString('utf8'));
  }

  /** Bind an incoming hello using the shared native implementation. No I/O occurs. */
  admitHello(source: string, callId?: string): HandshakeV2Contract {
    const bytes = native.managedV2AdmitHelloJson(this.name, Buffer.from(this.#canonical).toString('utf8'), source, callId);
    return HandshakeV2Contract.fromJson(callId === undefined ? 'Hello' : 'StreamHello', bytes.toString('utf8'));
  }

  /** Avoid implicit diagnostic serialization of startup content or its identity. */
  toJSON(): {name: ManagedContractName} { return {name: this.name}; }
  /** Keep routine Node inspection free of credential-bearing values. */
  [inspect.custom](): string { return `ManagedV2Contract(${this.name})`; }

  /** Copy bytes without permitting mutation of the native-admitted identity. */
  canonical(): Uint8Array { return Uint8Array.from(this.#canonical); }
}


/** Protocol-six handshakes have an independent immutable fingerprint. */
export const HANDSHAKE_V2_PROFILE = 'skulk.handshake/2' as const;
/** Return the native normative handshake fingerprint. */
export function handshakeV2ProfileDigest(): string { return native.handshakeV2ProfileDigest(); }

/** Immutable credential-bearing handshake; parsing is distinct from binding. */
export class HandshakeV2Contract {
  readonly #canonical: Uint8Array;
  readonly name: HandshakeContractName;

  private constructor(name: HandshakeContractName, source: string) {
    this.name = name;
    this.#canonical = native.handshakeV2ContractJson(name, source);
    Object.freeze(this);
  }

  /** Validate the explicit profile/kind/protocol; startup admission is separate. */
  static fromJson(name: HandshakeContractName, source: string): HandshakeV2Contract {
    return new HandshakeV2Contract(name, source);
  }

  /** Explicitly copy secret-bearing bytes for protected IPC. */
  canonical(): Uint8Array { return Uint8Array.from(this.#canonical); }
  /** Serialize only the public kind during incidental diagnostic encoding. */
  toJSON(): {name: HandshakeContractName} { return {name: this.name}; }
  /** Native token and identity fields never appear in routine Node inspection. */
  [inspect.custom](): string { return `HandshakeV2Contract(${this.name})`; }
}

/** Pure composition profile; independent of issued transport/startup contracts. */
export const COMPOSITION_PROFILE = 'skulk.composition/1' as const;
/** Closed composition metadata kinds; parsing never establishes provenance. */
export type CompositionContractName = 'Context' | 'DelegateRequest' | 'Policy';
/** Return the complete pure-attenuation fingerprint. */
export function compositionProfileDigest(): string { return native.compositionProfileDigest(); }
/** Immutable sensitive caller metadata. Hosts own active scope and replay state. */
export class CompositionContract {
  readonly #canonical: Uint8Array;
  readonly name: CompositionContractName;
  private constructor(name: CompositionContractName, source: string) {
    this.name = name;
    this.#canonical = native.compositionContractJson(name, source);
    Object.freeze(this);
  }
  /** Structural validation alone grants no execution rights. */
  static fromJson(name: CompositionContractName, source: string): CompositionContract {
    return new CompositionContract(name, source);
  }
  /** Explicit copy for protected IPC; no caller metadata in incidental encoding. */
  canonical(): Uint8Array { return Uint8Array.from(this.#canonical); }
  toJSON(): {name: CompositionContractName} { return {name: this.name}; }
  [inspect.custom](): string { return `CompositionContract(${this.name})`; }
  /** Pure attenuation against host observations; does not reserve or execute. */
  admit(request: CompositionContract, policy: CompositionContract, executor: string,
        requestBytes: Uint8Array, nowUnixMs: number, scopeId: string): CompositionContract {
    const source = native.admitDelegationJson(
      Buffer.from(this.#canonical).toString('utf8'),
      Buffer.from(request.#canonical).toString('utf8'),
      Buffer.from(policy.#canonical).toString('utf8'), executor,
      Buffer.from(requestBytes), nowUnixMs, scopeId,
    );
    return CompositionContract.fromJson('Context', Buffer.from(source).toString('utf8'));
  }
}

/** Explicit wire profile, separately fingerprinted from descriptors and startup. */
export const TRANSPORT_PROFILE = 'skulk.transport/1' as const;
/** Return the complete native protocol-five wire fingerprint. */
export function transportProfileDigest(): string { return native.transportProfileDigest(); }
/** Admit and frame closed native protocol-five control text. */
export function encodePortableControl(source: string): Uint8Array { return native.encodePortableControl(source); }
/** Return a canonical copy after complete control framing validation. */
export function decodePortableControl(bytes: Uint8Array): Uint8Array { return native.decodePortableControl(Buffer.from(bytes)); }
/** Frame portable scalar JSON and a separate bounded raw attachment. */
export function encodePortablePacket(projection: string, media: Uint8Array): Uint8Array { return native.encodePortablePacket(projection, Buffer.from(media)); }
/** Return exact media and native canonical metadata without a JS validator. */
export function decodePortablePacket(bytes: Uint8Array): {projection: string; media: Uint8Array} { return native.decodePortablePacket(Buffer.from(bytes)); }
/** Canonical projection and separately owned raw binary bytes. */
export interface PortablePacket {readonly projection: string; readonly media: Uint8Array;}
/** The handler owns iterator cleanup and closes the native session from finally. */
export interface PortableStream {
  nextInput(): Promise<PortablePacket | null>;
  sendOutput(projection: string, media: Uint8Array): Promise<void>;
  finish(): Promise<void>;
  close(): Promise<void>;
}
/** Cancellation or invalid/partial I/O fences every later control operation. */
export interface PortableControl {
  receive(): Promise<Uint8Array>;
  send(source: string): Promise<void>;
  close(): Promise<void>;
}
/** Open the exact startup endpoint and derive its native-bound hello. The host
 * separately owns current trust, original caller and one-use call admission. */
export async function openPortableStream(startup: ManagedContract, callId: string, mode: 'server_streaming' | 'client_streaming' | 'bidirectional', seconds: number): Promise<PortableStream> {
  if (startup.name !== 'Startup') throw new TypeError('startup record required');
  const session = await native.openPortableStream(Buffer.from(startup.canonical()).toString('utf8'), callId, mode, seconds);
  return Object.freeze({
    nextInput: () => session.nextInput(),
    sendOutput: (projection: string, media: Uint8Array) => session.sendOutput(projection, Buffer.from(media)),
    finish: () => session.finish(),
    close: () => session.close(),
  });
}
/** Native channel owns deadlines and uncertain I/O; close it from finally. */
export function openPortableControl(startup: ManagedContract, seconds: number): Promise<PortableControl> {
  if (startup.name !== 'Startup') throw new TypeError('startup record required');
  return native.openPortableControl(Buffer.from(startup.canonical()).toString('utf8'), seconds);
}

/** Independently versioned composed-call framing, separate from existing media. */
export const CALLS_PROFILE = 'skulk.calls/1' as const;
/** Complete native fingerprint; validation alone grants no invocation authority. */
export function callsProfileDigest(): string { return native.callsProfileDigest(); }
/** Admit metadata before body allocation; this is not complete frame admission. */
export function callPayloadSize(source: string): number { return native.callPayloadSize(source); }
/** Separate canonical metadata and exact raw body; no implicit content encoding. */
export interface CallFrame {readonly metadata: Uint8Array; readonly payload: Uint8Array;}
/** Native admission binds request bytes without a JavaScript validator fallback. */
export function encodeCallFrame(source: string, payload: Uint8Array): Uint8Array {
  return native.encodeCallFrame(source, Buffer.from(payload));
}
/** Reject malformed/trailing frames and return defensive copies of their bytes. */
export function decodeCallFrame(packet: Uint8Array): CallFrame {
  return native.decodeCallFrame(Buffer.from(packet));
}
/** Native channel owns backpressure, partial-I/O poisoning and timer cleanup. */
export interface CallControl {
  receive(): Promise<CallFrame>;
  send(source: string, payload: Uint8Array): Promise<void>;
  close(): Promise<void>;
}
/** Select this profile explicitly from an admitted runtime; never autodetect it. */
export async function openCallControl(startup: ManagedContract, seconds: number): Promise<CallControl> {
  if (startup.name !== 'Startup') throw new TypeError('startup record required');
  const channel = await native.openCallControl(Buffer.from(startup.canonical()).toString('utf8'), seconds);
  return Object.freeze({
    receive: () => channel.receive(),
    send: (source: string, payload: Uint8Array) => channel.send(source, Buffer.from(payload)),
    close: () => channel.close(),
  });
}

/** Explicit inference-declaration profile; not installed execution authority. */
export const PORTABLE_V2_PROFILE = 'skulk.portable/2' as const;
/** Fingerprint inference declarations and their unchanged closed schema language. */
export function portableV2ProfileDigest(): string { return native.portableV2ProfileDigest(); }
/** Immutable admitted contract. Reading its bytes cannot mutate its identity. */
export class PortableV2Contract {
  readonly #canonical: Uint8Array;
  readonly digest: string;
  readonly name: PortableContractName;

  private constructor(name: PortableContractName, source: string) {
    this.name = name;
    this.#canonical = native.portableV2ContractJson(name, source);
    this.digest = native.portableV2ContractDigest(name, source);
    Object.freeze(this);
  }

  /** Validate profile, defaults, I/O modes, schemas and envelope negotiation. */
  static fromJson(name: PortableContractName, source: string): PortableV2Contract {
    return new PortableV2Contract(name, source);
  }

  /** Return a copy so callers cannot modify the admitted canonical bytes. */
  canonical(): Uint8Array { return Uint8Array.from(this.#canonical); }
}


/** Read/inference attenuation has a separate negotiated identity. */
export const COMPOSITION_V2_PROFILE = 'skulk.composition/2' as const;
/** Fingerprint inference authority rules; a parsed record is never a credential. */
export function compositionV2ProfileDigest(): string { return native.compositionV2ProfileDigest(); }
/** Immutable sensitive caller metadata. Hosts own active scope and replay state. */
export class CompositionV2Contract {
  readonly #canonical: Uint8Array;
  readonly name: CompositionContractName;
  private constructor(name: CompositionContractName, source: string) {
    this.name = name;
    this.#canonical = native.compositionV2ContractJson(name, source);
    Object.freeze(this);
  }
  /** Structural validation alone grants no execution rights. */
  static fromJson(name: CompositionContractName, source: string): CompositionV2Contract {
    return new CompositionV2Contract(name, source);
  }
  /** Explicit copy for protected IPC; no caller metadata in incidental encoding. */
  canonical(): Uint8Array { return Uint8Array.from(this.#canonical); }
  toJSON(): {name: CompositionContractName} { return {name: this.name}; }
  [inspect.custom](): string { return `CompositionV2Contract(${this.name})`; }
  /** Pure attenuation against host observations; does not reserve or execute. */
  admit(request: CompositionV2Contract, policy: CompositionV2Contract, executor: string,
        requestBytes: Uint8Array, nowUnixMs: number, scopeId: string): CompositionV2Contract {
    const source = native.admitDelegationV2Json(
      Buffer.from(this.#canonical).toString('utf8'),
      Buffer.from(request.#canonical).toString('utf8'),
      Buffer.from(policy.#canonical).toString('utf8'), executor,
      Buffer.from(requestBytes), nowUnixMs, scopeId,
    );
    return CompositionV2Contract.fromJson('Context', Buffer.from(source).toString('utf8'));
  }
}

/** Explicit nullable authority deadlines have a separate negotiated identity. */
export const COMPOSITION_V3_PROFILE = 'skulk.composition/3' as const;
/** Fingerprint inference authority rules; a parsed record is never a credential. */
export function compositionV3ProfileDigest(): string { return native.compositionV3ProfileDigest(); }
/** Immutable sensitive caller metadata. Hosts own active scope and replay state. */
export class CompositionV3Contract {
  readonly #canonical: Uint8Array;
  readonly name: CompositionContractName;
  private constructor(name: CompositionContractName, source: string) {
    this.name = name;
    this.#canonical = native.compositionV3ContractJson(name, source);
    Object.freeze(this);
  }
  /** Structural validation alone grants no execution rights. */
  static fromJson(name: CompositionContractName, source: string): CompositionV3Contract {
    return new CompositionV3Contract(name, source);
  }
  /** Explicit copy for protected IPC; no caller metadata in incidental encoding. */
  canonical(): Uint8Array { return Uint8Array.from(this.#canonical); }
  toJSON(): {name: CompositionContractName} { return {name: this.name}; }
  [inspect.custom](): string { return `CompositionV3Contract(${this.name})`; }
  /** Pure attenuation against host observations; does not reserve or execute. */
  admit(request: CompositionV3Contract, policy: CompositionV3Contract, executor: string,
        requestBytes: Uint8Array, nowUnixMs: number, scopeId: string): CompositionV3Contract {
    const source = native.admitDelegationV3Json(
      Buffer.from(this.#canonical).toString('utf8'),
      Buffer.from(request.#canonical).toString('utf8'),
      Buffer.from(policy.#canonical).toString('utf8'), executor,
      Buffer.from(requestBytes), nowUnixMs, scopeId,
    );
    return CompositionV3Contract.fromJson('Context', Buffer.from(source).toString('utf8'));
  }
}

/** Explicit wire profile, separately fingerprinted from descriptors and startup. */
export const TRANSPORT_V2_PROFILE = 'skulk.transport/2' as const;
/** Return the complete native protocol-six wire fingerprint. */
export function transportV2ProfileDigest(): string { return native.transportV2ProfileDigest(); }
/** Admit and frame closed native protocol-six control text. */
export function encodeTransportV2Control(source: string): Uint8Array { return native.encodeTransportV2Control(source); }
/** Return a canonical copy after complete control framing validation. */
export function decodeTransportV2Control(bytes: Uint8Array): Uint8Array { return native.decodeTransportV2Control(Buffer.from(bytes)); }
/** Frame portable scalar JSON and a separate bounded raw attachment. */
export function encodeTransportV2Packet(projection: string, media: Uint8Array): Uint8Array { return native.encodeTransportV2Packet(projection, Buffer.from(media)); }
/** Return exact media and native canonical metadata without a JS validator. */
export function decodeTransportV2Packet(bytes: Uint8Array): {projection: string; media: Uint8Array} { return native.decodeTransportV2Packet(Buffer.from(bytes)); }
/** Open the exact startup endpoint and derive its native-bound hello. The host
 * separately owns current trust, original caller and one-use call admission. */
export async function openTransportV2Stream(startup: ManagedV2Contract, callId: string, mode: 'server_streaming' | 'client_streaming' | 'bidirectional', seconds: number | null = null): Promise<PortableStream> {
  if (startup.name !== 'Startup') throw new TypeError('startup record required');
  const session = await native.openTransportV2Stream(Buffer.from(startup.canonical()).toString('utf8'), callId, mode, seconds);
  return Object.freeze({
    nextInput: () => session.nextInput(),
    sendOutput: (projection: string, media: Uint8Array) => session.sendOutput(projection, Buffer.from(media)),
    finish: () => session.finish(),
    close: () => session.close(),
  });
}
/** Native channel owns deadlines and uncertain I/O; close it from finally. */
export function openTransportV2Control(startup: ManagedV2Contract, seconds: number): Promise<PortableControl> {
  if (startup.name !== 'Startup') throw new TypeError('startup record required');
  return native.openTransportV2Control(Buffer.from(startup.canonical()).toString('utf8'), seconds);
}


/** Independently versioned composed-call framing, separate from existing media. */
export const CALLS_V2_PROFILE = 'skulk.calls/2' as const;
/** Complete native fingerprint; validation alone grants no invocation authority. */
export function callsV2ProfileDigest(): string { return native.callsV2ProfileDigest(); }
/** Admit metadata before body allocation; this is not complete frame admission. */
export function callV2PayloadSize(source: string): number { return native.callV2PayloadSize(source); }
/** Native admission binds request bytes without a JavaScript validator fallback. */
export function encodeCallV2Frame(source: string, payload: Uint8Array): Uint8Array {
  return native.encodeCallV2Frame(source, Buffer.from(payload));
}
/** Reject malformed/trailing frames and return defensive copies of their bytes. */
export function decodeCallV2Frame(packet: Uint8Array): CallFrame {
  return native.decodeCallV2Frame(Buffer.from(packet));
}
/** Select this profile explicitly from an admitted runtime; never autodetect it. */
export async function openCallV2Control(startup: ManagedV2Contract, seconds: number): Promise<CallControl> {
  if (startup.name !== 'Startup') throw new TypeError('startup record required');
  const channel = await native.openCallV2Control(Buffer.from(startup.canonical()).toString('utf8'), seconds);
  return Object.freeze({
    receive: () => channel.receive(),
    send: (source: string, payload: Uint8Array) => channel.send(source, Buffer.from(payload)),
    close: () => channel.close(),
  });
}

/** Validate a header before allocating media; still admit the complete packet. */
export function transportV2MediaSize(header: Uint8Array): number {
  return native.transportV2MediaSize(Buffer.from(header));
}

/** Pure protocol-six host observation. This object does not authenticate callers,
 * consume startup grants, dispatch work, or replace the required control result. */
export class TransportV2Observer {
  readonly #observer: InstanceType<typeof native.TransportV2Observer>;
  /** Bind one actual call identity and a declared non-unary mode. */
  constructor(callId: string, mode: 'server_streaming' | 'client_streaming' | 'bidirectional') {
    this.#observer = new native.TransportV2Observer(callId, mode);
    Object.freeze(this);
  }
  /** Admit caller frames and half-close using the shared compiled lifecycle. */
  acceptInput(packet: Uint8Array): {projection: string; media: Uint8Array} {
    return this.#observer.acceptInput(Buffer.from(packet));
  }
  /** Admit provider frames and exactly one matching terminal/end pair. */
  acceptOutput(packet: Uint8Array): {projection: string; media: Uint8Array} {
    return this.#observer.acceptOutput(Buffer.from(packet));
  }
  /** Observe a successful caller half-close or a server-only input direction. */
  get inputCompleted(): boolean { return this.#observer.inputCompleted; }
  /** A terminal alone never proves cleanup or control completion. */
  get outputTerminated(): boolean { return this.#observer.outputTerminated; }
  /** Require the exact terminal/end pair; host authority stays separate. */
  get finished(): boolean { return this.#observer.finished; }
  /** Any invalid packet permanently fences this observer. */
  get failed(): boolean { return this.#observer.failed; }
  /** Avoid implicit diagnostic serialization of call identity or frame content. */
  toJSON(): {kind: string} { return {kind: 'TransportV2Observer'}; }
  /** Keep routine inspection free of call identities and payloads. */
  [inspect.custom](): string { return 'TransportV2Observer()'; }
}
