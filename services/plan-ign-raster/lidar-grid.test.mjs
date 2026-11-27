import assert from 'node:assert/strict';
import test from 'node:test';
import { blockBBox, LIDAR_GRID_V1, planLidarBlocks } from './lidar-grid.mjs';

const rectangle = (x1, y1, x2, y2) => [[x1, y1], [x2, y1], [x2, y2], [x1, y2], [x1, y1]];
const ready = (...polygons) => ({ status: 'READY', crs: 'EPSG:2154',
  geometry: { type: 'MultiPolygon', coordinates: polygons } });
const view = (bbox) => ({ crs: 'EPSG:2154', bbox });
const indices = (plan) => plan.blocks.map(({ ix, iy }) => [ix, iy]);

test('a 512 × 512, 0.5 m grid has stable half-open blocks from origin zero', () => {
  assert.deepEqual(blockBBox(0, 0), [0, 0, 256, 256]);
  assert.deepEqual(blockBBox(-1, 2), [-256, 512, 0, 768]);
  assert.equal(LIDAR_GRID_V1.crs, 'EPSG:2154');
  const mask = ready([rectangle(10, 10, 20, 20)]);
  assert.deepEqual(indices(planLidarBlocks(mask, view([0, 0, 256, 256]))), [[0, 0]]);
  assert.deepEqual(indices(planLidarBlocks(mask, view([9, 9, 21, 21]))), [[0, 0]]);
  assert.deepEqual(indices(planLidarBlocks(ready([rectangle(0, 0, 256, 256)]),
    view([0, 0, 512, 512]))), [[0, 0]]);
});

test('actual mask intersection handles disjoint parts, holes, overlap and view clipping', () => {
  const disjoint = ready([rectangle(1, 1, 20, 20)], [rectangle(1025, 1025, 1040, 1040)]);
  assert.deepEqual(indices(planLidarBlocks(disjoint, view([0, 0, 1280, 1280]))), [[0, 0], [4, 4]]);
  assert.deepEqual(indices(planLidarBlocks(disjoint, view([0, 0, 1000, 1000]))), [[0, 0]]);
  const withHole = ready([rectangle(0, 0, 768, 768), rectangle(256, 256, 512, 512)]);
  const blocks = indices(planLidarBlocks(withHole, view([0, 0, 768, 768])));
  assert.equal(blocks.length, 8);
  assert.equal(blocks.some(([x, y]) => x === 1 && y === 1), false);
  assert.deepEqual(indices(planLidarBlocks(ready([rectangle(1, 1, 20, 20)],
    [rectangle(2, 2, 30, 30)]), view([0, 0, 256, 256]))), [[0, 0]]);
  assert.deepEqual(indices(planLidarBlocks(ready([rectangle(200, 0, 300, 30)]),
    view([260, 0, 280, 30]))), [[1, 0]]);
});

test('EMPTY, ANOMALY and out-of-view masks never schedule a fetch', () => {
  for (const status of ['EMPTY', 'ANOMALY']) {
    assert.deepEqual(planLidarBlocks({ status, crs: 'EPSG:2154', geometry: null },
      view([0, 0, 256, 256])).blocks, []);
  }
  assert.deepEqual(planLidarBlocks(ready([rectangle(0, 0, 100, 100)]),
    view([500, 500, 600, 600])).blocks, []);
});

test('malformed masks and oversized candidate or result sets fail before acquisition', () => {
  assert.throws(() => planLidarBlocks(ready([[[0, 0], [1, 1]]]), view([0, 0, 256, 256])), /Invalid mask ring/);
  assert.throws(() => planLidarBlocks(ready([rectangle(0, 0, 100_000, 100_000)]),
    view([0, 0, 100_000, 100_000])), /candidate block limit/);
  assert.throws(() => planLidarBlocks(ready([rectangle(0, 0, 4096, 4096)]),
    view([0, 0, 4096, 4096])), /decoded byte limit/);
  assert.throws(() => planLidarBlocks({ ...ready([rectangle(0, 0, 1, 1)]), crs: 'EPSG:3857' },
    view([0, 0, 256, 256])), /declare EPSG:2154/);
});
