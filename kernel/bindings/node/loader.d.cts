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
declare const native: {
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
