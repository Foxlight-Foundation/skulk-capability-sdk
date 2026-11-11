import assert from 'node:assert/strict';
import {readFileSync,mkdtempSync,rmSync} from 'node:fs';
import {createRequire} from 'node:module';
import {createServer} from 'node:net';
import {join} from 'node:path';
import {test} from 'node:test';
import {setTimeout as delay} from 'node:timers/promises';
const native=createRequire(import.meta.url)('../dist/kernel.node');
const fixture=JSON.parse(readFileSync(new URL('../../../contracts/transport-v1-cases.json',import.meta.url),'utf8'));
const managed=JSON.parse(readFileSync(new URL('../../../contracts/managed-v1-cases.json',import.meta.url),'utf8'));
const projection=(sequence,kind)=>JSON.stringify({call_id:'call',direction:'provider_to_caller',sequence,kind});
function startup(path) {const value=JSON.parse(managed.cases.find(c=>c.name==='Startup'&&c.canonical).canonical); Object.assign(value,{socket:path,stream_socket:path,token:'a'.repeat(64)});return JSON.stringify(value);}
test('all independent full-wire observations match the Node ABI',()=>{
  assert.equal(native.transportProfileDigest(),fixture.profile_digest);
  for(const item of fixture.cases) {
    const encode=()=>item.action==='control'?native.encodePortableControl(item.source):native.encodePortablePacket(item.source,Buffer.from(item.media_hex,'hex'));
    if(!item.valid) {assert.throws(encode,undefined,item.id);continue;}
    const wire=encode();assert.equal(wire.toString('hex'),item.wire_hex,item.id);
    if(item.action==='control') assert.deepEqual(JSON.parse(native.decodePortableControl(wire)),JSON.parse(item.source));
    else assert.equal(native.decodePortablePacket(wire).media.toString('hex'),item.media_hex);
    for(const bad of [wire.subarray(0,wire.length-1),Buffer.concat([wire,Buffer.of(0)])]) {
      assert.throws(()=>item.action==='control'?native.decodePortableControl(bad):native.decodePortablePacket(bad));
    }
  }
  assert.throws(()=>native.encodePortablePacket('{"kind":"end","call_id":"\\ud800"}',Buffer.alloc(0)));
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
              assert.equal(header.protocol,5); assert.equal(header.contract_profile,'skulk.handshake/1');
              if(mode!=='server_streaming'){
                socket.write(native.encodePortablePacket(JSON.stringify({call_id:'call',direction:'caller_to_provider',sequence:0,kind:'started'}),Buffer.alloc(0)));
                socket.write(native.encodePortablePacket(JSON.stringify({call_id:'call',direction:'caller_to_provider',sequence:1,kind:'completed'}),Buffer.alloc(0)));
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
      session=await native.openPortableStream(startup(path),'call',mode,2);
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
  const root=mkdtempSync('/tmp/skulk-control5-');const path=join(root,'sock');
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
          if(!hello) {hello=true;assert.equal(record.kind,'hello');assert.equal(record.protocol,5);socket.write(Buffer.from(shutdown.wire_hex,'hex'));}
          else {assert.equal(record.kind,'shutdown');resolveReady();socket.write(Buffer.of(0,0));}
        }
      } catch(error) {rejectReady(error);socket.destroy();}
    });
  });
  await new Promise(resolve=>server.listen(path,resolve));let channel;
  try {
    channel=await native.openPortableControl(startup(path),2);
    assert.equal(JSON.parse(await channel.receive()).kind,'shutdown');
    await channel.send(shutdown.source);await ready;
    const receiving=assert.rejects(channel.receive(),/closed/);
    await delay(10);await channel.close();await receiving;
    await assert.rejects(channel.send(shutdown.source),/closed/);
  } finally {await channel?.close();peer?.destroy();await new Promise(resolve=>server.close(resolve));rmSync(root,{recursive:true,force:true});}
});
