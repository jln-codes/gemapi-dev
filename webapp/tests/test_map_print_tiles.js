const assert = require('node:assert/strict');
const test = require('node:test');
const { createPrintTileLayer, paperDimensions, checkedPrintTile, printTileFormat,
  waitForTiles } = require('../frontend/js/map-print.js');

const WEBP = Buffer.from('RIFF\x00\x00\x00\x00WEBP');
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg==', 'base64');
const JPEG = Buffer.concat([Buffer.from([255, 216, 255]), Buffer.alloc(12), Buffer.from([255, 217])]);
const imageResponse = (format) => new Response({ webp: WEBP, png: PNG, jpeg: JPEG }[format],
  { headers: { 'Content-Type': { webp: 'image/webp', png: 'image/png', jpeg: 'image/jpeg' }[format] } });

function harness(fetchImpl) {
  const objectUrls = new Set();
  let counter = 0;
  const runtime = {
    fetch: fetchImpl,
    setTimeout: (callback, ms) => setTimeout(callback, Math.min(ms, 2)),
    clearTimeout,
    URL: {
      createObjectURL() { const value = `blob:audit-${++counter}`; objectUrls.add(value); return value; },
      revokeObjectURL(value) { objectUrls.delete(value); },
    },
    document: { createElement() {
      return { set src(value) {
        this.source = value;
        queueMicrotask(() => this.onload?.());
      } };
    } },
  };
  const L = { tileLayer(template, options) {
    return { options, getTileUrl({ z, x, y }) {
      return template.replace('{z}', z).replace('{x}', x).replace('{y}', y);
    } };
  } };
  return { L, runtime, objectUrls };
}

test('cold A3 export loads more than eight metatiles and recovers from transient 503', async () => {
  const dimensions = paperDimensions('A3', 'landscape');
  assert.deepEqual([dimensions.widthPx, dimensions.heightPx], [2480, 1754]);
  const coords = [];
  for (let x = 103; x <= 113; x++) {
    for (let y = 203; y <= 210; y++) coords.push({ z: 10, x, y });
  }
  const metas = new Set(coords.map(({ x, y }) => `${Math.floor(x / 4)}/${Math.floor(y / 4)}`));
  assert.equal(coords.length, 88);
  assert.equal(metas.size, 12);

  const discovered = new Set();
  const attempts = new Map();
  let active = 0;
  let maximum = 0;
  let transient = 0;
  const h = harness(async (url) => {
    active++;
    maximum = Math.max(maximum, active);
    await new Promise((resolve) => setImmediate(resolve));
    active--;
    const parts = new URL(url).pathname.split('/');
    const z = parts.at(-3);
    const x = Number(parts.at(-2));
    const y = Number(parts.at(-1).replace(/\.webp$/, ''));
    const meta = `${Math.floor(x / 4)}/${Math.floor(y / 4)}`;
    const firstForTile = !attempts.has(url);
    attempts.set(url, (attempts.get(url) || 0) + 1);
    const overAdmission = !discovered.has(meta) && discovered.size >= 8;
    discovered.add(meta);
    if (overAdmission && firstForTile) {
      transient++;
      return new Response('busy', { status: 503 });
    }
    assert.equal(z, '10');
    return imageResponse('webp');
  });
  const layer = createPrintTileLayer(h.L,
    'https://example.test/basemap/tiles/v1/{z}/{x}/{y}.webp', {}, '© test', h.runtime);
  const loaded = coords.map((coord) => new Promise((resolve, reject) => {
    layer.createTile(coord, (error, image) => error ? reject(error) : resolve(image));
  }));
  const images = await Promise.all(loaded);
  assert.equal(images.length, 88);
  assert.equal(discovered.size, 12);
  assert(transient > 0);
  assert(maximum <= 6);
  assert.equal(layer.options.attribution, '© test');
  assert(images.every((image) => image.crossOrigin === 'anonymous'));
  layer.stopPrintTiles();
  assert.equal(h.objectUrls.size, 0);
});

test('print retries are limited to transient statuses; a permanent 404 is not retried', async () => {
  for (const [status, expectedCalls] of [[404, 1], [503, 6]]) {
    let calls = 0;
    const h = harness(async () => { calls++; return new Response('', { status }); });
    const layer = createPrintTileLayer(h.L,
      'https://example.test/{z}/{x}/{y}.webp', {}, '', h.runtime);
    const result = await new Promise((resolve) => {
      layer.createTile({ z: 0, x: 0, y: 0 }, (error) => resolve(error));
    });
    assert.match(result.message, new RegExp(`HTTP ${status}`));
    assert.equal(calls, expectedCalls);
    layer.stopPrintTiles();
  }
});

test('stopping an export cancels a pending retry without more requests', async () => {
  let calls = 0;
  const h = harness(async () => {
    calls++;
    return new Response('busy', { status: 503 });
  });
  h.runtime.setTimeout = setTimeout;
  const layer = createPrintTileLayer(h.L,
    'https://example.test/{z}/{x}/{y}.webp', {}, '', h.runtime);
  const finished = new Promise((resolve) => {
    layer.createTile({ z: 0, x: 0, y: 0 }, (error) => resolve(error));
  });
  while (calls === 0) await new Promise((resolve) => setImmediate(resolve));
  layer.stopPrintTiles();
  assert.match((await finished).message, /interrompu/);
  assert.equal(calls, 1);
  assert.equal(h.objectUrls.size, 0);
});

test('published PLANIGNV2 PNG and BD ORTHO JPEG use the selected source and attribution', async () => {
  for (const [id, format, zoom, attribution] of [
    ['ign-plan', 'png', 19, '© IGN — PLANIGNV2'],
    ['ign-ortho', 'jpeg', 19, '© IGN — BD ORTHO'],
  ]) {
    const calls = [];
    const h = harness(async (url) => { calls.push(url); return imageResponse(format); });
    const suffix = format === 'jpeg' ? 'jpg' : format;
    const layer = createPrintTileLayer(h.L,
      `https://app.test/basemap/${id}/tiles/{z}/{x}/{y}.${suffix}`,
      { minZoom: id === 'ign-ortho' ? 6 : 0, maxNativeZoom: zoom, maxZoom: zoom + 1 },
      attribution, h.runtime, format);
    await new Promise((resolve, reject) => layer.createTile({ z: zoom, x: 3, y: 4 },
      (error) => error ? reject(error) : resolve()));
    assert.deepEqual(calls, [`https://app.test/basemap/${id}/tiles/${zoom}/3/4.${suffix}`]);
    assert.equal(layer.options.attribution, attribution);
    assert.equal(layer.options.maxNativeZoom, 19);
    assert.equal(layer.options.maxZoom, 20);
    layer.stopPrintTiles();
    assert.equal(h.objectUrls.size, 0);
  }
});

test('territorial browser export fetches the captured base URL without a global fallback', async () => {
  const base = '30000000-0000-4000-8000-000000000001';
  const calls = [], h = harness(async url => { calls.push(url); return imageResponse('webp'); });
  const template = `https://app.test/api/bases/${base}/basemaps/ign-epure/tiles/v1/{z}/{x}/{y}.webp`;
  const layer = createPrintTileLayer(h.L, template,
    { minZoom: 0, maxNativeZoom: 18, maxZoom: 19 }, '© fixture', h.runtime, 'webp');
  await new Promise((resolve, reject) => layer.createTile({ z: 5, x: 17, y: 13 },
    error => error ? reject(error) : resolve()));
  assert.deepEqual(calls, [template.replace('{z}', 5).replace('{x}', 17).replace('{y}', 13)]);
  layer.stopPrintTiles(); assert.equal(h.objectUrls.size, 0);
});

test('missing, HTML and corrupted print tiles fail without a substitute provider', async () => {
  for (const response of [new Response('', { status: 404 }),
    new Response('<html>', { headers: { 'Content-Type': 'text/html' } }),
    new Response('not a PNG', { headers: { 'Content-Type': 'image/png' } })]) {
    let calls = 0;
    const h = harness(async () => { calls++; return response; });
    const layer = createPrintTileLayer(h.L, 'https://app.test/basemap/ign-plan/tiles/{z}/{x}/{y}.png',
      { maxNativeZoom: 19, maxZoom: 20 }, '© IGN', h.runtime, 'png');
    const error = await new Promise((resolve) => layer.createTile({ z: 19, x: 1, y: 1 }, resolve));
    assert.match(error.message, /HTTP 404|Type de tuile|Image du fond invalide/);
    assert.equal(calls, 1);
    layer.stopPrintTiles();
    assert.equal(h.objectUrls.size, 0);
  }
});

test('print image validation accepts only declared raster MIME and signature', async () => {
  for (const format of ['webp', 'png', 'jpeg']) {
    const blob = await checkedPrintTile(imageResponse(format), format);
    assert.equal(blob.type, { webp: 'image/webp', png: 'image/png', jpeg: 'image/jpeg' }[format]);
  }
  assert.equal(printTileFormat('/__offline-basemaps/id/{z}/{x}/{y}', 'jpeg'), 'jpeg');
  await assert.rejects(checkedPrintTile(imageResponse('png'), 'jpeg'), /Type de tuile/);
});

test('a basemap change aborts pending tile wait without printing a mixed source', async () => {
  const listeners = new Map();
  const layer = { on(name, callback) { listeners.set(name, callback); },
    off(name) { listeners.delete(name); } };
  const controller = new AbortController();
  const pending = waitForTiles(layer, controller.signal);
  controller.abort();
  await assert.rejects(pending, /fond a changé/);
  assert.equal(listeners.size, 0);
});

test('a stalled print tile load times out without leaving listeners behind', async () => {
  const listeners = new Map();
  const layer = { on(name, callback) { listeners.set(name, callback); },
    off(name) { listeners.delete(name); } };
  await assert.rejects(waitForTiles(layer, null, 5), /chargé à temps/);
  assert.equal(listeners.size, 0);
});

test('offline package print uses only its synthetic local route and refuses a missing tile', async () => {
  const local = 'https://app.test/__offline-basemaps/11111111-1111-4111-8111-111111111111/{z}/{x}/{y}';
  const requests = [];
  const h = harness(async (url) => {
    requests.push(url);
    return url.endsWith('/18/1/2') ? imageResponse('webp') : new Response('absent', { status: 404 });
  });
  const layer = createPrintTileLayer(h.L, local, { maxNativeZoom: 18, maxZoom: 19 },
    '© IGN — PLAN.IGN', h.runtime, 'webp');
  await new Promise((resolve, reject) => layer.createTile({ z: 18, x: 1, y: 2 },
    (error) => error ? reject(error) : resolve()));
  const missing = await new Promise((resolve) => layer.createTile({ z: 18, x: 1, y: 3 }, resolve));
  assert.match(missing.message, /HTTP 404/);
  assert(requests.every((url) => url.includes('/__offline-basemaps/')));
  layer.stopPrintTiles();
});
