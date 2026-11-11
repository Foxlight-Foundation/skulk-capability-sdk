/** Synthetic full wire observations from the actual source or installed facade. */
import {createInterface} from 'node:readline';
const native=await import(process.argv[2]??new URL('../dist/portable.js',import.meta.url).href);
for await(const line of createInterface({input:process.stdin,crlfDelay:Infinity})) {
  let response;
  try {
    const request=JSON.parse(line);
    const bytes=request.action==='control'?native.encodePortableControl(request.source):native.encodePortablePacket(request.source,Buffer.from(request.media_hex,'hex'));
    response={canonical:Buffer.from(bytes).toString('hex'),error:null};
  } catch(error) {
    if(String(error).includes('synthetic-do-not-echo-secret')) throw error;
    response={canonical:null,error:'transport_invalid'};
  }
  process.stdout.write(JSON.stringify(response)+'\n');
}
