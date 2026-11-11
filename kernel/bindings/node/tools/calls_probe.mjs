import {createInterface} from 'node:readline';
const native=await import(process.argv[2] ?? new URL('../dist/portable.js',import.meta.url).href);
for await (const line of createInterface({input:process.stdin,crlfDelay:Infinity})) {
  let response;
  try {
    const request=JSON.parse(line);
    let wire;
    if(request.action==='encode') wire=native.encodeCallFrame(request.source,Buffer.from(request.payload_hex,'hex'));
    else {
      const frame=native.decodeCallFrame(Buffer.from(request.packet_hex,'hex'));
      wire=native.encodeCallFrame(Buffer.from(frame.metadata).toString('utf8'),frame.payload);
    }
    response={canonical:Buffer.from(wire).toString('hex'),error:null};
  } catch {response={canonical:null,error:'calls_invalid'};}
  process.stdout.write(JSON.stringify(response)+'\n');
}
