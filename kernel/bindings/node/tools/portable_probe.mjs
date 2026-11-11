// Maintainer-only adapter: the same candidate facade used by the Node tests.
import {createInterface} from 'node:readline';
const {ManagedContract, PortableContract, PortableSchema, canonicalPortableJson} = await import(process.argv[2] ?? new URL('../dist/portable.js', import.meta.url).href);

for await (const line of createInterface({input: process.stdin, crlfDelay: Infinity})) {
  const request = JSON.parse(line);
  try {
    let response;
    if (request.operation === 'canonical') {
      response = {canonical: Buffer.from(canonicalPortableJson(request.source)).toString('ascii')};
    } else if (request.operation === 'schema') {
      response = {valid: new PortableSchema(request.definition).matches(request.source)};
    } else if (request.operation === 'managed') {
      const record = ManagedContract.fromJson(request.name, request.source);
      response = {canonical: Buffer.from(record.canonical()).toString('ascii'), digest: record.digest};
    } else if (request.operation === 'contract') {
      const record = PortableContract.fromJson(request.name, request.source);
      response = {canonical: Buffer.from(record.canonical()).toString('ascii'), digest: record.digest};
    } else {
      throw new Error('invalid maintainer probe operation');
    }
    process.stdout.write(JSON.stringify(response) + '\n');
  } catch (error) {
    if (!(error instanceof Error) || !/^portable_[a-z]+:/.test(error.message)) throw error;
    process.stdout.write(JSON.stringify({error: error.message.split(':', 1)[0]}) + '\n');
  }
}
