import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdtempSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {test} from 'node:test';
import {setTimeout as delay} from 'node:timers/promises';
const native=createRequire(import.meta.url)('../dist/kernel.node');
async function next(runner,kind){
  for(;;){const raw=await runner.nextRequest();assert.ok(raw);const request=JSON.parse(raw);if(request.kind==='abort')continue;assert.equal(request.kind,kind);return request}
}
async function state(runner,id,expected){
  for(let i=0;i<500;i++){const value=JSON.parse(await runner.status(id));if(value.state===expected)return value;await delay(2)}
  throw new Error('native observation did not converge');
}

test('Node bridge preserves uncertainty, original job identity and schema result',async()=>{
  const root=mkdtempSync('/tmp/skulk-node-operation-');const journal=new native.NativeJournal(join(root,'operations.sqlite'),'{}',100);
  const runner=native.operationRunner(journal,join(root,'logs'),'scope',0.005,0.2,'{"type":"object","required":["value"]}');
  try{
    await runner.reserve('op','{"number":1.0}');await runner.start();await runner.start();
    const submit=await next(runner,'submit');assert.equal(JSON.parse(await runner.status('op')).submission,'uncertain');
    assert.equal(runner.respond(submit.id,'{"kind":"accepted","reference":"original"}'),true);
    const observe=await next(runner,'observe');assert.equal(observe.reference,'original');
    runner.respond(observe.id,'{"kind":"observed","state":"succeeded","result":{"value":2}}');
    const result=await state(runner,'op','succeeded');assert.deepEqual(result.result,{value:2});assert.equal(result.backend_reference,'original');
    assert.equal(runner.respond(observe.id,'{"kind":"observed","state":"failed"}'),false);
  }finally{await runner.stop();journal.close();rmSync(root,{recursive:true,force:true})}
});

test('Node cancel acknowledgment is not backend termination',async()=>{
  const root=mkdtempSync('/tmp/skulk-node-operation-');const journal=new native.NativeJournal(join(root,'operations.sqlite'),'{}',100);
  const runner=native.operationRunner(journal,join(root,'logs'),'scope',0.005,0.2,'{}');
  try{
    await runner.reserve('op','{}');await runner.start();const submit=await next(runner,'submit');runner.respond(submit.id,'{"kind":"accepted","reference":"owned"}');
    const initial=await next(runner,'observe');await runner.cancel('op');runner.respond(initial.id,'{"kind":"observed","state":"running"}');
    const cancel=await next(runner,'cancel');runner.respond(cancel.id,'{"kind":"cancel_accepted"}');
    const observe=await next(runner,'observe');await delay(10);assert.equal(JSON.parse(await runner.status('op')).state,'running');
    runner.respond(observe.id,'{"kind":"observed","state":"cancelled"}');assert.equal((await state(runner,'op','cancelled')).result,null);
  }finally{await runner.stop();journal.close();rmSync(root,{recursive:true,force:true})}
});

test('Node submit timeout refuses late replies and leaves uncertainty without replay',async()=>{
  const root=mkdtempSync('/tmp/skulk-node-operation-');const journal=new native.NativeJournal(join(root,'operations.sqlite'),'{}',100);
  const runner=native.operationRunner(journal,join(root,'logs'),'scope',0.005,0.02,'{}');
  try{
    await runner.reserve('op','{}');await runner.start();const submit=await next(runner,'submit');
    const result=await state(runner,'op','interrupted');assert.equal(result.submission,'uncertain');assert.equal(result.backend_reference,null);
    assert.equal(runner.respond(submit.id,'{"kind":"accepted","reference":"late"}'),false);
  }finally{await runner.stop();journal.close();rmSync(root,{recursive:true,force:true})}
});

test('Node shared journal fences duplicate scope workers and refuses live close',async()=>{
  const root=mkdtempSync('/tmp/skulk-node-operation-');const journal=new native.NativeJournal(join(root,'operations.sqlite'),'{}',100);
  const first=native.operationRunner(journal,join(root,'logs'),'scope',0.005,0.2,'{}');
  const second=native.operationRunner(journal,join(root,'logs'),'scope',0.005,0.2,'{}');
  try{
    await first.reserve('op','{}');await first.start();
    await assert.rejects(second.start(),/storage/);
    assert.throws(()=>journal.close(),/busy/);assert.throws(()=>journal.releaseOwnership(),/busy/);
    assert.equal((await next(first,'submit')).operation_id,'op');
  }finally{await first.stop();await second.stop();journal.close();rmSync(root,{recursive:true,force:true})}
});
