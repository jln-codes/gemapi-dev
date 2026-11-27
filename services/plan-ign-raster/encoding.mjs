import sharp from 'sharp';
import { cropForTile, metatileGeometry, metatileTiles } from './geometry.mjs';

export const WEBP_QUALITY = 85;

export function prioritizedTiles(meta, requested) {
  const all = metatileTiles(meta);
  const index = all.findIndex((tile) => tile.z === requested.z &&
    tile.x === requested.x && tile.y === requested.y);
  if (index === -1) throw new Error('Requested XYZ is outside its metatile');
  return [all[index], ...all.filter((_, position) => position !== index)];
}

export async function encodeTile(rgba, meta, tile) {
  const { width, height } = metatileGeometry(meta);
  if (rgba.length !== width * height * 4) throw new Error('Unexpected metatile RGBA length');
  const buffer = await sharp(rgba, { raw: { width, height, channels: 4,
    premultiplied: true } }).extract(cropForTile(meta, tile))
    .webp({ quality: WEBP_QUALITY }).toBuffer();
  return { ...tile, buffer };
}

// The caller awaits the requested tile before submitting these secondary jobs.
export async function encodeRemaining(tiles, concurrency, encode) {
  if (!Number.isSafeInteger(concurrency) || concurrency < 1 || concurrency > 2) {
    throw new Error('Secondary encoding concurrency must be 1 or 2');
  }
  const results = new Array(tiles.length);
  let cursor = 0;
  await Promise.all(Array.from({ length: Math.min(concurrency, tiles.length) }, async () => {
    while (cursor < tiles.length) {
      const index = cursor++;
      results[index] = await encode(tiles[index]);
    }
  }));
  return results;
}
