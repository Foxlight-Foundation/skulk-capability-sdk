/** SDK 1.0 candidate facade. The kernel owns normalization and validation. */
import native from './loader.cjs';

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
