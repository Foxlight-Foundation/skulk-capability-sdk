/** A clean typed caller needs only the candidate facade and published types. */
import {PortableContract, PortableSchema, canonicalPortableJson, portableProfileDigest} from '../dist/portable.js';

const schema = new PortableSchema('{"type":"integer"}');
const matches: boolean = schema.matches('1.0');
const profile: string = portableProfileDigest();
const bytes: Uint8Array = canonicalPortableJson('{"value":1.0}');
const contract = PortableContract.fromJson('Descriptor', JSON.stringify({
  contract_profile: 'skulk.portable/1', profile_digest: profile,
  id: 'example.echo', version: '1.0.0', title: 'Echo', description: 'Typed caller',
  behavior: 'read', input_schema: {type: 'object'},
}));
const digest: string = contract.digest;
const canonical: Uint8Array = contract.canonical();
void [matches, bytes, digest, canonical];
