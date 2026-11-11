import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {inspect} from 'node:util';
import {createHash} from 'node:crypto';
import {CompositionContract, compositionProfileDigest} from '../dist/portable.js';
const fixture = JSON.parse(readFileSync(new URL('../../../contracts/composition-v1-cases.json', import.meta.url), 'utf8'));

test('complete independent composition observations', () => {
  assert.equal(compositionProfileDigest(), fixture.profile_digest);
  for (const c of fixture.cases) {
    const evaluate = () => {
      let record = CompositionContract.fromJson(c.name, c.source);
      if (c.action === 'admit') {
        record = CompositionContract.fromJson('Context', c.parent).admit(
          record, CompositionContract.fromJson('Policy', c.policy), c.executor,
          Buffer.from(c.request_hex, 'hex'), c.now_unix_ms, c.scope_id,
        );
      }
      return Buffer.from(record.canonical()).toString('utf8');
    };
    if (c.error) assert.throws(evaluate, error => error.message.includes(c.error) && !error.message.includes('synthetic-do-not-echo-secret'), c.id);
    else assert.equal(evaluate(), c.canonical, c.id);
  }
});
test('sensitive caller metadata stays immutable and out of inspection', () => {
  const record = CompositionContract.fromJson('Context', fixture.cases[0].source);
  assert.equal(JSON.stringify(record), '{"name":"Context"}');
  assert.equal(inspect(record), 'CompositionContract(Context)');
  const bytes = record.canonical(); bytes.fill(0);
  assert.equal(Buffer.from(record.canonical()).toString('utf8'), fixture.cases[0].canonical);
  assert.throws(() => { record.name = 'Policy'; });
});
test('actual request bytes respect the exact admission resource boundary', () => {
  const c = fixture.cases.find(c => c.id === 'exact-delegation');
  const payload = Buffer.alloc(16_777_216, 'x');
  const fields = JSON.parse(c.source);
  fields.request_sha256 = createHash('sha256').update(payload).digest('hex');
  const request = CompositionContract.fromJson('DelegateRequest', JSON.stringify(fields));
  const parent = CompositionContract.fromJson('Context', c.parent);
  const policy = CompositionContract.fromJson('Policy', c.policy);
  const child = parent.admit(request, policy, c.executor, payload, c.now_unix_ms, c.scope_id);
  assert.equal(JSON.parse(Buffer.from(child.canonical()).toString('utf8')).request_sha256, fields.request_sha256);
  assert.throws(() => parent.admit(request, policy, c.executor, Buffer.concat([payload, Buffer.from('x')]), c.now_unix_ms, c.scope_id), /composition_limit:/);
});
