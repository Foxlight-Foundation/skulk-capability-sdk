import test from 'node:test';
import assert from 'node:assert/strict';
import {validateContract} from '../dist/codec.js';
import native from '../dist/loader.cjs';

test('shared constructor authority preserves exact JSON values and serialization refusal', () => {
  const page = validateContract('OperationPage', {active:0, operations:[]});
  assert.equal(page.name, 'OperationPage');
  assert.deepEqual(JSON.parse(page.canonical().toString()), {active:0,next_offset:null,operations:[],outstanding:0});
  const record = validateContract('OperationCall', {op:'start',input:{text:'\ud800',integer:2n**128n}});
  assert.match(record.canonical().toString(), /\\ud800/);
  assert.match(record.canonical().toString(), /340282366920938463463374607431768211456/);
  const nonsendable = validateContract('OperationCall', {op:'start', input:{x:NaN}});
  assert.throws(() => nonsendable.canonical(), /serialized/);
});

test('raw media nests in native records and uses binary framing', () => {
  const media = validateContract('InlineMedia', {data:new Uint8Array([255,0,128]),media_type:'audio/test'});
  assert.throws(() => media.canonical(), /serialized/);
  const frame = validateContract('StreamPacket', {call_id:'c',direction:'provider_to_caller',sequence:1,kind:'chunk',media});
  const packet = native.decodeStreamPacket(frame.streamPacket());
  assert.deepEqual(packet.media, Buffer.from([255,0,128]));
  assert.equal(Object.hasOwn(JSON.parse(packet.projection).media,'data'), false);
});

test('constructor conversion accounts for aggregate input before serialization', () => {
  assert.throws(() => validateContract('OperationCall', {
    op:'start', input:{x:'x'.repeat(4_194_305)},
  }), /constructor exceeds bound/);
  assert.throws(() => validateContract('OperationCall', {
    op:'start', input:{a:'x'.repeat(2_100_000),b:'x'.repeat(2_100_000)},
  }), /constructor exceeds bound/);
});

test('media conversion uses intrinsic length and never invokes a supplied iterator', () => {
  const data = new Uint8Array([255,0,128]);
  Object.defineProperty(data, 'byteLength', {value:0});
  data[Symbol.iterator] = () => { throw new Error('caller iterator was invoked'); };
  const media = validateContract('InlineMedia', {data,media_type:'audio/test'});
  const frame = validateContract('StreamPacket', {call_id:'c',direction:'provider_to_caller',sequence:1,kind:'chunk',media});
  assert.deepEqual(native.decodeStreamPacket(frame.streamPacket()).media, Buffer.from([255,0,128]));
  const oversized = new Uint8Array(1_048_577);
  Object.defineProperty(oversized, 'byteLength', {value:0});
  oversized[Symbol.iterator] = () => { throw new Error('caller iterator was invoked'); };
  assert.throws(() => validateContract('InlineMedia', {data:oversized,media_type:'audio/test'}), /media exceeds bound/);
  const proxy = new Proxy(new Uint8Array([1]),{});
  assert.throws(() => validateContract('InlineMedia', {data:proxy,media_type:'audio/test'}), TypeError);
});

test('constructor conversion skips caller accessors and overridden native methods', () => {
  const data = [];
  Object.defineProperty(data, 0, {get(){throw new Error('caller getter was invoked');}, enumerable:true});
  assert.throws(() => validateContract('OperationCall', {op:'start',input:{x:data}}), /accessors are not supported/);
  const media = validateContract('InlineMedia', {data:new Uint8Array([255]),media_type:'audio/test'});
  media.constructorInput = () => {throw new Error('caller method was invoked');};
  const frame = validateContract('StreamPacket', {call_id:'c',direction:'provider_to_caller',sequence:1,kind:'chunk',media});
  assert.deepEqual(native.decodeStreamPacket(frame.streamPacket()).media, Buffer.from([255]));
});
