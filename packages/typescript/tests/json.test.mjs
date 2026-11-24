import assert from 'node:assert/strict';
import test from 'node:test';
import {encodeJson, parseJson} from '../dist/json.js';

test('portable conversion refuses lossy JavaScript values before stringify', () => {
  for (const value of [NaN, Infinity, undefined, 9007199254740992, '\ud800', [,,], {get hidden() {throw Error('getter ran');}}]) {
    assert.throws(() => encodeJson({value}));
  }
  const cyclic = {}; cyclic.self = cyclic;
  assert.throws(() => encodeJson(cyclic));
  assert.equal(encodeJson({value: 1}).toString(), encodeJson({value: 1.0}).toString());
  let called = false;
  assert.throws(() => encodeJson({get hidden() {called = true; return 'secret';}}));
  assert.equal(called, false);
});
test('portable JSON copies cannot mutate the source', () => {
  const original = {one: {two: [1]}};
  const copy = parseJson(encodeJson(original).toString());
  copy.one.two[0] = 2;
  assert.deepEqual(original, {one: {two: [1]}});
});
