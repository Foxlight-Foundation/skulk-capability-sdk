import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {test} from 'node:test';

const native = createRequire(import.meta.url)('../dist/kernel.node');
const fixture = new URL('../../../../tests/fixtures/author-baseline/sdk-0.5.0.json', import.meta.url);

test('native author validation retains independently frozen signed projections', () => {
  const reference = JSON.parse(readFileSync(fixture, 'utf8'));
  for (const [root, field] of [['WireMessage', 'canonical_cases'], ['Manifest', 'manifest_cases']]) {
    for (const item of reference[field]) {
      assert.equal(native.validateContractJson(root, item.canonical_utf8).toString('ascii'), item.canonical_utf8);
    }
  }
});

test('shared native types, defaults and semantic rules are identical for Node', () => {
  const text = native.validateContractJson('Bounds', '{"startup_seconds":1,"concurrency":true}').toString('ascii');
  assert.match(text, /"startup_seconds":1\.0/);
  assert.match(text, /"concurrency":1/);
  assert.doesNotMatch(text, /stream_seconds/);
  assert.throws(() => native.validateContractJson('Bounds', '{"max_children":true}'));
  assert.throws(() => native.validateContractJson('Bounds', '{"max_children":1.0}'));
  assert.throws(() => native.validateContractJson('OperationCall', '{"op":"status"}'));
  assert.throws(() => native.validateContractJson('OperationCall', '{"op":"list","offset":9007199254740993}'));
});
