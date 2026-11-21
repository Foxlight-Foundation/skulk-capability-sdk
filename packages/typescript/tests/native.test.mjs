import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import test from 'node:test';

const entry = new URL('../dist/native.js', import.meta.url).href;
test('unqualified Node versions refuse before loading the native addon', () => {
  for (const version of ['20.19.0', '22.17.0', '24.0.0']) {
    const result = spawnSync(process.execPath, ['--input-type=module', '-e', `
      Object.defineProperty(process.versions,'node',{value:${JSON.stringify(version)}});
      await import(${JSON.stringify(entry)});
    `], {encoding:'utf8', timeout:5000});
    assert.equal(result.status, 1);
    assert.match(result.stderr, /requires Node 22.18/);
  }
});
test('unsupported libc refuses with an explicit error before loading', () => {
  for (const header of [{}, {glibcVersionRuntime:'2.33'}]) {
    const result = spawnSync(process.execPath, ['--input-type=module', '-e', `
      Object.defineProperty(process,'platform',{value:'linux'});
      process.report.getReport=()=>({header:${JSON.stringify(header)}});
      await import(${JSON.stringify(entry)});
    `], {encoding:'utf8', timeout:5000});
    assert.equal(result.status, 1);
    assert.match(result.stderr, /requires glibc 2.34/);
  }
});
