import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {inspect} from 'node:util';
import test from 'node:test';
import {CapabilityEndpoint, CapabilityDescription, CapabilityProposal, CapabilityInterfaceError} from '../dist/index.js';

const original = JSON.parse(readFileSync(new URL('../../../tests_native/discovery-fixture.json', import.meta.url), 'utf8')).descriptor;

test('fixed intent bytes agree with independently recorded native vectors', () => {
  const vectors = JSON.parse(readFileSync(new URL('../../../tests_native/capability-interface-vectors.json', import.meta.url), 'utf8'));
  for (const vector of vectors) {
    const proposal = CapabilityProposal.prepare(new CapabilityEndpoint('receiver', vector.descriptor), vector.payload);
    assert.equal(Buffer.from(proposal.canonical()).toString(), vector.canonical);
    assert.equal(Buffer.from(proposal.requestBytes).toString(), vector.request);
    assert.equal(proposal.digest, vector.digest);
  }
});

test('inert intents preserve every effect and own arguments without approval', () => {
  for (const behavior of ['read', 'inference', 'proposal', 'execute']) {
    const descriptor = {...structuredClone(original), behavior};
    const endpoint = new CapabilityEndpoint('receiver', descriptor);
    const payload = {input:'private-proposal-marker', value:1.0};
    const proposal = CapabilityProposal.prepare(endpoint, payload);
    payload.input = 'mutated'; proposal.payload.input = 'changed-again';
    assert.equal(proposal.payload.input, 'private-proposal-marker');
    assert.equal(Buffer.from(proposal.requestBytes).toString(), '{"input":"private-proposal-marker","value":1}');
    const intent = JSON.parse(Buffer.from(proposal.canonical()).toString());
    assert.deepEqual(Object.keys(intent).sort(), ['descriptor','destination','format','kind','payload','request_sha256']);
    assert.equal(intent.kind, 'capability_request_proposal'); assert.equal(intent.format, 1);
    assert.deepEqual(intent.descriptor, endpoint.descriptor());
    assert.equal(intent.request_sha256, createHash('sha256').update(proposal.requestBytes).digest('hex'));
    assert.equal(proposal.digest, createHash('sha256').update(proposal.canonical()).digest('hex'));
    assert.equal(proposal.execute, undefined); assert.equal(proposal.approve, undefined);
    assert.ok(!inspect(proposal).includes('private-proposal-marker'));
    assert.equal(JSON.stringify(proposal), '"CapabilityProposal"');
    const canonical = proposal.canonical(); canonical[0] = 0;
    assert.equal(Buffer.from(proposal.canonical())[0], '{'.charCodeAt(0));
    const described = CapabilityDescription.fromEndpoint(endpoint);
    described.descriptor().behavior = 'read'; assert.equal(described.behavior, behavior);
    assert.equal(described.processingDomain, descriptor.processing_domain);
  }
});

test('full identifiers and native input rules refuse without private values', () => {
  for (const nodeId of ['', 'node\n', 'bad node', 'a'.repeat(129), undefined]) {
    assert.throws(() => CapabilityProposal.prepare(new CapabilityEndpoint(nodeId, original), {input:'private-proposal-marker'}),
      error => error instanceof CapabilityInterfaceError && error.code === 'invalid_descriptor'
        && !error.message.includes('private-proposal-marker'));
  }
  for (const payload of [{input:1}, {input:'private-proposal-marker', bad:NaN},
    {input:'private-proposal-marker', bad:2 ** 53}, {input:'x'.repeat(66000)}]) {
    const descriptor = {...structuredClone(original), input_schema: {type:'object', required:['input'], properties:{input:{type:'string'}}}};
    assert.throws(() => CapabilityProposal.prepare(new CapabilityEndpoint('receiver', descriptor), payload),
      error => error instanceof CapabilityInterfaceError && error.code === 'invalid_payload'
        && !error.message.includes('private-proposal-marker'));
  }
});
