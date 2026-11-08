import assert from 'node:assert/strict';
import {mkdtempSync, chmodSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createRequire} from 'node:module';
import {spawnSync} from 'node:child_process';
import {test} from 'node:test';
const {NativeJournal, canonicalJson} = createRequire(import.meta.url)('../dist/kernel.node');
const digest = 'a'.repeat(64);

test('native journal rollback, replay, recovery, ownership and close', () => {
  const root = mkdtempSync(join(tmpdir(), 'skulk-native-')); chmodSync(root, 0o700);
  const path = join(root, 'operations.sqlite');
  let first; let second;
  try {
    for (const time of [0.5, NaN, Infinity, -1, 2**53]) {
      assert.throws(() => new NativeJournal(path, '{}', time), /timestamp/);
    }
    first = new NativeJournal(path, '{}', 10);
    const call = (handle, method, fields = {}) => JSON.parse(handle.transact(JSON.stringify({method, scope: 'scope', now: 10, ...fields})));
    assert.equal(call(first, 'reserve', {operation_id: 'op', input_digest: digest, input: {number: 1}}).created, true);
    assert.equal(call(first, 'reserve', {operation_id: 'op', input_digest: digest, input: {number: 1}}).created, false);
    assert.throws(() => call(first, 'reserve', {operation_id: 'op', input_digest: 'b'.repeat(64), input: {number: 2}}), /conflict/);
    const before = call(first, 'status', {operation_id: 'op'});
    assert.throws(() => call(first, 'update', {operation_id: 'op', state: 'running', progress: 1.5}), /invalid/);
    assert.deepEqual(call(first, 'status', {operation_id: 'op'}), before);
    call(first, 'update', {operation_id: 'op', state: 'running', submission: 'accepted', backend_reference: 'original-job'});
    call(first, 'request_cancel', {operation_id: 'op'});
    assert.equal(call(first, 'started_at', {operation_id: 'op'}), 10);
    first.close();
    second = new NativeJournal(path, '{}', 20);
    const recovered = call(second, 'status', {operation_id: 'op', now: 20});
    assert.equal(recovered.state, 'interrupted');
    assert.equal(recovered.backend_reference, 'original-job');
    assert.equal(call(second, 'cancel_requested', {operation_id: 'op'}), true);
    assert.equal(call(second, 'next_queued'), null);
    assert.deepEqual(call(second, 'interrupted_accepted'), [['op','original-job']]);
    assert.equal(call(second, 'page').outstanding, 1);
    second.acquireOwnership();
    assert.throws(() => second.acquireOwnership(), /busy/);
    second.releaseOwnership(); second.acquireOwnership(); second.close();
    assert.throws(() => call(second, 'page'), /closed/);
    assert.equal(Buffer.from(canonicalJson('1.00')).toString(), '1.0');
  } finally { first?.close(); second?.close(); rmSync(root, {recursive: true, force: true}); }
});


test('Node reopens the real 0.4.0 fixture and Python reads its observations', () => {
  // Only this test invokes Python to materialize/inspect the SQLite fixture.
  // The loaded Node addon never invokes a runtime helper process.
  const python = process.env.SKULK_NATIVE_TEST_PYTHON;
  assert.ok(python, 'qualification must provide the frozen test interpreter');
  const root = mkdtempSync(join(tmpdir(), 'skulk-cross-language-')); chmodSync(root, 0o700);
  const path = join(root, 'operations.sqlite');
  const fixture = new URL('../../../../tests/fixtures/author-baseline/sdk-0.4.0-journal.sql', import.meta.url);
  const invokePython = (program, args = []) => {
    const result = spawnSync(python, ['-c', program, path, ...args], {encoding: 'utf8', timeout: 10000});
    assert.equal(result.status, 0, result.stderr);
  };
  let journal;
  try {
    invokePython('import pathlib,sqlite3,sys\np=pathlib.Path(sys.argv[1])\nwith sqlite3.connect(p) as db: db.executescript(pathlib.Path(sys.argv[2]).read_text())\np.chmod(0o600)', [fixture.pathname]);
    journal = new NativeJournal(path, '{"max_queued":8,"retain":4,"reservations":64}', 2000);
    const call = (method, fields = {}) => JSON.parse(journal.transact(JSON.stringify({method, scope: 'legacy-scope', now: 2000, ...fields})));
    assert.equal(call('status', {operation_id: 'accepted'}).backend_reference, 'synthetic-backend-job');
    assert.equal(call('status', {operation_id: 'uncertain'}).submission, 'uncertain');
    assert.equal(call('started_at', {operation_id: 'accepted'}), 1000);
    assert.equal(call('cancel_requested', {operation_id: 'accepted'}), true);
    assert.equal(call('next_queued'), null);
    const prunedDigest = createRequire(import.meta.url)('../dist/kernel.node').inputDigestTagged('legacy-scope',
      JSON.stringify(['object', [[[108,97,98,101,108], ['string', [112,114,117,110,101,100]]]]]));
    assert.throws(() => call('reserve', {operation_id: 'pruned', input_digest: prunedDigest, input: {label: 'pruned'}}), /retired/);
    call('update', {operation_id: 'accepted', state: 'succeeded', result: {artifact: 'node-observation'}});
    journal.close();
    invokePython('from pathlib import Path\nimport sys\nfrom skulk_capability_sdk.contracts import OperationBounds\nfrom skulk_capability_sdk.operations import OperationJournal\nj=OperationJournal(Path(sys.argv[1]),OperationBounds(max_queued=8,retain=4,reservations=64),clock=lambda:2000)\ns=j.status("accepted","legacy-scope")\nassert s.state=="succeeded" and s.result=={"artifact":"node-observation"}\nassert s.backend_reference=="synthetic-backend-job"\nassert j.started_at("accepted","legacy-scope")==1000\nassert j.cancel_requested("accepted","legacy-scope")\nassert j.status("uncertain","legacy-scope").submission=="uncertain"');
  } finally { journal?.close(); rmSync(root, {recursive: true, force: true}); }
});
