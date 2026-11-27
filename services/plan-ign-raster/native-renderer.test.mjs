import assert from 'node:assert/strict';
import test from 'node:test';
import { createNativeRenderer } from './native-renderer.mjs';
import { METADATA_URL, STYLE_URL } from './source.mjs';
import { metatileForTile } from './geometry.mjs';

const tileUrl = 'https://data.geopf.fr/tms/1.0.0/PLAN.IGN/{z}/{x}/{y}.pbf';
const metadata = { format: 'pbf', crs: 'EPSG:3857', minzoom: 0, maxzoom: 18,
  tiles: [tileUrl] };
const style = { version: 8,
  glyphs: 'https://data.geopf.fr/annexes/ressources/vectorTiles/fonts/{fontstack}/{range}.pbf',
  sprite: 'https://data.geopf.fr/annexes/ressources/vectorTiles/styles/PLAN.IGN/sprite/PlanIgn',
  sources: { plan_ign: { type: 'vector', scheme: 'xyz', tiles: [tileUrl], url: METADATA_URL } },
  layers: [] };
const silentLogger = { log() {} };

function fakeResources() {
  const requests = [];
  return { requests, get: async (url) => {
    requests.push(url);
    if (url === STYLE_URL) return Buffer.from(JSON.stringify(style));
    if (url === METADATA_URL) return Buffer.from(JSON.stringify(metadata));
    throw new Error(`Unexpected URL: ${url}`);
  } };
}

test('initializes one persistent Native map and reuses it for multiple renders', async () => {
  const resources = fakeResources();
  let constructed = 0;
  let released = 0;
  let rendered = 0;
  let loadedStyle;
  const renderer = await createNativeRenderer({ cacheRoot: new URL('../../audits/fake-ign-cache', import.meta.url).pathname,
    generation: 'test', logger: silentLogger,
    resourceCacheFactory: () => resources,
    mapFactory(options) {
      constructed++;
      assert.equal(options.mode, 'static');
      return {
        load(value) { loadedStyle = value; },
        render(_viewport, callback) { rendered++; callback(null, Buffer.alloc(2560 * 2560 * 4, rendered)); },
        release() { released++; },
      };
    } });
  const meta = metatileForTile(17, 66309, 45633);
  assert.equal((await renderer.render(meta))[0], 1);
  assert.equal((await renderer.render(meta))[0], 2);
  assert.equal(constructed, 1);
  assert.equal(rendered, 2);
  assert.equal(Object.values(loadedStyle.sources)[0].maxzoom, 18);
  assert.deepEqual(resources.requests, [STYLE_URL, METADATA_URL]);
  await renderer.release();
  assert.equal(released, 1);
});

test('a Native style load error releases the map before startup fails', async () => {
  let released = 0;
  await assert.rejects(createNativeRenderer({ cacheRoot: new URL('../../audits/fake-ign-cache', import.meta.url).pathname,
    generation: 'test', resourceCacheFactory: fakeResources,
    mapFactory() { return { load() { throw new Error('load failed'); },
      release() { released++; } }; } }), /load failed/);
  assert.equal(released, 1);
});
