import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {inspect} from 'node:util';
import {CompositionV2Contract, PortableV2Contract, compositionV2ProfileDigest, portableV2ProfileDigest} from '../dist/portable.js';
const fixture = JSON.parse(readFileSync(new URL('../../../contracts/inference-v2-cases.json', import.meta.url), 'utf8'));

test('complete independent inference declaration and authority observations', () => {
  assert.equal(compositionV2ProfileDigest(), fixture.profile_digest);
  assert.equal(portableV2ProfileDigest(), fixture.author_profile_digest);
  for (const c of fixture.cases) {
    const evaluate = () => {
      if (c.action === 'author') {
        const value = PortableV2Contract.fromJson(c.name, c.source);
        return {canonical: Buffer.from(value.canonical()).toString('utf8'), digest: value.digest};
      }
      let value = CompositionV2Contract.fromJson(c.name, c.source);
      if (c.action === 'admit') value = CompositionV2Contract.fromJson('Context', c.parent).admit(
        value, CompositionV2Contract.fromJson('Policy', c.policy), c.executor,
        Buffer.from(c.request_hex, 'hex'), c.now_unix_ms, c.scope_id,
      );
      return {canonical: Buffer.from(value.canonical()).toString('utf8'), digest: null};
    };
    if (c.error) assert.throws(evaluate, error => error.message.startsWith(c.error + ':') && !error.message.includes('synthetic-do-not-echo-secret'), c.id);
    else assert.deepEqual(evaluate(), {canonical: c.canonical, digest: c.digest}, c.id);
  }
});

test('sensitive inference metadata stays frozen and out of incidental inspection', () => {
  const record = CompositionV2Contract.fromJson('Context', fixture.cases[0].source);
  assert.equal(JSON.stringify(record), '{"name":"Context"}');
  assert.equal(inspect(record), 'CompositionV2Contract(Context)');
  const bytes = record.canonical(); bytes.fill(0);
  assert.equal(Buffer.from(record.canonical()).toString('utf8'), fixture.cases[0].canonical);
  assert.throws(() => { record.name = 'Policy'; });
});
