import assert from 'node:assert/strict';
import {readFileSync,mkdtempSync,rmSync} from 'node:fs';
import {createRequire} from 'node:module';
import {createServer} from 'node:net';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {test} from 'node:test';
const native=createRequire(import.meta.url)('../dist/kernel.node');
const cases=JSON.parse(readFileSync(new URL('../../../contracts/calls-v1-cases.json',import.meta.url),'utf8'));
const managed=JSON.parse(readFileSync(new URL('../../../contracts/managed-v1-cases.json',import.meta.url),'utf8'));
function startup(path) {const value=JSON.parse(managed.cases.find(c=>c.name==='Startup'&&c.canonical).canonical);Object.assign(value,{socket:path,stream_socket:path,token:'a'.repeat(64)});return JSON.stringify(value);}
function invoke() {const item=cases.cases.find(c=>c.id==='invoke-unary');return [JSON.parse(item.source),Buffer.from(item.payload_hex,'hex')];}
test('all independent composed-call wire observations match the native Node ABI',()=>{
  assert.equal(native.callsProfileDigest(),cases.profile_digest);
  for(const item of cases.cases) {
    const encode=()=>{
      if(item.action==='encode')return native.encodeCallFrame(item.source,Buffer.from(item.payload_hex,'hex'));
      const frame=native.decodeCallFrame(Buffer.from(item.packet_hex,'hex'));
      return native.encodeCallFrame(frame.metadata.toString('utf8'),frame.payload);
    };
    if(item.error!==null)assert.throws(encode,undefined,item.id);
    else assert.equal(encode().toString('hex'),item.canonical,item.id);
  }
});
test('request binding preserves raw JSON whitespace and numeric spelling',()=>{
  const [metadata,payload]=invoke();
  const frame=native.decodeCallFrame(native.encodeCallFrame(JSON.stringify(metadata),payload));
  assert.deepEqual(frame.payload,payload);
  assert.throws(()=>native.encodeCallFrame(JSON.stringify(metadata),Buffer.from(JSON.stringify(JSON.parse(payload)))));
});
test('exact raw payload boundary is distinct from bounded metadata',()=>{
  const [metadata]=invoke();
  const payload=Buffer.concat([Buffer.from('{"x":"'),Buffer.alloc(16777216-8,120),Buffer.from('"}')]);
  metadata.payload_size=payload.length;metadata.context.request_sha256=createHash('sha256').update(payload).digest('hex');
  const frame=native.decodeCallFrame(native.encodeCallFrame(JSON.stringify(metadata),payload));
  assert.deepEqual(frame.payload,payload);
  assert.throws(()=>native.encodeCallFrame(JSON.stringify(metadata),Buffer.concat([payload,Buffer.of(32)])));
});
test('native call channel uses protected hello and exchanges exact raw bodies',{timeout:5000},async()=>{
  const root=mkdtempSync('/tmp/calls-node-');const path=join(root,'sock');
  const item=cases.cases.find(c=>c.id==='invoke-unary');const sockets=new Set();
  let resolveDone,rejectDone;
  const done=new Promise((resolve,reject)=>{resolveDone=resolve;rejectDone=reject});
  const server=createServer(socket=>{
    sockets.add(socket);socket.on('close',()=>sockets.delete(socket));
    let buffer=Buffer.alloc(0),hello=false;
    socket.on('data',data=>{
      try {
        buffer=Buffer.concat([buffer,data]);
        while(buffer.length>=4) {
          const length=buffer.readUInt32BE(0);assert.ok(length>=2&&length<=262144);
          if(buffer.length<4+length)break;
          const header=JSON.parse(buffer.subarray(4,4+length));
          const payloadSize=hello?header.payload_size:0;
          if(buffer.length<4+length+payloadSize)break;
          const payload=buffer.subarray(4+length,4+length+payloadSize);
          buffer=buffer.subarray(4+length+payloadSize);
          if(!hello){
            assert.equal(header.contract_profile,'skulk.handshake/1');assert.equal(header.token,'a'.repeat(64));
            native.managedAdmitHelloJson('Startup',startup(path),JSON.stringify(header));
            hello=true;socket.write(Buffer.from(item.canonical,'hex'));
          }else{assert.equal(header.kind,'result');assert.equal(header.call_id,'call-1');resolveDone(Buffer.from(payload));}
        }
      }catch(error){rejectDone(error);socket.destroy();}
    });socket.on('error',rejectDone);
  });
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(path,resolve)});
  let channel;
  try {
    channel=await native.openCallControl(startup(path),3);
    const frame=await channel.receive();assert.deepEqual(frame.payload,Buffer.from(item.payload_hex,'hex'));
    const payload=Buffer.from('{ "ok" : true }');
    await channel.send(JSON.stringify({contract_profile:'skulk.calls/1',profile_digest:native.callsProfileDigest(),protocol:1,kind:'result',call_id:'call-1',error:null,payload_size:payload.length}),payload);
    assert.deepEqual(await done,payload);
    for(const socket of sockets)socket.write(Buffer.of(0,0));
    const pending=assert.rejects(channel.receive());
    await channel.close();await pending;
    const shutdown=cases.cases.find(c=>c.id==='shutdown');
    await assert.rejects(async()=>channel.send(shutdown.source,Buffer.alloc(0)));
  }finally{
    await channel?.close();for(const socket of sockets)socket.destroy();
    await new Promise(resolve=>server.close(resolve));rmSync(root,{recursive:true,force:true});
  }
});
test('native bounded header hint never substitutes for actual body admission',()=>{
  const [metadata,payload]=invoke();const source=JSON.stringify(metadata);
  assert.equal(native.callPayloadSize(source),payload.length);
  const changed=Buffer.from(payload.toString('utf8').replace('hello','other'));
  assert.equal(changed.length,payload.length);assert.throws(()=>native.encodeCallFrame(source,changed));
  metadata.payload_size=16777216;assert.equal(native.callPayloadSize(JSON.stringify(metadata)),16777216);
  metadata.payload_size++;assert.throws(()=>native.callPayloadSize(JSON.stringify(metadata)));
  const [bad]=invoke();bad.context.caller_id='';assert.throws(()=>native.callPayloadSize(JSON.stringify(bad)));
});
