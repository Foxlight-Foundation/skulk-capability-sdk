import assert from 'node:assert/strict';
import {readFileSync,mkdtempSync,rmSync} from 'node:fs';
import {createRequire} from 'node:module';
import {createServer} from 'node:net';
import {join} from 'node:path';
import {test} from 'node:test';
import {setTimeout as delay} from 'node:timers/promises';
const native=createRequire(import.meta.url)('../dist/kernel.node');
const fixture=JSON.parse(readFileSync(new URL('../../../contracts/transport-v2-cases.json',import.meta.url),'utf8'));
const managed=JSON.parse(readFileSync(new URL('../../../contracts/managed-v2-cases.json',import.meta.url),'utf8'));
const projection=(sequence,kind)=>JSON.stringify({call_id:'call',direction:'provider_to_caller',sequence,kind});
function startup(path) {const value=JSON.parse(managed.cases.find(c=>c.name==='Startup'&&c.canonical).canonical); Object.assign(value,{socket:path,stream_socket:path,token:'a'.repeat(64)});return JSON.stringify(value);}
test('all independent full-wire observations match the Node ABI',()=>{
  assert.equal(native.transportV2ProfileDigest(),fixture.profile_digest);
  for(const item of fixture.cases) {
    const encode=()=>item.action==='control'?native.encodeTransportV2Control(item.source):native.encodeTransportV2Packet(item.source,Buffer.from(item.media_hex,'hex'));
    if(!item.valid) {assert.throws(encode,undefined,item.id);continue;}
    const wire=encode();assert.equal(wire.toString('hex'),item.wire_hex,item.id);
    if(item.action==='control') assert.deepEqual(JSON.parse(native.decodeTransportV2Control(wire)),JSON.parse(item.source));
    else assert.equal(native.decodeTransportV2Packet(wire).media.toString('hex'),item.media_hex);
    for(const bad of [wire.subarray(0,wire.length-1),Buffer.concat([wire,Buffer.of(0)])]) {
      assert.throws(()=>item.action==='control'?native.decodeTransportV2Control(bad):native.decodeTransportV2Packet(bad));
    }
  }
  assert.throws(()=>native.encodeTransportV2Packet('{"kind":"end","call_id":"\\ud800"}',Buffer.alloc(0)));
});
for(const mode of ['server_streaming','client_streaming','bidirectional']){
  test(`${mode} native lifecycle keeps health responsive and holds terminal until cleanup`,async()=>{
    const root=mkdtempSync('/tmp/skulk-node-stream-'); const path=join(root,'sock');
    let cleaned=false; let resolveDone,rejectDone;
    const done=new Promise((resolve,reject)=>{resolveDone=resolve;rejectDone=reject});
    const sockets=new Set();
    const server=createServer(socket=>{
      sockets.add(socket); socket.on('close',()=>sockets.delete(socket));
      let buffer=Buffer.alloc(0); const packets=[];
      socket.on('data',data=>{
        try{
          buffer=Buffer.concat([buffer,data]);
          while(buffer.length>=4){
            const length=buffer.readUInt32BE(0);
            if(buffer.length<4+length) break;
            const header=JSON.parse(buffer.subarray(4,4+length).toString());
            const mediaSize=header.media_size??0;
            if(buffer.length<4+length+mediaSize) break;
            buffer=buffer.subarray(4+length+mediaSize);
            if(header.kind==='stream_hello'){
              assert.equal(header.protocol,6); assert.equal(header.contract_profile,'skulk.handshake/2');
              if(mode!=='server_streaming'){
                socket.write(native.encodeTransportV2Packet(JSON.stringify({call_id:'call',direction:'caller_to_provider',sequence:0,kind:'started'}),Buffer.alloc(0)));
                socket.write(native.encodeTransportV2Packet(JSON.stringify({call_id:'call',direction:'caller_to_provider',sequence:1,kind:'completed'}),Buffer.alloc(0)));
              }
            }else{
              assert.equal(cleaned,true,'terminal must follow language cleanup');
              packets.push(header.frame);
              if(header.frame.kind==='end') resolveDone(packets);
            }
          }
        }catch(error){rejectDone(error);socket.destroy()}
      });
      socket.on('error',rejectDone);
    });
    await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(path,resolve)});
    let session; let ticks=0; const health=setInterval(()=>ticks++,2);
    try{
      session=await native.openTransportV2Stream(startup(path),'call',mode,null);
      if(mode!=='server_streaming'){
        assert.equal(JSON.parse((await session.nextInput()).projection).kind,'started');
        assert.equal(JSON.parse((await session.nextInput()).projection).kind,'completed');
        assert.equal(await session.nextInput(),null);
      }
      await session.sendOutput(projection(1,'completed'),Buffer.alloc(0));
      await delay(40); assert.ok(ticks>1);
      cleaned=true; await session.finish();
      const packets=await Promise.race([done,delay(1000).then(()=>{throw new Error('missing cleanup ack')})]);
      assert.deepEqual(packets.map(packet=>packet.kind),['completed','end']);
    }finally{
      clearInterval(health); await session?.close();
      for(const socket of sockets)socket.destroy();
      await new Promise(resolve=>server.close(resolve)); rmSync(root,{recursive:true,force:true});
    }
  });
}


test('native control channel carries whole records and fences partial reads on close',async()=>{
  const root=mkdtempSync('/tmp/skulk-control6-');const path=join(root,'sock');
  const shutdown=fixture.cases.find(c=>c.id==='shutdown');
  let peer;let resolveReady,rejectReady;const ready=new Promise((resolve,reject)=>{resolveReady=resolve;rejectReady=reject});
  const server=createServer(socket=>{
    peer=socket;let buffer=Buffer.alloc(0);let hello=false;
    socket.on('error',rejectReady);
    socket.on('data',data=>{
      try {
        buffer=Buffer.concat([buffer,data]);
        while(buffer.length>=4) {
          const size=buffer.readUInt32BE(0);if(buffer.length<4+size)break;
          const record=JSON.parse(buffer.subarray(4,4+size));buffer=buffer.subarray(4+size);
          if(!hello) {hello=true;assert.equal(record.kind,'hello');assert.equal(record.protocol,6);socket.write(Buffer.from(shutdown.wire_hex,'hex'));}
          else {assert.equal(record.kind,'shutdown');resolveReady();socket.write(Buffer.of(0,0));}
        }
      } catch(error) {rejectReady(error);socket.destroy();}
    });
  });
  await new Promise(resolve=>server.listen(path,resolve));let channel;
  try {
    channel=await native.openTransportV2Control(startup(path),2);
    assert.equal(JSON.parse(await channel.receive()).kind,'shutdown');
    await channel.send(shutdown.source);await ready;
    const receiving=assert.rejects(channel.receive(),/closed/);
    await delay(10);await channel.close();await receiving;
    await assert.rejects(channel.send(shutdown.source),/closed/);
  } finally {await channel?.close();peer?.destroy();await new Promise(resolve=>server.close(resolve));rmSync(root,{recursive:true,force:true});}
});

import {CompositionV3Contract, CompositionV2Contract, compositionV3ProfileDigest} from '../dist/portable.js';
import {inspect} from 'node:util';
test('nullable execution authority matches every independent canonical/refusal witness', () => {
  const fixture=JSON.parse(readFileSync(new URL('../../../contracts/inference-v3-cases.json',import.meta.url),'utf8'));
  assert.equal(compositionV3ProfileDigest(), fixture.profile_digest);
  for (const c of fixture.cases) {
    const evaluate=()=> {
      let value=CompositionV3Contract.fromJson(c.name,c.source);
      if(c.action==='admit') value=CompositionV3Contract.fromJson('Context',c.parent).admit(value,CompositionV3Contract.fromJson('Policy',c.policy),c.executor,Buffer.from(c.request_hex,'hex'),c.now_unix_ms,c.scope_id);
      return Buffer.from(value.canonical()).toString();
    };
    if(c.error) assert.throws(evaluate,error=>error.message.startsWith(c.error+':')&&!error.message.includes('synthetic-do-not-echo-secret'),c.id);
    else assert.equal(evaluate(),c.canonical,c.id);
  }
  const source=fixture.cases.find(c=>c.name==='Context').source;
  const record=CompositionV3Contract.fromJson('Context',source);
  assert.equal(inspect(record),'CompositionV3Contract(Context)');
  assert.equal(JSON.stringify(record),'{"name":"Context"}');
  const bytes=record.canonical();bytes.fill(0);assert.notDeepEqual(record.canonical(),bytes);
  assert.throws(()=>CompositionV2Contract.fromJson('Context',source));
});
