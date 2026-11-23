import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {test} from 'node:test';
const native = createRequire(import.meta.url)('../dist/kernel.node');
const fixture = JSON.parse(readFileSync(new URL('../../../contracts/discovery-v1-cases.json', import.meta.url), 'utf8'));
const template = JSON.parse(readFileSync(new URL('../../../contracts/discovery-offer.json', import.meta.url), 'utf8'));
for (const [count, rich, depth] of [[64, true, 0], [1000, false, 0], [1, false, 62]]) {
  test(`directory outer budget admits ${count} valid ${depth ? 'deep' : rich ? 'rich' : 'simple'} offers`, () => {
    const offer = structuredClone(template);
    if (rich) {
      offer.descriptor.input_schema = {type: "object", properties: {selection: {enum: Array.from({length:512}, (_, i) => i)}}};
    }
    if (depth) {
      let nested = 0;
      for (let i=0;i<depth;i++) nested = [nested];
      offer.descriptor.input_schema.default = nested;
    }
    if (rich || depth) offer.target.descriptor_revision = native.portableV2ContractDigest('Descriptor', JSON.stringify(offer.descriptor));
    const body = Buffer.from(JSON.stringify(Array.from({length:count}, (_, i) => ({...offer, plugin_id:`managed.example${i}`}))));
    const probe = fixture.cases[0].request;
    const query = native.discoveryRequest(probe.startup, probe.context, probe.parent, '1');
    const metadata = native.decodeDiscoveryFrame(query).metadata.toString('utf8');
    const reply = native.discoveryReply(probe.startup, metadata, body, null);
    const result = native.discoveryResponse(probe.startup, metadata, reply);
    assert.equal(result.error ?? null, null);
    assert.deepEqual(result.payload, body);
  });
}
