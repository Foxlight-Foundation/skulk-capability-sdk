import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {inspect} from 'node:util';
import test from 'node:test';
import {HandshakeContract, ManagedContract, handshakeProfileDigest} from '../dist/portable.js';

const fixture = JSON.parse(readFileSync(new URL('../../../contracts/handshake-v1-cases.json', import.meta.url), 'utf8'));
function evaluate(c) {
  if (c.action === 'parse') return HandshakeContract.fromJson(c.name, c.source);
  const startup = ManagedContract.fromJson('Startup', c.startup);
  return c.action === 'create' ? startup.hello(c.call_id ?? undefined) : startup.admitHello(c.source, c.call_id ?? undefined);
}

test('native handles match all independent handshake/factory/binding observations', () => {
  assert.equal(handshakeProfileDigest(), fixture.profile_digest);
  for (const c of fixture.cases) {
    if (c.error) assert.throws(() => evaluate(c), e => {
      assert.equal(e.message.split(':', 1)[0], c.error, c.id);
      assert.ok(!e.message.includes('synthetic-do-not-echo-secret'));
      return true;
    });
    else assert.equal(Buffer.from(evaluate(c).canonical()).toString('ascii'), c.canonical, c.id);
  }
});

test('immutable startup/handshake handles copy bytes and keep diagnostics inert', () => {
  const c = fixture.cases.find(c => c.action === 'create' && !c.error);
  const startup = ManagedContract.fromJson('Startup', c.startup);
  const hello = startup.hello();
  for (const r of [startup, hello]) {
    const before = r.canonical();
    r.canonical().fill(0);
    assert.deepEqual(r.canonical(), before);
    assert.throws(() => { r.name = 'another'; }, TypeError);
    assert.ok(!inspect(r).includes(JSON.parse(c.startup).token));
    assert.deepEqual(JSON.parse(JSON.stringify(r)), {name:r.name});
  }
  assert.equal(inspect(startup), 'ManagedContract(Startup)');
  assert.equal(inspect(hello), 'HandshakeContract(Hello)');
  assert.throws(() => startup.admitHello('\ud800'), {message:/^portable_unicode:/});
});


test('a manifest handle cannot authenticate as a private startup', () => {
  const source = JSON.parse(readFileSync(new URL('../../../contracts/managed-v1-cases.json', import.meta.url), 'utf8'));
  const c = source.cases.find(c => c.name === 'Manifest' && c.canonical);
  const manifest = ManagedContract.fromJson('Manifest', c.canonical);
  assert.throws(() => manifest.hello(), {message:/^portable_contract:/});
});
