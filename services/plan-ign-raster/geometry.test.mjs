import test from 'node:test';
import assert from 'node:assert/strict';
import { BUFFER_PX, METATILE_SIZE, TILE_SIZE, cropForTile,
  metatileForTile, metatileGeometry, metatileTiles } from './geometry.mjs';

test('global XYZ grid gives every sibling the same metatile', () => {
  const a = metatileForTile(17, 66213, 45037);
  for (const tile of metatileTiles(a)) assert.deepEqual(metatileForTile(tile.z, tile.x, tile.y), a);
  assert.equal(a.originX % METATILE_SIZE, 0);
  assert.equal(a.originY % METATILE_SIZE, 0);
  assert.equal(metatileTiles(a).length, 16);
  assert.deepEqual(metatileGeometry(a).viewport.width, 2560);
  assert.deepEqual(metatileGeometry(a).viewport.height, 2560);
  assert.deepEqual(cropForTile(a, { z: 17, x: a.originX + 1, y: a.originY + 2 }),
    { left: BUFFER_PX + TILE_SIZE, top: BUFFER_PX + 2 * TILE_SIZE,
      width: TILE_SIZE, height: TILE_SIZE });
});

test('low zoom and world edges retain X wrap and bound Y', () => {
  assert.deepEqual(metatileForTile(0, 0, 0),
    { z: 0, originX: 0, originY: 0, width: 1, height: 1, metatileSize: 4 });
  assert.deepEqual(metatileGeometry(metatileForTile(0, 0, 0)).buffer,
    { left: 256, top: 0, right: 256, bottom: 0 });
  assert.deepEqual(metatileGeometry(metatileForTile(6, 63, 63)).buffer,
    { left: 256, top: 256, right: 256, bottom: 0 });
  assert.throws(() => metatileForTile(6, -1, 0));
  assert.throws(() => metatileForTile(6, 64, 0));
  assert.throws(() => cropForTile(metatileForTile(6, 0, 0), { z: 6, x: 4, y: 0 }));
});
