/** Private native qualification API. The approved facade converts language
 * values; this addon owns journal state, stream framing and transport rules. */
import type {Buffer} from 'node:buffer';

type NativePacket = {projection: string; media: Buffer};
declare class PortableSchema {
  constructor(source: string);
  matches(source: string): boolean;
}
declare class NativeAuthorContract {
  private constructor();
  readonly name: string;
  canonical(): Buffer;
  streamPacket(): Buffer;
  constructorInput(): string;
}
declare class NativeJournal {
  constructor(path: string, boundsJson: string, now: number);
  transact(request: string): string;
  acquireOwnership(): void;
  releaseOwnership(): void;
  close(): void;
}
declare class NativeStream {
  private constructor();
  nextInput(): Promise<NativePacket | null>;
  sendOutput(projection: string, media: Buffer): Promise<void>;
  finish(): Promise<void>;
  close(): Promise<void>;
}
declare class NativeOperationRunner {
  private constructor();
  start(): Promise<void>;
  stop(): Promise<void>;
  reserve(operationId: string | null, payload: string): Promise<string>;
  status(operationId: string): Promise<string | null>;
  cancel(operationId: string): Promise<string>;
  log(operationId: string, offset: number): Promise<string>;
  nextRequest(): Promise<string | null>;
  respond(id: number, response: string): boolean;
}
declare class NativeControl {
  private constructor();
  receive(): Promise<Buffer>;
  send(source: string): Promise<void>;
  close(): Promise<void>;
}
interface CallFrame {metadata: Buffer; payload: Buffer;}
declare class NativeCallControl {
  private constructor();
  receive(): Promise<CallFrame>;
  send(source: string, payload: Buffer): Promise<void>;
  close(): Promise<void>;
}
declare const native: {
  callsProfileDigest(): string;
  callPayloadSize(source: string): number;
  encodeCallFrame(source: string, payload: Buffer): Buffer;
  decodeCallFrame(packet: Buffer): CallFrame;
  openCallControl(startup: string, seconds: number): Promise<NativeCallControl>;
  /** Explicit inference-aware structural admission, never caller authority. */
  compositionV2ProfileDigest(): string;
  compositionV2ContractJson(name: 'Context' | 'DelegateRequest' | 'Policy', source: string): Buffer;
  admitDelegationV2Json(parent: string, request: string, policy: string, executor: string, requestBytes: Buffer, nowUnixMs: number, scopeId: string): Buffer;
  portableV2ProfileDigest(): string;
  portableV2ContractJson(name: 'Descriptor' | 'ContractEnvelope', source: string): Buffer;
  portableV2ContractDigest(name: 'Descriptor' | 'ContractEnvelope', source: string): string;
  compositionProfileDigest(): string;
  compositionContractJson(name: 'Context' | 'DelegateRequest' | 'Policy', source: string): Buffer;
  admitDelegationJson(parent: string, request: string, policy: string, executor: string, requestBytes: Buffer, nowUnixMs: number, scopeId: string): Buffer;
  transportProfileDigest(): string;
  encodePortableControl(source: string): Buffer;
  decodePortableControl(packet: Buffer): Buffer;
  encodePortablePacket(projection: string, media: Buffer): Buffer;
  decodePortablePacket(packet: Buffer): NativePacket;
  openPortableStream(startup: string, callId: string, mode: string, seconds: number): Promise<NativeStream>;
  openPortableControl(startup: string, seconds: number): Promise<NativeControl>;
  handshakeProfileDigest(): string;
  handshakeContractJson(name: 'Hello' | 'StreamHello', source: string): Buffer;
  managedHelloJson(name: 'Manifest' | 'Startup', startup: string, callId?: string): Buffer;
  managedAdmitHelloJson(name: 'Manifest' | 'Startup', startup: string, source: string, callId?: string): Buffer;
  managedProfileDigest(): string;
  managedContractJson(name: 'Manifest' | 'Startup', source: string): Buffer;
  managedContractDigest(name: 'Manifest' | 'Startup', source: string): string;
  handshakeV2ProfileDigest(): string;
  handshakeV2ContractJson(name: 'Hello' | 'StreamHello', source: string): Buffer;
  managedV2HelloJson(name: 'Manifest' | 'Startup', startup: string, callId?: string): Buffer;
  managedV2AdmitHelloJson(name: 'Manifest' | 'Startup', startup: string, source: string, callId?: string): Buffer;
  managedV2ProfileDigest(): string;
  managedV2ContractJson(name: 'Manifest' | 'Startup', source: string): Buffer;
  managedV2ContractDigest(name: 'Manifest' | 'Startup', source: string): string;
  portableProfileDigest(): string;
  PortableSchema: typeof PortableSchema;
  portableCanonicalJson(source: string): Buffer;
  portableContractJson(name: 'Descriptor' | 'ContractEnvelope', source: string): Buffer;
  portableContractDigest(name: 'Descriptor' | 'ContractEnvelope', source: string): string;
  portableSchemaMatches(definition: string, instance: string): boolean;
  schemaMatches(definition: string, instance: string): boolean;
  schemaMatchesInstalled(definition: string, instance: string): boolean;
  operationRunner(journal: NativeJournal, logs: string, scope: string, pollSeconds: number,
                  adapterSeconds: number, resultSchema: string): NativeOperationRunner;
  NativeAuthorContract: typeof NativeAuthorContract;
  validateContractValue(name: string, tagged: string): NativeAuthorContract;
  canonicalJson(input: string): Buffer;
  validateContractJson(name: string, input: string): Buffer;
  canonicalTagged(input: string): Buffer;
  inputDigestTagged(scope: string, input: string): string;
  encodeStreamPacket(projection: string, media: Buffer): Buffer;
  decodeStreamPacket(packet: Buffer): NativePacket;
  openStream(socket: string, hello: string, callId: string, mode: string, seconds: number): Promise<NativeStream>;
  NativeJournal: typeof NativeJournal;
};
export = native;
