import {TextDecoder} from 'node:util';
import {native, canonicalValue} from './native.js';

/** Portable JSON values. Unsupported numbers, Unicode and objects refuse natively. */
export type JsonValue = null | boolean | number | string | readonly JsonValue[] | JsonObject;
/** A portable object; ownership is copied at public API boundaries. */
export interface JsonObject {readonly [key: string]: JsonValue;}
const decoder = new TextDecoder('utf-8', {fatal: true});

/** Decode exact UTF-8 without silently replacing malformed request bytes. */
export function utf8(bytes: Uint8Array): string { return decoder.decode(bytes); }
/** Serialize language values through the issued bridge and portable native policy. */
export function encodeJson(value: JsonValue): Buffer {
  return native.portableCanonicalJson(utf8(canonicalValue(value)));
}
/** Parse native-admitted portable JSON, without a JavaScript validator fallback. */
export function parseJson(source: string): JsonValue {
  const value: unknown = JSON.parse(utf8(native.portableCanonicalJson(source)));
  return value as JsonValue;
}
/** Return an owned portable copy rather than exposing internal mutable metadata. */
export function copyJson<Value extends JsonValue>(value: Value): Value {
  return parseJson(utf8(encodeJson(value))) as Value;
}
export function object(value: JsonValue | undefined): JsonObject {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError('Expected an admitted JSON object');
  }
  return value as JsonObject;
}
export function text(value: JsonValue | undefined): string {
  if (typeof value !== 'string') throw new TypeError('Expected an admitted string');
  return value;
}
export function number(value: JsonValue | undefined): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new TypeError('Expected an admitted number');
  return value;
}
export function array(value: JsonValue | undefined): readonly JsonValue[] {
  if (!Array.isArray(value)) throw new TypeError('Expected an admitted array');
  return value;
}
