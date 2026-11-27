#!/usr/bin/env node
import { performance } from 'node:perf_hooks';
import { metatileForTile } from './geometry.mjs';

const base = (process.argv[2] || 'http://127.0.0.1:8788').replace(/\/$/, '');
const xyz = process.argv.slice(3);
const tiles = (xyz.length ? xyz : [
  '17/66309/45633', // Loire, first request and repeat
  '17/66393/45097', // Paris, first simultaneous request
  '17/66394/45098', // Paris, same metatile sibling
]).map((value) => {
  if (!/^\d+\/\d+\/\d+$/.test(value)) throw new Error(`Invalid XYZ: ${value}`);
  const [z, x, y] = value.split('/').map(Number);
  return { z, x, y };
});

if (tiles.length !== 3) throw new Error('Usage: npm run benchmark -- [base-url] [z/x/y repeat-z/x/y sibling-z/x/y]');
const metaA = metatileForTile(tiles[1].z, tiles[1].x, tiles[1].y);
const metaB = metatileForTile(tiles[2].z, tiles[2].x, tiles[2].y);
if (metaA.z !== metaB.z || metaA.originX !== metaB.originX ||
    metaA.originY !== metaB.originY ||
    (tiles[1].x === tiles[2].x && tiles[1].y === tiles[2].y)) {
  throw new Error('The last two XYZ must be distinct siblings in one metatile');
}

const tilejsonResponse = await fetch(`${base}/basemap/plan-ign.json`);
if (!tilejsonResponse.ok) throw new Error(`TileJSON HTTP ${tilejsonResponse.status}`);
const tileTemplate = (await tilejsonResponse.json()).tiles?.[0];
if (typeof tileTemplate !== 'string') throw new Error('TileJSON has no raster template');

async function health() {
  const response = await fetch(`${base}/health`);
  if (!response.ok) throw new Error(`Health HTTP ${response.status}`);
  return {
    rssMiB: response.headers.get('x-process-rss-mib'),
    renderCount: Number(response.headers.get('x-renderer-render-count')),
    lastRenderMs: response.headers.get('x-renderer-last-render-ms'),
  };
}

async function getTile(tile) {
  const label = `${tile.z}/${tile.x}/${tile.y}`;
  const started = performance.now();
  const url = new URL(tileTemplate.replace('{z}', tile.z)
    .replace('{x}', tile.x).replace('{y}', tile.y), base);
  const response = await fetch(url);
  const bytes = await response.arrayBuffer();
  const result = { label, status: response.status, cache: response.headers.get('x-cache'),
    ms: Math.round(performance.now() - started), bytes: bytes.byteLength };
  if (!response.ok || response.headers.get('content-type') !== 'image/webp') {
    throw new Error(`${label}: HTTP ${response.status}, ${response.headers.get('content-type')}`);
  }
  return result;
}

const before = await health();
const first = await getTile(tiles[0]);
const repeat = await getTile(tiles[0]);
const concurrent = await Promise.all([getTile(tiles[1]), getTile(tiles[2])]);
const after = await health();
for (const result of [first, repeat, ...concurrent]) {
  console.log(`${result.cache.padEnd(4)} ${result.label} HTTP ${result.status} ${result.ms} ms ${result.bytes} B`);
}
console.log(`RENDER count +${after.renderCount - before.renderCount}; last ${after.lastRenderMs} ms`);
console.log(`RSS ${before.rssMiB} → ${after.rssMiB} MiB`);
if (first.cache !== 'MISS' || concurrent.every((result) => result.cache === 'HIT')) {
  console.log('Cache already warm: use a new CACHE_GENERATION to measure cold MISS/WAIT without deleting data.');
}
