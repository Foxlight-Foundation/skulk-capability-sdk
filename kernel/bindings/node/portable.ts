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
