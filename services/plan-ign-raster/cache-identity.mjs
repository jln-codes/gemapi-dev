import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { BUFFER_PX, METATILE_SIZE, TILE_SIZE } from './geometry.mjs';
import { WEBP_QUALITY } from './encoding.mjs';

export const DEFAULT_CACHE_GENERATION = '2026-09';
const DEPENDENCY_LOCK = readFileSync(new URL('./package-lock.json', import.meta.url));
const ENGINE_REVISION = 1;

export function validateGeneration(value) {
  if (typeof value !== 'string' || !/^[a-z0-9][a-z0-9-]{0,31}$/.test(value)) {
    throw new Error('Invalid CACHE_GENERATION');
  }
  return value;
}

// The operator increments the generation when upstream MVT, sprites or glyphs
// change. The hash catches local style/metadata and renderer policy changes.
export function cacheVersion(styleBytes, metadataBytes, generation = DEFAULT_CACHE_GENERATION) {
  const epoch = validateGeneration(generation);
  const policy = JSON.stringify({
    tileSize: TILE_SIZE, metatileSize: METATILE_SIZE, bufferPx: BUFFER_PX,
    format: 'webp', quality: WEBP_QUALITY, engineRevision: ENGINE_REVISION,
  });
  const digest = createHash('sha256').update(styleBytes).update(metadataBytes)
    .update(DEPENDENCY_LOCK).update(policy).digest('hex').slice(0, 16);
  return `${epoch}-${digest}`;
}
