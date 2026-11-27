import { createHash } from 'node:crypto';
import { LIDAR_GRID_V1, lidarGrid, MAX_VIEW_BYTES, MAX_PLANNED_BLOCKS,
  planLidarBlocks, usefulResolutionLevel } from './lidar-grid.mjs';
import { lidarBlockIdentity } from './lidar-block-cache.mjs';

export const LIDAR_DISTRIBUTION_VERSION = 'LIDAR_DISTRIBUTION_V2';

function viewOf(value) {
  const box = value?.bbox;
  if (value?.crs !== LIDAR_GRID_V1.crs || !Array.isArray(box) || box.length !== 4 ||
      !box.every((number) => typeof number === 'number' && Number.isFinite(number)) ||
      box[0] >= box[2] || box[1] >= box[3] ||
      box.some(n => Math.abs(n) > 1e12)) {
    throw new Error('Invalid bounded LiDAR view');
  }
  lidarGrid(value.level ?? 0);
  return { crs: LIDAR_GRID_V1.crs, bbox: box, level: value.level ?? 0 };
}

export function createLidarDistribution(acquisition, product) {
  if (!acquisition?.getBlock || product?.artifact?.grid_id !== LIDAR_GRID_V1.id) {
    throw new Error('LiDAR acquisition and grid are required');
  }
  function plan({ masks, view }) {
    if (masks?.version !== 'LIDAR_MASK_V1' || masks.crs !== LIDAR_GRID_V1.crs ||
        !Array.isArray(masks.masks) || masks.masks.length < 1 || masks.masks.length > 1024) {
      throw new Error('Invalid LiDAR mask selection');
    }
    const boundedView = viewOf(view);
    const coordinates = [];
    for (const mask of masks.masks) {
      if (!['READY', 'EMPTY', 'ANOMALY'].includes(mask?.status)) {
        throw new Error('Invalid LiDAR mask state');
      }
      if (mask.status === 'READY') {
        if (mask.geometry?.type !== 'MultiPolygon' || !Array.isArray(mask.geometry.coordinates)) {
          throw new Error('Invalid READY LiDAR mask');
        }
        coordinates.push(...mask.geometry.coordinates);
      }
    }
    const union = coordinates.length
      ? { status: 'READY', crs: LIDAR_GRID_V1.crs,
        geometry: { type: 'MultiPolygon', coordinates } }
      : { status: 'EMPTY', crs: LIDAR_GRID_V1.crs };
    // Planning the coverage union is equivalent to planning each READY mask
    // separately and deduplicating blocks. No new geometry is persisted.
    let planned, grid;
    const initialLevel = Math.min(boundedView.level, usefulResolutionLevel(union));
    for (let level = initialLevel; level <= 30; level++) {
      grid = lidarGrid(level);
      try { planned = planLidarBlocks(union, boundedView, grid); break; }
      catch (error) {
        if (!/limit exceeded/.test(error.message)) throw error;
      }
    }
    if (!planned) throw new Error('LiDAR work limit exceeded');
    if (planned.blocks.length > MAX_PLANNED_BLOCKS ||
        planned.estimated_bytes > MAX_VIEW_BYTES) {
      throw new Error('LiDAR block limit exceeded');
    }
    const blocks = planned.blocks.map(block => {
      const identity = lidarBlockIdentity(product, block);
      return { id: createHash('sha256').update(`lidar-distribution-v1:${identity.key}`)
        .digest('hex'), ...block, level: grid.level || 0, availability: 'UNCHECKED' };
    });
    return { version: LIDAR_DISTRIBUTION_VERSION, grid_id: LIDAR_GRID_V1.id,
      crs: LIDAR_GRID_V1.crs, level: grid.level || 0, resolution_m: grid.resolutionM,
      pixels: LIDAR_GRID_V1.pixels, generation: product.artifact.generation,
      view: boundedView, mask: union.geometry || { type: 'MultiPolygon', coordinates: [] },
      blocks, estimated_bytes: planned.estimated_bytes };
  }
  async function read(request, { signal } = {}) {
    const planned = plan(request);
    const wanted = request.block;
    const block = planned.blocks.find((item) => item.id === wanted?.id &&
      item.ix === wanted?.ix && item.iy === wanted?.iy);
    if (!block) return null;
    const acquired = await acquisition.getBlock(block, { signal });
    return { block, acquired, planned };
  }
  return { plan, read };
}
