/** Observe packaged synthetic authority witnesses through the actual Node addon. */
import readline from 'node:readline';
const facade = await import(process.argv[2] ?? '../dist/portable.js');
for await (const line of readline.createInterface({input: process.stdin, crlfDelay: Infinity})) {
  let observation;
  try {
    const request = JSON.parse(line);
    if (request.profile !== undefined && request.profile !== 'skulk.composition/3') throw new Error('composition_invalid');
    const factory = request.profile ? facade.CompositionV3Contract : facade.CompositionV2Contract;
    if (request.action === 'author') {
      const value = facade.PortableV2Contract.fromJson(request.name, request.source);
      observation = {canonical: Buffer.from(value.canonical()).toString('utf8'), digest: value.digest, error: null};
    } else {
      let value = factory.fromJson(request.name, request.source);
      if (request.action === 'admit') {
        const parent = factory.fromJson('Context', request.parent);
        const policy = factory.fromJson('Policy', request.policy);
        value = parent.admit(value, policy, request.executor, Buffer.from(request.request_hex, 'hex'), request.now_unix_ms, request.scope_id);
      }
      observation = {canonical: Buffer.from(value.canonical()).toString('utf8'), digest: null, error: null};
    }
  } catch (error) {
    const code = error.message.split(':', 1)[0];
    if (!/^(composition|portable)_/.test(code) || error.message.includes('synthetic-do-not-echo-secret')) throw error;
    observation = {canonical: null, digest: null, error: code};
  }
  process.stdout.write(`${JSON.stringify(observation)}\n`);
}
