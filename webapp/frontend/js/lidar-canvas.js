/* Shared Leaflet Canvas renderer for the isolated demo and Carte. */
(function (root, factory) {
  const api = factory(root.LeafletLayerOrder);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LidarCanvas = api;
})(globalThis, function (layerOrder) {
  "use strict";
  const LAMBERT93 = "+proj=lcc +lat_1=49 +lat_2=44 +lat_0=46.5 +lon_0=3 " +
    "+x_0=700000 +y_0=6600000 +ellps=GRS80 +units=m +no_defs";
  const MAX_SCREEN_PIXELS = 2_500_000;
  function defineProjection(proj4) { proj4.defs("EPSG:2154", LAMBERT93); }
  function renderSize(map) {
    const size = map.getSize();
    if (!(size.x > 0 && size.y > 0)) throw new Error("Fenêtre LiDAR invalide.");
    const scale = Math.min(1, Math.sqrt(MAX_SCREEN_PIXELS / (size.x * size.y)));
    return { width: Math.max(1, Math.floor(size.x * scale)), height: Math.max(1, Math.floor(size.y * scale)),
      cssWidth: size.x, cssHeight: size.y };
  }
  function viewLevel(map, bbox) {
    const { width, height } = renderSize(map);
    const metresPerPixel = Math.max((bbox[2] - bbox[0]) / width, (bbox[3] - bbox[1]) / height);
    if (!Number.isFinite(metresPerPixel) || metresPerPixel <= 0) throw new Error("Échelle LiDAR invalide.");
    return Math.min(30, Math.max(0, Math.floor(Math.log2(metresPerPixel / 0.5))));
  }
  function projectedBounds(map, proj4) {
    const size = map.getSize(), samples = [];
    for (let i = 0; i <= 16; i++) {
      const x = size.x * i / 16, y = size.y * i / 16;
      samples.push([x, 0], [x, size.y], [0, y], [size.x, y]);
    }
    const points = samples.map(point => {
      const coordinate = map.containerPointToLatLng(point);
      return proj4("EPSG:4326", "EPSG:2154", [coordinate.lng, coordinate.lat]);
    });
    const xs = points.map(point => point[0]), ys = points.map(point => point[1]);
    return [Math.min(...xs) - 0.5, Math.min(...ys) - 0.5,
      Math.max(...xs) + 0.5, Math.max(...ys) + 0.5];
  }
  const LIDAR_PANE = layerOrder?.PANES?.LIDAR || {
    name: "lidarRelief", zIndex: 325, pointerEvents: "none",
  };
  function createOverlay(map, L, pane = LIDAR_PANE.name, zIndex = LIDAR_PANE.zIndex) {
    if (!map.getPane(pane)) map.createPane(pane);
    map.getPane(pane).style.zIndex = String(zIndex);
    map.getPane(pane).style.pointerEvents = LIDAR_PANE.pointerEvents;
    const Overlay = L.Layer.extend({
      onAdd(host) {
        this.map = host;
        this.canvas = L.DomUtil.create("canvas", "", host.getPane(pane));
        this.canvas.style.pointerEvents = "none";
        this.reset();
      },
      onRemove() { this.canvas.remove(); },
      reset() {
        const size = renderSize(this.map);
        this.canvas.width = size.width; this.canvas.height = size.height;
        this.canvas.style.width = `${size.cssWidth}px`; this.canvas.style.height = `${size.cssHeight}px`;
        L.DomUtil.setPosition(this.canvas, this.map.containerPointToLayerPoint([0, 0]));
      },
      clear() { this.reset(); },
    });
    return new Overlay().addTo(map);
  }
  async function sampleView({ map, proj4, core, blocks, mask, assertCurrent = () => {},
    frame = () => new Promise(resolve => requestAnimationFrame(resolve)) }) {
    const { width, height, cssWidth, cssHeight } = renderSize(map);
    const first = blocks.values().next().value;
    const resolution = (Array.isArray(first) ? first[0] : first)?.resolution || core.RESOLUTION;
    let alpha = null;
    if (mask) {
      if (mask.type !== "MultiPolygon" || !Array.isArray(mask.coordinates)) throw new Error("Masque LiDAR invalide.");
      const stencil = document.createElement("canvas");
      stencil.width = width; stencil.height = height;
      const ctx = stencil.getContext("2d", { willReadFrequently: true });
      ctx.beginPath();
      for (const polygon of mask.coordinates) for (const ring of polygon) {
        assertCurrent();
        ring.forEach(([east, north], i) => {
          const [lng, lat] = proj4("EPSG:2154", "EPSG:4326", [east, north]);
          const point = map.latLngToContainerPoint([lat, lng]);
          ctx[i ? "lineTo" : "moveTo"](point.x * width / cssWidth, point.y * height / cssHeight);
        });
        ctx.closePath();
      }
      ctx.fill("evenodd");
      alpha = ctx.getImageData(0, 0, width, height).data;
    }
    const samples = new Float32Array(width * height);
    samples.fill(NaN);
    let maximumError = 0;
    const project = (x, y) => {
      const latlng = map.containerPointToLatLng([x * cssWidth / width, y * cssHeight / height]);
      return proj4("EPSG:4326", "EPSG:2154", [latlng.lng, latlng.lat]);
    };
    function patch(left, top, patchWidth, patchHeight) {
      if (alpha) {
        let covered = false;
        for (let row = top; row < top + patchHeight && !covered; row++) {
          for (let col = left; col < left + patchWidth; col++) {
            if (alpha[(row * width + col) * 4 + 3]) { covered = true; break; }
          }
        }
        if (!covered) return;
      }
      const x0 = left + 0.5, x1 = left + patchWidth - 0.5;
      const y0 = top + 0.5, y1 = top + patchHeight - 0.5;
      const a = project(x0, y0), b = project(x1, y0);
      const c = project(x0, y1), d = project(x1, y1);
      const interpolate = (u, v) => [
        (1 - v) * ((1 - u) * a[0] + u * b[0]) + v * ((1 - u) * c[0] + u * d[0]),
        (1 - v) * ((1 - u) * a[1] + u * b[1]) + v * ((1 - u) * c[1] + u * d[1]),
      ];
      let error = 0;
      for (const [u, v] of [[0.5, 0], [0, 0.5], [0.5, 0.5], [1, 0.5], [0.5, 1]]) {
        const actual = project(x0 + (x1 - x0) * u, y0 + (y1 - y0) * v);
        const approximated = interpolate(u, v);
        error = Math.max(error, Math.hypot(actual[0] - approximated[0], actual[1] - approximated[1]));
      }
      if (error > 0.01 && (patchWidth > 1 || patchHeight > 1)) {
        if (patchWidth <= 8 && patchHeight <= 8) {
          for (let row = 0; row < patchHeight; row++) for (let col = 0; col < patchWidth; col++) {
            const [east, north] = project(left + col + 0.5, top + row + 0.5);
            const at = (top + row) * width + left + col;
            if (!alpha || alpha[at * 4 + 3]) samples[at] = core.sample(blocks, east, north, resolution);
          }
          return;
        }
        const w = Math.ceil(patchWidth / 2), h = Math.ceil(patchHeight / 2);
        patch(left, top, w, h);
        if (patchWidth > w) patch(left + w, top, patchWidth - w, h);
        if (patchHeight > h) patch(left, top + h, w, patchHeight - h);
        if (patchWidth > w && patchHeight > h) patch(left + w, top + h, patchWidth - w, patchHeight - h);
        return;
      }
      maximumError = Math.max(maximumError, error);
      for (let row = 0; row < patchHeight; row++) {
        const v = patchHeight === 1 ? 0 : row / (patchHeight - 1);
        for (let col = 0; col < patchWidth; col++) {
          const u = patchWidth === 1 ? 0 : col / (patchWidth - 1);
          const [east, north] = interpolate(u, v);
          const at = (top + row) * width + left + col;
          if (!alpha || alpha[at * 4 + 3]) samples[at] = core.sample(blocks, east, north, resolution);
        }
      }
    }
    for (let y = 0; y < height; y += 32) {
      assertCurrent();
      for (let x = 0; x < width; x += 32) patch(x, y,
        Math.min(32, width - x), Math.min(32, height - y));
      await frame();
    }
    assertCurrent();
    return { samples, width, height, maximumError };
  }
  function paint(overlay, core, sampled) {
    if (!sampled) return null;
    const range = core.rangeOf(sampled.samples);
    const context = overlay.canvas.getContext("2d");
    if (!range) context.clearRect(0, 0, overlay.canvas.width, overlay.canvas.height);
    else context.putImageData(new ImageData(core.colorize(sampled.samples, range.min, range.max),
      sampled.width, sampled.height), 0, 0);
    return range;
  }
  return { defineProjection, projectedBounds, viewLevel, renderSize, createOverlay, sampleView, paint, MAX_SCREEN_PIXELS };
});
