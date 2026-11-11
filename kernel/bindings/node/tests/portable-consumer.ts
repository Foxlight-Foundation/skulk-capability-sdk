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

// Managed records are a separate type/profile, preserving portable identities.
import {ManagedContract, managedProfileDigest} from '../dist/portable.js';
const managedFingerprint: string = managedProfileDigest();
function admittedManifest(source: string): Uint8Array {
  const manifest = ManagedContract.fromJson('Manifest', source);
  const fullIdentity: string = manifest.digest;
  void fullIdentity;
  return manifest.canonical();
}
void managedFingerprint;
void admittedManifest;

// New handshake handles remain explicit and distinct from legacy stream I/O.
import {HandshakeContract, handshakeProfileDigest} from '../dist/portable.js';
const handshake: HandshakeContract = ManagedContract.fromJson('Startup', '{}').hello();
const bound: HandshakeContract = ManagedContract.fromJson('Startup', '{}').admitHello('{}', 'call');
const handshakeFingerprint: string = handshakeProfileDigest();
void [handshake, bound, handshakeFingerprint];
// @ts-expect-error only the two explicit handshake roots are admitted
HandshakeContract.fromJson('Startup', '{}');
