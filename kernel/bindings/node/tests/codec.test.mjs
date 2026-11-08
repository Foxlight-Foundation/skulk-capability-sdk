import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {test} from 'node:test';
import {canonical, inputDigest, jsonFloat} from '../dist/codec.js';
const native = createRequire(import.meta.url)('../dist/kernel.node');
const fixtures = new URL('../../../../tests/fixtures/author-baseline/', import.meta.url);
const text = bytes => Buffer.from(bytes).toString('ascii');
const payloads = [{number: 1}, {number: jsonFloat(1)}, {number: -0}, {number: 1e-7},
  {number: 1e20}, {number: 9007199254740993n}, {'𐀀': '🦊', '\ue000': 'é', nested: [null, true, '\n']}];

for (const version of ['0.4.0', '0.5.0']) {
  test(`frozen ${version} bytes and scope hashes through Node values`, () => {
    const reference = JSON.parse(readFileSync(new URL(`sdk-${version}.json`, fixtures), 'utf8'));
    reference.canonical_cases.forEach((item, index) => {
      const message = {...item.message, remaining_seconds: jsonFloat(item.message.remaining_seconds), payload: payloads[index]};
      const bytes = canonical(message);
      assert.equal(text(bytes), item.canonical_utf8);
      assert.equal(createHash('sha256').update(bytes).digest('hex'), item.sha256);
      assert.equal(text(native.canonicalJson(item.canonical_utf8)), item.canonical_utf8);
    });
    reference.operation_input_cases.forEach((item, index) => {
      assert.equal(inputDigest(item.scope, payloads[index]), item.sha256);
    });
    reference.manifest_cases.forEach(item => {
      const message = {...item.message, bounds: Object.fromEntries(Object.entries(item.message.bounds)
        .map(([key,value]) => [key, key.endsWith('_seconds') ? jsonFloat(value) : value]))};
      assert.equal(text(canonical(message)), item.canonical_utf8);
    });
  });
}

test('number kinds, Unicode points and refusal of lossy conversion', () => {
  assert.equal(text(canonical({integer: 1, float: jsonFloat(1), zero: -0, big: 2n**128n, text: '\ud800'})),
    '{"big":340282366920938463463374607431768211456,"float":1.0,"integer":1,"text":"\\ud800","zero":-0.0}');
  assert.equal(inputDigest('s', {text: '\ud800'}), '58a01e59583df2f8c03dd2ac0dc87f941666db49b9d8dc4b5abf53bc053c3017');
  assert.equal(inputDigest('\ud800', {number: jsonFloat(1)}),
    createHash('sha256').update('{"input":{"number":1.0},"scope":"\\ud800"}').digest('hex'));
  for (const number of [NaN, Infinity, -Infinity]) assert.throws(() => canonical(number), /non-finite/);
  for (const value of [undefined, new Date(), {value: undefined}, Array(1), {[Symbol('key')]: 1}]) {
    assert.throws(() => canonical(value));
  }
  const cyclic = []; cyclic.push(cyclic);
  assert.throws(() => canonical(cyclic), /cyclic/);
  assert.throws(() => canonical({get secret() {throw new Error('must not run')}}), /accessors/);
  assert.throws(() => native.canonicalJson('{"credential":1,"credential":2}'), /duplicate/);
  assert.throws(() => native.canonicalTagged('["integer","01"]'), /integer/);
  assert.throws(() => native.canonicalTagged('["object",[[[97],["null"]],[[97],["null"]]]]'), /duplicate/);
});
