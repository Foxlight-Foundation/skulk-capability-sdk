import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';

const require = createRequire(import.meta.url);
const native = require('../dist/loader.cjs');

test('schema JSON text is never narrowed through JavaScript numbers', () => {
  const schema = '{"minimum":9007199254740993}';
  assert.equal(native.schemaMatches(schema, '9007199254740993'), true);
  assert.equal(native.schemaMatches(schema, '9007199254740992'), false);
  assert.equal(native.schemaMatches('{"multipleOf":0.1}', '0.3'), false);
});

test('schema strings preserve surrogates and Python anchor behavior', () => {
  assert.equal(native.schemaMatches('{"type":"string"}', '"\\ud800"'), true);
  assert.equal(native.schemaMatches('{"const":"\\ud800"}', '"\\udb80\\udc00"'), false);
  assert.equal(native.schemaMatches('{"pattern":"^a$"}', '"a\\n"'), true);
});

test('unavailable references cannot turn negation into acceptance', () => {
  assert.equal(native.schemaMatches('{"not":{"$ref":"https://example.invalid/schema"}}', '1'), false);
  assert.throws(() => native.schemaMatches('secret malformed schema', '1'),
    {code: 'InvalidArg', message: 'invalid payload schema'});
});

test('legacy composed and schema-valued coverage preserves Python acceptance', () => {
  const draft = '"$schema":"https://json-schema.org/draft/2019-09/schema"';
  assert.equal(native.schemaMatches(`{${draft},"allOf":[{"additionalProperties":{"type":"integer"}}],"unevaluatedProperties":false}`, '{"x":1}'), false);
  assert.equal(native.schemaMatches(`{${draft},"unevaluatedProperties":{"type":"integer"}}`, '{"type":"s"}'), true);
});

test('legacy static resources retain coverage across the Node ABI', () => {
  const draft = '"$schema":"https://json-schema.org/draft/2019-09/schema"';
  assert.equal(native.schemaMatches(`{${draft},"$defs":{"v":{"additionalProperties":{"type":"integer"}}},"$ref":"#/$defs/v","unevaluatedProperties":false}`, '{"x":1}'), false);
  assert.equal(native.schemaMatches(`{${draft},"$defs":{"v":{"unevaluatedProperties":{"type":"integer"}}},"$ref":"#/$defs/v","unevaluatedProperties":false}`, '{"type":"s"}'), true);
});

test('recursive coverage and negated cycles retain producer acceptance', () => {
  const draft = '"$schema":"https://json-schema.org/draft/2019-09/schema"';
  const schema = `{${draft},"$recursiveAnchor":true,"properties":{"child":{"$recursiveRef":"#"}},"unevaluatedProperties":{"type":"integer"}}`;
  assert.equal(native.schemaMatches(schema, '{"child":{"type":"s"}}'), true);
  assert.equal(native.schemaMatches(schema, '{"child":{"x":"s"}}'), false);
  assert.equal(native.schemaMatches(`{${draft},"not":{"$ref":"#"},"unevaluatedProperties":false}`, '{}'), false);
});

test('installed owner root dialect remains separate from generic selection', () => {
  const schema = '{"$schema":"http://json-schema.org/draft-07/schema#","definitions":{"a":{"type":"object"}},"$ref":"#/definitions/a","type":"integer"}';
  assert.equal(native.schemaMatches(schema, '{}'), true);
  assert.equal(native.schemaMatchesInstalled(schema, '{}'), false);
  assert.throws(() => native.schemaMatchesInstalled('{"$schema":1}', '{}'),
    {code: 'InvalidArg', message: 'invalid payload schema'});
});

test('installed metadata budget never widens the payload envelope', () => {
  const schema = JSON.stringify({description: 'x'.repeat(70000)});
  assert.throws(() => native.schemaMatches(schema, '{}'),
    {code: 'InvalidArg', message: 'invalid payload schema'});
  assert.equal(native.schemaMatchesInstalled(schema, JSON.stringify({x: 'a'.repeat(63000)})), true);
  assert.equal(native.schemaMatchesInstalled(schema, JSON.stringify({x: 'a'.repeat(66000)})), false);
});
