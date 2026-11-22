import assert from 'node:assert/strict';
import test from 'node:test';
import {PassThrough, Readable} from 'node:stream';
import {jobFieldSource} from '../dist/job-contracts.js';
import {jobFrames, sendJobFrame, parseJobMessage} from '../dist/job-channel.js';

test('native field projection preserves nested punctuation, escaped keys and canonical numbers', () => {
  assert.equal(jobFieldSource('{"input":{"z":["x,}\\\"",{"a":1.0}]},"op":"start"}', 'input'), '{"z":["x,}\\\"",{"a":1}]}');
  assert.equal(jobFieldSource('{"inpu\\u0074":{"n":1e-7,"label":"é"}}', 'input'), '{"label":"\\u00e9","n":1e-07}');
  for (const raw of ['{"input":{},"input":{}}', '{"input":NaN}', '{"op":"start"}', '[]']) assert.throws(() => jobFieldSource(raw, 'input'));
});

test('private framing retains a large nested native payload without a public JSON fallback', async () => {
  const stream = new PassThrough(), value = {kind:'adapter', value:'"'.repeat(60000)};
  const messages = (async () => { const result = []; for await (const message of jobFrames(stream)) result.push(message); return result; })();
  await sendJobFrame(stream, value); stream.end();
  assert.deepEqual(await messages, [value]);
  assert.throws(() => parseJobMessage('{"kind":1,"kind":2}'));
  assert.throws(() => parseJobMessage('{"kind":NaN}'));
  assert.throws(() => parseJobMessage('[]'));
});

test('partial and oversized private frames refuse before dispatch', async () => {
  for (const bytes of [Buffer.from([0,0,0]), Buffer.from([0,0,0,8,123]), Buffer.from([0,4,0,1])]) {
    await assert.rejects(async () => { for await (const ignored of jobFrames(Readable.from([bytes]))) void ignored; });
  }
});
