import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {PortableContract, PortableSchema, canonicalPortableJson, portableProfileDigest} from '../dist/portable.js';

const fixture = JSON.parse(readFileSync(new URL('../../../contracts/portable-v1-cases.json', import.meta.url), 'utf8'));

test('candidate TypeScript facade delegates new contract/identity to installed addon', () => {
  assert.equal(portableProfileDigest(), fixture.profile_digest);
  for (const row of fixture.codec) {
    if (row.error) assert.throws(() => canonicalPortableJson(row.source), {message: new RegExp(`^${row.error}:`)});
    else assert.equal(Buffer.from(canonicalPortableJson(row.source)).toString('ascii'), row.canonical);
  }
  for (const row of fixture.contracts) {
    if (row.error) assert.throws(() => PortableContract.fromJson(row.name, row.source), {message: new RegExp(`^${row.error}:`)});
    else {
      const record = PortableContract.fromJson(row.name, row.source);
      assert.equal(Buffer.from(record.canonical()).toString('ascii'), row.canonical);
      assert.equal(record.digest, row.digest);
      const mutable = record.canonical(); mutable.fill(0);
      assert.equal(Buffer.from(record.canonical()).toString('ascii'), row.canonical);
      assert.equal(Object.isFrozen(record), true);
    }
  }
});

test('compiled candidate schema retains no per-call state or JavaScript fallback', () => {
  const schema = new PortableSchema('{"type":"integer","minimum":2}');
  for (let repeat=0; repeat<20; repeat++) {
    assert.equal(schema.matches('2.0'), true);
    assert.equal(schema.matches('true'), false);
    assert.equal(schema.matches('1'), false);
  }
  assert.throws(() => schema.matches('9007199254740992'), {message: /^portable_number:/});
  assert.equal(schema.matches('3'), true);
  assert.throws(() => new PortableSchema('{"pattern":"(?=synthetic-secret)"}'), error => !error.message.includes('synthetic-secret'));
});

test('raw JavaScript UTF-16 is validated before UTF-8 conversion can substitute it', () => {
  for (const source of ['"\ud800"', '{"\udfff":1}']) {
    assert.throws(() => canonicalPortableJson(source), {message: /^portable_unicode:/});
    assert.throws(() => new PortableSchema('true').matches(source), {message: /^portable_unicode:/});
  }
});
