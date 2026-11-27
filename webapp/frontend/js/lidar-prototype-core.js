/* Pure numerical parts of the isolated LiDAR prototype. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LidarPrototypeCore = api;
})(globalThis, function () {
  "use strict";

  const PIXELS = 512, RESOLUTION = 0.5, SIDE = PIXELS * RESOLUTION;
  const MAX_TIFF_BYTES = 8 * 1024 * 1024;
  const MAX_BLOCKS = 128, MAX_VIEW_BYTES = 24 * 1024 * 1024;
  const stops = [
    [42, 72, 128], [50, 142, 165], [110, 178, 128],
    [218, 213, 128], [182, 113, 67], [246, 241, 224],
  ];
  const key = (ix, iy) => `${ix}:${iy}`;
  const near = (a, b) => Number.isFinite(a) && Math.abs(a - b) < 1e-6;
  function resolutionAt(level = 0) {
    if (!Number.isInteger(level) || level < 0 || level > 30) throw new Error("Résolution LiDAR invalide.");
    return RESOLUTION * 2 ** level;
  }
  function blockGeometry(block) {
    if (!Number.isSafeInteger(block?.ix) || !Number.isSafeInteger(block?.iy)) throw new Error("Indices LiDAR invalides.");
    const resolution = resolutionAt(block.level), side = PIXELS * resolution;
    const window = block.window || [0, 0, PIXELS, PIXELS];
    if (!Array.isArray(window) || window.length !== 4 ||
        !window.every(n => Number.isInteger(n) && n >= 0 && n <= PIXELS) ||
        window[0] >= window[2] || window[1] >= window[3]) throw new Error("Fenêtre LiDAR invalide.");
    return { resolution, width: window[2] - window[0], height: window[3] - window[1],
      bbox: [block.ix * side + window[0] * resolution, block.iy * side + window[1] * resolution,
        block.ix * side + window[2] * resolution, block.iy * side + window[3] * resolution] };
  }
  function validatePlan(plan) {
    if (plan?.version !== "LIDAR_DISTRIBUTION_V2" || plan.grid_id !== "epsg2154-050cm-512-v1" ||
        plan.crs !== "EPSG:2154" || !Array.isArray(plan.blocks) || plan.blocks.length > MAX_BLOCKS ||
        plan.mask?.type !== "MultiPolygon" || !Array.isArray(plan.mask.coordinates)) {
      throw new Error("Plan LiDAR trop volumineux ou grille inattendue.");
    }
    const resolution = resolutionAt(plan.level);
    if (plan.resolution_m !== undefined && plan.resolution_m !== resolution) throw new Error("Résolution du plan LiDAR incohérente.");
    let bytes = 0, vertices = 0;
    const ids = new Set();
    for (const block of plan.blocks) {
      const geometry = blockGeometry(block);
      if (!/^[0-9a-f]{64}$/.test(block.id) || ids.has(block.id) ||
          !Array.isArray(block.bbox) || block.bbox.length !== 4 ||
          block.bbox.some((value, i) => value !== geometry.bbox[i]) ||
          (block.level || 0) !== (plan.level || 0) ||
          (block.width !== undefined && block.width !== geometry.width) ||
          (block.height !== undefined && block.height !== geometry.height)) throw new Error("Bloc du plan LiDAR invalide.");
      ids.add(block.id);
      bytes += geometry.width * geometry.height * 4;
    }
    for (const polygon of plan.mask.coordinates) {
      if (!Array.isArray(polygon) || !polygon.length) throw new Error("Masque LiDAR invalide.");
      for (const ring of polygon) {
        if (!Array.isArray(ring) || ring.length < 4 || (vertices += ring.length) > 200000 ||
            ring.some(p => !Array.isArray(p) || p.length !== 2 || !p.every(Number.isFinite)) ||
            ring[0].some((n, i) => n !== ring.at(-1)[i])) throw new Error("Masque LiDAR invalide.");
      }
    }
    if (bytes > MAX_VIEW_BYTES) throw new Error("Plan LiDAR trop volumineux.");
    return bytes;
  }

  async function decodeBlock(buffer, block, geotiff) {
    if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 8 ||
        buffer.byteLength > MAX_TIFF_BYTES || !Number.isSafeInteger(block?.ix) ||
        !Number.isSafeInteger(block?.iy)) throw new Error("Bloc GeoTIFF invalide.");
    const tiff = await geotiff.fromArrayBuffer(buffer);
    if (await tiff.getImageCount() !== 1) throw new Error("GeoTIFF multibande ou multipage.");
    const image = await tiff.getImage();
    const geo = image.getGeoKeys();
    const [x, y] = image.getOrigin();
    const [dx, dy] = image.getResolution();
    const bbox = image.getBoundingBox();
    const { resolution, width, height, bbox: expected } = blockGeometry(block);
    if (image.getWidth() !== width || image.getHeight() !== height ||
        image.getSamplesPerPixel() !== 1 || image.getBitsPerSample() !== 32 ||
        geo.GTModelTypeGeoKey !== 1 || geo.GTRasterTypeGeoKey !== 1 ||
        !(geo.ProjectedCSTypeGeoKey === 2154 ||
          (geo.ProjectedCSTypeGeoKey === 32767 &&
           String(geo.GTCitationGeoKey || "").startsWith("EPSG:2154"))) ||
        !near(x, expected[0]) || !near(y, expected[3]) ||
        !near(dx, resolution) || !near(dy, -resolution) ||
        bbox.some((value, index) => !near(value, expected[index]))) {
      throw new Error("Géoréférencement du bloc incohérent avec la grille L2a.");
    }
    const nodataTag = image.getGDALNoData();
    if (nodataTag == null || nodataTag === "") throw new Error("NoData GeoTIFF absent.");
    const nodata = Number(nodataTag);
    if (!Number.isFinite(nodata) && !Number.isNaN(nodata)) {
      throw new Error("NoData GeoTIFF invalide.");
    }
    // Read the received Float32 cells, without decoder interpolation or rounding.
    const values = await image.readRasters({ samples: [0], interleave: true });
    if (!(values instanceof Float32Array) || values.length !== width * height) {
      throw new Error("GeoTIFF Float32 mono-bande requis.");
    }
    return { id: block.id, ix: block.ix, iy: block.iy, level: block.level || 0,
      resolution, width, height, bbox: expected,
      nodata, values, bytes: values.byteLength };
  }

  function sample(blocks, x, y, resolution = RESOLUTION) {
    const side = PIXELS * resolution;
    const ix = Math.floor(x / side);
    // The northern edge belongs to the block below it, whose first row is there.
    const iy = Math.ceil(y / side) - 1;
    const found = blocks.get(key(ix, iy));
    if (!found) return NaN;
    for (const block of Array.isArray(found) ? found : [found]) {
      const col = Math.floor((x - block.bbox[0]) / resolution);
      const row = Math.floor((block.bbox[3] - y) / resolution);
      if (col < 0 || col >= block.width || row < 0 || row >= block.height) continue;
      const value = block.values[row * block.width + col];
      return Number.isFinite(value) &&
        !(Number.isNaN(block.nodata) ? Number.isNaN(value) : value === block.nodata) ? value : NaN;
    }
    return NaN;
  }

  function rangeOf(values) {
    let min = Infinity, max = -Infinity, count = 0;
    for (const value of values) {
      if (!Number.isFinite(value)) continue;
      min = Math.min(min, value); max = Math.max(max, value); count++;
    }
    return count ? { min, max, count } : null;
  }

  function color(value, min, max) {
    if (!Number.isFinite(value)) return [0, 0, 0, 0];
    const t = min === max ? 0.5 : Math.max(0, Math.min(1, (value - min) / (max - min)));
    const segment = Math.min(stops.length - 2, Math.floor(t * (stops.length - 1)));
    const mix = t * (stops.length - 1) - segment;
    return stops[segment].map((channel, i) =>
      Math.round(channel * (1 - mix) + stops[segment + 1][i] * mix)).concat(255);
  }

  function colorize(values, min, max) {
    const rgba = new Uint8ClampedArray(values.length * 4);
    for (let i = 0; i < values.length; i++) {
      const value = values[i];
      if (!Number.isFinite(value)) continue;
      const t = min === max ? 0.5 : Math.max(0, Math.min(1, (value - min) / (max - min)));
      const segment = Math.min(stops.length - 2, Math.floor(t * (stops.length - 1)));
      const mix = t * (stops.length - 1) - segment;
      const from = stops[segment], to = stops[segment + 1], at = i * 4;
      rgba[at] = Math.round(from[0] * (1 - mix) + to[0] * mix);
      rgba[at + 1] = Math.round(from[1] * (1 - mix) + to[1] * mix);
      rgba[at + 2] = Math.round(from[2] * (1 - mix) + to[2] * mix);
      rgba[at + 3] = 255;
    }
    return rgba;
  }

  return { PIXELS, RESOLUTION, SIDE, MAX_BLOCKS, MAX_VIEW_BYTES, resolutionAt, blockGeometry, validatePlan,
    key, decodeBlock, sample, rangeOf, color, colorize };
});
