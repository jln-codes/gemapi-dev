(function (root, factory) {
  const api = factory(root.OfflineBasemapStore || (typeof module === "object" && module.exports
    ? require("./offline-basemap-store.js") : null));
  if (typeof module === "object" && module.exports) module.exports = api;
  root.OfflineBasemapDownload = api;
})(typeof window !== "undefined" ? window : globalThis, function (storage) {
  "use strict";

  const MAX_DOWNLOAD_TILES = 5000;
  const AVERAGE_BYTES = Object.freeze({ webp: 28000, png: 52000, jpeg: 45000 });
  const RETRIES = 2;
  const TIMEOUT_MS = 8000;
  const CONCURRENCY = 3;
  const MAX_LAT = 85.05112878;

  function worldPoint(lon, lat, zoom) {
    const n = 2 ** zoom;
    const radians = Math.max(-MAX_LAT, Math.min(MAX_LAT, lat)) * Math.PI / 180;
    return [Math.max(0, Math.min(n, (lon + 180) / 360 * n)),
      Math.max(0, Math.min(n, (1 - Math.asinh(Math.tan(radians)) / Math.PI) / 2 * n))];
  }

  function inRing(point, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const a = ring[i], b = ring[j];
      if ((a[1] > point[1]) !== (b[1] > point[1]) &&
          point[0] < (b[0] - a[0]) * (point[1] - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside;
    }
    return inside;
  }

  function segmentCrossesBox(a, b, x, y) {
    const inside = (p) => p[0] >= x && p[0] <= x + 1 && p[1] >= y && p[1] <= y + 1;
    if (inside(a) || inside(b)) return true;
    const edges = [ [[x, y], [x + 1, y]], [[x + 1, y], [x + 1, y + 1]],
      [[x + 1, y + 1], [x, y + 1]], [[x, y + 1], [x, y]] ];
    const orientation = (p, q, r) => (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
    for (const [c, d] of edges) {
      const ab1 = orientation(a, b, c), ab2 = orientation(a, b, d);
      const cd1 = orientation(c, d, a), cd2 = orientation(c, d, b);
      if (ab1 * ab2 <= 0 && cd1 * cd2 <= 0) return true;
    }
    return false;
  }

  function tileIntersectsPolygon(x, y, polygon) {
    const corners = [[x + 0.5, y + 0.5], [x, y], [x + 1, y], [x, y + 1], [x + 1, y + 1]];
    if (corners.some((point) => inRing(point, polygon[0]) &&
        !polygon.slice(1).some((hole) => inRing(point, hole)))) return true;
    for (const ring of polygon) {
      for (let i = 1; i < ring.length; i++) {
        if (segmentCrossesBox(ring[i - 1], ring[i], x, y)) return true;
      }
    }
    return false;
  }

  function polygonsAtZoom(geometry, zoom) {
    if (!geometry) return null;
    const polygons = geometry.type === "Polygon" ? [geometry.coordinates]
      : geometry.type === "MultiPolygon" ? geometry.coordinates : null;
    if (!polygons || !polygons.length) throw new TypeError("Géométrie de territoire invalide.");
    let vertices = 0;
    return polygons.map((polygon) => {
      if (!Array.isArray(polygon) || !polygon.length) throw new TypeError("Anneau de territoire absent.");
      return polygon.map((ring) => {
        vertices += ring.length;
        if (!Array.isArray(ring) || ring.length < 4 || vertices > 50000 ||
            ring.some((point) => !Array.isArray(point) || point.length < 2 ||
              !Number.isFinite(point[0]) || !Number.isFinite(point[1]) ||
              Math.abs(point[0]) > 180 || Math.abs(point[1]) > MAX_LAT)) {
          throw new TypeError("Coordonnées de territoire invalides.");
        }
        return ring.map(([lon, lat]) => worldPoint(lon, lat, zoom));
      });
    });
  }

  function scanlineRanges(ring, row) {
    const result = [];
    for (const sample of [row + 0.001, row + 0.5, row + 0.999]) {
      const crossings = [];
      for (let i = 1; i < ring.length; i++) {
        const a = ring[i - 1], b = ring[i];
        if ((a[1] > sample) !== (b[1] > sample)) {
          crossings.push(a[0] + (sample - a[1]) * (b[0] - a[0]) / (b[1] - a[1]));
        }
      }
      crossings.sort((a, b) => a - b);
      for (let i = 0; i + 1 < crossings.length; i += 2) result.push([crossings[i], crossings[i + 1]]);
    }
    for (const point of ring) if (point[1] >= row && point[1] <= row + 1) result.push([point[0], point[0]]);
    return result;
  }

  function planTiles({ bounds, geometry = null, zoomMin, zoomMax, maxTiles = MAX_DOWNLOAD_TILES }) {
    if (!Array.isArray(bounds) || bounds.length !== 4 || !bounds.every(Number.isFinite) ||
        bounds[0] < -180 || bounds[2] > 180 || bounds[0] >= bounds[2] ||
        bounds[1] < -MAX_LAT || bounds[3] > MAX_LAT || bounds[1] >= bounds[3] ||
        !Number.isSafeInteger(zoomMin) || !Number.isSafeInteger(zoomMax) ||
        zoomMin < 0 || zoomMax > 30 || zoomMin > zoomMax ||
        !Number.isSafeInteger(maxTiles) || maxTiles < 1 || maxTiles > storage.MAX_TILES) {
      throw new TypeError("Emprise ou plage de zoom invalide.");
    }
    const keys = new Set(), byZoom = {};
    for (let z = zoomMin; z <= zoomMax; z++) {
      const n = 2 ** z;
      const [west, north] = worldPoint(bounds[0], bounds[3], z);
      const [east, south] = worldPoint(bounds[2], bounds[1], z);
      const minX = Math.max(0, Math.floor(west)), maxX = Math.min(n - 1, Math.floor(east));
      const minY = Math.max(0, Math.floor(north)), maxY = Math.min(n - 1, Math.floor(south));
      const polygons = polygonsAtZoom(geometry, z);
      let count = 0;
      for (let y = minY; y <= maxY; y++) {
        const candidates = new Set();
        if (polygons) {
          for (const polygon of polygons) {
            for (const [lo, hi] of scanlineRanges(polygon[0], y)) {
              for (let x = Math.max(minX, Math.floor(lo) - 1); x <= Math.min(maxX, Math.floor(hi) + 1); x++) {
                candidates.add(x);
                if (candidates.size + keys.size > maxTiles * 8) throw new RangeError("Zone trop étendue pour un paquet.");
              }
            }
          }
        } else {
          if (maxX - minX + 1 + keys.size > maxTiles) throw new RangeError("Zone trop étendue pour un paquet.");
          for (let x = minX; x <= maxX; x++) candidates.add(x);
        }
        for (const x of candidates) {
          if (polygons && !polygons.some((polygon) => tileIntersectsPolygon(x, y, polygon))) continue;
          const key = `${z}/${x}/${y}`;
          if (!keys.has(key)) { keys.add(key); count++; }
          if (keys.size > maxTiles) throw new RangeError("Zone trop étendue pour un paquet.");
        }
      }
      byZoom[z] = count;
    }
    return { keys: [...keys], byZoom, count: keys.size, bounds: [...bounds], zoomMin, zoomMax };
  }

  async function estimateStorage(plan, format, storageManager, { reserveBytes = 100 * 1024 * 1024 } = {}) {
    if (!AVERAGE_BYTES[format]) throw new TypeError("Format de tuile inconnu.");
    const estimatedBytes = plan.count * AVERAGE_BYTES[format];
    let quota = null, usage = null, persisted = null;
    try {
      const estimate = await storageManager?.estimate?.();
      if (Number.isFinite(estimate?.quota) && Number.isFinite(estimate?.usage)) {
        quota = estimate.quota; usage = estimate.usage;
      }
      persisted = typeof storageManager?.persisted === "function" ? await storageManager.persisted() : null;
    } catch (_) { /* Browser estimates are advisory, never a guarantee. */ }
    const freeEstimate = quota === null ? null : Math.max(0, quota - usage);
    const safetyReserve = quota === null ? reserveBytes : Math.max(reserveBytes, Math.ceil(quota * 0.15));
    return { byZoom: plan.byZoom, count: plan.count, bytesPerTileAssumed: AVERAGE_BYTES[format],
      estimatedBytes, quota, usage, freeEstimate, safetyReserve, persisted,
      risk: freeEstimate === null ? "unknown" : estimatedBytes + safetyReserve > freeEstimate ? "high" : "estimated-fit" };
  }

  function sourceTemplate(descriptor, entry, origin, contextKey = null) {
    if (entry?.offline?.status !== "available" || entry.offline.retention !== "authorized") {
      throw new Error("Préparation locale non autorisée pour ce fond.");
    }
    if (descriptor?.options?.minZoom !== entry.zoom.native_min ||
        descriptor.options.maxNativeZoom !== entry.zoom.native_max ||
        descriptor.options.maxZoom !== entry.zoom.display_max) throw new Error("Zoom TileJSON incompatible.");
    const url = new URL(descriptor.url, origin);
    if (url.origin !== origin || url.search || url.hash) {
      throw new Error("Source de tuiles non autorisée.");
    }
    const template = url.pathname.replace(/%7B/gi, "{").replace(/%7D/gi, "}");
    if (!/^\/basemap\/[a-zA-Z0-9/_-]+\/\{z\}\/\{x\}\/\{y\}\.(?:webp|png|jpg)$/.test(template)) {
      throw new Error("Gabarit XYZ invalide.");
    }
    return template;
  }

  async function generationFor(template, cryptoObject) {
    const bytes = new TextEncoder().encode(template);
    const digest = await cryptoObject.subtle.digest("SHA-256", bytes);
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  function createManager({ store, fetchImpl = globalThis.fetch.bind(globalThis),
    storageManager = globalThis.navigator?.storage, cryptoObject = globalThis.crypto,
    origin = globalThis.location?.origin, onProgress = () => {},
    concurrency = CONCURRENCY, timeoutMs = TIMEOUT_MS } = {}) {
    if (!store || !origin || !cryptoObject?.randomUUID || !cryptoObject?.subtle ||
        !Number.isSafeInteger(concurrency) || concurrency < 1 || concurrency > 4) {
      throw new TypeError("Gestionnaire de téléchargements invalide.");
    }
    const running = new Map();
    async function requestPersistence() {
      return typeof storageManager?.persist === "function" ? storageManager.persist() : false;
    }
    async function prepare({ entry, descriptor, territoryId, contextKey, bounds, geometry, zoomMin, zoomMax,
      plannedKeys = null }) {
      const template = sourceTemplate(descriptor, entry, origin, contextKey);
      if (zoomMin < entry.offline.min_zoom || zoomMax > entry.offline.max_zoom) {
        throw new Error("Zoom hors du contrat de préparation.");
      }
      let plan;
      if (plannedKeys) {
        if (!Array.isArray(plannedKeys) || !plannedKeys.length || plannedKeys.length > MAX_DOWNLOAD_TILES) {
          throw new Error("Plan de mise à jour invalide.");
        }
        const keys = [...new Set(plannedKeys.map((key) => {
          const tile = storage.parseTileKey(key);
          if (tile.z < zoomMin || tile.z > zoomMax) throw new Error("Zoom du plan de mise à jour invalide.");
          return tile.key;
        }))];
        const byZoom = Object.fromEntries(Array.from({ length: zoomMax - zoomMin + 1 }, (_, index) => [zoomMin + index, 0]));
        keys.forEach((key) => { byZoom[Number(key.split("/")[0])]++; });
        plan = { keys, count: keys.length, byZoom, bounds, zoomMin, zoomMax };
      } else plan = planTiles({ bounds, geometry, zoomMin, zoomMax });
      const estimate = await estimateStorage(plan, entry.tile_format, storageManager);
      if (estimate.risk === "high") throw new Error("Espace local estimé insuffisant avec la réserve de sécurité.");
      const manifest = await store.create({ contextKey, basemapId: entry.id, territoryId, bounds,
        zoomMin, zoomMax, generation: await generationFor(template, cryptoObject),
        sourceTemplate: template, format: entry.tile_format, expectedKeys: plan.keys });
      return { manifest, estimate };
    }
    function tileUrl(manifest, key) {
      const { z, x, y } = storage.parseTileKey(key);
      return manifest.sourceTemplate.replace("{z}", z).replace("{x}", x).replace("{y}", y);
    }
    async function fetchTile(manifest, key, signal) {
      let lastError;
      for (let attempt = 0; attempt <= RETRIES; attempt++) {
        if (signal.aborted) throw new DOMException("Téléchargement interrompu.", "AbortError");
        const timeout = new AbortController();
        const abort = () => timeout.abort();
        signal.addEventListener("abort", abort, { once: true });
        const timer = setTimeout(() => timeout.abort(), timeoutMs);
        try {
          const response = await fetchImpl(tileUrl(manifest, key), {
            signal: timeout.signal, credentials: "same-origin", cache: "no-store",
          });
          if (response.ok) return response;
          const error = new Error(`Tuile HTTP ${response.status}`);
          error.status = response.status;
          if (![429, 500, 503].includes(response.status)) throw error;
          lastError = error;
        } catch (error) {
          if (signal.aborted) throw new DOMException("Téléchargement interrompu.", "AbortError");
          if (error.status && ![429, 500, 503].includes(error.status)) throw error;
          lastError = error;
        } finally {
          clearTimeout(timer);
          signal.removeEventListener("abort", abort);
        }
        if (attempt < RETRIES) await new Promise((resolve) => setTimeout(resolve, 250 * (attempt + 1)));
      }
      throw lastError;
    }
    async function quotaGuard() {
      const snapshot = await storageManager?.estimate?.();
      if (Number.isFinite(snapshot?.quota) && Number.isFinite(snapshot?.usage) &&
          snapshot.quota - snapshot.usage < Math.max(100 * 1024 * 1024, snapshot.quota * 0.15) + storage.MAX_TILE_BYTES) {
        const error = new Error("Réserve locale insuffisante ; téléchargement suspendu.");
        error.name = "QuotaExceededError";
        throw error;
      }
    }
    async function run(id, contextKey) {
      if (running.has(id)) return running.get(id).promise;
      const controller = new AbortController(), owner = cryptoObject.randomUUID();
      const task = { controller, promise: null };
      task.promise = (async () => {
        if (!await store.acquireLease(id, contextKey, owner)) throw new Error("Paquet déjà téléchargé dans un autre onglet.");
        try {
          let manifest = await store.inspect(id, contextKey);
          if (manifest.state === "ready") return manifest;
          manifest = await store.setState(id, contextKey, "downloading");
          const missing = [];
          for (const key of manifest.expectedKeys) if (!await store.hasTile(id, contextKey, key)) missing.push(key);
          manifest = await store.inspect(id, contextKey);
          onProgress(manifest);
          let cursor = 0, failure = null;
          const worker = async () => {
            while (!controller.signal.aborted && !failure) {
              const key = missing[cursor++];
              if (!key) return;
              try {
                await quotaGuard();
                const response = await fetchTile(manifest, key, controller.signal);
                if (controller.signal.aborted) return;
                const bytes = await store.putTile(id, contextKey, key, response);
                manifest = await store.noteTile(id, contextKey, bytes);
                if (!await store.acquireLease(id, contextKey, owner)) throw new Error("Bail du téléchargement perdu.");
                onProgress(manifest);
              } catch (error) {
                if (!controller.signal.aborted) { failure = error; controller.abort(); }
                return;
              }
            }
          };
          await Promise.all(Array.from({ length: Math.min(concurrency, missing.length) }, worker));
          if (failure) {
            const message = failure.name === "QuotaExceededError" ? "Espace local insuffisant." : failure.message;
            await store.setState(id, contextKey, failure.name === "QuotaExceededError" ? "interrupted" : "error", message);
            throw failure;
          }
          if (controller.signal.aborted) return store.setState(id, contextKey, "interrupted");
          const checked = await store.inspect(id, contextKey, { activate: true });
          if (checked.state !== "ready") return store.setState(id, contextKey, "incomplete", "Tuiles manquantes après vérification.");
          onProgress(checked);
          return checked;
        } finally {
          await store.releaseLease(id, contextKey, owner);
        }
      })().finally(() => running.delete(id));
      running.set(id, task);
      return task.promise;
    }
    async function pause(id, contextKey) {
      const task = running.get(id);
      if (task) {
        task.controller.abort();
        try { await task.promise; } catch (_) { /* The manifest contains the reason. */ }
      }
      const manifest = await store.get(id, contextKey);
      return manifest.state === "downloading" ? store.setState(id, contextKey, "interrupted") : manifest;
    }
    async function cancel(id, contextKey) {
      await pause(id, contextKey);
      await store.remove(id, contextKey);
    }
    return { prepare, run, pause, cancel, requestPersistence, estimateStorage,
      isRunning: (id) => running.has(id) };
  }

  return { planTiles, estimateStorage, sourceTemplate, generationFor, createManager,
    MAX_DOWNLOAD_TILES, AVERAGE_BYTES };
});
