import assert from 'node:assert/strict';
import http from 'node:http';
import { mkdir, mkdtemp, readFile, rm, utimes } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import sharp from 'sharp';
import { parseCachePolicies } from './cache-policy.mjs';
import { parseCacheProducts } from './cache-products.mjs';
import { createCacheRegistry } from './cache-registry.mjs';
import { closeServer, createRequestHandler, listen } from './server.mjs';
import { createWmtsServices, validWmtsImage, WmtsError } from './wmts-raster.mjs';
import { A, B, deferred } from './test-fixtures/global-native.mjs';

const TOML = new URL('../../webapp/backend/digues_webapp/basemaps.toml', import.meta.url);
const PRODUCTS = new URL('../../webapp/backend/digues_webapp/cartographic_caches.json', import.meta.url);
const auditRoot = path.resolve(import.meta.dirname, '../../audits');
const quiet = { error() {} };

async function context(t) {
  await mkdir(auditRoot, { recursive: true });
  const root = await mkdtemp(path.join(auditRoot, 'wmts-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const png = await sharp({ create: { width: 256, height: 256, channels: 3,
    background: '#116699' } }).png().toBuffer();
  const jpeg = await sharp({ create: { width: 256, height: 256, channels: 3,
    background: '#889955' } }).jpeg().toBuffer();
  const calls = [];
  let respond = null;
  const provider = http.createServer(async (req, res) => {
    const query = new URL(req.url, 'http://localhost').searchParams;
    calls.push(Object.fromEntries(query));
    const selected = await respond?.(query);
    if (selected === 'timeout') return;
    const status = selected?.status || 200;
    const format = query.get('FORMAT');
    res.writeHead(status, { 'Content-Type': selected?.mime || format || 'text/plain' });
    res.end(selected?.bytes || (format === 'image/png' ? png : jpeg));
  });
  const address = await listen(provider, { host: '127.0.0.1', port: 0 });
  t.after(() => closeServer(provider));
  // Exercise the shipped catalogue, without enabling hidden funds in a test copy.
  const text = await readFile(TOML, 'utf8');
  const catalogue = parseCachePolicies(text);
  const productDocument = JSON.parse(await readFile(PRODUCTS, 'utf8'));
  const products = parseCacheProducts(productDocument);
  for (const def of catalogue.definitions.values()) {
    if (def.adapter === 'wmts_raster') def.provider_endpoint = `http://127.0.0.1:${address.port}/wmts`;
  }
  const caches = createCacheRegistry(root, products, { freeBytes: async () => 10 ** 12 });
  const services = createWmtsServices(catalogue, caches,
    { allowInsecureProvider: true, timeoutMs: 500, retries: 1 });
  const epure = { renderer: { cacheVersion: 'epure-test' },
    getTile() { throw new Error('Épuré must remain independent'); } };
  const handler = createRequestHandler(epure, { logger: quiet, wmtsServices: services });
  t.after(() => Promise.all([...services.values()].map((service) => service.close())));
  return { root, catalogue, products, caches, services, handler, calls, png, jpeg,
    setResponse(fn) { respond = fn; } };
}

async function request(handler, url, headers = {}) {
  const response = { status: null, headers: null, body: Buffer.alloc(0),
    writeHead(status, headers) {
      this.status = status;
      this.headers = Object.fromEntries(Object.entries(headers)
        .map(([key, value]) => [key.toLowerCase(), value]));
    },
    end(body = '') { this.body = Buffer.from(body); },
  };
  await handler({ method: 'GET', url, headers }, response);
  return response;
}

test('two bases reuse each global Plan and Ortho tile without a second acquisition', async t => {
  const ctx = await context(t);
  for (const [id, tile] of [['ign-plan', {z:0,x:0,y:0}], ['ign-ortho',{z:6,x:32,y:22}]]) {
    const service = ctx.services.get(id), before = ctx.calls.length;
    const bytes = await service.getTile(tile);
    let status;
    assert.deepEqual(await service.getTile(tile, value => {status=value;}), bytes);
    assert.equal(status,'HIT'); assert.equal(ctx.calls.length,before+1);
    assert.equal((await ctx.caches.get(id).tiles.snapshot()).fileCount,1);
  }
  assert.equal((await request(ctx.handler, '/internal/basemaps/v1/ign-plan.json')).status,404);
});
test('different global WMTS tiles acquire concurrently and identical requests join', async t => {
  const ctx=await context(t), entered=deferred(), release=deferred();
  let calls=0;
  ctx.setResponse(async()=>{if(++calls===1){entered.resolve(); await release.promise;}});
  const service=ctx.services.get('ign-plan'); service.timeoutMs=5000;
  const a=service.getTile({z:6,x:32,y:22}); await entered.promise;
  const same=service.getTile({z:6,x:32,y:22});
  try { assert.deepEqual(await service.getTile({z:6,x:33,y:22}),ctx.png); assert.equal(calls,2); }
  finally {release.resolve(); await Promise.all([a,same]);}
});

function url(service, z, x, y) {
  return service.tilejson().tiles[0].replace('{z}', z).replace('{x}', x).replace('{y}', y);
}

test('catalogue drives two distinct WMTS routes and leaves Épuré unchanged', async (t) => {
  const ctx = await context(t);
  const plan = ctx.services.get('ign-plan');
  const ortho = ctx.services.get('ign-ortho');
  assert.equal(ctx.services.size, 2);
  assert.deepEqual((await request(ctx.handler, '/basemap/plan-ign.json')).status, 200);
  assert.equal((await request(ctx.handler, '/basemap/ign-plan.json')).status, 200);
  assert.equal((await request(ctx.handler, '/basemap/ign-ortho.json')).status, 200);
  assert.equal(plan.tilejson().maxzoom, 19);
  assert.equal(ortho.tilejson().minzoom, 6);
  assert.equal(plan.tilejson().format, 'png');
  assert.equal(ortho.tilejson().format, 'jpeg');
  assert.notEqual(ctx.caches.get('ign-plan').root, ctx.caches.get('ign-ortho').root);
  const a = await request(ctx.handler, url(plan, 0, 0, 0));
  const b = await request(ctx.handler, url(ortho, 6, 32, 22));
  assert.equal(a.status, 200);
  assert.equal(b.status, 200);
  assert.deepEqual(a.body, ctx.png);
  assert.deepEqual(b.body, ctx.jpeg);
  assert.deepEqual(ctx.calls.map((call) => [call.LAYER, call.TILEMATRIXSET,
    call.TILEMATRIX, call.TILECOL, call.TILEROW]), [
    ['GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2', 'PM_0_19', '0', '0', '0'],
    ['HR.ORTHOIMAGERY.ORTHOPHOTOS', 'PM_6_19', '6', '32', '22'],
  ]);
  assert.equal((await ctx.caches.get('ign-plan').tiles.snapshot()).fileCount, 1);
  assert.equal((await ctx.caches.get('ign-ortho').tiles.snapshot()).fileCount, 1);
});

test('native zooms, XYZ grid, matrix limits and old versions fail before upstream', async (t) => {
  const ctx = await context(t);
  const plan = ctx.services.get('ign-plan');
  const ortho = ctx.services.get('ign-ortho');
  for (const [service, z, x, y] of [
    [plan, 6, 32, 22], [plan, 18, 132618, 91266], [plan, 19, 265236, 182532],
    [ortho, 6, 32, 22], [ortho, 18, 132618, 91266], [ortho, 19, 265236, 182532],
  ]) assert.equal((await request(ctx.handler, url(service, z, x, y))).status, 200);
  const prior = ctx.calls.length;
  for (const bad of [url(plan, 20, 0, 0), url(plan, 2, 4, 0),
    url(ortho, 0, 0, 0), url(ortho, 6, -1, 0),
    url(plan, 'x', 0, 0)]) assert.equal((await request(ctx.handler, bad)).status, 400);
  assert.equal((await request(ctx.handler, url(ortho, 6, 32, 1))).status, 404);
  assert.equal((await request(ctx.handler, url(plan, 19, 0, 0))).status, 404);
  assert.equal((await request(ctx.handler, url(plan, 0, 0, 0)
    .replace(plan.cacheVersion, 'old-version'))).status, 410);
  assert.equal(ctx.calls.length, prior);
});

test('HIT survives restart and concurrent misses deduplicate', async (t) => {
  const ctx = await context(t);
  const plan = ctx.services.get('ign-plan');
  const tile = url(plan, 18, 132618, 91266);
  const [a, b] = await Promise.all([request(ctx.handler, tile), request(ctx.handler, tile)]);
  assert.equal(a.status, 200);
  assert.equal(b.status, 200);
  assert.equal(ctx.calls.length, 1);
  const hit = await request(ctx.handler, tile);
  assert.equal(hit.headers['x-cache'], 'HIT');
  assert.equal(ctx.calls.length, 1);
  await plan.close();
  const restarted = createWmtsServices(ctx.catalogue,
    createCacheRegistry(ctx.root, ctx.products, { freeBytes: async () => 10 ** 12 }),
    { allowInsecureProvider: true, timeoutMs: 150, retries: 1 }).get('ign-plan');
  t.after(() => restarted.close());
  assert.deepEqual(await restarted.getTile({ z: 18, x: 132618, y: 91266 }), ctx.png);
  assert.equal(ctx.calls.length, 1);
});

test('404, 429, 500, timeout and invalid images never poison cache or another fund', async (t) => {
  const ctx = await context(t);
  const plan = ctx.services.get('ign-plan');
  const ortho = ctx.services.get('ign-ortho');
  for (const [kind, response, expected, calls] of [
    ['404', { status: 404, mime: 'text/plain' }, 404, 1],
    ['429', { status: 429, mime: 'text/plain' }, 503, 2],
    ['500', { status: 500, mime: 'text/plain' }, 503, 2],
    ['html', { mime: 'text/html', bytes: Buffer.from('<html>bad</html>') }, 503, 2],
    ['bad-signature', { mime: 'image/png', bytes: Buffer.from('<html>bad</html>') }, 503, 2],
    ['timeout', 'timeout', 503, 2],
  ]) {
    const before = ctx.calls.length;
    ctx.setResponse((query) => query.get('LAYER') === plan.definition.provider_layer ? response : null);
    assert.equal((await request(ctx.handler, url(plan, 10, 512, 500))).status, expected, kind);
    assert.equal(ctx.calls.length - before, calls, kind);
    assert.equal((await request(ctx.handler, url(ortho, 6, 32, 22))).status, 200, kind);
    assert.equal((await ctx.caches.get('ign-plan').tiles.snapshot()).fileCount, 0);
  }
});

test('corrupt cache and stale valid data are handled independently', async (t) => {
  const ctx = await context(t);
  const plan = ctx.services.get('ign-plan');
  const tile = { z: 18, x: 132618, y: 91266 };
  await plan.getTile(tile);
  const key = `${plan.cacheVersion}/${tile.z}/${tile.x}/${tile.y}.png`;
  await ctx.caches.get('ign-plan').tiles.write(key, ctx.jpeg, () => true);
  assert.deepEqual(await plan.getTile(tile), ctx.png);
  assert.equal(ctx.caches.get('ign-plan').tiles.metrics.corrupt, 1);
  const filename = ctx.caches.get('ign-plan').tiles.filename(key);
  const old = new Date(Date.now() - 2 * 86400 * 1000);
  await utimes(filename, old, old);
  ctx.setResponse(() => ({ status: 500, mime: 'text/plain' }));
  assert.deepEqual(await plan.getTile(tile), ctx.png);
  assert.equal((await ctx.caches.get('ign-plan').tiles.snapshot()).fileCount, 1);
});

test('image validation rejects HTML, wrong signatures and truncated raster', async () => {
  const png = await sharp({ create: { width: 256, height: 256, channels: 3,
    background: '#fff' } }).png().toBuffer();
  assert.equal(await validWmtsImage(png, 'png'), true);
  assert.equal(await validWmtsImage(Buffer.from('<html></html>'), 'png'), false);
  assert.equal(await validWmtsImage(png.subarray(0, 40), 'png'), false);
  assert.equal(await validWmtsImage(png, 'jpeg'), false);
});

test('a configured third WMTS basemap uses the same adapter and its own cache', async (t) => {
  const ctx = await context(t);
  const source = await readFile(TOML, 'utf8');
  const planBlock = source.split('\n[[basemaps]]')[2];
  const extra = `\n[[basemaps]]${planBlock}`
    .replaceAll('id = "ign-plan"', 'id = "third"')
    .replace('order = 20', 'order = 40')
    .replace('public_tilejson_url = "/basemap/ign-plan.json"',
      'public_tilejson_url = "/basemap/third.json"')
    .replace('provider_layer = "GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2"',
      'provider_layer = "THIRD.LAYER"')
    .replace('directory = "ign/plan"', 'directory = "third"');
  const catalogue = parseCachePolicies(source + extra);
  catalogue.definitions.get('third').provider_endpoint =
    ctx.catalogue.definitions.get('ign-plan').provider_endpoint;
  const productDocument = JSON.parse(await readFile(PRODUCTS, 'utf8'));
  productDocument.products.push({ ...productDocument.products[1], id: 'third',
    display_name: 'Third raster', directory: 'third', policy: 'persistent-reconstructible' });
  const caches = createCacheRegistry(ctx.root, parseCacheProducts(productDocument),
    { freeBytes: async () => 10 ** 12 });
  const services = createWmtsServices(catalogue, caches,
    { allowInsecureProvider: true, timeoutMs: 150, retries: 1 });
  t.after(() => Promise.all([...services.values()].map((service) => service.close())));
  assert.equal((await services.get('third').getTile({ z: 0, x: 0, y: 0 })).equals(ctx.png), true);
  assert.equal((await caches.get('third').tiles.snapshot()).fileCount, 1);
  assert.equal((await ctx.caches.get('ign-plan').tiles.snapshot()).fileCount, 0);
  assert.equal(ctx.calls.at(-1).LAYER, 'THIRD.LAYER');
});

test('an explicitly disabled WMTS stays unpublished without affecting the other funds', async (t) => {
  const ctx = await context(t);
  ctx.catalogue.definitions.get('ign-plan').enabled = false;
  const services = createWmtsServices(ctx.catalogue, ctx.caches, { allowInsecureProvider: true });
  t.after(() => Promise.all([...services.values()].map(service => service.close())));
  const handler = createRequestHandler({ renderer: { cacheVersion: 'epure-test' } },
    { logger: quiet, wmtsServices: services });
  assert.equal((await request(handler, '/basemap/ign-plan.json')).status, 404);
  assert.equal((await request(handler, '/basemap/ign-ortho.json')).status, 200);
  assert.equal((await request(handler, '/basemap/plan-ign.json')).status, 200);
  assert.equal(ctx.calls.length, 0);
});

test('publication lock rejects an unauthorized configured fond', async (t) => {
  const ctx = await context(t);
  ctx.catalogue.definitions.get('ign-plan').wmts_publication = 'review-required';
  assert.throws(() => createWmtsServices(ctx.catalogue, ctx.caches,
    { allowInsecureProvider: true }), /not authorized/);
  await assert.rejects(() => ctx.services.get('ign-ortho').getTile({ z: 20, x: 0, y: 0 }),
    (error) => error instanceof WmtsError && error.status === 400);
});

test('a cold desktop view fits the default queue while overload stays bounded', async (t) => {
  // Regression: a 1280px viewport requests 24 cold tiles; the former 4+16
  // admission budget rejected four. Cover a 1920px view too, without raising
  // upstream concurrency or disk quotas. The provider is local and blocked.
  const ctx = await context(t);
  const plan = ctx.services.get('ign-plan');
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  ctx.setResponse(async query => {
    if (query.get('LAYER') === plan.definition.provider_layer) await gate;
    return null;
  });
  const pending = [];
  let refused = 0;
  const enqueue = i => pending.push(request(ctx.handler, url(plan, 10, 500 + i, 343))
    .then(response => { if (response.status !== 200) refused++; return response; }));
  const admitted = async count => {
    const deadline = Date.now() + 3000;
    while (plan.inflight.size + refused < count && Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 5));
    }
    assert.equal(plan.inflight.size + refused, count);
  };
  try {
    for (let i = 0; i < 40; i++) enqueue(i);
    await admitted(40);
    assert.equal(refused, 0, 'a standard cold desktop view must not lose tiles');
    assert.equal(plan.active, 4);
    assert.equal(plan.waiters.length, 36);
    const capacity = plan.concurrency + plan.queueLimit;
    for (let i = 40; i < capacity; i++) enqueue(i);
    await admitted(capacity);
    assert.equal(refused, 0);
    assert.equal(plan.waiters.length, plan.queueLimit);
    assert.equal(ctx.calls.filter(row => row.LAYER === plan.definition.provider_layer).length, 4);
    assert.equal((await request(ctx.handler, url(plan, 10, 500 + capacity, 343))).status, 503);
    assert.equal((await request(ctx.handler, url(ctx.services.get('ign-ortho'), 6, 32, 22))).status, 200);
  } finally {
    release();
    const responses = await Promise.all(pending);
    if (!refused) assert(responses.every(response => response.status === 200));
  }
});

test('WMTS concurrency is bounded and a full queue refuses only that provider', async (t) => {
  const ctx = await context(t);
  const plan = ctx.services.get('ign-plan');
  plan.concurrency = 1;
  plan.queueLimit = 0;
  plan.timeoutMs = 5000;
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  ctx.setResponse(async (query) => {
    if (query.get('LAYER') === plan.definition.provider_layer) await gate;
    return null;
  });
  const first = request(ctx.handler, url(plan, 18, 132618, 91266));
  for (let i = 0; i < 50 && ctx.calls.length === 0; i++) {
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.equal(ctx.calls.length, 1);
  assert.equal((await request(ctx.handler, url(plan, 18, 132619, 91266))).status, 503);
  assert.equal((await request(ctx.handler,
    url(ctx.services.get('ign-ortho'), 6, 32, 22))).status, 200);
  release();
  assert.equal((await first).status, 200);
  assert.equal(ctx.calls.filter((call) => call.LAYER === plan.definition.provider_layer).length, 1);
});

test('cache quota and disk guard reject writes without changing another fund', async (t) => {
  const ctx = await context(t);
  const plan = ctx.services.get('ign-plan');
  const ortho = ctx.services.get('ign-ortho');
  const store = ctx.caches.get('ign-plan').tiles;
  store.quotaBytes = 100;
  assert.equal((await request(ctx.handler, url(plan, 6, 32, 22))).status, 503);
  assert.equal((await store.snapshot()).fileCount, 0);
  assert.equal((await request(ctx.handler, url(ortho, 6, 32, 22))).status, 200);
  store.quotaBytes = ctx.products.capacityBytes;
  store.freeBytes = async () => 0;
  assert.equal((await request(ctx.handler, url(plan, 6, 32, 22))).status, 503);
  assert.equal((await store.snapshot()).fileCount, 0);
  assert(store.metrics.noSpace >= 2);
});

test('shutdown waits for admitted work and refuses a new cold request', async (t) => {
  const ctx = await context(t);
  const plan = ctx.services.get('ign-plan');
  let release;
  plan.timeoutMs = 5000;
  const gate = new Promise((resolve) => { release = resolve; });
  ctx.setResponse(async (query) => {
    if (query.get('LAYER') === plan.definition.provider_layer) await gate;
    return null;
  });
  const tile = { z: 6, x: 32, y: 22 };
  const first = plan.getTile(tile);
  for (let i = 0; i < 50 && ctx.calls.length === 0; i++) {
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  const closing = plan.close();
  await assert.rejects(() => plan.getTile({ z: 6, x: 33, y: 22 }),
    (error) => error instanceof WmtsError && error.status === 503);
  release();
  assert.deepEqual(await first, ctx.png);
  await closing;
});
