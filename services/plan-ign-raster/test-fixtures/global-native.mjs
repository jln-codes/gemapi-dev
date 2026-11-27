import { mkdtemp, rm } from 'node:fs/promises';
import path from 'node:path';
import sharp from 'sharp';
import { TARGETS, tile, glyphs, styleFor } from './context.mjs';
import { loadCacheProducts } from '../cache-products.mjs';
import { createCacheRegistry } from '../cache-registry.mjs';
import { createResourceCache, STYLE_URL, METADATA_URL } from '../source.mjs';
import { createNativeRenderer } from '../native-renderer.mjs';

export const A = Object.freeze({ instance_id: TARGETS.A.instance, structure_id: TARGETS.A.structure,
  business_database_id: TARGETS.A.base });
export const B = Object.freeze({ ...A, business_database_id: TARGETS.B.base });
export const MVT_TEMPLATE = 'https://data.geopf.fr/tms/1.0.0/PLAN.IGN/{z}/{x}/{y}.pbf';
export const metadata = { format: 'pbf', crs: 'EPSG:3857', minzoom: 0, maxzoom: 18, tiles: [MVT_TEMPLATE] };
export const quiet = { log() {}, error() {}, warn() {} };
export function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
}
export function officialFixtureStyle() {
  const result = styleFor(TARGETS.A);
  result.sources = { plan_ign: { type: 'vector', scheme: 'xyz', tiles: [MVT_TEMPLATE], url: METADATA_URL } };
  result.glyphs = 'https://data.geopf.fr/annexes/ressources/vectorTiles/fonts/{fontstack}/{range}.pbf';
  result.sprite = 'https://data.geopf.fr/annexes/ressources/vectorTiles/styles/PLAN.IGN/sprite/PlanIgn';
  for (const layer of result.layers) if (layer.source) layer.source = 'plan_ign';
  return result;
}

// All provider responses are synthetic, behind the real source/FileCache stack.
// No network fallback: unknown URLs throw a permanent error.
export async function nativeHarness(t, { mapFactory, gate, style = officialFixtureStyle(),
  timeoutMs = 15000, failMvt = () => false } = {}) {
  const root = await mkdtemp(path.resolve(import.meta.dirname, '../../../audits/carto-global-native-'));
  const catalogue = await loadCacheProducts(new URL('../../../webapp/backend/digues_webapp/cartographic_caches.json', import.meta.url));
  const registry = createCacheRegistry(root, catalogue, { freeBytes: async () => 100 * 1024 ** 3 });
  const requests = [], loads = [], beforeCleanup = [];
  const sprite = await sharp({ create: { width: 16, height: 16, channels: 4, background: '#00cc66' } }).png().toBuffer();
  const resourceCacheFactory = (directory, options) => createResourceCache(directory, { ...options,
    fetchImpl: async url => {
      const name = directory.includes(B.business_database_id) ? 'B' : 'A';
      const mvt = /^https:\/\/data.geopf.fr\/tms\/1.0.0\/PLAN.IGN\/(\d+)\/(\d+)\/(\d+)\.pbf$/.exec(url);
      requests.push({ name, url, directory, mvt: Boolean(mvt) });
      if (mvt) {
        if (failMvt(name)) throw Object.assign(new Error('Synthetic MVT acquisition interrupted'), { retryable: false });
        if (gate?.name === name) { gate.entered.resolve(); await gate.release.promise; }
        return new Response(tile(TARGETS[name], ...mvt.slice(1).map(Number)));
      }
      if (url === STYLE_URL) return new Response(JSON.stringify(style));
      if (url === METADATA_URL) return new Response(JSON.stringify(metadata));
      if (url.startsWith(style.glyphs.split('{')[0])) return new Response(glyphs());
      if (url === style.sprite + '.json') return new Response(JSON.stringify({ 'green-dot': {
        x: 0, y: 0, width: 16, height: 16, pixelRatio: 1 } }));
      if (url === style.sprite + '.png') return new Response(sprite);
      throw Object.assign(new Error('Unexpected synthetic provider URL'), { retryable: false });
    } });
  const renderer = await createNativeRenderer({ cacheRoot: root, generation: 'test-v1',
    resourceStore: registry.get('ign-epure').resources,
    resourceCacheFactory, timeoutMs, logger: quiet, mapFactory: mapFactory && (options => {
      const map = mapFactory(options);
      return { load(style) { loads.push(structuredClone(style)); return map.load(style); },
        render: map.render.bind(map), release: map.release.bind(map) };
    }) });
  t.after(async () => {
    for (const cleanup of beforeCleanup) await cleanup();
    await renderer.release(); await rm(root, { recursive: true, force: true });
  });
  return { root, registry, renderer, requests, loads, style, beforeCleanup };
}
