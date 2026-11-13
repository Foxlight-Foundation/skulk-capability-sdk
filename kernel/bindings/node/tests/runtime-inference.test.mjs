import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {inspect} from 'node:util';
import {ManagedContract, HandshakeContract, ManagedV2Contract, HandshakeV2Contract, managedV2ProfileDigest, handshakeV2ProfileDigest} from '../dist/portable.js';
const load = name => JSON.parse(readFileSync(new URL(`../../../contracts/${name}-v2-cases.json`, import.meta.url), 'utf8'));
const managed = load('managed'), handshake = load('handshake');

test('complete independent managed/2 and handshake/2 records and refusals', () => {
  assert.equal(managedV2ProfileDigest(), managed.profile_digest);
  assert.equal(handshakeV2ProfileDigest(), handshake.profile_digest);
  for (const c of [...managed.cases, ...handshake.cases]) {
    const evaluate = () => {
      let value;
      if (c.operation === 'managed_v2') value = ManagedV2Contract.fromJson(c.name, c.source);
      else if (c.action === 'parse') value = HandshakeV2Contract.fromJson(c.name, c.source);
      else {
        const startup = ManagedV2Contract.fromJson('Startup', c.startup);
        value = c.action === 'create' ? startup.hello(c.call_id ?? undefined) : startup.admitHello(c.source, c.call_id ?? undefined);
      }
      return {canonical: Buffer.from(value.canonical()).toString('utf8'), digest: c.operation === 'managed_v2' ? value.digest : null};
    };
    if (c.error) assert.throws(evaluate, e => e.message.startsWith(c.error + ':') && !e.message.includes('synthetic-do-not-echo-secret'), c.id);
    else assert.deepEqual(evaluate(), {canonical: c.canonical, digest: c.digest ?? null}, c.id);
  }
});

test('inference startup cannot leak through inspection or downgrade into legacy readers', () => {
  const startup = ManagedV2Contract.fromJson('Startup', handshake.cases[0].startup);
  const hello = startup.hello();
  assert.equal(inspect(startup), 'ManagedV2Contract(Startup)');
  assert.equal(inspect(hello), 'HandshakeV2Contract(Hello)');
  assert.equal(JSON.stringify(startup), '{"name":"Startup"}');
  assert.equal(JSON.stringify(hello), '{"name":"Hello"}');
  const bytes = startup.canonical(); bytes.fill(0);
  assert.notDeepEqual(startup.canonical(), bytes);
  assert.throws(() => { startup.name = 'Manifest'; });
  assert.throws(() => ManagedContract.fromJson('Startup', handshake.cases[0].startup), /^Error: portable_profile:/);
  assert.throws(() => HandshakeContract.fromJson('Hello', Buffer.from(hello.canonical()).toString()), /^Error: portable_profile:/);
  for (const [factory, name] of [[ManagedV2Contract, 'Startup'], [HandshakeV2Contract, 'Hello']]) {
    assert.throws(() => factory.fromJson(name, '\ud800'), /^Error: portable_unicode:/);
  }
});
