import assert from 'node:assert/strict';
import {readFileSync, mkdtempSync, rmSync} from 'node:fs';
import {createRequire} from 'node:module';
import {createServer} from 'node:net';
import {join} from 'node:path';
import {test} from 'node:test';
import {setTimeout as delay} from 'node:timers/promises';
const native = createRequire(import.meta.url)('../dist/kernel.node');
const managed = JSON.parse(readFileSync(new URL('../../../contracts/managed-v2-cases.json', import.meta.url)));
const packet = (direction, sequence, kind) => ({call_id:'call', direction, sequence, kind});
function startup(path) {
  const value = JSON.parse(managed.cases.find(c=>c.name==='Startup'&&c.canonical).canonical);
  Object.assign(value,{socket:path,stream_socket:path,token:'a'.repeat(64)});
  return JSON.stringify(value);
}
for (const mode of ['server_streaming','client_streaming','bidirectional']) {
  test(`${mode} caller preserves media and waits for the exact end`, async () => {
    const root = mkdtempSync('/tmp/caller-'), path = join(root,'sock');
    const sockets = new Set(); let resolveDone, rejectDone, releaseEnd;
    const done = new Promise((resolve,reject)=>{resolveDone=resolve;rejectDone=reject});
    const server = createServer(socket=>{
      sockets.add(socket); socket.on('close',()=>sockets.delete(socket));
      let buffer=Buffer.alloc(0), input=[];
      const respond=()=>{
        let sequence=1;
        if(mode!=='client_streaming') {
          const frame={...packet('provider_to_caller',sequence++,'chunk'),payload:{fraction:1.0},media:{kind:'inline',media_type:'audio/pcm'}};
          socket.write(native.encodeTransportV2Packet(JSON.stringify(frame),Buffer.of(255,0)));
        }
        socket.write(native.encodeTransportV2Packet(JSON.stringify(packet('provider_to_caller',sequence,'completed')),Buffer.alloc(0)));
        releaseEnd=()=>socket.write(native.encodeTransportV2Packet('{"kind":"end","call_id":"call"}',Buffer.alloc(0)));
      };
      socket.on('data',data=>{
        try {
          buffer=Buffer.concat([buffer,data]);
          while(buffer.length>=4) {
            const size=buffer.readUInt32BE(); if(buffer.length<4+size) break;
            const header=JSON.parse(buffer.subarray(4,4+size));
            const mediaSize=header.media_size??0;if(buffer.length<4+size+mediaSize) break;
            const media=buffer.subarray(4+size,4+size+mediaSize);buffer=buffer.subarray(4+size+mediaSize);
            if(header.kind==='stream_hello') {
              assert.equal(header.call_id,'call');assert.equal(header.token,'a'.repeat(64));
              assert.equal(header.protocol,6);if(mode==='server_streaming')respond();
            } else {
              input.push(header.frame.kind);
              assert.deepEqual(media,header.frame.kind==='chunk'?Buffer.of(0,255):Buffer.alloc(0));
              if(header.frame.kind==='completed'){assert.deepEqual(input,['started','chunk','completed']);respond();}
            }
          }
        }catch(error){rejectDone(error);socket.destroy()}
      });
      socket.on('end',()=>resolveDone());socket.on('error',rejectDone);
    });
    await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(path,resolve)});
    let session;
    try {
      session=await native.openTransportV2ClientStream(startup(path),'call',mode,null);
      if(mode!=='server_streaming') {
        await session.sendInput(JSON.stringify(packet('caller_to_provider',0,'started')),Buffer.alloc(0));
        await session.sendInput(JSON.stringify({...packet('caller_to_provider',1,'chunk'),media:{kind:'inline',media_type:'audio/pcm'}}),Buffer.of(0,255));
        await session.sendInput(JSON.stringify(packet('caller_to_provider',2,'completed')),Buffer.alloc(0));
      }
      if(mode!=='client_streaming') assert.deepEqual((await session.nextOutput()).media,Buffer.of(255,0));
      assert.equal(JSON.parse((await session.nextOutput()).projection).kind,'completed');
      let ended=false;const ending=session.nextOutput().then(value=>{ended=true;return value});
      await delay(20);assert.equal(ended,false);releaseEnd();assert.equal(await ending,null);
      await session.finish();await done;await Promise.all([session.close(),session.close()]);
      await assert.rejects(session.nextOutput(),/closed/);
    }finally{
      await session?.close();for(const socket of sockets)socket.destroy();
      await new Promise(resolve=>server.close(resolve));rmSync(root,{recursive:true,force:true});
    }
  });
}

test('typed caller facade closes a pending read without blocking Node health', async () => {
  const {ManagedV2Contract,openTransportV2ClientStream}=await import('../dist/portable.js');
  const root=mkdtempSync('/tmp/caller-'),path=join(root,'sock');
  const sockets=new Set();
  const server=createServer(socket=>{
    sockets.add(socket);socket.on('close',()=>sockets.delete(socket));
    socket.on('data',()=>{});socket.on('error',()=>{});
  });
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(path,resolve)});
  let session,ticks=0;const health=setInterval(()=>ticks++,2);
  try {
    session=await openTransportV2ClientStream(ManagedV2Contract.fromJson('Startup',startup(path)),'call','bidirectional');
    const pending=assert.rejects(session.nextOutput(),/closed/);
    await delay(30);assert.ok(ticks>1);
    await Promise.all([session.close(),session.close()]);await pending;
    await assert.rejects(session.sendInput(JSON.stringify(packet('caller_to_provider',0,'started')),new Uint8Array()),/closed/);
  }finally{
    clearInterval(health);await session?.close();for(const socket of sockets)socket.destroy();
    await new Promise(resolve=>server.close(resolve));rmSync(root,{recursive:true,force:true});
  }
});
