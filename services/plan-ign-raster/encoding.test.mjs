import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';
import sharp from 'sharp';
import { encodeRemaining, encodeTile, prioritizedTiles, WEBP_QUALITY } from './encoding.mjs';
import { cropForTile, metatileForTile, metatileGeometry, metatileTiles } from './geometry.mjs';

const requested = { z: 17, x: 66309, y: 45633 };
const meta = metatileForTile(requested.z, requested.x, requested.y);
const digest = (bytes) => createHash('sha256').update(bytes).digest('hex');

function syntheticMetatile() {
  const geometry = metatileGeometry(meta);
  const rgba = Buffer.alloc(geometry.width * geometry.height * 4, 255);
  metatileTiles(meta).forEach((tile, index) => {
    const crop = cropForTile(meta, tile);
    const color = [index * 12 + 20, 200 - index * 8, 100 + index * 7, 255];
    for (let y = crop.top; y < crop.top + 512; y++) {
      for (let x = crop.left; x < crop.left + 512; x++) {
        rgba.set(color, (y * geometry.width + x) * 4);
      }
    }
  });
  return rgba;
}

test('the requested XYZ is first on the unchanged global metatile grid', () => {
  const before = structuredClone(meta);
  const ordered = prioritizedTiles(meta, requested);
  assert.deepEqual(ordered[0], requested);
  assert.equal(ordered.length, 16);
  assert.equal(new Set(ordered.map(({ z, x, y }) => `${z}/${x}/${y}`)).size, 16);
  assert.deepEqual(meta, before);
  assert.throws(() => prioritizedTiles(meta, { z: 17, x: 66312, y: 45633 }));
});

test('only the 15 remaining tiles use bounded two-slot concurrency', async () => {
  const ordered = prioritizedTiles(meta, requested);
  let active = 0;
  let maximum = 0;
  const rest = await encodeRemaining(ordered.slice(1), 2, async (tile) => {
    active++;
    maximum = Math.max(maximum, active);
    await new Promise((resolve) => setTimeout(resolve, 2));
    active--;
    return tile;
  });
  assert.deepEqual(rest, ordered.slice(1));
  assert.equal(maximum, 2);
  await assert.rejects(encodeRemaining([], 3, async () => null));
});

test('the production pipeline yields 16 deterministic matching 512px WebP crops', async () => {
  const rgba = syntheticMetatile();
  const ordered = prioritizedTiles(meta, requested);
  const first = await encodeTile(rgba, meta, ordered[0]);
  const rest = await encodeRemaining(ordered.slice(1), 2,
    (tile) => encodeTile(rgba, meta, tile));
  const outputs = [first, ...rest];
  assert.equal(WEBP_QUALITY, 85);
  assert.equal(outputs.length, 16);
  for (let index = 0; index < outputs.length; index++) {
    const output = outputs[index];
    const info = await sharp(output.buffer).metadata();
    assert.equal(info.format, 'webp');
    assert.equal(info.width, 512);
    assert.equal(info.height, 512);
    assert.equal(digest((await encodeTile(rgba, meta, output)).buffer), digest(output.buffer));
    const pixel = await sharp(output.buffer).extract({ left: 256, top: 256,
      width: 1, height: 1 }).removeAlpha().raw().toBuffer();
    const originalIndex = metatileTiles(meta).findIndex((tile) =>
      tile.x === output.x && tile.y === output.y);
    const expected = [originalIndex * 12 + 20, 200 - originalIndex * 8,
      100 + originalIndex * 7];
    for (let channel = 0; channel < 3; channel++) {
      assert(Math.abs(pixel[channel] - expected[channel]) < 8,
        `Wrong crop for ${output.x}/${output.y}, channel ${channel}`);
    }
  }
});
