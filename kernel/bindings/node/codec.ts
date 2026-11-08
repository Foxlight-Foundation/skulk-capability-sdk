/** A readable conversion facade. Serialization, validation and hashes are native.
 * JavaScript loses the spelling distinction between 1 and 1.0 on JSON.parse.
 * Use jsonFloat(1) when a schema or an existing Python identity requires 1.0;
 * bigint carries arbitrary exact integers. Ordinary safe integer Numbers map
 * to integers, all other finite Numbers (including -0) map to floats.
 */
import native from './loader.cjs';
const {canonicalTagged, inputDigestTagged} = native;

/** Preserve a floating-point kind even when its numeric value is integral. */
export class JsonFloat {
  constructor(readonly value: number) {
    if (!Number.isFinite(value)) throw new TypeError('non-finite JSON value');
    Object.freeze(this);
  }
}
/** Declare the float kind explicitly when interoperating with Python values. */
export function jsonFloat(value: number): JsonFloat { return new JsonFloat(value); }
export type JsonValue = null | boolean | number | bigint | string | JsonFloat
  | readonly JsonValue[] | {readonly [key: string]: JsonValue};

export type ConstructorValue = null | boolean | number | bigint | string | JsonFloat
  | Uint8Array | ReturnType<typeof native.validateContractValue>
  | readonly ConstructorValue[] | {readonly [key: string]: ConstructorValue};

type Tagged = readonly ['null'] | readonly ['bool', boolean]
  | readonly ['integer', string] | readonly ['float', string]
  | readonly ['string', readonly number[]] | readonly ['array', readonly Tagged[]]
  | readonly ['bytes', string]
  | readonly ['object', readonly (readonly [readonly number[], Tagged])[]];

function points(value: string): number[] {
  return Array.from(value, character => character.codePointAt(0)!);
}
function float(value: number, constructor = false): Tagged {
  if (!constructor && !Number.isFinite(value)) throw new TypeError('non-finite JSON value');
  const bits = new DataView(new ArrayBuffer(8));
  bits.setFloat64(0, value, false);
  return ['float', bits.getBigUint64(0, false).toString(16).padStart(16, '0')];
}
const typedArrayLength: (this: Uint8Array) => number = Object.getOwnPropertyDescriptor(
  Object.getPrototypeOf(Uint8Array.prototype) as object, 'length',
)!.get! as (this: Uint8Array) => number;

const nativeConstructorInput: (this: ReturnType<typeof native.validateContractValue>) => string =
  native.NativeAuthorContract.prototype.constructorInput;

type Budget = {remaining: number};
function charge(budget: Budget | undefined, amount: number): void {
  if (!budget) return;
  if (amount > budget.remaining) throw new TypeError('constructor exceeds bound');
  budget.remaining -= amount;
}
function convert(value: JsonValue | ConstructorValue, active: Set<object>, depth: number, constructor = false, budget?: Budget): Tagged {
  if (depth > 256) throw new TypeError('JSON nesting exceeds the bound');
  charge(budget, 16);
  if (constructor && value instanceof native.NativeAuthorContract) {
    const encoded = nativeConstructorInput.call(value);
    charge(budget, encoded.length);
    return JSON.parse(encoded) as Tagged;
  }
  if (constructor && value instanceof Uint8Array) {
    const length = typedArrayLength.call(value);
    if (length > 1_048_576) throw new TypeError('media exceeds bound');
    charge(budget, length);
    const bytes: string[] = [];
    for (let index = 0; index < length; index++) {
      const byte = value[index];
      if (typeof byte !== 'number') throw new TypeError('media changed during conversion');
      bytes.push(byte.toString(16).padStart(2, '0'));
    }
    return ['bytes', bytes.join('')];
  }
  if (constructor && typeof value === 'string' && value.length > 4_194_304) throw new TypeError('constructor exceeds bound');
  if (value === null) return ['null'];
  if (typeof value === 'boolean') return ['bool', value];
  if (typeof value === 'string') { charge(budget, value.length * 4); return ['string', points(value)]; }
  if (typeof value === 'bigint') {
    if (budget && (value >> BigInt(budget.remaining * 3)) !== 0n && (value >> BigInt(budget.remaining * 3)) !== -1n) {
      throw new TypeError('constructor exceeds bound');
    }
    const digits = value.toString(); charge(budget, digits.length); return ['integer', digits];
  }
  if (typeof value === 'number') {
    return Number.isSafeInteger(value) && !Object.is(value, -0)
      ? ['integer', value.toString()] : float(value, constructor);
  }
  if (value instanceof JsonFloat) return float(value.value, constructor);
  if (typeof value !== 'object') throw new TypeError('unsupported JSON value');
  if (active.has(value)) throw new TypeError('cyclic JSON value');
  active.add(value);
  try {
    if (Array.isArray(value)) {
      // map skips holes, which JSON.stringify would silently turn into nulls.
      const items: Tagged[] = [];
      for (let index = 0; index < value.length; index++) {
        if (!Object.hasOwn(value, index)) throw new TypeError('sparse JSON array');
        const descriptor = Object.getOwnPropertyDescriptor(value, index)!;
        if (!Object.hasOwn(descriptor, 'value')) throw new TypeError('JSON accessors are not supported');
        items.push(convert(descriptor.value as JsonValue | ConstructorValue, active, depth + 1, constructor, budget));
      }
      return ['array', items];
    }
    const prototype: unknown = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) {
      throw new TypeError('JSON objects must be plain records');
    }
    if (Object.getOwnPropertySymbols(value).length !== 0) throw new TypeError('JSON keys must be strings');
    const entries: [number[], Tagged][] = [];
    for (const key of Object.keys(value)) {
      const descriptor = Object.getOwnPropertyDescriptor(value, key)!;
      if (!Object.hasOwn(descriptor, 'value')) throw new TypeError('JSON accessors are not supported');
      charge(budget, key.length * 4);
      entries.push([points(key), convert(descriptor.value as JsonValue | ConstructorValue, active, depth + 1, constructor, budget)]);
    }
    return ['object', entries];
  } finally { active.delete(value); }
}
/** Canonical bytes are produced by the kernel, never by JSON.stringify here. */
export function canonical(value: JsonValue): Uint8Array {
  return canonicalTagged(JSON.stringify(convert(value, new Set(), 0)));
}
/** The same scope/input identity as the Python SDK, including lone surrogates. */
export function inputDigest(scope: string, value: {readonly [key: string]: JsonValue}): string {
  return inputDigestTagged(scope, JSON.stringify(convert(value, new Set(), 0)));
}

/** Native validation preserves constructor data until canonical or binary framing. */
export function validateContract(name: string, value: ConstructorValue): ReturnType<typeof native.validateContractValue> {
  const tagged = JSON.stringify(convert(value, new Set(), 0, true, {remaining: 16 * 1024 * 1024}));
  if (tagged.length > 16 * 1024 * 1024) throw new TypeError('constructor exceeds bound');
  return native.validateContractValue(name, tagged);
}
