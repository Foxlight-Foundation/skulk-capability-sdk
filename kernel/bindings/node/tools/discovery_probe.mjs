import {createInterface} from 'node:readline';
const path=process.argv[2] ? new URL(process.argv[2]) : new URL('../dist/loader.cjs',import.meta.url);
const native=(await import(path.href)).default;
for await(const line of createInterface({input:process.stdin,crlfDelay:Infinity})) {
 let result;
 try {
  const v=JSON.parse(line);let value;
  switch(v.action) {
   case 'request':value=native.discoveryRequest(v.startup,v.context,v.parent,v.sequence).toString('hex');break;
   case 'admit':value=native.admitDiscoveryRequest(v.startup,Buffer.from(v.packet_hex,'hex')).toString('utf8');break;
   case 'reply':value=native.discoveryReply(v.startup,v.source,Buffer.from(v.payload_hex,'hex'),v.code).toString('hex');break;
   case 'response':{const r=native.discoveryResponse(v.startup,v.source,Buffer.from(v.packet_hex,'hex'));value=JSON.stringify({code:r.error??null,payload_hex:r.payload.toString('hex')});break;}
   case 'hint':value=String(native.discoveryPayloadSize(v.source));break;
   default:throw new Error('Invalid action');
  }
  result={canonical:value,error:null};
 }catch {result={canonical:null,error:'discovery_invalid'};}
 console.log(JSON.stringify(result));
}
