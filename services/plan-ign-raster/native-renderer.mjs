import path from 'node:path';
import { cacheVersion, validateGeneration } from './cache-identity.mjs';
import { createRenderQueue, DEFAULT_RENDER_TIMEOUT_MS } from './render-queue.mjs';
import { createEpureSource } from './epure-source.mjs';
import { metatileGeometry } from './geometry.mjs';
import {
  METADATA_URL,
  STYLE_URL,
  createResourceCache,
  validateOfficialStyle,
} from './source.mjs';

function makeNativeRequest(resources) {
  return (request, callback) => {
    resources.get(request.url).then(
      (data) => callback(null, { data }),
      (error) => callback(error),
    );
  };
}

export async function createNativeRenderer({ cacheRoot, generation, timeoutMs = DEFAULT_RENDER_TIMEOUT_MS,
  logger = console, resourceCacheFactory = createResourceCache, resourceStore = null,
  retainOnError = true, mapFactory, fetchImpl } = {}) {
  const epoch = validateGeneration(generation);
  const resources = resourceCacheFactory(
    path.join(cacheRoot, 'ign', 'epure', 'resources', epoch),
    { store: resourceStore, retainOnError, fetchImpl });
  const [styleBytes, metadataBytes] = await Promise.all([
    resources.get(STYLE_URL),
    resources.get(METADATA_URL),
  ]);
  let style;
  try {
    const metadata = JSON.parse(metadataBytes);
    if (metadata.minzoom !== 0 || metadata.maxzoom !== 18) {
      throw new Error(`PLAN.IGN zoom metadata changed: ${metadata.minzoom}–${metadata.maxzoom}`);
    }
    style = validateOfficialStyle(JSON.parse(styleBytes), metadata);
  } catch (error) {
    await Promise.all([resources.invalidate?.(STYLE_URL),
      resources.invalidate?.(METADATA_URL)]);
    throw error;
  }
  const version = cacheVersion(styleBytes, metadataBytes, epoch);
  const source = createEpureSource({ generation: epoch, version, style,
    metadata: JSON.parse(metadataBytes), resources });
  const options = { mode: 'static', ratio: 1, request: makeNativeRequest(source) };
  const map = mapFactory ? mapFactory(options) :
    new (await import('@maplibre/maplibre-gl-native')).default.Map(options);
  let activeStyle = source.prepared();
  try { map.load(activeStyle.style); }
  catch (error) { map.release(); throw error; }
  const queue = createRenderQueue(map, { timeoutMs, logger,
    prepare: async (_unused, assertUsable) => {
      const prepared = source.prepared();
      assertUsable();
      if (prepared !== activeStyle) {
        try { map.load(prepared.style); activeStyle = prepared; }
        catch (error) { error.fatalRenderer = true; throw error; }
      }
    },
    validate: async (_unused, error, bytes, meta) => {
      try {
        await source.drain();
        const { width, height } = metatileGeometry(meta);
        if (!error && (!bytes || bytes.length !== width * height * 4)) throw new Error('Invalid Native RGBA result');
      } catch (failure) { source.invalidate(); activeStyle = null; throw failure; }
      if (error) { source.invalidate(); activeStyle = null; }
    },
  });
  return { resources, cacheVersion: version, ...queue };
}
