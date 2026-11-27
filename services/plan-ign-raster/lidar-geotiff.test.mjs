import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { fixtureTagOffset, syntheticLidarGeoTiff } from './lidar-fixture.mjs';
import { MAX_GEOTIFF_BYTES, validateLidarGeoTiff } from './lidar-geotiff.mjs';

const block = { ix: 0, iy: 0 };
const mutate = (fn) => { const bytes = syntheticLidarGeoTiff(block); fn(bytes); return bytes; };

test('synthetic uncompressed and DEFLATE Float32 GeoTIFF decode on the exact grid', async () => {
  const normal = await validateLidarGeoTiff(syntheticLidarGeoTiff(block), block);
  assert.deepEqual(normal.bbox, [0, 0, 256, 256]);
  assert.equal(normal.nodata, -9999);
  assert.equal(normal.validPixels, 512 * 512);
  assert.equal(normal.hasElevation, true);
  const ignKeys = await validateLidarGeoTiff(syntheticLidarGeoTiff(block,
    { ignCitation: true }), block);
  assert.equal(ignKeys.crs, 'EPSG:2154');
  const noData = await validateLidarGeoTiff(syntheticLidarGeoTiff(block,
    { value: -9999 }), block);
  assert.equal(noData.validPixels, 0);
  assert.equal(noData.hasElevation, false);
  const compressed = await readFile(new URL('./test-fixtures/lidar-compressed-deflate.tif', import.meta.url));
  assert.equal((await validateLidarGeoTiff(compressed, block)).bytes, compressed.length);
});

test('multiresolution windows retain Float32, exact CRS/extent and reject another resolution', async () => {
  for (const level of [1,4,10,20]) {
    const part = {ix:1,iy:2,level,window:[2,3,9,5]};
    const bytes = syntheticLidarGeoTiff(part, {value:12.125});
    const checked = await validateLidarGeoTiff(bytes,part);
    assert.equal(checked.width,7); assert.equal(checked.height,2);
    assert.equal(checked.validPixels,14);
    await assert.rejects(validateLidarGeoTiff(bytes,{...part,level:level+1}), /alignment/);
    await assert.rejects(validateLidarGeoTiff(bytes,{...part,window:[3,3,10,5]}), /alignment/);
  }
});

test('HTML, signature-only, huge and truncated payloads are rejected', async () => {
  for (const bytes of [Buffer.from('<html>error</html>'), Buffer.from('II*\0'),
    Buffer.alloc(MAX_GEOTIFF_BYTES + 1), syntheticLidarGeoTiff(block).subarray(0, 300)]) {
    await assert.rejects(validateLidarGeoTiff(bytes, block));
  }
});

test('single Float32 band, size, CRS, transform and NoData are mandatory', async () => {
  const variants = [
    mutate((b) => b.writeUInt32LE(513, fixtureTagOffset(b, 256) + 8)),
    mutate((b) => b.writeUInt16LE(2, fixtureTagOffset(b, 277) + 8)),
    mutate((b) => b.writeUInt16LE(1, fixtureTagOffset(b, 339) + 8)),
    mutate((b) => { const at = b.readUInt32LE(fixtureTagOffset(b, 34735) + 8);
      b.writeUInt16LE(3857, at + 30); }),
    mutate((b) => { const at = b.readUInt32LE(fixtureTagOffset(b, 33550) + 8);
      b.writeDoubleLE(1, at); }),
    mutate((b) => b.writeUInt16LE(9999, fixtureTagOffset(b, 42113))),
    mutate((b) => b.writeUInt16LE(5, fixtureTagOffset(b, 259) + 8)),
  ];
  for (const bytes of variants) await assert.rejects(validateLidarGeoTiff(bytes, block));
  await assert.rejects(validateLidarGeoTiff(syntheticLidarGeoTiff(block, { xOffset: 0.5 }), block),
    /grid alignment/);
  await assert.rejects(validateLidarGeoTiff(syntheticLidarGeoTiff(block), { ix: 1, iy: 0 }),
    /grid alignment/);
  await assert.rejects(validateLidarGeoTiff(syntheticLidarGeoTiff(block,
    { ignCitation: true, srid: 3857 }), block), /EPSG:2154/);
});

test('non-finite decoded elevations and malformed IFD offsets are rejected', async () => {
  const invalid = mutate((b) => {
    const offset = b.readUInt32LE(fixtureTagOffset(b, 273) + 8);
    b.writeFloatLE(Infinity, offset);
  });
  await assert.rejects(validateLidarGeoTiff(invalid, block), /Incoherent/);
  const tagOutside = mutate((b) => b.writeUInt32LE(b.length + 1, fixtureTagOffset(b, 33550) + 8));
  await assert.rejects(validateLidarGeoTiff(tagOutside, block), /outside file/);
});
