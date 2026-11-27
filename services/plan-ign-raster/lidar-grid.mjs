// LIDAR_GRID_V1 is a fixed, metric PixelIsArea grid. Its indices do not depend
// on a browser viewport or on the order in which masks are requested.
export const LIDAR_GRID_V1 = Object.freeze({ id: 'epsg2154-050cm-512-v1', crs: 'EPSG:2154',
  originX: 0, originY: 0, resolutionM: 0.5, pixels: 512, blockM: 256 });
export const MAX_PLANNED_BLOCKS = 128;
export const MAX_VIEW_BYTES = 24 * 1024 * 1024;
const MAX_CANDIDATES = 8192;
const MAX_VERTICES = 200_000;
const MAX_CLIPPED_POINTS = 2_000_000;
export function lidarGrid(level = 0) {
  if (!Number.isInteger(level) || level < 0 || level > 30) throw new Error('Invalid LiDAR level');
  return level === 0 ? LIDAR_GRID_V1 : Object.freeze({ ...LIDAR_GRID_V1,
    level, resolutionM: 0.5 * 2 ** level, blockM: 256 * 2 ** level });
}

function finite(value) { return typeof value === 'number' && Number.isFinite(value); }
function bbox(value) {
  if (!Array.isArray(value) || value.length !== 4 || !value.every(finite) ||
      value[0] >= value[2] || value[1] >= value[3]) throw new Error('Invalid EPSG:2154 bbox');
  return value;
}
function grid(definition) {
  const expected = lidarGrid(definition?.level);
  if (Object.keys(expected).some(key => definition?.[key] !== expected[key])) {
    throw new Error('Unsupported LiDAR grid');
  }
  return definition;
}
export function blockBBox(ix, iy, definition = LIDAR_GRID_V1) {
  const g = grid(definition);
  if (!Number.isSafeInteger(ix) || !Number.isSafeInteger(iy)) throw new Error('Invalid block index');
  const x = g.originX + ix * g.blockM;
  const y = g.originY + iy * g.blockM;
  if (![x, y, x + g.blockM, y + g.blockM].every(Number.isSafeInteger)) {
    throw new Error('Block outside exact grid range');
  }
  return [x, y, x + g.blockM, y + g.blockM];
}

// Windows are integer cells relative to the south-west of the aligned tile.
// They are derived from authorized masks, never accepted as a free WMS bbox.
export function blockGeometry(block, definition = lidarGrid(block?.level)) {
  const bounds = blockBBox(block?.ix, block?.iy, definition);
  const window = block.window || [0, 0, 512, 512];
  if (!Array.isArray(window) || window.length !== 4 ||
      !window.every(n => Number.isInteger(n) && n >= 0 && n <= 512) ||
      window[0] >= window[2] || window[1] >= window[3]) throw new Error('Invalid LiDAR window');
  const r = definition.resolutionM;
  return { bbox: [bounds[0] + window[0] * r, bounds[1] + window[1] * r,
    bounds[0] + window[2] * r, bounds[1] + window[3] * r],
  width: window[2] - window[0], height: window[3] - window[1], resolutionM: r };
}

function clipRing(ring, rect) {
  // Sutherland–Hodgman clips each ring to the convex block/view rectangle.
  let points = ring;
  const sides = [
    [0, rect[0], true], [0, rect[2], false],
    [1, rect[1], true], [1, rect[3], false],
  ];
  for (const [axis, bound, lower] of sides) {
    if (!points.length) return [];
    const inside = (p) => lower ? p[axis] >= bound : p[axis] <= bound;
    const output = [];
    let previous = points.at(-1);
    for (const point of points) {
      const currentInside = inside(point), previousInside = inside(previous);
      if (currentInside !== previousInside) {
        const fraction = (bound - previous[axis]) / (point[axis] - previous[axis]);
        const crossed = [previous[0] + fraction * (point[0] - previous[0]),
          previous[1] + fraction * (point[1] - previous[1])];
        crossed[axis] = bound;
        output.push(crossed);
      }
      if (currentInside) output.push(point);
      previous = point;
    }
    points = output;
  }
  return points;
}
function ringArea(points, rect) {
  let twiceArea = 0;
  for (let i = 0; i < points.length; i++) {
    const a = points[i], b = points[(i + 1) % points.length];
    twiceArea += (a[0] - rect[0]) * (b[1] - rect[1]) -
      (b[0] - rect[0]) * (a[1] - rect[1]);
  }
  return Math.abs(twiceArea) / 2;
}

function intersectBBox(a, b) {
  const intersection = [Math.max(a[0], b[0]), Math.max(a[1], b[1]),
    Math.min(a[2], b[2]), Math.min(a[3], b[3])];
  return intersection[0] < intersection[2] && intersection[1] < intersection[3] ? intersection : null;
}

function polygonsOf(mask) {
  if (mask?.status === 'EMPTY' || mask?.status === 'ANOMALY') return [];
  if (mask?.status !== 'READY' || mask.geometry?.type !== 'MultiPolygon' ||
      !Array.isArray(mask.geometry.coordinates)) throw new Error('Invalid LIDAR_MASK_V1 geometry');
  let vertices = 0;
  return mask.geometry.coordinates.map((polygon) => {
    if (!Array.isArray(polygon) || !polygon.length) throw new Error('Invalid mask polygon');
    const bounds = [Infinity, Infinity, -Infinity, -Infinity];
    const rings = polygon.map((ring) => {
      if (!Array.isArray(ring) || ring.length < 4 || (vertices += ring.length) > MAX_VERTICES ||
          ring.some((p) => !Array.isArray(p) || p.length !== 2 || !p.every(finite)) ||
          ring[0][0] !== ring.at(-1)[0] || ring[0][1] !== ring.at(-1)[1]) {
        throw new Error('Invalid mask ring');
      }
      for (const [x, y] of ring) {
        bounds[0] = Math.min(bounds[0], x); bounds[1] = Math.min(bounds[1], y);
        bounds[2] = Math.max(bounds[2], x); bounds[3] = Math.max(bounds[3], y);
      }
      return ring;
    });
    return { rings, bounds };
  });
}

export function usefulResolutionLevel(mask) {
  // Do not ask for a continental-sized cell around a small buffered object.
  // 2*area/perimeter estimates the useful width (including narrow corridors).
  // Keep several samples across it even when the object is subpixel on screen.
  let width = Infinity;
  for (const polygon of polygonsOf(mask)) {
    let area = 0, perimeter = 0;
    polygon.rings.forEach((ring, index) => {
      area += (index ? -1 : 1) * ringArea(ring, polygon.bounds);
      for (let i = 1; i < ring.length; i++) perimeter += Math.hypot(ring[i][0]-ring[i-1][0], ring[i][1]-ring[i-1][1]);
    });
    if (area > 0 && perimeter > 0) width = Math.min(width, 2 * area / perimeter);
  }
  return Number.isFinite(width) ? Math.min(30,Math.max(0,Math.floor(Math.log2(width / 4 / .5)))) : 0;
}

export function planLidarBlocks(mask, viewBbox, definition = LIDAR_GRID_V1) {
  const g = grid(definition);
  if (mask?.crs !== g.crs || viewBbox?.crs !== g.crs) {
    throw new Error('LiDAR mask and view must declare EPSG:2154');
  }
  const view = bbox(viewBbox.bbox);
  const polygons = polygonsOf(mask);
  if (!polygons.length) return { status: mask.status, grid_id: g.id, blocks: [], estimated_bytes: 0 };
  const planned = new Map(), seen = new Set();
  let estimated_bytes = 0, work = 0, clippedPoints = 0;
  function clippedRing(ring, rect) {
    if ((clippedPoints += ring.length) > MAX_CLIPPED_POINTS) throw new Error('LiDAR clipping work limit exceeded');
    return clipRing(ring, rect);
  }
  function add(ix, iy, window) {
    const full = window.every((n, i) => n === [0, 0, 512, 512][i]);
    const block = { ix, iy, ...(g.level ? { level: g.level } : {}), ...(!full ? {window} : {}) };
    const geometry = blockGeometry(block, g);
    planned.set(`${ix}:${iy}:${window}`, { ...block, bbox: geometry.bbox,
      width: geometry.width, height: geometry.height });
    estimated_bytes += geometry.width * geometry.height * 4;
    if (planned.size > MAX_PLANNED_BLOCKS) throw new Error('LiDAR planned block limit exceeded');
    if (estimated_bytes > MAX_VIEW_BYTES) throw new Error('LiDAR decoded byte limit exceeded');
  }
  function crop(ix, iy, rect) {
    if (!intersectBBox(rect, view)) return;
    if (++work > MAX_CANDIDATES) throw new Error('LiDAR candidate block limit exceeded');
    let area = 0;
    const used = [Infinity, Infinity, -Infinity, -Infinity];
    for (const polygon of polygons) {
      if (!intersectBBox(polygon.bounds, rect)) continue;
      const outer = clippedRing(polygon.rings[0], rect);
      area += ringArea(outer, rect);
      for (const hole of polygon.rings.slice(1)) area -= ringArea(clippedRing(hole, rect), rect);
      for (const [x, y] of outer) {
        used[0] = Math.min(used[0], x); used[1] = Math.min(used[1], y);
        used[2] = Math.max(used[2], x); used[3] = Math.max(used[3], y);
      }
    }
    if (area <= 0) return;
    const base = blockBBox(ix, iy, g), r = g.resolutionM;
    const window = [Math.floor((used[0] - base[0]) / r), Math.floor((used[1] - base[1]) / r),
      Math.ceil((used[2] - base[0]) / r), Math.ceil((used[3] - base[1]) / r)];
    const { bbox: tight, width, height } = blockGeometry({ ix, iy, window }, g);
    // A sparse diagonal or a hole must not become a huge full rectangle.
    // Subdivide only while the window spans >1 cell in both directions.
    // Beyond that, the unavoidable footprint is one pixel at this resolution.
    if (width > 1 && height > 1 && width * height * r * r > 4 * area) {
      const axis = width >= height ? 0 : 1;
      const middle = tight[axis] + Math.floor((axis === 0 ? width : height) / 2) * r;
      const left = [...tight], right = [...tight];
      left[axis + 2] = middle; right[axis] = middle;
      crop(ix, iy, left); crop(ix, iy, right);
    } else {
      const visible = intersectBBox(tight, view);
      if (!visible) return;
      let visibleArea = 0;
      for (const polygon of polygons) {
        if (!intersectBBox(polygon.bounds, visible)) continue;
        visibleArea += ringArea(clippedRing(polygon.rings[0], visible), visible);
        for (const hole of polygon.rings.slice(1)) visibleArea -= ringArea(clippedRing(hole, visible), visible);
      }
      if (visibleArea > 0) add(ix, iy, window);
    }
  }
  for (const polygon of polygons) {
    const bounds = intersectBBox(polygon.bounds, view);
    if (!bounds) continue;
    const minX = Math.floor((bounds[0] - g.originX) / g.blockM);
    const maxX = Math.ceil((bounds[2] - g.originX) / g.blockM) - 1;
    const minY = Math.floor((bounds[1] - g.originY) / g.blockM);
    const maxY = Math.ceil((bounds[3] - g.originY) / g.blockM) - 1;
    if ((maxX - minX + 1) * (maxY - minY + 1) > MAX_CANDIDATES) {
      throw new Error('LiDAR candidate block limit exceeded');
    }
    for (let ix = minX; ix <= maxX; ix++) for (let iy = minY; iy <= maxY; iy++) {
      const key = `${ix}:${iy}`;
      if (seen.has(key)) continue;
      if (seen.size >= MAX_CANDIDATES) throw new Error('LiDAR candidate block limit exceeded');
      const block = blockBBox(ix, iy, g), clipped = intersectBBox(block, view);
      if (!clipped) continue;
      seen.add(key);
      // Keep windows anchored to mask+tile, not subpixel viewport movements.
      // Only windows with real mask area in the view can be acquired.
      crop(ix, iy, block);
    }
  }
  const blocks = [...planned.values()].sort((a, b) => a.ix - b.ix || a.iy - b.iy);
  return { status: 'READY', grid_id: g.id, blocks, estimated_bytes };
}
