(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.OfflineBasemapStore = api;
})(typeof self !== "undefined" ? self : globalThis, function () {
  "use strict";

  const DB_NAME = "digues-app-basemap-packages";
  const DB_VERSION = 2;
  const CACHE_PREFIX = "digues-basemap-package-";
  const LOCAL_PREFIX = "/__offline-basemaps/";
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const ID = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;
  const TILE = /^(0|[1-9][0-9]*)\/(0|[1-9][0-9]*)\/(0|[1-9][0-9]*)$/;
  const MAX_TILES = 20000;
  const MAX_TILE_BYTES = 4 * 1024 * 1024;
  const MIME = Object.freeze({ webp: "image/webp", png: "image/png", jpeg: "image/jpeg" });
  const STATES = new Set(["preparing", "downloading", "interrupted", "incomplete", "ready", "error", "obsolete", "deleting"]);

  function cacheName(id) {
    if (!UUID.test(id)) throw new TypeError("Identifiant de paquet invalide.");
    return CACHE_PREFIX + id.toLowerCase();
  }

  function parseTileKey(value) {
    const match = typeof value === "string" && TILE.exec(value);
    if (!match) throw new TypeError("Coordonnées de tuile invalides.");
    const [z, x, y] = match.slice(1).map(Number);
    if (!Number.isSafeInteger(z) || z > 30 || x >= 2 ** z || y >= 2 ** z) {
      throw new TypeError("Tuile hors de la matrice XYZ.");
    }
    return { z, x, y, key: `${z}/${x}/${y}` };
  }

  function parseLocalPath(pathname) {
    const match = /^\/__offline-basemaps\/([0-9a-f-]{36})\/(0|[1-9][0-9]*)\/(0|[1-9][0-9]*)\/(0|[1-9][0-9]*)$/.exec(pathname);
    if (!match || !UUID.test(match[1])) return null;
    try { return { packageId: match[1].toLowerCase(), ...parseTileKey(match.slice(2).join("/")) }; }
    catch (_) { return null; }
  }

  function localTileUrl(origin, id, key) {
    cacheName(id);
    parseTileKey(key);
    return new URL(`${LOCAL_PREFIX}${id}/${key}`, origin).href;
  }

  function imageBytes(bytes, format) {
    if (!(bytes instanceof ArrayBuffer) || bytes.byteLength < 12 || bytes.byteLength > MAX_TILE_BYTES || !MIME[format]) return false;
    const data = new Uint8Array(bytes);
    if (format === "png") return data.length >= 24 &&
      [137, 80, 78, 71, 13, 10, 26, 10].every((value, index) => data[index] === value);
    if (format === "jpeg") return data.length >= 16 && data[0] === 255 && data[1] === 216 &&
      data[2] === 255 && data.at(-2) === 255 && data.at(-1) === 217;
    return String.fromCharCode(...data.slice(0, 4)) === "RIFF" &&
      String.fromCharCode(...data.slice(8, 12)) === "WEBP";
  }

  function validateSpec(spec) {
    const { contextKey, basemapId, territoryId, bounds, zoomMin, zoomMax,
      generation, sourceTemplate, format, expectedKeys } = spec || {};
    if (typeof contextKey !== "string" || !contextKey || contextKey.length > 180 ||
        !ID.test(basemapId || "") || !UUID.test(territoryId || "") ||
        !Array.isArray(bounds) || bounds.length !== 4 || !bounds.every(Number.isFinite) ||
        bounds[0] < -180 || bounds[2] > 180 || bounds[0] >= bounds[2] ||
        bounds[1] < -85.051129 || bounds[3] > 85.051129 || bounds[1] >= bounds[3] ||
        !Number.isSafeInteger(zoomMin) || !Number.isSafeInteger(zoomMax) ||
        zoomMin < 0 || zoomMax > 30 || zoomMin > zoomMax ||
        typeof generation !== "string" || !/^[a-zA-Z0-9_-]{8,128}$/.test(generation) ||
        typeof sourceTemplate !== "string" ||
        !(/^\/basemap\/[a-zA-Z0-9/_-]+\/\{z\}\/\{x\}\/\{y\}\.(?:webp|png|jpg)$/.test(sourceTemplate)) ||
        !sourceTemplate.endsWith(`.${format === "jpeg" ? "jpg" : format}`) ||
        !MIME[format] || !Array.isArray(expectedKeys) || !expectedKeys.length || expectedKeys.length > MAX_TILES) {
      throw new TypeError("Manifeste de fond invalide.");
    }
    const seen = new Set();
    for (const key of expectedKeys) {
      const tile = parseTileKey(key);
      if (tile.z < zoomMin || tile.z > zoomMax || seen.has(tile.key)) throw new TypeError("Ensemble de tuiles invalide.");
      seen.add(tile.key);
    }
    return { contextKey, basemapId, territoryId: territoryId.toLowerCase(), bounds: [...bounds],
      zoomMin, zoomMax, generation, sourceTemplate, format, expectedKeys: [...seen] };
  }

  function requestResult(request) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  function openDatabase(indexedDB) {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = (event) => {
        const db = request.result;
        if (event.oldVersion === 0) {
          db.createObjectStore("packages", { keyPath: "id" });
          db.createObjectStore("pointers", { keyPath: "key" });
          db.createObjectStore("catalogues", { keyPath: "contextKey" });
        }
        if (!db.objectStoreNames.contains("lidarPlans")) db.createObjectStore("lidarPlans", { keyPath: "key" });
        if (!db.objectStoreNames.contains("lidarBlocks")) db.createObjectStore("lidarBlocks", { keyPath: "key" });
      };
      request.onsuccess = () => {
        const db = request.result;
        if ([...db.objectStoreNames].sort().join(",") !== "catalogues,lidarBlocks,lidarPlans,packages,pointers") {
          db.close(); reject(new Error("Schéma cartographique local incompatible.")); return;
        }
        db.onversionchange = () => db.close();
        resolve(db);
      };
      request.onerror = () => reject(request.error);
    });
  }

  function createPersistence(indexedDB) {
    if (!indexedDB?.open) throw new Error("IndexedDB indisponible.");
    let dbPromise;
    const db = () => dbPromise ||= openDatabase(indexedDB);
    async function one(storeName, key) {
      const connection = await db();
      return requestResult(connection.transaction(storeName, "readonly").objectStore(storeName).get(key));
    }
    async function all(storeName) {
      const connection = await db();
      return requestResult(connection.transaction(storeName, "readonly").objectStore(storeName).getAll());
    }
    async function write(storeName, value) {
      const connection = await db();
      return new Promise((resolve, reject) => {
        const tx = connection.transaction(storeName, "readwrite");
        tx.objectStore(storeName).put(value);
        tx.oncomplete = () => resolve(value);
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error || new Error("Transaction annulée."));
      });
    }
    async function remove(storeName, key) {
      const connection = await db();
      return new Promise((resolve, reject) => {
        const tx = connection.transaction(storeName, "readwrite");
        tx.objectStore(storeName).delete(key);
        tx.oncomplete = resolve;
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error || new Error("Transaction annulée."));
      });
    }
    async function removePackage(id, key) {
      const connection = await db();
      return new Promise((resolve, reject) => {
        const tx = connection.transaction(["packages", "pointers"], "readwrite");
        const pointer = tx.objectStore("pointers").get(key);
        pointer.onsuccess = () => {
          if (pointer.result?.packageId === id) tx.objectStore("pointers").delete(key);
          tx.objectStore("packages").delete(id);
        };
        tx.oncomplete = resolve;
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error || new Error("Transaction annulée."));
      });
    }
    async function mutate(id, callback, pointerKey = null) {
      const connection = await db();
      return new Promise((resolve, reject) => {
        const tx = connection.transaction(pointerKey ? ["packages", "pointers"] : ["packages"], "readwrite");
        let value;
        const request = tx.objectStore("packages").get(id);
        request.onsuccess = () => {
          try {
            value = callback(request.result);
            if (!value) throw new Error("Paquet absent.");
            tx.objectStore("packages").put(value);
            if (pointerKey) tx.objectStore("pointers").put({ key: pointerKey, packageId: id });
          } catch (error) { tx.abort(); reject(error); }
        };
        tx.oncomplete = () => resolve(value);
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error || new Error("Transaction annulée."));
      });
    }
    return { one, all, write, remove, removePackage, mutate };
  }

  function pointerKey(manifest) {
    return `${manifest.contextKey}:${manifest.basemapId}:${manifest.territoryId}`;
  }

  function createStore({ indexedDB = globalThis.indexedDB, cacheStorage = globalThis.caches,
    origin = globalThis.location?.origin, persistence, now = () => Date.now(), uuid = () => globalThis.crypto.randomUUID() } = {}) {
    if (!cacheStorage?.open || !cacheStorage?.delete || !origin || !/^https?:/.test(origin)) {
      throw new Error("Cache Storage ou origine indisponible.");
    }
    const records = persistence || createPersistence(indexedDB);
    const checkContext = (manifest, contextKey) => {
      if (!manifest || manifest.contextKey !== contextKey) throw new Error("Paquet absent de ce contexte.");
      return manifest;
    };
    async function get(id, contextKey) { return checkContext(await records.one("packages", id), contextKey); }
    async function create(spec) {
      const valid = validateSpec(spec);
      const id = uuid().toLowerCase();
      cacheName(id);
      const timestamp = now();
      const manifest = { ...valid, id, state: "preparing", expectedCount: valid.expectedKeys.length,
        verifiedCount: 0, storedBytes: 0, createdAt: timestamp, updatedAt: timestamp,
        lastError: null, leaseOwner: null, leaseUntil: 0 };
      if (await records.one("packages", id)) throw new Error("Identifiant de paquet déjà utilisé.");
      await records.write("packages", manifest);
      return manifest;
    }
    async function list(contextKey) {
      return (await records.all("packages")).filter((row) => row.contextKey === contextKey && row.state !== "deleting");
    }
    async function setState(id, contextKey, state, message = null) {
      if (!STATES.has(state) || state === "ready" || state === "deleting" || state === "preparing") {
        throw new TypeError("Transition de paquet invalide.");
      }
      return records.mutate(id, (old) => {
        const current = checkContext(old, contextKey);
        if (current.state === "deleting" || current.state === "ready" && state !== "obsolete" ||
            current.state === "obsolete" && state !== "downloading") {
          throw new Error("Paquet non modifiable.");
        }
        return { ...current, state, lastError: message, updatedAt: now() };
      });
    }
    async function putTile(id, contextKey, key, response) {
      const manifest = await get(id, contextKey);
      const tile = parseTileKey(key);
      if (!manifest.expectedKeys.includes(tile.key) || manifest.state === "deleting" ||
          !["preparing", "downloading", "interrupted", "incomplete", "error"].includes(manifest.state)) {
        throw new Error("Tuile non attendue ou paquet non modifiable.");
      }
      if (!response?.ok || response.type === "opaque" || response.type === "opaqueredirect" ||
          response.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !== MIME[manifest.format]) {
        throw new Error("Réponse de tuile invalide.");
      }
      const bytes = await response.arrayBuffer();
      if (!imageBytes(bytes, manifest.format)) throw new Error("Image de tuile invalide.");
      const cache = await cacheStorage.open(cacheName(id));
      const url = localTileUrl(origin, id, tile.key);
      await cache.put(url, new Response(bytes, { headers: { "Content-Type": MIME[manifest.format],
        "Content-Length": String(bytes.byteLength), "Cache-Control": "no-store" } }));
      return bytes.byteLength;
    }
    async function inspect(id, contextKey, { activate = false } = {}) {
      const manifest = await get(id, contextKey);
      const cache = await cacheStorage.open(cacheName(id));
      let count = 0, bytes = 0;
      for (const key of manifest.expectedKeys) {
        const response = await cache.match(localTileUrl(origin, id, key));
        if (!response || response.headers.get("content-type") !== MIME[manifest.format]) continue;
        const payload = await response.arrayBuffer();
        if (!imageBytes(payload, manifest.format)) { await cache.delete(localTileUrl(origin, id, key)); continue; }
        count++; bytes += payload.byteLength;
      }
      const complete = count === manifest.expectedCount;
      return records.mutate(id, (old) => {
        const current = checkContext(old, contextKey);
        if (current.state === "deleting") throw new Error("Paquet en suppression.");
        return { ...current, verifiedCount: count, storedBytes: bytes,
          state: complete && activate ? "ready" : current.state === "ready" && !complete ? "incomplete" : current.state,
          updatedAt: now() };
      }, complete && activate ? pointerKey(manifest) : null);
    }
    async function hasTile(id, contextKey, key) {
      const manifest = await get(id, contextKey);
      const tile = parseTileKey(key);
      if (!manifest.expectedKeys.includes(tile.key)) return null;
      const cache = await cacheStorage.open(cacheName(id));
      const url = localTileUrl(origin, id, tile.key);
      const response = await cache.match(url);
      if (!response) return null;
      const bytes = await response.arrayBuffer();
      if (response.headers.get("content-type") === MIME[manifest.format] && imageBytes(bytes, manifest.format)) {
        return bytes.byteLength;
      }
      await cache.delete(url);
      return null;
    }
    async function noteTile(id, contextKey, byteLength) {
      if (!Number.isSafeInteger(byteLength) || byteLength <= 0 || byteLength > MAX_TILE_BYTES) throw new TypeError("Taille de tuile invalide.");
      return records.mutate(id, (old) => {
        const current = checkContext(old, contextKey);
        if (current.state !== "downloading") throw new Error("Paquet non téléchargeable.");
        return { ...current, verifiedCount: Math.min(current.expectedCount, current.verifiedCount + 1),
          storedBytes: current.storedBytes + byteLength, updatedAt: now() };
      });
    }
    async function acquireLease(id, contextKey, owner, durationMs = 20000) {
      if (!UUID.test(owner) || !Number.isSafeInteger(durationMs) || durationMs < 5000 || durationMs > 60000) {
        throw new TypeError("Bail de téléchargement invalide.");
      }
      let acquired = false;
      await records.mutate(id, (old) => {
        const current = checkContext(old, contextKey);
        if (current.leaseOwner && current.leaseOwner !== owner && current.leaseUntil > now()) return current;
        acquired = true;
        return { ...current, leaseOwner: owner, leaseUntil: now() + durationMs };
      });
      return acquired;
    }
    async function releaseLease(id, contextKey, owner) {
      await records.mutate(id, (old) => {
        const current = checkContext(old, contextKey);
        return current.leaseOwner === owner ? { ...current, leaseOwner: null, leaseUntil: 0 } : current;
      });
    }
    async function resolve(id, contextKey, key) {
      const manifest = await get(id, contextKey);
      const tile = parseTileKey(key);
      if (manifest.state !== "ready" || !manifest.expectedKeys.includes(tile.key)) return null;
      const cache = await cacheStorage.open(cacheName(id));
      const url = localTileUrl(origin, id, tile.key);
      const response = await cache.match(url);
      if (response && response.headers.get("content-type") === MIME[manifest.format] &&
          imageBytes(await response.clone().arrayBuffer(), manifest.format)) return response;
      await records.mutate(id, (old) => ({ ...checkContext(old, contextKey), state: "incomplete",
        updatedAt: now(), lastError: "Tuile locale absente ou corrompue." }));
      return null;
    }
    async function remove(id, contextKey) {
      const manifest = await records.mutate(id, (old) => ({ ...checkContext(old, contextKey), state: "deleting", updatedAt: now() }));
      await cacheStorage.delete(cacheName(id));
      await records.removePackage(id, pointerKey(manifest));
    }
    async function recover(contextKey) {
      const allPackages = await records.all("packages");
      const rows = allPackages.filter((row) => row.contextKey === contextKey);
      for (const row of rows) {
        if (row.state === "deleting") {
          await cacheStorage.delete(cacheName(row.id));
          await records.removePackage(row.id, pointerKey(row));
        } else if (row.state === "downloading" && row.leaseUntil <= now()) {
          await records.mutate(row.id, (old) => ({ ...checkContext(old, contextKey),
            state: "interrupted", leaseOwner: null, leaseUntil: 0,
            lastError: "Téléchargement interrompu à la fermeture.", updatedAt: now() }));
        } else if (row.state === "ready") {
          await inspect(row.id, contextKey);
        }
      }
      if (typeof cacheStorage.keys === "function") {
        const known = new Set((await records.all("packages")).map((row) => cacheName(row.id)));
        for (const name of await cacheStorage.keys()) {
          if (name.startsWith(CACHE_PREFIX) && !known.has(name)) await cacheStorage.delete(name);
        }
      }
      return list(contextKey);
    }
    async function saveCatalogue(contextKey, catalogue) {
      if (typeof contextKey !== "string" || !contextKey) throw new TypeError("Contexte invalide.");
      await records.write("catalogues", { contextKey, catalogue, updatedAt: now() });
    }
    async function getCatalogue(contextKey) { return (await records.one("catalogues", contextKey))?.catalogue || null; }
    async function activePointer(contextKey, basemapId, territoryId) {
      return (await records.one("pointers", `${contextKey}:${basemapId}:${territoryId}`))?.packageId || null;
    }
    return { create, get, list, setState, putTile, inspect, hasTile, noteTile,
      acquireLease, releaseLease, resolve, remove, recover, saveCatalogue,
      getCatalogue, activePointer };
  }

  // LiDAR uses the same local cartographic database and persistence operations as
  // basemap packages. Plans are view leases, never a substitute for server rights.
  function createLidarStore({ indexedDB = globalThis.indexedDB, persistence,
    cryptoImpl = globalThis.crypto, now = () => Date.now(),
    core = globalThis.LidarPrototypeCore,
    maxBytes = 96 * 1024 * 1024 } = {}) {
    const records = persistence || createPersistence(indexedDB);
    const BLOCK_ID = /^[0-9a-f]{64}$/;
    const GRID = "epsg2154-050cm-512-v1";
    const MAX_BLOCK_BYTES = 8 * 1024 * 1024;
    const validContext = key => typeof key === "string" && key.length > 0 && key.length <= 180;
    const validSelection = rows => Array.isArray(rows) && rows.length >= 1 && rows.length <= 1024 &&
      rows.every(row => ["SE", "AH"].includes(row?.type) && UUID.test(row.id));
    function selectionKey(rows) {
      if (!validSelection(rows)) throw new TypeError("Sélection LiDAR invalide.");
      return [...new Set(rows.map(row => `${row.type}:${row.id.toLowerCase()}`))].sort().join("|");
    }
    function validBbox(box) {
      return Array.isArray(box) && box.length === 4 && box.every(Number.isFinite) &&
        box[0] < box[2] && box[1] < box[3];
    }
    function validBlock(block) {
      try {
        const { bbox } = core.blockGeometry(block);
        return BLOCK_ID.test(block?.id || "") && Array.isArray(block.bbox) &&
          block.bbox.length === 4 && block.bbox.every((value, i) => value === bbox[i]);
      } catch (_) { return false; }
    }
    const blockKey = (contextKey, id) => `${contextKey}:${GRID}:${id}`;
    async function digest(bytes) {
      return [...new Uint8Array(await cryptoImpl.subtle.digest("SHA-256", bytes))]
        .map(value => value.toString(16).padStart(2, "0")).join("");
    }
    async function putPlan(contextKey, selection, bbox, plan) {
      if (!validContext(contextKey) || !validBbox(bbox) || !Number.isSafeInteger(plan?.policy_version) || plan.policy_version < 1 ||
          !Number.isSafeInteger(plan.buffer_m) || plan.buffer_m < 5 || plan.buffer_m > 100 || plan?.grid_id !== GRID ||
          plan.crs !== "EPSG:2154" || !Array.isArray(plan.blocks) || !plan.blocks.every(validBlock)) {
        throw new TypeError("Plan LiDAR local invalide.");
      }
      core.validatePlan(plan);
      const key = `${contextKey}:${selectionKey(selection)}:${plan.policy_version}:${plan.level || 0}:${bbox.join(",")}`;
      const value = { key, contextKey, selection: selectionKey(selection), bbox: [...bbox],
        blocks: plan.blocks.map(block => ({ ...block, bbox: [...block.bbox] })),
        ensembles: plan.ensembles?.map(({ type, id, status }) => ({ type, id, status })) || [],
        version: plan.version, policy_version: plan.policy_version, buffer_m: plan.buffer_m, crs: plan.crs, level: plan.level || 0, mask: plan.mask,
        grid_id: GRID, updatedAt: now() };
      await records.write("lidarPlans", value);
      const plans = (await records.all("lidarPlans")).filter(row => row.contextKey === contextKey);
      const latest = Math.max(...plans.map(row => row.policy_version || 0));
      // A late response from another tab must never remove a newer policy.
      const current = plans.filter(row => row.policy_version === latest).sort((a, b) =>
        b.updatedAt - a.updatedAt || Number(b.key === key) - Number(a.key === key) || a.key.localeCompare(b.key));
      for (const old of [...plans.filter(row => row.policy_version !== latest), ...current.slice(40)])
        await records.remove("lidarPlans", old.key);
      return value;
    }
    async function findPlan(contextKey, selection, bbox, level = 0) {
      if (!validContext(contextKey) || !validBbox(bbox)) return null;
      // Automatic view selection can reuse an already authorized local plan
      // without a source snapshot. Context/epoch and viewport remain mandatory.
      const key = selection === null ? null : selectionKey(selection);
      const plans = await records.all("lidarPlans");
      const latest = Math.max(0, ...plans.filter(row => row.contextKey === contextKey).map(row => row.policy_version || 0));
      const candidates = plans.filter(row => row.contextKey === contextKey && row.policy_version === latest &&
        (key === null ? validSelection(row.ensembles) && row.selection === selectionKey(row.ensembles) : row.selection === key) &&
        Number.isSafeInteger(row.policy_version) && row.policy_version > 0 &&
        row.version === "LIDAR_DISTRIBUTION_V2" && row.grid_id === GRID && validBbox(row.bbox) && row.bbox[0] < bbox[2] &&
        row.bbox[1] < bbox[3] && row.bbox[2] > bbox[0] && row.bbox[3] > bbox[1]);
      const contained = candidates.filter(row => row.bbox[0] <= bbox[0] && row.bbox[1] <= bbox[1] &&
        row.bbox[2] >= bbox[2] && row.bbox[3] >= bbox[3]);
      const chosen = (contained.length ? contained : candidates)
        .sort((a, b) => Math.abs(a.level - level) - Math.abs(b.level - level) || b.updatedAt - a.updatedAt)[0];
      if (chosen) core.validatePlan(chosen);
      return chosen ? { ...chosen, partialView: !contained.length } : null;
    }
    async function putBlock(contextKey, block, bytes, status = "DATA") {
      if (!validContext(contextKey) || !validBlock(block) ||
          !["DATA", "NO_DATA", "NO_COVERAGE"].includes(status) ||
          (status === "NO_COVERAGE" ? bytes !== null :
            !(bytes instanceof ArrayBuffer) || bytes.byteLength < 8 || bytes.byteLength > MAX_BLOCK_BYTES)) {
        throw new TypeError("Bloc LiDAR local invalide.");
      }
      const rows = (await records.all("lidarBlocks")).filter(row => row.contextKey === contextKey);
      const existing = rows.find(row => row.id === block.id);
      const used = rows.reduce((sum, row) => sum + (row.size || 0), 0) - (existing?.size || 0);
      const incoming = bytes?.byteLength || 0;
      if (used + incoming > maxBytes) {
        for (const row of rows.filter(row => row.id !== block.id).sort((a, b) => a.usedAt - b.usedAt)) {
          await records.remove("lidarBlocks", row.key);
          if ((await records.all("lidarBlocks")).filter(item => item.contextKey === contextKey)
            .reduce((sum, item) => sum + (item.size || 0), 0) + incoming <= maxBytes) break;
        }
      }
      if (incoming > maxBytes) throw new Error("Quota local LiDAR dépassé.");
      const key = blockKey(contextKey, block.id);
      await records.write("lidarBlocks", { key, contextKey, id: block.id, ix: block.ix,
        iy: block.iy, bbox: [...block.bbox], status, bytes, size: incoming,
        sha256: bytes ? await digest(bytes) : null, usedAt: now() });
    }
    async function getBlock(contextKey, block) {
      if (!validContext(contextKey) || !validBlock(block)) return null;
      const key = blockKey(contextKey, block.id);
      const row = await records.one("lidarBlocks", key);
      if (!row || row.contextKey !== contextKey || row.ix !== block.ix || row.iy !== block.iy ||
          row.bbox.some((value, index) => value !== block.bbox[index])) return null;
      if (row.status === "NO_COVERAGE") return { status: row.status, bytes: null };
      if (!(row.bytes instanceof ArrayBuffer) || row.size !== row.bytes.byteLength ||
          row.size > MAX_BLOCK_BYTES || await digest(row.bytes) !== row.sha256) {
        await records.remove("lidarBlocks", key);
        return null;
      }
      return { status: row.status, bytes: row.bytes };
    }
    async function dropBlock(contextKey, block) {
      if (!validContext(contextKey) || !validBlock(block)) return;
      await records.remove("lidarBlocks", blockKey(contextKey, block.id));
    }
    async function inspectPlan(contextKey, plan) {
      let present = 0;
      for (const block of plan.blocks) if (await getBlock(contextKey, block)) present++;
      return { expected: plan.blocks.length, present, ready: present === plan.blocks.length };
    }
    return { putPlan, findPlan, putBlock, getBlock, dropBlock, inspectPlan, selectionKey, GRID, maxBytes };
  }

  return { DB_NAME, CACHE_PREFIX, LOCAL_PREFIX, MIME, MAX_TILES, MAX_TILE_BYTES, cacheName, parseTileKey,
    parseLocalPath, localTileUrl, imageBytes, validateSpec, createPersistence, createStore, createLidarStore };
});
