// Maintainer-only ABI qualification; input is preserved as exact JSON text.
import {createInterface} from 'node:readline';
import {createRequire} from 'node:module';

const require = createRequire(import.meta.url);
const native = require('../dist/loader.cjs');
const matches = process.argv.includes("--installed") ? native.schemaMatchesInstalled : native.schemaMatches;
const lines = createInterface({input: process.stdin, crlfDelay: Infinity});
for await (const line of lines) {
  const offset = line.indexOf('\t');
  if (offset < 0) throw new Error('schema probe input requires two documents');
  try {
    console.log(matches(line.slice(0, offset), line.slice(offset + 1)) ? 'valid' : 'invalid');
  } catch (error) {
    if (error.code !== 'InvalidArg' || error.message !== 'invalid payload schema') throw error;
    console.log('schema-error');
  }
}
