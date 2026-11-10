import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {ManagedContract, managedProfileDigest} from '../dist/portable.js';

const fixture = JSON.parse(readFileSync(new URL('../../../contracts/managed-v1-cases.json', import.meta.url), 'utf8'));

test('complete independent managed records through the native addon', () => {
  assert.equal(managedProfileDigest(), fixture.profile_digest);
  for (const c of fixture.cases) {
    if (c.error) {
      assert.throws(() => ManagedContract.fromJson(c.name, c.source), error => {
        assert.ok(error instanceof Error);
        assert.equal(error.message.split(':', 1)[0], c.error, c.id);
        assert.ok(!error.message.includes('synthetic-do-not-echo-secret'));
        return true;
      });
    } else {
      const record = ManagedContract.fromJson(c.name, c.source);
      assert.equal(Buffer.from(record.canonical()).toString('ascii'), c.canonical, c.id);
      assert.equal(record.digest, c.digest, c.id);
      assert.equal(record.digest.length, 64);
      const copy = record.canonical();
      copy.fill(0);
      assert.equal(Buffer.from(record.canonical()).toString('ascii'), c.canonical);
    }
  }
});

test('malformed JavaScript strings refuse before UTF-8 substitution', () => {
  assert.throws(() => ManagedContract.fromJson('Startup', '\ud800'), {message: /^portable_unicode:/});
});
