import assert from 'node:assert/strict';
import {readFileSync,mkdtempSync,rmSync} from 'node:fs';
import {createRequire} from 'node:module';
import {createServer} from 'node:net';
import {join} from 'node:path';
import {test} from 'node:test';
import {setTimeout as delay} from 'node:timers/promises';
const native=createRequire(import.meta.url)('../dist/kernel.node');
const reference=JSON.parse(readFileSync(new URL('../../../../tests/fixtures/author-baseline/sdk-0.5.0-streams.json',import.meta.url),'utf8'));
const hello=JSON.stringify({kind:'stream_hello',protocol:4,call_id:'call',token:'0'.repeat(64),capability_node_id:'node',manifest_sha256:'a'.repeat(64)});
const projection=(sequence,kind)=>JSON.stringify({call_id:'call',direction:'provider_to_caller',sequence,kind});

test('Node decodes frozen Python packets and preserves exact numeric text and binary media',()=>{
  for(const item of reference.packets){
    const packet=native.decodeStreamPacket(Buffer.from(item.wire_hex,'hex'));
    assert.deepEqual(packet.media,Buffer.from(item.media_hex,'hex'));
    assert.deepEqual(JSON.parse(packet.projection),item.projection);
    assert.deepEqual(native.decodeStreamPacket(native.encodeStreamPacket(packet.projection,packet.media)),packet);
  }
  const numeric=native.decodeStreamPacket(Buffer.from(reference.packets[2].wire_hex,'hex')).projection;
  assert.ok(numeric.includes('"float":1.0'));
  assert.ok(numeric.includes('18446744073709551616'));
  assert.ok(numeric.includes('"zero":-0.0'));
  const metadata=JSON.stringify({call_id:'call',direction:'provider_to_caller',sequence:1,kind:'chunk',media:{kind:'inline',media_type:'audio/pcm'}});
  const media=Buffer.alloc(1048576,255);
  assert.deepEqual(native.decodeStreamPacket(native.encodeStreamPacket(metadata,media)).media,media);
  assert.throws(()=>native.encodeStreamPacket(metadata,Buffer.alloc(1048577)),/media exceeds/);
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
              assert.equal(header.protocol,4);
              if(mode!=='server_streaming'){
                socket.write(Buffer.from(reference.packets[0].wire_hex,'hex'));
                socket.write(Buffer.from(reference.packets[1].wire_hex,'hex'));
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
      session=await native.openStream(path,hello,'call',mode,2);
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

test('native deadline closes a retained idle connection and rejects protocol downgrade',async()=>{
  const root=mkdtempSync('/tmp/skulk-node-stream-');const path=join(root,'sock');
  let resolveEnd;const ended=new Promise(resolve=>{resolveEnd=resolve});
  const sockets=new Set();
  const server=createServer(socket=>{sockets.add(socket);socket.on('data',()=>{});socket.on('end',resolveEnd);socket.on('error',()=>{});socket.on('close',()=>sockets.delete(socket))});
  await new Promise(resolve=>server.listen(path,resolve));let session;
  try{
    await assert.rejects(native.openStream(path,hello.replace('"protocol":4','"protocol":3'),'call','bidirectional',1),/invalid stream/);
    session=await native.openStream(path,hello,'call','bidirectional',0.05);
    await assert.rejects(session.nextInput(),/deadline/);
    await Promise.race([ended,delay(1000).then(()=>{throw new Error('retained socket did not close')})]);
  }finally{await session?.close();for(const socket of sockets)socket.destroy();await new Promise(resolve=>server.close(resolve));rmSync(root,{recursive:true,force:true})}
});
