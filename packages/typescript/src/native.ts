import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {dirname, join} from 'node:path';
import {pathToFileURL} from 'node:url';
import type addon from './native-api.cjs';
import {cohort} from './cohort.js';

const require = createRequire(import.meta.url);
const [nodeMajor = 0, nodeMinor = 0] = process.versions.node.split('.').map(Number);
if (nodeMajor !== 22 || nodeMinor < 18) throw new Error('The native SDK runtime requires Node 22.18 or later in the Node 22 line');
const platform = process.platform === 'darwin' && process.arch === 'arm64'
  ? 'macos-arm64'
  : process.platform === 'linux' && process.arch === 'arm64' ? 'linux-arm64'
  : process.platform === 'linux' && process.arch === 'x64' ? 'linux-x64' : null;
if (platform === null) throw new Error('This platform has no admitted native SDK runtime');
if (process.platform === 'linux') {
  const report = process.report?.getReport();
  const header: unknown = typeof report === 'object' && report !== null && 'header' in report ? report.header : null;
  const version: unknown = typeof header === 'object' && header !== null && 'glibcVersionRuntime' in header ? header.glibcVersionRuntime : null;
  if (typeof version !== 'string' || !/^\d+\.\d+/.test(version)) throw new Error('The native SDK runtime requires glibc 2.34 or later');
  const [major = 0, minor = 0] = version.split('.').map(Number);
  if (major < 2 || (major === 2 && minor < 34)) throw new Error('The native SDK runtime requires glibc 2.34 or later');
}
const selected = cohort[platform];
const root = dirname(require.resolve(`${selected.dependency}/package.json`));
for (const [file, expected] of Object.entries(selected.files)) {
  if (createHash('sha256').update(readFileSync(join(root, file))).digest('hex') !== expected) {
    throw new Error('Native SDK runtime identity differs from the issued cohort');
  }
}
// This is an FFI declaration for the exact measured binary, never a source or
// validator fallback. Merely importing it grants no host/caller authority.
const loaded: unknown = require(join(root, 'dist/kernel.node'));
export const native = loaded as typeof addon;
const codec: unknown = await import(pathToFileURL(join(root, 'dist/codec.js')).href);
if (typeof codec !== 'object' || codec === null || !('canonical' in codec)
    || typeof codec.canonical !== 'function') throw new Error('Native JSON bridge unavailable');
export const canonicalValue = codec.canonical as (value: unknown) => Uint8Array;
