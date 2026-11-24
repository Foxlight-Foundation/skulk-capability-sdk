import {createHash} from 'node:crypto';
import {inspect} from 'node:util';
import models from './job-schema-models.json' with {type: 'json'};
import {native} from './native.js';
import {array, copyJson, encodeJson, object, parseJson, text, utf8, type JsonObject, type JsonValue} from './json.js';

export const MAX_JOB_OFFSET = 9007199254740991;
export const JOB_PAGE_SIZE = 16;
type Mutable = Record<string, JsonValue>;
const mutable = (value: JsonObject): Mutable => Object.assign(Object.create(null) as Mutable, object(copyJson(value)));

/** Hash the same compiled portable canonical bytes used by the Python facade. */
export function jobDigest(value: JsonValue): string { return createHash('sha256').update(encodeJson(value)).digest('hex'); }

function hoist(schema: Mutable, definitions: Mutable): void {
  const nested = schema.$defs; delete schema.$defs;
  if (nested === undefined || nested === null || Array.isArray(nested) || typeof nested !== 'object') return;
  for (const [name, definition] of Object.entries(nested)) {
    if (Object.hasOwn(definitions, name) && !encodeJson(definitions[name] ?? null).equals(encodeJson(definition))) {
      throw new TypeError('Conflicting job schema definition');
    }
    definitions[name] = definition;
  }
}

/** Compose the frozen Python operation envelopes from shared model templates. */
function schemas(input: JsonObject, result: JsonObject): readonly [JsonObject, JsonObject] {
  const inputBody = mutable(input), callDefinitions = mutable({});
  hoist(inputBody, callDefinitions);
  const identifier = {type: 'string', minLength: 1, maxLength: 128, pattern: '^[a-zA-Z0-9._:@-]+$'};
  const offset = {type: 'integer', minimum: 0, maximum: MAX_JOB_OFFSET};
  const verb = (op: string, required: readonly string[], extra: JsonObject): JsonObject => ({
    type: 'object', properties: {op: {const: op}, ...extra}, required: ['op', ...required], additionalProperties: false,
  });
  const call: Mutable = {title: 'OperationCall', oneOf: [
    verb('start', ['input'], {input: inputBody, operation_id: identifier}),
    verb('plan', ['input'], {input: inputBody}),
    verb('status', ['operation_id'], {operation_id: identifier}),
    verb('cancel', ['operation_id'], {operation_id: identifier}),
    verb('log', ['operation_id'], {operation_id: identifier, offset}),
    verb('list', [], {offset}),
  ]};
  if (Object.keys(callDefinitions).length !== 0) call.$defs = callDefinitions;
  const template = object(parseJson(JSON.stringify(models)));
  const status = mutable(object(template.status)), resultBody = mutable(result);
  status.properties = {...object(status.properties), result: {anyOf: [resultBody, {type: 'null'}], default: null}};
  const definitions = mutable({}); hoist(resultBody, definitions);
  const variants = [status, ...['page', 'log', 'plan'].map(name => mutable(object(template[name])))];
  for (const variant of variants) hoist(variant, definitions);
  if (Object.hasOwn(definitions, 'OperationStatus')) definitions.OperationStatus = status;
  return [call, {oneOf: variants, ...(Object.keys(definitions).length === 0 ? {} : {$defs: definitions})}];
}

/** Author-declared schemas and processing domain for a read/inference job pair. */
export interface JobDefinitionOptions {
  readonly id: string;
  readonly version: string;
  readonly title: string;
  readonly description: string;
  readonly inputSchema: JsonObject;
  readonly resultSchema: JsonObject;
  readonly processingDomain?: 'local' | 'private_site' | 'external';
}

/** Compiled declarations; neither descriptors nor exported metadata grant rights. */
export class JobDefinition {
  readonly #canonical: Buffer;
  private constructor(canonical: Buffer) { this.#canonical = Buffer.from(canonical); Object.freeze(this); }
  /** Create the same read/work descriptors and revisions as Python JobDefinition. */
  static create(options: JobDefinitionOptions): JobDefinition {
    const [call, output] = schemas(options.inputSchema, options.resultSchema);
    const descriptors: JsonObject[] = [];
    for (const suffix of ['read', 'work'] as const) {
      const verbs = suffix === 'read' ? ['status', 'list', 'log'] : ['start', 'cancel', 'plan'];
      const input = {...call, oneOf: array(call.oneOf).filter(variant => verbs.includes(text(object(object(object(variant).properties).op).const)))};
      const descriptor = {contract_profile: 'skulk.portable/2', profile_digest: native.portableV2ProfileDigest(),
        id: options.id + '.' + suffix, version: options.version, title: options.title, description: options.description,
        behavior: suffix === 'read' ? 'read' : 'inference', processing_domain: suffix === 'read' ? 'local' : options.processingDomain ?? 'local',
        io_mode: 'unary', input_schema: input, output_schema: output, annotations: {'skulk.operation': '1', 'skulk.jobs': '1'}};
      descriptors.push(object(parseJson(utf8(native.portableV2ContractJson('Descriptor', utf8(encodeJson(descriptor)))))));
    }
    return new JobDefinition(encodeJson({descriptors, result_schema: options.resultSchema}));
  }
  /** Return independent declarations for the signed manifest. */
  descriptors(): readonly JsonObject[] { return array(object(parseJson(utf8(this.#canonical))).descriptors).map(value => copyJson(object(value))); }
  /** Return an owned result-schema copy for native callback validation. */
  resultSchema(): JsonObject { return copyJson(object(object(parseJson(utf8(this.#canonical))).result_schema)); }
  toJSON(): string { return 'JobDefinition'; }
  [inspect.custom](): string { return 'JobDefinition'; }
}

/** Extract one field without converting raw JSON floating-point tokens to JS integers.
 * The native reader validates and canonicalizes first; this scanner only projects
 * an existing field. It is not a JSON or operation validator.
 */
export function jobFieldSource(source: string, field: string): string {
  const canonical = utf8(native.portableCanonicalJson(source));
  const stringEnd = (start: number): number => {
    for (let i = start + 1; i < canonical.length; i++) {
      if (canonical[i] === '\\') i++;
      else if (canonical[i] === '"') return i + 1;
    }
    throw new TypeError('Native job field unavailable');
  };
  if (canonical[0] !== '{') throw new TypeError('Native job object required');
  let at = 1;
  while (at < canonical.length && canonical[at] !== '}') {
    const keyEnd = stringEnd(at), key: unknown = JSON.parse(canonical.slice(at, keyEnd));
    if (canonical[keyEnd] !== ':') throw new TypeError('Native job field unavailable');
    const start = keyEnd + 1; let end = start, depth = 0;
    for (; end < canonical.length; end++) {
      const c = canonical[end];
      if (c === '"') { end = stringEnd(end) - 1; continue; }
      if (c === '{' || c === '[') depth++;
      else if (c === '}' || c === ']') { if (depth === 0) break; depth--; }
      else if (c === ',' && depth === 0) break;
    }
    if (key === field) return canonical.slice(start, end);
    at = end + 1;
  }
  throw new TypeError('Native job field unavailable');
}
