import assert from 'node:assert/strict';
import test from 'node:test';
import {Resources, Cancelled} from '../dist/resources.js';

test('cancelled acquisition remains owned through its actual cleanup', async () => {
  const resources = new Resources(), signal = new AbortController();
  const acquisition = Promise.withResolvers(), cleanup = Promise.withResolvers();
  let started = false, ended = false;
  const entered = resources.enter(() => acquisition.promise, signal.signal);
  const refused = assert.rejects(entered, Cancelled);
  signal.abort();
  const closing = resources.close().then(() => {ended = true;});
  acquisition.resolve({async close() {started = true; await cleanup.promise;}});
  await refused;
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(started, true); assert.equal(ended, false);
  assert.equal(resources.close() instanceof Promise, true);
  cleanup.resolve(); await closing; assert.equal(ended, true);
  await assert.rejects(resources.enter(async () => ({async close() {}}), signal.signal), Cancelled);
});

test('LIFO cleanup drains all resources even when one fails', async () => {
  const resources = new Resources(), order = [];
  resources.add({async close() {order.push('first');}});
  resources.add({async close() {order.push('second'); throw Error('backend secret');}});
  resources.add({async close() {order.push('third');}});
  await assert.rejects(resources.close(), error => error.message === 'Application resource cleanup failed');
  assert.deepEqual(order, ['third','second','first']);
  await assert.rejects(resources.close());
  assert.deepEqual(order, ['third','second','first']);
});
