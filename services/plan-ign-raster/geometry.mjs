// Adapted from Tidalia scripts/raster/raster-geometry.mjs and
// raster-renderer.mjs (main branch, September 2026). Only the XYZ geometry
// required by this isolated experiment is retained. The on-demand grid is
// anchored at global XYZ zero, unlike Tidalia's bbox-anchored batch grid.

export const TILE_SIZE = 512;
export const METATILE_SIZE = 4;
export const BUFFER_PX = 256;

export function metatileForTile(z, x, y, size = METATILE_SIZE) {
  const world = 2 ** z;
  if (!Number.isSafeInteger(z) || z < 0 || z > 26 ||
      !Number.isSafeInteger(x) || !Number.isSafeInteger(y) ||
      x < 0 || y < 0 || x >= world || y >= world ||
      !Number.isSafeInteger(size) || size < 1) {
    throw new Error(`Invalid XYZ tile ${z}/${x}/${y}`);
  }
  const originX = Math.floor(x / size) * size;
  const originY = Math.floor(y / size) * size;
  return {
    z, originX, originY, width: Math.min(size, world - originX),
    height: Math.min(size, world - originY), metatileSize: size,
  };
}

export function metatileTiles(meta) {
  const tiles = [];
  for (let x = meta.originX; x < meta.originX + meta.width; x++) {
    for (let y = meta.originY; y < meta.originY + meta.height; y++) {
      tiles.push({ z: meta.z, x, y });
    }
  }
  return tiles;
}

export function lngLatToTile(z, longitude, latitude) {
  const n = 2 ** z;
  const lat = Math.max(-85.0511287798066, Math.min(85.0511287798066, latitude));
  const rad = lat * Math.PI / 180;
  return {
    x: Math.min(n - 1, Math.max(0, Math.floor((longitude + 180) / 360 * n))),
    y: Math.min(n - 1, Math.max(0, Math.floor((1 - Math.asinh(Math.tan(rad)) / Math.PI) / 2 * n))),
  };
}

export function fractionalTilePointToLngLat(z, x, y) {
  const n = 2 ** z;
  return [x / n * 360 - 180,
    Math.atan(Math.sinh(Math.PI * (1 - 2 * y / n))) * 180 / Math.PI];
}

export function metatileGeometry(meta, tileSize = TILE_SIZE, bufferPx = BUFFER_PX) {
  const world = 2 ** meta.z;
  if (!Number.isSafeInteger(meta.z) || meta.z < 0 || meta.z > 26 ||
      !Number.isSafeInteger(meta.originX) || !Number.isSafeInteger(meta.originY) ||
      !Number.isSafeInteger(meta.width) || !Number.isSafeInteger(meta.height) ||
      meta.originX < 0 || meta.originY < 0 || meta.width < 1 || meta.height < 1 ||
      meta.originX + meta.width > world || meta.originY + meta.height > world ||
      !Number.isSafeInteger(tileSize) || tileSize < 1 ||
      !Number.isSafeInteger(bufferPx) || bufferPx < 0) {
    throw new Error('Invalid metatile geometry');
  }
  const buffer = {
    left: bufferPx,
    top: meta.originY > 0 ? bufferPx : 0,
    right: bufferPx,
    bottom: meta.originY + meta.height < world ? bufferPx : 0,
  };
  const west = meta.originX - buffer.left / tileSize;
  const north = meta.originY - buffer.top / tileSize;
  const east = meta.originX + meta.width + buffer.right / tileSize;
  const south = meta.originY + meta.height + buffer.bottom / tileSize;
  const width = meta.width * tileSize + buffer.left + buffer.right;
  const height = meta.height * tileSize + buffer.top + buffer.bottom;
  if (height > world * tileSize) throw new Error('Viewport exceeds world height');
  return {
    width, height, buffer,
    center: fractionalTilePointToLngLat(meta.z, (west + east) / 2, (north + south) / 2),
    viewport: { center: fractionalTilePointToLngLat(meta.z, (west + east) / 2, (north + south) / 2),
      zoom: meta.z, bearing: 0, pitch: 0, width, height },
  };
}

export function cropForTile(meta, tile, tileSize = TILE_SIZE, bufferPx = BUFFER_PX) {
  const geometry = metatileGeometry(meta, tileSize, bufferPx);
  if (tile.z !== meta.z || tile.x < meta.originX || tile.x >= meta.originX + meta.width ||
      tile.y < meta.originY || tile.y >= meta.originY + meta.height) {
    throw new Error('XYZ tile is outside its metatile');
  }
  return {
    left: geometry.buffer.left + (tile.x - meta.originX) * tileSize,
    top: geometry.buffer.top + (tile.y - meta.originY) * tileSize,
    width: tileSize, height: tileSize,
  };
}
