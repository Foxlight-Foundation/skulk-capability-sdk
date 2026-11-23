import assert from 'node:assert/strict';
import {readFileSync, mkdtempSync, writeFileSync, rmSync} from 'node:fs';
import {createServer} from 'node:http';
import {createServer as createHttpsServer} from 'node:https';
import {execFileSync, spawn} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {once} from 'node:events';
import test from 'node:test';
import {CapabilityDirectory, DiscoveryError, discoverCapabilities} from '../dist/discovery.js';
import {native} from '../dist/native.js';

const fixture = JSON.parse(readFileSync(new URL('../../../tests_native/discovery-fixture.json', import.meta.url), 'utf8'));
const document = values => Buffer.from(JSON.stringify(values));

test('shared byte-level metadata decisions agree with Python', () => {
  const cases = JSON.parse(readFileSync(new URL('../../../tests_native/discovery-cases.json', import.meta.url), 'utf8'));
  for (const value of cases) {
    if (value.code === null) CapabilityDirectory.fromJson(Buffer.from(value.source));
    else assert.throws(() => CapabilityDirectory.fromJson(Buffer.from(value.source)), error => error instanceof DiscoveryError && error.code === value.code, value.name);
  }
});

test('discovery retains exact owned descriptors and null inference lifetime', () => {
  const directory = CapabilityDirectory.fromJson(document([fixture]));
  const offer = directory.select('managed.example', 'example', '1.0.0');
  assert.equal(offer.inferenceSeconds, null);
  assert.equal(offer.ioMode, 'unary');
  assert.equal(offer.runtimeDigest, 'a'.repeat(64));
  const descriptor = offer.endpoint.descriptor(); descriptor.behavior = 'execute';
  assert.equal(offer.endpoint.descriptor().behavior, 'inference');
  assert.ok(Object.isFrozen(directory) && Object.isFrozen(directory.offers) && Object.isFrozen(offer));
  assert.deepEqual(CapabilityDirectory.fromJson(Buffer.from('[]')).offers, []);
  assert.throws(() => directory.select('managed.other', 'example', '1.0.0'), /exactly one/);
});

test('changed and future metadata refuse atomically with sanitized failures', () => {
  const changes = [
    [['runtime_digest'], 'x'.repeat(64)], [['incarnation'], 'b'.repeat(64)],
    [['plugin_id'], 'invalid/provider'], [['target', 'node_id'], 'bad node'],
    [['target', 'capability_id'], 'other'], [['target', 'version'], '9.0.0'],
    [['target', 'descriptor_revision'], '0'.repeat(64)], [['target', 'behavior'], 'read'],
    [['target', 'processing_domain'], 'local'], [['target', 'extra'], true],
    [['extra'], true], [['stream_seconds'], true], [['cleanup_seconds'], 0],
    [['startup_seconds'], -1], [['inference_seconds'], false],
    [['descriptor', 'behavior'], 'execute'], [['descriptor', 'unknown_field'], 'private-response-marker'],
  ];
  for (const [path, value] of changes) {
    const offer = structuredClone(fixture); let parent = offer;
    for (const key of path.slice(0, -1)) parent = parent[key];
    parent[path.at(-1)] = value;
    assert.throws(() => CapabilityDirectory.fromJson(document([offer])), error => error instanceof DiscoveryError && error.code === 'invalid_directory' && !error.message.includes('private-response-marker'));
  }
  assert.throws(() => CapabilityDirectory.fromJson(document([fixture, fixture])), DiscoveryError);
  const duplicate = document([fixture]).toString().replace('"plugin_id":', '"plugin_id":"managed.other","plugin_id":');
  assert.throws(() => CapabilityDirectory.fromJson(Buffer.from(duplicate)), DiscoveryError);
});

test('directory metadata can exceed the portable payload bound', () => {
  const offers = Array.from({length: 120}, (_, index) => {
    const value = structuredClone(fixture); value.target.node_id = `node-${index}`; return value;
  });
  assert.ok(document(offers).length > 65536);
  const directory = CapabilityDirectory.fromJson(document(offers));
  assert.equal(directory.offers.length, 120);
  assert.throws(() => directory.select('managed.example', 'example', '1.0.0'), /exactly one/);
  assert.equal(directory.select('managed.example', 'example', '1.0.0', 'node-2').endpoint.nodeId, 'node-2');
});

test('a descriptor keeps the compiled contract bound rather than the payload bound', () => {
  const offer = structuredClone(fixture);
  for (const key of ['input_schema', 'output_schema']) offer.descriptor[key] = {type: 'object', description: 'x'.repeat(35000)};
  offer.target.descriptor_revision = native.portableV2ContractDigest('Descriptor', JSON.stringify(offer.descriptor));
  assert.equal(CapabilityDirectory.fromJson(document([offer])).offers.length, 1);
});

test('all four modes retain schemas and explicit owner ceilings', () => {
  for (const mode of ['unary', 'server_streaming', 'client_streaming', 'bidirectional']) {
    const offer = structuredClone(fixture);
    offer.descriptor.io_mode = mode;
    if (mode === 'client_streaming' || mode === 'bidirectional') offer.descriptor.input_chunk_schema = {type: 'object', required: ['input']};
    if (mode === 'server_streaming' || mode === 'bidirectional') offer.descriptor.output_chunk_schema = {type: 'object', required: ['output']};
    if (mode !== 'unary') offer.descriptor.output_schema = {type: 'object', required: ['output']};
    offer.target.descriptor_revision = native.portableV2ContractDigest('Descriptor', JSON.stringify(offer.descriptor));
    offer.inference_seconds = 9.25;
    const result = CapabilityDirectory.fromJson(document([offer])).offers[0];
    assert.equal(result.ioMode, mode);
    assert.equal(result.inferenceSeconds, 9.25);
  }
});

async function serving(body, work, {status = 200, stalled = false, headers = {}} = {}) {
  const requests = [];
  const closed = [];
  let onRequest;
  const received = new Promise(resolve => {onRequest = resolve;});
  const server = createServer((request, response) => {
    requests.push({url: request.url, bearer: request.headers.authorization});
    onRequest();
    closed.push(new Promise(resolve => request.socket.once('close', resolve)));
    request.on('error', () => {}); // A peer abort is the expected cleanup witness.
    response.writeHead(status, {'content-type': 'application/json', ...headers});
    if (stalled) response.flushHeaders(); else response.end(body);
  });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  try { return await work(`http://127.0.0.1:${server.address().port}`, requests, closed, received); }
  finally { server.closeAllConnections(); server.close(); await once(server, 'close'); }
}

test('real HTTP discovery sends one bearer-authenticated metadata read', async () => {
  await serving(document([fixture]), async (base, requests) => {
    const result = await discoverCapabilities(base, 'synthetic-client-key');
    assert.equal(result.offers.length, 1);
    assert.deepEqual(requests, [{url: '/v1/native-inference', bearer: 'Bearer synthetic-client-key'}]);
  });
});

test('HTTP denial and redirects do not retry or disclose the body', async () => {
  for (const status of [302, 401, 403, 404, 500]) {
    await serving(Buffer.from('private-response-marker'), async (base, requests) => {
      await assert.rejects(discoverCapabilities(base, 'synthetic-client-key'), error => error instanceof DiscoveryError && !error.message.includes('private-response-marker'));
      assert.equal(requests.length, 1);
    }, {status, headers: {location: 'http://127.0.0.1:1/'}});
  }
});

test('stalled metadata closes on timeout and caller cancellation', {timeout: 10000}, async () => {
  await serving(Buffer.alloc(0), async (base, _, closed) => {
    await assert.rejects(discoverCapabilities(base, 'synthetic-client-key', {timeoutSeconds: 1}), error => error instanceof DiscoveryError && error.code === 'timeout');
    await Promise.all(closed);
  }, {stalled: true});
  await serving(Buffer.alloc(0), async (base, _, closed, received) => {
    const controller = new AbortController();
    const operation = discoverCapabilities(base, 'synthetic-client-key', {signal: controller.signal});
    await received;
    controller.abort();
    await assert.rejects(operation, error => error instanceof DiscoveryError && error.code === 'cancelled');
    await Promise.all(closed);
  }, {stalled: true});
});

test('unsafe origins are refused before HTTP starts', async () => {
  for (const origin of ['http://example.com', 'http://localhost', 'https://user:secret@example.com', 'https://example.com/path', 'https://example.com?token=secret', 'https://example.com/#fragment', 'http://127.0.0.1:0', 'http://127.0.0.1:99999']) {
    await assert.rejects(discoverCapabilities(origin, 'synthetic-client-key'), TypeError);
  }
});

test('redirects cannot forward the bearer to another real origin', async () => {
  await serving(Buffer.from('[]'), async (sink, forwarded) => {
    await serving(Buffer.from('[]'), async (origin, requests) => {
      await assert.rejects(discoverCapabilities(origin, 'synthetic-client-key'), DiscoveryError);
      assert.equal(requests.length, 1);
      assert.equal(forwarded.length, 0);
    }, {status: 302, headers: {location: sink + '/v1/native-inference'}});
  });
});

test('oversized HTTP metadata is bounded and its active body retired', async () => {
  await serving(Buffer.alloc(8 * 1024 * 1024 + 1, 32), async (base, requests, closed) => {
    await assert.rejects(discoverCapabilities(base, 'synthetic-client-key'), error => error instanceof DiscoveryError && error.code === 'limit');
    assert.equal(requests.length, 1);
    await Promise.all(closed);
  });
});

test('a refused status retires a stalled body immediately', async () => {
  await serving(Buffer.alloc(0), async (base, requests, closed) => {
    await assert.rejects(discoverCapabilities(base, 'synthetic-client-key'), error => error instanceof DiscoveryError && error.code === 'denied');
    assert.equal(requests.length, 1);
    await Promise.all(closed);
  }, {status: 401, stalled: true});
});

test('environment proxies cannot receive the client bearer', {timeout: 10000}, async () => {
  await serving(Buffer.from('[]'), async (proxy, intercepted) => {
    await serving(document([fixture]), async (origin, requests) => {
      const entry = new URL('../dist/discovery.js', import.meta.url).href;
      const script = `import {discoverCapabilities} from ${JSON.stringify(entry)}; const value=await discoverCapabilities(${JSON.stringify(origin)},'synthetic-client-key'); console.log(value.offers.length);`;
      const child = spawn(process.execPath, ['--input-type=module', '-e', script], {
        env: {...process.env, NODE_USE_ENV_PROXY: '1', HTTP_PROXY: proxy, HTTPS_PROXY: proxy, ALL_PROXY: proxy, NO_PROXY: ''},
        stdio: ['ignore', 'pipe', 'pipe'],
      });
      let output = ''; let errors = '';
      child.stdout.on('data', value => {output += value;});
      child.stderr.on('data', value => {errors += value;});
      try {
        const [code] = await once(child, 'close');
        assert.equal(code, 0, errors);
        assert.equal(output.trim(), '1');
        assert.equal(requests.length, 1);
        assert.equal(intercepted.length, 0);
      } finally { if (child.exitCode === null) child.kill(); }
    });
  });
});

test('private TLS roots retain certificate-chain and hostname verification', async () => {
  for (const correctName of [true, false]) {
    const work = mkdtempSync(join(tmpdir(), 'discovery-tls-'));
    let server;
    try {
      const configuration = join(work, 'openssl.cnf');
      const key = join(work, 'key.pem'); const cert = join(work, 'certificate.pem');
      writeFileSync(configuration, `[req]\nprompt=no\ndistinguished_name=name\nx509_extensions=extensions\n[name]\nCN=Discovery fixture\n[extensions]\nbasicConstraints=critical,CA:true\nsubjectAltName=${correctName ? 'IP:127.0.0.1' : 'DNS:other.invalid'}\n`, {mode: 0o600});
      execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-config', configuration, '-keyout', key, '-out', cert, '-days', '1'], {stdio: 'ignore', timeout: 10000});
      const authority = readFileSync(cert, 'utf8');
      const requests = [];
      server = createHttpsServer({key: readFileSync(key), cert: authority}, (request, response) => {
        requests.push(request.url); response.writeHead(200, {'content-type': 'application/json'}); response.end(document([fixture]));
      });
      server.listen(0, '127.0.0.1'); await once(server, 'listening');
      const base = `https://127.0.0.1:${server.address().port}`;
      await assert.rejects(discoverCapabilities(base, 'synthetic-client-key'), DiscoveryError);
      assert.equal(requests.length, 0);
      if (correctName) {
        const result = await discoverCapabilities(base, 'synthetic-client-key', {certificateAuthority: authority});
        assert.equal(result.offers.length, 1);
        assert.equal(requests.length, 1);
      } else {
        await assert.rejects(discoverCapabilities(base, 'synthetic-client-key', {certificateAuthority: authority}), DiscoveryError);
        assert.equal(requests.length, 0);
      }
    } finally {
      if (server !== undefined) {server.closeAllConnections(); server.close(); await once(server, 'close');}
      rmSync(work, {recursive: true, force: true});
    }
  }
});
