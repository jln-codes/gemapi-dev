import { randomUUID } from 'node:crypto';
import { constants } from 'node:fs';
import { lstat, mkdir, open, opendir, readFile, readdir, rename, rm, stat, statfs, utimes, writeFile } from 'node:fs/promises';
import path from 'node:path';

const ORPHAN_AGE_MS = 60 * 60 * 1000;
const KEY = /^[a-z0-9][a-z0-9._-]*(?:\/[a-z0-9][a-z0-9._-]*)*$/;

export class CacheCapacityError extends Error {
  constructor(message) {
    super(message);
    this.code = 'CACHE_CAPACITY';
  }
}

function safeKey(key) {
  if (typeof key !== 'string' || !KEY.test(key) || key.split('/').some((part) => part === '..')) {
    throw new Error('Invalid cache key');
  }
  return key;
}

async function readBounded(filename, maxBytes) {
  const handle = await open(filename, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const chunks = [];
    let total = 0;
    while (total <= maxBytes) {
      const chunk = Buffer.allocUnsafe(Math.min(65_536, maxBytes + 1 - total));
      const { bytesRead } = await handle.read(chunk);
      if (!bytesRead) return Buffer.concat(chunks, total);
      chunks.push(chunk.subarray(0, bytesRead));
      total += bytesRead;
    }
    return null;
  } finally { await handle.close(); }
}

// One process owns one cache tree. All reads, writes and evictions are serialized
// within that owner; a second writer on the same tree is unsupported.
export class FileCache {
  constructor({ root, quotaBytes, minFreeBytes, maxAgeSeconds = 0, activeGeneration = null,
    ignoredDirectories = [], writeGate = async (task) => task(),
    writesEnabled = true, eviction = 'lru',
    freeBytes = async (directory) => {
      const info = await statfs(directory);
      return Number(info.bavail) * Number(info.bsize);
    } }) {
    if (!path.isAbsolute(root) || !Number.isSafeInteger(quotaBytes) || quotaBytes < 1 ||
        !Number.isSafeInteger(minFreeBytes) || minFreeBytes < 0 ||
        !Number.isSafeInteger(maxAgeSeconds) || maxAgeSeconds < 0 ||
        eviction !== 'lru') {
      throw new Error('Invalid cache capacity or root');
    }
    this.root = root;
    this.quotaBytes = quotaBytes;
    this.minFreeBytes = minFreeBytes;
    this.maxAgeMs = maxAgeSeconds * 1000;
    this.activeGeneration = activeGeneration;
    this.ignoredDirectories = new Set(ignoredDirectories);
    this.freeBytes = freeBytes;
    this.writeGate = writeGate;
    this.writesEnabled = writesEnabled;
    this.eviction = eviction;
    this.metrics = { hits: 0, misses: 0, readErrors: 0, writeErrors: 0,
      corrupt: 0, evictions: 0, noSpace: 0 };
    this.pins = new Map();
    this.inventory = null;
    this.occupiedBytes = 0;
    this.lastEvictionAt = null;
    this.tail = Promise.resolve();
  }

  filename(key) { return path.join(this.root, safeKey(key)); }

  async #locked(task) {
    const previous = this.tail;
    let unlock;
    this.tail = new Promise((resolve) => { unlock = resolve; });
    await previous;
    try { return await task(); } finally { unlock(); }
  }

  pin(keys) {
    const validated = keys.map(safeKey);
    for (const key of validated) this.pins.set(key, (this.pins.get(key) || 0) + 1);
    return () => {
      for (const key of validated) {
        const count = this.pins.get(key);
        if (count === 1) this.pins.delete(key);
        else if (count) this.pins.set(key, count - 1);
      }
    };
  }

  async #files(directory = this.root, prefix = '') {
    const entries = await readdir(directory, { withFileTypes: true }).catch((error) => {
      if (error.code === 'ENOENT') return [];
      throw error;
    });
    const files = [];
    for (const entry of entries) {
      // Refuse symlinks and unexpected filesystem entries; never follow them.
      if (!entry.isDirectory() && !entry.isFile()) continue;
      const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
      if (!KEY.test(relative)) continue;
      if (entry.isDirectory() && !prefix && this.ignoredDirectories.has(entry.name)) continue;
      const filename = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        for (const nested of await this.#files(filename, relative)) files.push(nested);
      }
      else {
        const info = await stat(filename).catch((error) => {
          if (error.code === 'ENOENT') return null;
          throw error;
        });
        if (!info) continue;
        if (entry.name.endsWith('.tmp')) {
          if (Date.now() - info.mtimeMs > ORPHAN_AGE_MS) await rm(filename, { force: true });
          continue;
        }
        files.push({ key: relative, filename, size: info.size, atimeMs: info.atimeMs,
          mtimeMs: info.mtimeMs });
      }
    }
    return files;
  }

  async #index() {
    if (!this.inventory) {
      this.inventory = new Map((await this.#files()).map((item) => [item.key, item]));
      this.occupiedBytes = [...this.inventory.values()].reduce((sum, item) => sum + item.size, 0);
    }
    return this.inventory;
  }

  async read(key, validate = () => true, { maxBytes = null } = {}) {
    if (maxBytes !== null && (!Number.isSafeInteger(maxBytes) || maxBytes < 1)) {
      throw new Error('Invalid maximum cache read size');
    }
    return this.#locked(async () => {
      const filename = this.filename(key);
      let bytes;
      try { bytes = maxBytes === null ? await readFile(filename) : await readBounded(filename, maxBytes); } catch (error) {
        if (error.code === 'ENOENT') {
          this.metrics.misses++;
          this.occupiedBytes -= this.inventory?.get(key)?.size || 0;
          this.inventory?.delete(key);
          return null;
        }
        this.metrics.readErrors++;
        throw error;
      }
      if (bytes === null || !(await validate(bytes))) {
        this.metrics.corrupt++;
        this.metrics.misses++;
        await rm(filename, { force: true });
        this.occupiedBytes -= this.inventory?.get(key)?.size || 0;
        this.inventory?.delete(key);
        return null;
      }
      const info = await stat(filename);
      await utimes(filename, new Date(), info.mtime);
      const indexed = this.inventory?.get(key);
      if (indexed) indexed.atimeMs = Date.now();
      this.metrics.hits++;
      return { bytes, stale: this.maxAgeMs > 0 && Date.now() - info.mtimeMs > this.maxAgeMs };
    });
  }

  async write(key, bytes, validate = () => true, { signal } = {}) {
    signal?.throwIfAborted();
    if (!Buffer.isBuffer(bytes) || !bytes.length || !(await validate(bytes))) {
      throw new Error('Refusing an empty or invalid cache buffer');
    }
    return this.writeGate(() => this.#locked(async () => {
      const filename = this.filename(key);
      try {
        if (!this.writesEnabled) {
          this.metrics.noSpace++;
          throw new CacheCapacityError('Cache writes suspended');
        }
        await mkdir(this.root, { recursive: true });
        const inventory = await this.#index();
        const existing = inventory.get(key);
        let occupied = this.occupiedBytes - (existing?.size || 0);
        const free = await this.freeBytes(this.root);
        if (bytes.length > this.quotaBytes) {
          this.metrics.noSpace++;
          throw new CacheCapacityError('Cache quota or disk free-space guard reached');
        }
        if (occupied + bytes.length > this.quotaBytes || free - bytes.length < this.minFreeBytes) {
          const victims = [...inventory.values()]
            .filter((item) => item.key !== key && !this.pins.has(item.key))
            .sort((a, b) => {
              return a.atimeMs - b.atimeMs || a.key.localeCompare(b.key, 'en');
            });
          let index = 0;
          let released = 0;
          while ((occupied + bytes.length > this.quotaBytes || free + released - bytes.length < this.minFreeBytes) && index < victims.length) {
            const victim = victims[index++];
            await rm(victim.filename, { force: true });
            inventory.delete(victim.key);
            occupied -= victim.size;
            released += victim.size;
            this.occupiedBytes -= victim.size;
            this.metrics.evictions++;
            this.lastEvictionAt = new Date().toISOString();
          }
        }
        if (occupied + bytes.length > this.quotaBytes ||
            await this.freeBytes(this.root) - bytes.length < this.minFreeBytes) {
          this.metrics.noSpace++;
          throw new CacheCapacityError('Cache quota reached with protected entries');
        }
        await mkdir(path.dirname(filename), { recursive: true });
        const temporary = `${filename}.${process.pid}.${randomUUID()}.tmp`;
        try {
          await writeFile(temporary, bytes, { flag: 'wx' });
          signal?.throwIfAborted();
          await rename(temporary, filename);
          this.occupiedBytes = occupied + bytes.length;
          inventory.set(key, { key, filename, size: bytes.length,
            atimeMs: Date.now(), mtimeMs: Date.now() });
        } finally {
          await rm(temporary, { force: true });
        }
      } catch (error) {
        if (error.code === 'ENOSPC') this.metrics.noSpace++;
        throw error;
      }
    }), { cache: this, key, bytes, signal }).catch(error => {
      this.metrics.writeErrors++;
      throw error;
    });
  }

  async usage() {
    return this.#locked(async () => { await this.#index(); return this.occupiedBytes; });
  }

  async candidates() {
    return this.#locked(async () => [...(await this.#index()).values()]
      .filter(item => !this.pins.has(item.key)).map(item => ({ ...item })));
  }

  async evict(candidate) {
    return this.#locked(async () => {
      const item = (await this.#index()).get(candidate.key);
      if (!item || this.pins.has(item.key) || item.atimeMs !== candidate.atimeMs ||
          item.mtimeMs !== candidate.mtimeMs || item.size !== candidate.size) return 0;
      await rm(item.filename, { force: true });
      this.inventory.delete(item.key);
      this.occupiedBytes -= item.size;
      this.metrics.evictions++;
      this.lastEvictionAt = new Date().toISOString();
      return item.size;
    });
  }

  async invalidate(key) {
    return this.#locked(async () => {
      await rm(this.filename(key), { force: true });
      this.occupiedBytes -= this.inventory?.get(key)?.size || 0;
      this.inventory?.delete(key);
    });
  }

  async purgeGeneration(generation) {
    safeKey(generation);
    if (generation.includes('/') || generation === this.activeGeneration) {
      throw new Error('Cannot purge the active or invalid generation');
    }
    return this.#locked(async () => {
      const inventory = await this.#index();
      const files = [...inventory.values()];
      let removed = 0;
      for (const item of files) {
        if (!item.key.startsWith(`${generation}/`) || this.pins.has(item.key)) continue;
        await rm(item.filename, { force: true });
        inventory.delete(item.key);
        this.occupiedBytes -= item.size;
        removed++;
      }
      return removed;
    });
  }

  async #observationFiles(directory, prefix, observation) {
    observation.step();
    const info = await lstat(directory).catch(e => e.code === 'ENOENT' ? null : Promise.reject(e));
    if (!info) return [];
    if (!info.isDirectory()) throw new Error('Unsafe observation directory');
    const files = [];
    const entries = await opendir(directory);
    for await (const entry of entries) {
      observation.step();
      if (entry.isSymbolicLink()) throw new Error('Unsafe observation entry');
      const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
      if (!KEY.test(relative)) continue;
      if (entry.isDirectory() && !prefix && this.ignoredDirectories.has(entry.name)) continue;
      const filename = path.join(directory, entry.name);
      if (entry.isDirectory()) files.push(...await this.#observationFiles(filename, relative, observation));
      else if (entry.isFile() && !entry.name.endsWith('.tmp')) {
        const item = await lstat(filename);
        if (!item.isFile()) throw new Error('Changed observation file');
        files.push({ size: item.size });
      }
    }
    return files;
  }

  // Observation never initializes the maintenance index or removes old temporaries.
  async observe(observation) {
    return this.#locked(async () => {
      observation.step();
      if (this.inventory) return { logical_bytes: this.occupiedBytes, file_count: this.inventory.size,
        source: 'owner_inventory', physical_bytes: null };
      const files = await this.#observationFiles(this.root, '', observation);
      return { logical_bytes: files.reduce((n, file) => n + file.size, 0), file_count: files.length,
        source: this.inventory ? 'owner_inventory' : 'readonly_metadata', physical_bytes: null };
    });
  }

  async snapshot() {
    return this.#locked(async () => {
      const files = [...(await this.#index()).values()];
      const sizeBytes = this.occupiedBytes;
      return { sizeBytes, fileCount: files.length, activeGeneration: this.activeGeneration,
        writesEnabled: this.writesEnabled, lastEvictionAt: this.lastEvictionAt,
        eviction: this.eviction,
        oldestModifiedAt: files.length ? new Date(files.reduce((age, item) =>
          Math.min(age, item.mtimeMs), Infinity)).toISOString() : null,
        ...this.metrics };
    });
  }
}
