import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, readdir, rm, utimes, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { assertOfficialUrl, createResourceCache, MAX_RESOURCE_BYTES, sha256,
  STYLE_URL, validateOfficialStyle } from './source.mjs';
import { FileCache } from './file-cache.mjs';

const tileUrl = 'https://data.geopf.fr/tms/1.0.0/PLAN.IGN/17/1/2.pbf';
const validStyle = JSON.stringify({ version: 8, sources: { plan_ign: { type: 'vector' } },
  layers: [] });

test('only official IGN resource URLs can enter the resource cache', () => {
  assert.equal(assertOfficialUrl('https://data.geopf.fr/tms/1.0.0/PLAN.IGN/15/1/2.pbf'),
    'https://data.geopf.fr/tms/1.0.0/PLAN.IGN/15/1/2.pbf');
  assert.throws(() => assertOfficialUrl('https://example.org/tiles.pbf'));
  assert.throws(() => assertOfficialUrl('https://data.geopf.fr/tms/1.0.0/OTHER/0/0/0.pbf'));
});

test('a transient style fetch timeout is retried during startup', async () => {
  const directory = await mkdtemp(path.join(path.resolve(import.meta.dirname, '../../audits'), 'ign-service-style-retry-'));
  const url = 'https://data.geopf.fr/annexes/ressources/vectorTiles/styles/PLAN.IGN/epure.json';
  let attempts = 0;
  try {
    const cache = createResourceCache(directory, { fetchImpl: async () => {
      attempts++;
      if (attempts === 1) throw new Error('network timeout');
      return new Response('{"version":8,"sources":{"plan_ign":{"type":"vector"}},"layers":[]}');
    } });
    assert.equal(JSON.parse(await cache.get(url)).version, 8);
    assert.equal(attempts, 2);
    assert.equal(cache.snapshot().retryResponses, 1);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('cache is reusable after constructing a new renderer/cache instance', async () => {
  const directory = await mkdtemp(path.join(path.resolve(import.meta.dirname, '../../audits'), 'ign-poc-cache-test-'));
  const url = 'https://data.geopf.fr/tms/1.0.0/PLAN.IGN/15/1/2.pbf';
  let fetched = 0;
  try {
    const first = createResourceCache(directory, { fetchImpl: async () => {
      fetched++;
      return new Response('tile');
    } });
    assert.equal((await first.get(url)).toString(), 'tile');
    const second = createResourceCache(directory, { offline: true });
    assert.equal((await second.get(url)).toString(), 'tile');
    assert.equal(fetched, 1);
    assert.equal(second.snapshot().diskHits, 1);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('managed resource cache retains valid stale assets during an IGN outage', async () => {
  const root = await mkdtemp(path.join(path.resolve(import.meta.dirname, '../../audits'), 'ign-managed-resource-test-'));
  const resourceRoot = path.join(root, 'resources');
  const directory = path.join(resourceRoot, 'epoch');
  const store = new FileCache({ root: resourceRoot, quotaBytes: 1000,
    minFreeBytes: 0, maxAgeSeconds: 1, activeGeneration: 'epoch',
    freeBytes: async () => 10000 });
  try {
    const warm = createResourceCache(directory, { store, fetchImpl: async () =>
      new Response(validStyle) });
    assert.equal((await warm.get(STYLE_URL)).toString(), validStyle);
    const filename = path.join(directory, `${sha256(STYLE_URL)}.bin`);
    const old = new Date(Date.now() - 5000);
    await utimes(filename, old, old);
    const offline = createResourceCache(directory, { store, fetchImpl: async () => {
      throw new Error('IGN unavailable');
    } });
    assert.equal((await offline.get(STYLE_URL)).toString(), validStyle);
    assert.equal(offline.snapshot().errors, 1);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('a transient MVT 404 is retried and a later success is cached', async () => {
  const directory = await mkdtemp(path.join(path.resolve(import.meta.dirname, '../../audits'), 'ign-poc-retry-test-'));
  let attempts = 0;
  try {
    const cache = createResourceCache(directory, { fetchImpl: async () => {
      attempts++;
      return attempts === 1 ? new Response('', { status: 404 }) : new Response('tile');
    } });
    assert.equal((await cache.get('https://data.geopf.fr/tms/1.0.0/PLAN.IGN/17/1/2.pbf')).toString(), 'tile');
    assert.equal(cache.snapshot().retryResponses, 1);
    assert.equal(attempts, 2);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('an HTML 200 cannot poison the style cache, including after restart', async () => {
  const directory = await mkdtemp(path.join(path.resolve(import.meta.dirname, '../../audits'), 'ign-bad-json-test-'));
  let fetches = 0;
  try {
    const bad = createResourceCache(directory, { fetchImpl: async () => {
      fetches++;
      return new Response('<html>maintenance</html>', { status: 200 });
    } });
    await assert.rejects(bad.get(STYLE_URL), /Invalid IGN JSON/);
    assert.deepEqual(await readdir(directory).catch(() => []), []);

    const good = createResourceCache(directory, { fetchImpl: async () => {
      fetches++;
      return new Response(validStyle);
    } });
    assert.equal((await good.get(STYLE_URL)).toString(), validStyle);
    assert.equal(fetches, 2);
    await writeFile(path.join(directory, `${sha256(STYLE_URL)}.bin`), '<html>old cache</html>');
    const recovered = createResourceCache(directory, { fetchImpl: async () => {
      fetches++;
      return new Response(validStyle);
    } });
    assert.equal((await recovered.get(STYLE_URL)).toString(), validStyle);
    assert.equal(fetches, 3);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('a rejected binary resource can be invalidated without changing the generation', async () => {
  const directory = await mkdtemp(path.join(path.resolve(import.meta.dirname, '../../audits'), 'ign-invalidate-test-'));
  let fetches = 0;
  try {
    const cache = createResourceCache(directory, { fetchImpl: async () =>
      new Response(`tile-${++fetches}`) });
    assert.equal((await cache.get(tileUrl)).toString(), 'tile-1');
    await cache.invalidate(tileUrl);
    assert.equal((await cache.get(tileUrl)).toString(), 'tile-2');
    assert.equal(fetches, 2);
    await assert.rejects(cache.invalidate('https://example.org/other.pbf'), /Unexpected/);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('Content-Length above 8 MiB is refused before reading the body', async () => {
  const directory = await mkdtemp(path.join(path.resolve(import.meta.dirname, '../../audits'), 'ign-size-header-test-'));
  let pulls = 0;
  try {
    const cache = createResourceCache(directory, { fetchImpl: async () => new Response(
      new ReadableStream({ pull(controller) { pulls++; controller.enqueue(new Uint8Array(1)); } }),
      { headers: { 'Content-Length': String(MAX_RESOURCE_BYTES + 1) } }) });
    await assert.rejects(cache.get(tileUrl), /unexpectedly large/);
    assert.equal(pulls <= 1, true);
    assert.deepEqual(await readdir(directory).catch(() => []), []);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('chunked body stops at the limit and a body just below it succeeds', async () => {
  const directory = await mkdtemp(path.join(path.resolve(import.meta.dirname, '../../audits'), 'ign-size-stream-test-'));
  let pulls = 0;
  try {
    const cache = createResourceCache(directory, { fetchImpl: async () => new Response(
      new ReadableStream({ pull(controller) {
        pulls++;
        controller.enqueue(new Uint8Array(1024 * 1024));
      } })) });
    await assert.rejects(cache.get(tileUrl), /unexpectedly large/);
    assert(pulls <= 10, `Read too many chunks: ${pulls}`);
    const accepted = createResourceCache(directory, { fetchImpl: async () =>
      new Response(Buffer.alloc(MAX_RESOURCE_BYTES - 1)) });
    assert.equal((await accepted.get(tileUrl)).length, MAX_RESOURCE_BYTES - 1);
    assert.equal((await readFile(path.join(directory, `${sha256(tileUrl)}.bin`))).length,
      MAX_RESOURCE_BYTES - 1);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('only redirects within official IGN paths are followed, with a finite limit', async () => {
  const directory = await mkdtemp(path.join(path.resolve(import.meta.dirname, '../../audits'), 'ign-redirect-test-'));
  const nextUrl = 'https://data.geopf.fr/tms/1.0.0/PLAN.IGN/17/1/3.pbf';
  const seen = [];
  try {
    const cache = createResourceCache(directory, { fetchImpl: async (url, options) => {
      seen.push([url, options.redirect]);
      return url === tileUrl ? Response.redirect(nextUrl, 302) : new Response('tile');
    } });
    assert.equal((await cache.get(tileUrl)).toString(), 'tile');
    assert.deepEqual(seen, [[tileUrl, 'manual'], [nextUrl, 'manual']]);
    await cache.invalidate(tileUrl);
    const refused = createResourceCache(directory, { fetchImpl: async () =>
      Response.redirect('https://example.org/private', 302) });
    await assert.rejects(refused.get(tileUrl), /redirect outside official/);
    const looping = createResourceCache(directory, { fetchImpl: async () =>
      Response.redirect(tileUrl, 302) });
    await assert.rejects(looping.get(tileUrl), /Unexpected IGN redirect/);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('official style retains 321 layer definitions and changes only source URL handling', () => {
  const tile = 'https://data.geopf.fr/tms/1.0.0/PLAN.IGN/{z}/{x}/{y}.pbf';
  const style = { version: 8, glyphs: 'https://data.geopf.fr/annexes/ressources/vectorTiles/fonts/{fontstack}/{range}.pbf',
    sprite: 'https://data.geopf.fr/annexes/ressources/vectorTiles/styles/PLAN.IGN/sprite/PlanIgn',
    sources: { plan_ign: { type: 'vector', scheme: 'xyz', tiles: [tile],
      url: 'https://data.geopf.fr/tms/1.0.0/PLAN.IGN/metadata.json' } },
    layers: Array.from({ length: 321 }, (_, i) => ({ id: String(i) })) };
  const result = validateOfficialStyle(style, { format: 'pbf', crs: 'EPSG:3857',
    minzoom: 0, maxzoom: 18, tiles: [tile] });
  assert.deepEqual(result.layers, style.layers);
  assert.equal(result.sources.plan_ign.url, undefined);
  assert.equal(result.sources.plan_ign.maxzoom, 18);
});

// A: active source acquisition is protected from global LRU, released on success/error.
test('concurrent source acquisitions pin one shared key until completion', async () => {
 const pinned=new Set();let release,entered;
 const gate=new Promise(r=>{release=r;}),started=new Promise(r=>{entered=r;});
 const store={pin(keys){keys.forEach(k=>pinned.add(k));return()=>keys.forEach(k=>pinned.delete(k));},
  async read(){return null;},async write(){assert.equal(pinned.size,1);}};
 const cache=createResourceCache('/virtual/test-v1',{store,fetchImpl:async()=>{entered();await gate;return new Response(validStyle);}});
 const first=cache.get(STYLE_URL), second=cache.get(STYLE_URL);await started;
 assert.equal(pinned.size,1);release();assert.deepEqual(await first,await second);assert.equal(pinned.size,0);
});
