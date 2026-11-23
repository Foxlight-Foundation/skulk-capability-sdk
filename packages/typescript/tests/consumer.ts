import {CapabilityApp, CapabilityEndpoint, OutputFrame, JobDefinition, DurableJobs, SubmissionRefused, discoverCapabilities, type CapabilityDirectory, type CapabilityOffer, type ApplicationFactory, type JobAdapter, type Resource, type JsonObject} from '@foxlight/skulk-capability-sdk';

/** Compile read-only discovery against only the installed public declarations. */
export async function discover(baseUrl: string, bearer: string): Promise<CapabilityOffer | undefined> {
  const directory: CapabilityDirectory = await discoverCapabilities(baseUrl, bearer);
  const offer = directory.offers[0];
  if (offer !== undefined) {
    const seconds: number | null = offer.inferenceSeconds;
    void seconds;
    // @ts-expect-error an observation is not an execution grant
    void offer.grant;
    // @ts-expect-error the runtime identity is immutable
    offer.runtimeDigest = 'replacement';
  }
  return offer;
}

// A staged module's default export receives only selected runtime metadata.
const createApplication: ApplicationFactory = runtime => new CapabilityApp({
  credentialHelper: runtime.credentialHelper,
});
export default createApplication;

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

/** Compile the complete public durable-job setup without private runtime imports. */
export function jobApplication(adapter: JobAdapter, directory: string): CapabilityApp {
  const definition = JobDefinition.create({id:'author.jobs',version:'1.0.0',
    title:'Author jobs',description:'Native inference jobs',
    inputSchema:{type:'object'},resultSchema:{type:'object'},processingDomain:'external'});
  const jobs = new DurableJobs(definition, adapter, {
    async plan(input, signal) {
      if (signal.aborted) throw new SubmissionRefused();
      return {plan_digest:'a'.repeat(64),summary:'Estimate',expires_at:2147483648,input};
    },
  });
  const application = new CapabilityApp({setup:(startup,signal)=>jobs.serve(startup,{directory,signal}),ready:()=>jobs.ready});
  jobs.register(application);
  return application;
}
// @ts-expect-error an undeclared processing domain cannot become remote inference
JobDefinition.create({id:'x',version:'1.0.0',title:'x',description:'x',inputSchema:{},resultSchema:{},processingDomain:'anywhere'});
// @ts-expect-error durable execution needs an explicit planner
new DurableJobs(JobDefinition.create({id:'x',version:'1.0.0',title:'x',description:'x',inputSchema:{},resultSchema:{}}), {});
