import {createHash} from 'node:crypto';
import {constants, closeSync, fstatSync, readSync} from 'node:fs';
import {open} from 'node:fs/promises';
import {isAbsolute} from 'node:path';
import {pathToFileURL} from 'node:url';
import {CapabilityApp} from './application.js';
import type {PythonCredentialHelper} from './credentials.js';
import {native} from './native.js';
import {encodeJson, object, parseJson, text, utf8} from './json.js';

/** Installer-selected language runtime; metadata does not grant caller rights. */
export interface ApplicationRuntime { readonly credentialHelper: PythonCredentialHelper; }
/** A public app module exports this factory as its default export. */
export type ApplicationFactory = (runtime: ApplicationRuntime) => CapabilityApp | Promise<CapabilityApp>;

const schema = new native.PortableSchema(utf8(encodeJson({type:'object',additionalProperties:false,
  required:['format','entry','entrySha256','credentialHelper'],properties:{
    format:{const:1},entry:{type:'string',minLength:1,maxLength:4096},
    entrySha256:{type:'string',pattern:'^[a-f0-9]{64}$'},
    credentialHelper:{type:'object',additionalProperties:false,required:['executable','executableSha256'],properties:{
      executable:{type:'string',minLength:1,maxLength:4096},executableSha256:{type:'string',pattern:'^[a-f0-9]{64}$'},
    }},
  }})));

function selection(descriptor: number): string {
  try {
    const info = fstatSync(descriptor);
    if (!info.isFile() || info.uid !== process.geteuid?.() || (info.mode & 0o077) !== 0) throw new Error('Selected application requires a private regular descriptor');
    const bytes = Buffer.alloc(16385); let offset = 0;
    while (offset < bytes.length) {
      const count = readSync(descriptor, bytes, offset, bytes.length-offset, null);
      if (count === 0) break;
      offset += count;
    }
    if (offset > 16384) throw new Error('Selected application metadata exceeds its bound');
    const raw = utf8(bytes.subarray(0,offset));
    if (!schema.matches(raw)) throw new Error('Selected application metadata differs');
    return raw;
  } finally { closeSync(descriptor); }
}

/** Run a factory only after private runtime selection and exact entry measurement.
 * The Python bootstrap verifies the complete asset tree first. This last check
 * binds the imported entry again, without interpreting managed startup in Python.
 */
export async function serveSelectedApplication(configurationDescriptor: number, runtimeDescriptor: number): Promise<void> {
  let consumed = false;
  try {
    const value = object(parseJson(selection(runtimeDescriptor))), entry = text(value.entry), helper = object(value.credentialHelper);
    if (!isAbsolute(entry) || !isAbsolute(text(helper.executable))) throw new Error('Selected application requires absolute runtime paths');
    const file = await open(entry, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    try {
      const info = await file.stat();
      if (!info.isFile() || ![0,process.geteuid?.()].includes(info.uid) || (info.mode & 0o022) !== 0 || info.size > 64*1024*1024) throw new Error('Selected application entry has unsafe custody');
      const hash = createHash('sha256'); let size = 0;
      for await (const bytes of file.createReadStream({autoClose:false})) {
        if (!Buffer.isBuffer(bytes)) throw new Error('Selected application entry requires bytes');
        size += bytes.length;
        if (size > 64*1024*1024) throw new Error('Selected application entry exceeds its bound');
        hash.update(bytes);
      }
      if (size !== info.size || hash.digest('hex') !== value.entrySha256) throw new Error('Selected application entry differs');
    } finally { await file.close(); }
    const module: unknown = await import(pathToFileURL(entry).href);
    if (typeof module !== 'object' || module === null || !('default' in module) || typeof module.default !== 'function') throw new Error('Selected application must export a factory');
    const factory = module.default as ApplicationFactory;
    const runtime: ApplicationRuntime = Object.freeze({credentialHelper:Object.freeze({executable:text(helper.executable),executableSha256:text(helper.executableSha256)})});
    const app = await factory(runtime);
    if (!(app instanceof CapabilityApp)) throw new Error('Selected application must return CapabilityApp');
    consumed = true;
    await app.serve(configurationDescriptor);
  } finally { if (!consumed) closeSync(configurationDescriptor); }
}
