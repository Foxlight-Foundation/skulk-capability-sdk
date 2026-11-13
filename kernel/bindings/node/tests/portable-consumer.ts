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

import {transportProfileDigest, encodePortableControl, decodePortableControl, encodePortablePacket, decodePortablePacket, openPortableStream, openPortableControl} from '../dist/portable.js';
const wireFingerprint: string = transportProfileDigest();
const controlBytes: Uint8Array = encodePortableControl('{}');
const controlJson: Uint8Array = decodePortableControl(controlBytes);
const wireBytes: Uint8Array = encodePortablePacket('{}', new Uint8Array());
const decoded: {projection: string; media: Uint8Array} = decodePortablePacket(wireBytes);
function typedChannels(startup: ManagedContract) {
  return [openPortableStream(startup, 'call', 'bidirectional', 5), openPortableControl(startup, 5)];
}
void [wireFingerprint, controlJson, decoded, typedChannels];

// Caller metadata is explicitly typed and never a substitute for host authority.
import {CompositionContract, compositionProfileDigest} from '../dist/portable.js';
const compositionFingerprint: string = compositionProfileDigest();
const parentContext = CompositionContract.fromJson('Context', '{}');
const delegation = CompositionContract.fromJson('DelegateRequest', '{}');
const currentPolicy = CompositionContract.fromJson('Policy', '{}');
const childContext: CompositionContract = parentContext.admit(
  delegation, currentPolicy, '{}', new Uint8Array(), 1000, '0'.repeat(64),
);
void [compositionFingerprint, childContext];
// @ts-expect-error author invocation records cannot be reinterpreted as context
CompositionContract.fromJson('Invoke', '{}');

// Composed-call wire metadata and raw bodies stay separate and explicitly typed.
import {callsProfileDigest, callPayloadSize, encodeCallFrame, decodeCallFrame, openCallControl} from '../dist/portable.js';
import type {CallFrame, CallControl} from '../dist/portable.js';
const callsFingerprint: string = callsProfileDigest();
const callBodySize: number = callPayloadSize('{}');
const callBytes: Uint8Array = encodeCallFrame('{}', new Uint8Array());
const callFrame: CallFrame = decodeCallFrame(callBytes);
function typedCallChannel(startup: ManagedContract): Promise<CallControl> {
  return openCallControl(startup, 5);
}
void [callsFingerprint, callBodySize, callFrame, typedCallChannel];
// @ts-expect-error raw call bodies are byte buffers, never implicit JSON strings
encodeCallFrame('{}', 'body');

// Explicit inference types do not widen any existing legacy handle.
import {PortableV2Contract, CompositionV2Contract, portableV2ProfileDigest, compositionV2ProfileDigest} from '../dist/portable.js';
const inferenceAuthor = PortableV2Contract.fromJson('Descriptor', '{}');
const inferenceContext = CompositionV2Contract.fromJson('Context', '{}');
const inferencePolicy = CompositionV2Contract.fromJson('Policy', '{}');
const inferenceRequest = CompositionV2Contract.fromJson('DelegateRequest', '{}');
const inferenceChild: CompositionV2Contract = inferenceContext.admit(
  inferenceRequest, inferencePolicy, '{}', new Uint8Array(), 1000, '0'.repeat(64),
);
void [inferenceAuthor, inferenceChild, portableV2ProfileDigest(), compositionV2ProfileDigest()];
// @ts-expect-error distinct native profiles cannot mix through typed delegation
inferenceContext.admit(delegation, inferencePolicy, '{}', new Uint8Array(), 1000, '0'.repeat(64));

import {ManagedV2Contract, HandshakeV2Contract, managedV2ProfileDigest, handshakeV2ProfileDigest} from '../dist/portable.js';
/** Compile the separate inference startup API without selecting a legacy channel. */
export function inferenceStartupConsumer(source: string, helloSource: string): Uint8Array {
  const startup: ManagedV2Contract = ManagedV2Contract.fromJson('Startup', source);
  const hello: HandshakeV2Contract = startup.hello();
  const matched: HandshakeV2Contract = startup.admitHello(helloSource);
  const identity: string = managedV2ProfileDigest() + handshakeV2ProfileDigest();
  void identity; void hello;
  return matched.canonical();
}

// Nullable overall inference deadlines remain explicit across every entry point.
import {CompositionV3Contract, compositionV3ProfileDigest, transportV2ProfileDigest, callsV2ProfileDigest,
  encodeTransportV2Control, decodeTransportV2Control, encodeTransportV2Packet, decodeTransportV2Packet,
  encodeCallV2Frame, decodeCallV2Frame, callV2PayloadSize,
  openTransportV2Stream, openTransportV2Control, openCallV2Control} from '../dist/portable.js';
/** Compile the explicit cohort, with null or real owner/credential deadlines. */
export function executionConsumer(startup: ManagedV2Contract, source: string): void {
  const parent = CompositionV3Contract.fromJson('Context', source);
  const request = CompositionV3Contract.fromJson('DelegateRequest', source);
  const policy = CompositionV3Contract.fromJson('Policy', source);
  const child: CompositionV3Contract = parent.admit(request, policy, source, new Uint8Array(), 1000, '0'.repeat(64));
  const streaming: Promise<import('../dist/portable.js').PortableStream> = openTransportV2Stream(startup, 'call', 'bidirectional');
  const bounded = openTransportV2Stream(startup, 'call', 'bidirectional', 7200);
  const control: Promise<import('../dist/portable.js').PortableControl> = openTransportV2Control(startup, 30);
  const calls: Promise<CallControl> = openCallV2Control(startup, 30);
  const wire: Uint8Array = encodeCallV2Frame(source, new Uint8Array());
  const decoded: CallFrame = decodeCallV2Frame(wire);
  const size: number = callV2PayloadSize(source);
  void [child, streaming, bounded, control, calls, decoded, size, compositionV3ProfileDigest(), transportV2ProfileDigest(), callsV2ProfileDigest(),
    decodeTransportV2Control(encodeTransportV2Control(source)), decodeTransportV2Packet(encodeTransportV2Packet(source, new Uint8Array()))];
  // @ts-expect-error profile two composition cannot inherit nullable expiry implicitly
  parent.admit(inferenceRequest, policy, source, new Uint8Array(), 1000, '0'.repeat(64));
  // @ts-expect-error legacy startup must not select the new execution transport
  openTransportV2Stream(ManagedContract.fromJson('Startup', source), 'call', 'bidirectional');
}
