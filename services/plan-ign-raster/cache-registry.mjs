import path from 'node:path';
import { lstat, mkdir, statfs } from 'node:fs/promises';
import { FileCache, CacheCapacityError } from './file-cache.mjs';

export async function assertCacheTree(root, target) {
  const relative = path.relative(root, target);
  if (relative.startsWith('..') || path.isAbsolute(relative)) throw new Error('Unsafe cache path');
  let current = root, device = null;
  for (const part of ['', ...relative.split(path.sep)]) {
    if (part) current = path.join(current, part);
    const info = await lstat(current).catch(error => error.code === 'ENOENT' ? null : Promise.reject(error));
    if (info && (!info.isDirectory() || device !== null && info.dev !== device)) throw new Error('Unsafe cache tree');
    if (info && device === null) device = info.dev;
  }
}

// A single owner, filesystem and envelope for public, reconstructible data.
// Historical instances/ and common/ trees are never adopted or removed here.
export function createCacheRegistry(cacheRoot, catalogue, { activeGenerations = {},
  freeBytes = async directory => { const fs = await statfs(directory); return Number(fs.bavail) * Number(fs.bsize); },
} = {}) {
  const caches = new Map();
  let tail = Promise.resolve(), initialized = false;
  const serialized = async task => {
    const previous = tail; let release;
    tail = new Promise(resolve => { release = resolve; });
    await previous;
    try { return await task(); } finally { release(); }
  };
  const stores = () => [...caches.values()].flatMap(entry => [entry.tiles, entry.resources].filter(Boolean));
  const reject = cache => { cache.metrics.noSpace++; throw new CacheCapacityError('Global cache capacity or protected entries'); };
  const writeGate = (task, { cache, key, bytes, signal }) => serialized(async () => {
    signal?.throwIfAborted();
    await assertCacheTree(cacheRoot, path.dirname(cache.filename(key)));
    if (!initialized) {
      await mkdir(cacheRoot, { recursive: true });
      const device = (await lstat(cacheRoot)).dev;
      for (const store of stores()) {
        await assertCacheTree(cacheRoot, store.root);
        await mkdir(store.root, { recursive: true });
        if ((await lstat(store.root)).dev !== device) throw new Error('Cache products must share a filesystem');
        await store.usage();
      }
      initialized = true;
    }
    const all = stores();
    let used = all.reduce((n, store) => n + store.occupiedBytes, 0);
    const old = cache.inventory.get(key)?.size || 0;
    let free = await freeBytes(cacheRoot);
    // Atomic replacement needs the new temporary in addition to the old file.
    const needed = Math.max(0, used + bytes.length - old - catalogue.capacityBytes,
      catalogue.minFreeBytes + bytes.length - free);
    if (bytes.length > catalogue.capacityBytes) reject(cache);
    const candidates = (needed ? (await Promise.all(all.map(async store => (await store.candidates())
      .filter(item => store !== cache || item.key !== key).map(item => ({ store, item }))))).flat() : [])
      .sort((a, b) => a.item.atimeMs - b.item.atimeMs ||
        (a.store.root + '/' + a.item.key).localeCompare(b.store.root + '/' + b.item.key, 'en'));
    // File lengths bound the logical budget, not physical space reclaimable.
    // Small/sparse/shared files and external writers require statfs observation.
    if (candidates.reduce((n, value) => n + value.item.size, 0) <
        Math.max(0, used + bytes.length - old - catalogue.capacityBytes)) reject(cache);
    for (const candidate of candidates) {
      if (used + bytes.length - old <= catalogue.capacityBytes &&
          free - bytes.length >= catalogue.minFreeBytes) break;
      signal?.throwIfAborted();
      await assertCacheTree(cacheRoot, path.dirname(candidate.item.filename));
      const size = await candidate.store.evict(candidate.item);
      used -= size;
      free = await freeBytes(cacheRoot);
    }
    if (used + bytes.length - old > catalogue.capacityBytes ||
        await freeBytes(cacheRoot) - bytes.length < catalogue.minFreeBytes) reject(cache);
    signal?.throwIfAborted();
    return task();
  });
  for (const [id, policy] of catalogue.products) {
    if (policy.scope !== 'global') throw new Error('Only global public cache products are supported');
    const root = path.join(cacheRoot, policy.directory);
    const common = { minFreeBytes: catalogue.minFreeBytes, quotaBytes: catalogue.capacityBytes,
      freeBytes, writeGate, eviction: 'lru' };
    const resources = policy.resources ? new FileCache({ ...common, root: path.join(root, 'resources'),
      maxAgeSeconds: policy.resource_max_age_seconds, activeGeneration: activeGenerations[id]?.resources }) : null;
    const tiles = new FileCache({ ...common, root, maxAgeSeconds: policy.tile_max_age_seconds,
      activeGeneration: activeGenerations[id]?.tiles || policy.artifact?.generation,
      ignoredDirectories: resources ? ['resources'] : [] });
    caches.set(id, { id, key: id, target: null, kind: 'global', policy, root, tiles, resources });
  }
  caches.resolve = async id => {
    const entry = caches.get(id);
    if (!entry) throw new Error('Unknown cache product');
    await assertCacheTree(cacheRoot, entry.root);
    return entry;
  };
  caches.withWritesPaused = serialized;
  return caches;
}

export async function cacheStats(registry) {
  const products = [];
  for (const { id, key, target, kind, tiles, resources } of registry.values()) {
    const tileStats = await tiles.snapshot(), resourceStats = resources ? await resources.snapshot() : null;
    products.push({ id, key, target, kind, sizeBytes: tileStats.sizeBytes + (resourceStats?.sizeBytes || 0),
      fileCount: tileStats.fileCount + (resourceStats?.fileCount || 0), tiles: tileStats, resources: resourceStats });
  }
  return { products };
}
