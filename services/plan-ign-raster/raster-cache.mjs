import { randomUUID } from 'node:crypto';
import { mkdir, readFile, readdir, rename, rm, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import sharp from 'sharp';
import { TILE_SIZE } from './geometry.mjs';

export const PROVIDER_STYLE = path.join('ign', 'epure');
const ORPHAN_AGE_MS = 60 * 60 * 1000;

function validateVersion(version) {
  if (typeof version !== 'string' || !/^[a-z0-9][a-z0-9-]{0,63}$/.test(version)) {
    throw new Error('Invalid raster cache generation');
  }
  return version;
}

export function tileKey({ z, x, y }) {
  return `${z}/${x}/${y}`;
}

export async function validateWebp(bytes) {
  if (!Buffer.isBuffer(bytes) || bytes.length < 12 ||
      bytes.toString('ascii', 0, 4) !== 'RIFF' ||
      bytes.toString('ascii', 8, 12) !== 'WEBP' ||
      bytes.readUInt32LE(4) + 8 !== bytes.length) return false;
  try {
    const metadata = await sharp(bytes).metadata();
    return metadata.format === 'webp' && metadata.width === TILE_SIZE &&
      metadata.height === TILE_SIZE;
  } catch {
    return false;
  }
}

export class RasterCache {
  constructor(cacheRoot, version, { validateRaster = validateWebp, store = null } = {}) {
    this.version = validateVersion(version);
    this.root = path.join(cacheRoot, PROVIDER_STYLE, validateVersion(version));
    this.validateRaster = validateRaster;
    this.store = store;
  }

  storeKey(tile) { return `${this.version}/${tileKey(tile)}.webp`; }

  pinTiles(tiles) {
    return this.store?.pin(tiles.map((tile) => this.storeKey(tile))) || (() => {});
  }

  filename({ z, x, y }) {
    return path.join(this.root, String(z), String(x), `${y}.webp`);
  }

  async read(tile) {
    if (this.store) return (await this.store.read(this.storeKey(tile), this.validateRaster))?.bytes || null;
    try {
      const bytes = await readFile(this.filename(tile));
      return await this.validateRaster(bytes) ? bytes : null;
    } catch (error) {
      if (error.code === 'ENOENT') return null;
      throw error;
    }
  }

  async write(tile, bytes) {
    if (this.store) {
      await this.store.write(this.storeKey(tile), bytes, this.validateRaster);
      return;
    }
    if (!Buffer.isBuffer(bytes) || bytes.length === 0 ||
        !(await this.validateRaster(bytes))) {
      throw new Error('Refusing an empty or invalid raster buffer');
    }
    const filename = this.filename(tile);
    const directory = path.dirname(filename);
    await mkdir(directory, { recursive: true });
    await this.cleanupOrphans(tile);
    const temporary = `${filename}.${process.pid}.${randomUUID()}.tmp`;
    try {
      await writeFile(temporary, bytes);
      await rename(temporary, filename);
    } finally {
      await rm(temporary, { force: true });
    }
  }

  // A crashed process can leave .tmp files. Only this XYZ's old temporaries
  // are removed; recent files may belong to another live process.
  async cleanupOrphans(tile, now = Date.now()) {
    const filename = this.filename(tile);
    const directory = path.dirname(filename);
    let entries;
    try {
      entries = await readdir(directory);
    } catch (error) {
      if (error.code === 'ENOENT') return;
      throw error;
    }
    const prefix = `${path.basename(filename)}.`;
    for (const entry of entries) {
      if (!entry.startsWith(prefix) || !entry.endsWith('.tmp')) continue;
      const candidate = path.join(directory, entry);
      const info = await stat(candidate).catch((error) => {
        if (error.code === 'ENOENT') return null;
        throw error;
      });
      if (info?.isFile() && now - info.mtimeMs > ORPHAN_AGE_MS) {
        await rm(candidate, { force: true });
      }
    }
  }
}
