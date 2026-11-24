import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {join} from 'node:path';
import {createInterface} from 'node:readline';
import {test} from 'node:test';
const native = createRequire(import.meta.url)('../dist/kernel.node');

async function pythonOwner(directory) {
  const python = process.env.SKULK_NATIVE_TEST_PYTHON;
  assert.ok(python, 'qualification must explicitly select its Python peer');
  const child = spawn(python, ['-c',
    "import sys; from pathlib import Path; from skulk_capability_sdk.storage import HostLock; " +
    "held=HostLock(Path(sys.argv[1])); print('held',flush=True); sys.stdin.buffer.read(); held.close()",
    directory], {stdio:['pipe','pipe','pipe']});
  const closed = once(child, 'close');
  const lines = createInterface({input:child.stdout,signal:AbortSignal.timeout(5000)})[Symbol.asyncIterator]();
  try {
    const ready = await lines.next();
    assert.equal(ready.value, 'held');
    return {child,closed};
  } catch (error) { child.kill('SIGKILL'); await closed; throw error; }
}

async function pythonProbe(directory) {
  const python=process.env.SKULK_NATIVE_TEST_PYTHON; assert.ok(python);
  const child=spawn(python,['-c',
    "import sys,errno; from pathlib import Path; from skulk_capability_sdk.storage import HostLock\n"+
    "try: held=HostLock(Path(sys.argv[1]))\n"+
    "except OSError as error:\n assert error.errno in (errno.EAGAIN,errno.EWOULDBLOCK); print('busy')\n"+
    "else:\n held.close(); print('free')",directory], {stdio:['ignore','pipe','pipe']});
  const closed=once(child,'close');let output='';child.stdout.on('data',b=>output+=b);
  try { const [code]=await closed; assert.equal(code,0); return output.trim(); }
  finally {child.kill('SIGKILL');await closed;}
}

test('Node shares the real Python facade fence before metadata or journal I/O', {timeout:10000}, async () => {
  const root = mkdtempSync('/tmp/skulk-node-custody-'), directory=join(root,'custody');
  const marker=join(root,'native-jobs.json'); writeFileSync(marker,'original caller bindings');
  const {child,closed} = await pythonOwner(directory);
  let ticks=0; const pulse=setInterval(()=>ticks++,5);
  try {
    await assert.rejects(native.openProcessFence(directory), /busy/);
    assert.ok(ticks>4, 'native filesystem contention blocked Node control');
    assert.equal(readFileSync(marker,'utf8'),'original caller bindings');
    assert.deepEqual(readdirSync(directory),['supervisor.lock']);
    const inode=statSync(join(directory,'supervisor.lock')).ino;
    child.stdin.end(); await closed;
    const held=await native.openProcessFence(directory);
    try { assert.equal(await pythonProbe(directory),'busy'); }
    finally {held.close();held.close();}
    assert.equal(await pythonProbe(directory),'free');
    assert.equal(statSync(join(directory,'supervisor.lock')).ino,inode);
  } finally { clearInterval(pulse); child.kill('SIGKILL'); await closed; rmSync(root,{recursive:true,force:true}); }
});

test('a malformed native directory string refuses before filesystem access', async () => {
  await assert.rejects(native.openProcessFence('\ud800'));
});

test('late Node acquisition is kept through cancellation and actual cleanup', {timeout:10000}, async () => {
  const root=mkdtempSync('/tmp/skulk-node-custody-'), directory=join(root,'custody');
  const {child,closed}=await pythonOwner(directory);
  let cancelled=false,cleanup=false;
  const pending=native.openProcessFence(directory).then(held=>{
    try { assert.ok(cancelled); } finally { held.close(); cleanup=true; }
  });
  try {
    cancelled=true; assert.equal(cleanup,false);
    child.stdin.end(); await closed; await pending;
    assert.ok(cleanup);
    const next=await native.openProcessFence(directory); next.close();
  } finally { child.kill('SIGKILL'); await closed; await pending; rmSync(root,{recursive:true,force:true}); }
});
