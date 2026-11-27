import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import test from 'node:test';
import sharp from 'sharp';
import { encodeTile } from './encoding.mjs';
import { cropForTile, metatileForTile, metatileGeometry } from './geometry.mjs';

test('Sharp 0.35.4 keeps a deterministic 512px WebP 85 crop', async () => {
  const tile = { z: 17, x: 66309, y: 45633 };
  const meta = metatileForTile(tile.z, tile.x, tile.y);
  const { width, height } = metatileGeometry(meta);
  const rgba = Buffer.alloc(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const offset = (y * width + x) * 4;
      rgba[offset] = (x * 7 + y) % 256;
      rgba[offset + 1] = (x + y * 3) % 256;
      rgba[offset + 2] = (x * 2 + y * 5) % 256;
      rgba[offset + 3] = 255;
    }
  }
  const started = performance.now();
  const first = await encodeTile(rgba, meta, tile);
  const elapsedMs = Math.round(performance.now() - started);
  const second = await encodeTile(rgba, meta, tile);
  assert.deepEqual(first.buffer, second.buffer);
  const info = await sharp(first.buffer).metadata();
  assert.equal(info.format, 'webp');
  assert.equal(info.width, 512);
  assert.equal(info.height, 512);
  assert(first.buffer.length > 0);
  const decoded = await sharp(first.buffer).removeAlpha().raw().toBuffer();
  const crop = cropForTile(meta, tile);
  let absoluteError = 0;
  for (let y = 0; y < 512; y++) {
    for (let x = 0; x < 512; x++) {
      const source = ((crop.top + y) * width + crop.left + x) * 4;
      const output = (y * 512 + x) * 3;
      for (let channel = 0; channel < 3; channel++) {
        absoluteError += Math.abs(rgba[source + channel] - decoded[output + channel]);
      }
    }
  }
  const meanAbsoluteError = absoluteError / (512 * 512 * 3);
  assert(meanAbsoluteError < 25, `WebP crop quality regressed: MAE ${meanAbsoluteError}`);
  const hash = createHash('sha256').update(first.buffer).digest('hex');
  console.log(`Sharp ${sharp.versions.vips} WebP85: ${elapsedMs} ms, ${first.buffer.length} bytes, MAE ${meanAbsoluteError.toFixed(2)}, RSS ${Math.round(process.memoryUsage().rss / 1048576)} MiB, sha256 ${hash}`);
});
