import assert from 'node:assert/strict';
import {inspect} from 'node:util';
import test from 'node:test';
import {CredentialReader, CredentialUnavailable, SecretBytes} from '../dist/credentials.js';

test('secret representations redact and return only independent copies', () => {
  const input = Buffer.from('synthetic-sensitive-value');
  const secret = new SecretBytes(input); input.fill(0);
  for (const value of [String(secret), JSON.stringify(secret), inspect(secret)]) {
    assert.equal(value.includes('synthetic-sensitive-value'), false);
  }
  const first = secret.getSecretValue(); first.fill(0);
  assert.equal(Buffer.from(secret.getSecretValue()).toString(), 'synthetic-sensitive-value');
});

test('undeclared reads refuse before helper resolution and absent selection never falls back', async () => {
  const reader = new CredentialReader(null, [{credential_id:'api-token',required:false}], {
    executable:'/not-an-ambient-program', executableSha256:'0'.repeat(64),
  });
  await assert.rejects(reader.read('another-account'), TypeError);
  await assert.rejects(reader.read('api-token'), CredentialUnavailable);
  await reader.close(); await reader.close();
  await assert.rejects(reader.read('api-token'));
});
