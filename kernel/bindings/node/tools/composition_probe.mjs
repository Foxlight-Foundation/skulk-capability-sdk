import readline from 'node:readline';
const api = await import(process.argv[2] ?? '../dist/portable.js');
for await (const line of readline.createInterface({input: process.stdin})) {
  let response;
  try {
    const c = JSON.parse(line);
    let record = api.CompositionContract.fromJson(c.name, c.source);
    if (c.action === 'admit') {
      const parent = api.CompositionContract.fromJson('Context', c.parent);
      const policy = api.CompositionContract.fromJson('Policy', c.policy);
      record = parent.admit(record, policy, c.executor, Buffer.from(c.request_hex, 'hex'), c.now_unix_ms, c.scope_id);
    }
    response = {canonical: Buffer.from(record.canonical()).toString('utf8'), error: null};
  } catch (error) {
    const code = /composition_[a-z]+/.exec(error.message)?.[0];
    if (!code || error.message.includes('synthetic-do-not-echo-secret')) throw error;
    response = {canonical: null, error: code};
  }
  console.log(JSON.stringify(response));
}
