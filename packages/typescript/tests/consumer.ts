import {CapabilityApp, CapabilityEndpoint, OutputFrame, type Resource, type JsonObject} from '@foxlight/skulk-capability-sdk';

// Compile as an independent author against the public package's declarations.
const app = new CapabilityApp({
  async setup(startup, signal): Promise<Resource> {
    const configuration = startup.configuration;
    if (configuration !== null && signal.aborted) throw Error('setup cancelled');
    const secret = await startup.credentials.read('api-token', {signal});
    if (secret !== null) void secret.getSecretValue();
    return {async close() {}};
  },
  ready: () => true,
  surfaces: () => [],
});
app.unary('author.echo', async call => ({text: call.payload.text ?? null}), {version: '1.0.0'});
app.stream('author.audio', async function* (call, inputs) {
  for await (const input of inputs) {
    if (call.signal.aborted) return;
    yield new OutputFrame('chunk', input.payload, {media: input.media});
  }
  yield new OutputFrame('completed', {});
});
export function bindClient(descriptor: JsonObject): void {
  const endpoint = new CapabilityEndpoint('receiver', descriptor);
  app.unary('author.client', async call => {
    const resource = await call.enterResource(async () => ({async close() {}}));
    void resource;
    const reply = await call.call(endpoint, {text:'hello'});
    await call.stream(endpoint, reply.payload, async stream => {
      await stream.send({text:'input'}, {media:new Uint8Array([0,255])});
      await stream.completeInput();
      for await (const frame of stream) void frame.media;
    });
    return {reply: reply.payload};
  });
}
// @ts-expect-error handlers cannot return arbitrary class instances or scalars
app.unary('author.invalid', async () => 42);
// @ts-expect-error unknown modes/terminal kinds cannot silently become unary
new OutputFrame('unknown');
// @ts-expect-error a descriptor is required; metadata alone grants no call rights
new CapabilityEndpoint('receiver');
