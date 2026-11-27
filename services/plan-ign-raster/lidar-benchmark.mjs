#!/usr/bin/env node
// Reproducible local I/O benchmark. Writes only disposable synthetic TIFFs in audits/.
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import path from 'node:path';
import { performance } from 'node:perf_hooks';
import { loadCacheProducts } from './cache-products.mjs';
import { cacheStats, createCacheRegistry } from './cache-registry.mjs';
import { CacheSettings } from './cache-settings.mjs';
import { createLidarBlockCacheFactory, LIDAR_PRODUCT_ID } from './lidar-block-cache.mjs';
import { syntheticLidarGeoTiff } from './lidar-fixture.mjs';

const auditRoot = path.resolve(import.meta.dirname, '../../audits');
const productFile = new URL('../../webapp/backend/digues_webapp/cartographic_caches.json', import.meta.url);
const ms = (start) => Math.round((performance.now() - start) * 100) / 100;

async function caseRun(count, catalogue) {
  const root = await mkdtemp(path.join(auditRoot, `lidar-bench-${count}-`));
  try {
    const start = performance.now();
    const registry = createCacheRegistry(root, catalogue);
    const settings = new CacheSettings({ cacheRoot: root, catalogue, registry,
      logger: { info() {}, warn() {}, error() {} } });
    await settings.load();
    const init_ms = ms(start);
    const block = { ix: 0, iy: 0 };
    const bytes = syntheticLidarGeoTiff(block);
    // All callers share one numeric cache and one global envelope.

    const factory = createLidarBlockCacheFactory(registry, catalogue);
    const caches = [];
    const initialRss = process.memoryUsage().rss;
    let peakRss = initialRss;
    const writeStart = performance.now();
    for (let index = 0; index < count; index++) {
      const cache = await factory.get();
      caches.push(cache);
      await cache.write(block, bytes);
      peakRss = Math.max(peakRss, process.memoryUsage().rss);
    }
    const write_ms = ms(writeStart);
    const readStart = performance.now();
    for (const cache of caches) await cache.read(block);
    const read_ms = ms(readStart);
    peakRss = Math.max(peakRss, process.memoryUsage().rss);
    const concurrentStart = performance.now();
    for (let offset = 0; offset < caches.length; offset += 16) {
      await Promise.all(caches.slice(offset, offset + 16).map((cache) => cache.read(block)));
    }
    const concurrent_read_ms = ms(concurrentStart);
    peakRss = Math.max(peakRss, process.memoryUsage().rss);
    const priorHits = caches[0].entry.tiles.metrics.hits;
    const dedupStart = performance.now();
    await Promise.all(Array.from({ length: 20 }, () => caches[0].read(block)));
    const dedup_read_ms = ms(dedupStart);
    const dedup_file_reads = caches[0].entry.tiles.metrics.hits - priorHits;
    const before = caches[0].entry.tiles.metrics.evictions;
    await caches[0].write({ ix: 1, iy: 0 }, syntheticLidarGeoTiff({ ix: 1, iy: 0 }));
    const evictions = caches[0].entry.tiles.metrics.evictions - before;
    const onDisk = (await cacheStats(registry)).products.filter((item) => item.id === LIDAR_PRODUCT_ID);
    const restartStart = performance.now();
    const restarted = createCacheRegistry(root, catalogue);
    const restored = new CacheSettings({ cacheRoot: root, catalogue, registry: restarted,
      logger: { info() {}, warn() {}, error() {} } });
    await restored.load();
    const restart_init_ms = ms(restartStart);
    const inventoryStart = performance.now();
    const afterRestart = (await cacheStats(restarted)).products.filter((item) => item.id === LIDAR_PRODUCT_ID);
    const inventory_ms = ms(inventoryStart);
    peakRss = Math.max(peakRss, process.memoryUsage().rss);
    return { bases: count, init_ms, write_ms, read_ms, concurrent_read_ms,
      dedup_read_ms, dedup_file_reads, restart_init_ms, inventory_ms,
      rss_initial_bytes: initialRss, rss_peak_bytes: peakRss,
      files: onDisk.reduce((sum, item) => sum + item.fileCount, 0),
      disk_bytes: onDisk.reduce((sum, item) => sum + item.sizeBytes, 0),
      restarted_files: afterRestart.reduce((sum, item) => sum + item.fileCount, 0),
      evictions, fixture_bytes: bytes.length };
  } finally { await rm(root, { recursive: true, force: true }); }
}

await mkdir(auditRoot, { recursive: true });
const catalogue = await loadCacheProducts(productFile);
const result = { node: process.version, fixture: 'uncompressed synthetic Float32 GeoTIFF',
  samples: [] };
const requested = process.argv.slice(2);
if (requested.some((value) => !['1', '10', '100'].includes(value))) {
  throw new Error('Usage: node lidar-benchmark.mjs [1|10|100 ...]');
}
for (const count of requested.length ? [...new Set(requested)].map(Number) : [1, 10, 100]) {
  result.samples.push(await caseRun(count, catalogue));
}
console.log(JSON.stringify(result, null, 2));
