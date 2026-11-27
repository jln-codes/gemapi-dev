const assert = require("node:assert/strict");
const test = require("node:test");
const crypto = require("node:crypto");
const storage = require("../frontend/js/offline-basemap-store.js");
const download = require("../frontend/js/offline-basemap-download.js");

const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg==", "base64");
const TERRITORY = "22222222-2222-4222-8222-222222222222";
const CONTEXT = "instance:user:base:epoch";
const entry = { id: "ign-plan", tile_format: "png", zoom: { native_min: 0, native_max: 19, display_max: 20 },
  offline: { status: "available", retention: "authorized", min_zoom: 0, max_zoom: 19 } };
const descriptor = { url: "https://app.test/basemap/ign-plan/tiles/generation-1/{z}/{x}/{y}.png",
  options: { minZoom: 0, maxNativeZoom: 19, maxZoom: 20 } };

function harness(fetchImpl, quota = 1024 * 1024 * 1024) {
  const records = Object.fromEntries(["packages", "pointers", "catalogues"].map((name) => [name, new Map()]));
  const key = { packages: "id", pointers: "key", catalogues: "contextKey" };
  const persistence = {
    async one(name, id) { return records[name].get(id); },
    async all(name) { return [...records[name].values()]; },
    async write(name, value) { records[name].set(value[key[name]], value); return value; },
    async removePackage(id, pointerKey) {
      if (records.pointers.get(pointerKey)?.packageId === id) records.pointers.delete(pointerKey);
      records.packages.delete(id);
    },
    async mutate(id, callback, pointerKey) {
      const value = callback(records.packages.get(id));
      records.packages.set(id, value);
      if (pointerKey) records.pointers.set(pointerKey, { key: pointerKey, packageId: id });
      return value;
    },
  };
  const caches = new Map();
  const cacheStorage = {
    async open(name) {
      if (!caches.has(name)) caches.set(name, new Map());
      const map = caches.get(name);
      return { async put(url, response) { map.set(url, response.clone()); },
        async match(url) { return map.get(url)?.clone(); }, async delete(url) { return map.delete(url); } };
    },
    async delete(name) { return caches.delete(name); },
  };
  let usage = 0;
  const storageManager = { async estimate() { return { quota, usage }; },
    async persisted() { return false; }, async persist() { return true; } };
  const store = storage.createStore({ persistence, cacheStorage, origin: "https://app.test",
    uuid: () => crypto.randomUUID() });
  const progress = [];
  const manager = download.createManager({ store, fetchImpl, storageManager, cryptoObject: crypto.webcrypto,
    origin: "https://app.test", onProgress: (row) => progress.push([row.state, row.verifiedCount]) });
  return { store, manager, progress, caches, setUsage(value) { usage = value; } };
}
function prepare(manager, overrides = {}) {
  return manager.prepare({ entry, descriptor, territoryId: TERRITORY, contextKey: CONTEXT,
    bounds: [2, 45, 2.01, 45.01], zoomMin: 0, zoomMax: 1, ...overrides });
}

test("global downloads retain local context separation and reject removed territorial URLs", async () => {
 const contextKey='instance:user:base-a:epoch';
 const seen=[];
 const h=harness(async url=>{seen.push(url);return new Response(PNG,{headers:{'Content-Type':'image/png'}});});
 const {manifest}=await prepare(h.manager,{contextKey});
 assert.equal((await h.manager.run(manifest.id,contextKey)).state,'ready');
 assert(seen.length&&seen.every(url=>url.startsWith('/basemap/')));
 await assert.rejects(h.store.get(manifest.id,CONTEXT),/absent de ce contexte/);
 await assert.rejects(prepare(h.manager,{descriptor:{...descriptor,url:'https://app.test/api/bases/30000000-0000-4000-8000-000000000001/basemaps/ign-plan/tiles/v1/{z}/{x}/{y}.png'}}),/XYZ invalide/);
});

test("XYZ planning uses actual narrow polygon rather than its large bounding box", () => {
  const geometry = { type: "Polygon", coordinates: [[
    [0, 0], [0.002, 0], [1.002, 1], [1, 1], [0, 0],
  ]] };
  const bounds = [0, 0, 1.002, 1];
  const rectangle = download.planTiles({ bounds, zoomMin: 14, zoomMax: 14, maxTiles: 5000 });
  const narrow = download.planTiles({ bounds, geometry, zoomMin: 14, zoomMax: 14, maxTiles: 5000 });
  assert.ok(narrow.count > 0);
  assert.ok(narrow.count < rectangle.count / 5);
  assert.equal(narrow.byZoom[14], narrow.count);
  assert.throws(() => download.planTiles({ bounds, zoomMin: 14, zoomMax: 19 }));
});

test("storage estimate exposes assumed bytes, origin-wide quota and uncertainty", async () => {
  const plan = download.planTiles({ bounds: [2, 45, 2.01, 45.01], zoomMin: 0, zoomMax: 1 });
  const enough = await download.estimateStorage(plan, "png", { estimate: async () => ({ quota: 1024 ** 3, usage: 0 }),
    persisted: async () => false });
  assert.equal(enough.bytesPerTileAssumed, 52000);
  assert.equal(enough.risk, "estimated-fit");
  assert.equal(enough.persisted, false);
  const scarce = await download.estimateStorage(plan, "png", { estimate: async () => ({ quota: 1024 ** 2, usage: 0 }) });
  assert.equal(scarce.risk, "high");
  const unknown = await download.estimateStorage(plan, "png", null);
  assert.equal(unknown.risk, "unknown");
});

test("download retries transient failures, then verifies a persistent ready package", async () => {
  let attempts = 0;
  const h = harness(async () => { attempts++; return attempts === 1 ? new Response("busy", { status: 503 })
    : new Response(PNG, { headers: { "Content-Type": "image/png" } }); });
  const { manifest } = await prepare(h.manager);
  const result = await h.manager.run(manifest.id, CONTEXT);
  assert.equal(result.state, "ready");
  assert.equal(result.verifiedCount, result.expectedCount);
  assert.equal(attempts, result.expectedCount + 1);
  assert.equal((await h.store.get(manifest.id, CONTEXT)).state, "ready");
  assert.equal(await h.manager.requestPersistence(), true);
  assert.equal(h.progress.at(-1)[0], "ready");
});

test("404 is permanent for the attempt and resume skips valid staged tiles", async () => {
  let fail = true, calls = 0;
  const h = harness(async (url) => {
    calls++;
    if (fail && url.includes("/1/")) return new Response("missing", { status: 404 });
    return new Response(PNG, { headers: { "Content-Type": "image/png" } });
  });
  const { manifest } = await prepare(h.manager);
  await assert.rejects(h.manager.run(manifest.id, CONTEXT), /HTTP 404/);
  const staged = await h.store.inspect(manifest.id, CONTEXT);
  assert.ok(staged.verifiedCount < staged.expectedCount);
  fail = false;
  const before = calls;
  const done = await h.manager.run(manifest.id, CONTEXT);
  assert.equal(done.state, "ready");
  assert.equal(calls - before, done.expectedCount - staged.verifiedCount);
});

test("quota reserve suspends writes without deleting cached packages", async () => {
  const h = harness(async () => new Response(PNG, { headers: { "Content-Type": "image/png" } }));
  const { manifest } = await prepare(h.manager);
  h.setUsage(1024 ** 3 - 10 * 1024 * 1024);
  await assert.rejects(h.manager.run(manifest.id, CONTEXT), /Réserve locale insuffisante/);
  assert.equal((await h.store.get(manifest.id, CONTEXT)).state, "interrupted");
  assert.equal(h.caches.size <= 1, true);
});

test("publication and preparation require explicit authorized retention", async () => {
  const h = harness(async () => new Response(PNG, { headers: { "Content-Type": "image/png" } }));
  await assert.rejects(prepare(h.manager, { entry: { ...entry, offline: { ...entry.offline,
    retention: "review-required" } } }), /non autorisée/);
  await assert.rejects(prepare(h.manager, { descriptor: { ...descriptor,
    url: "https://outside.test/tiles/{z}/{x}/{y}.png" } }), /non autorisée/);
});

test("pause survives a manager restart and resume fetches only missing tiles", async () => {
  let hanging = true, calls = 0;
  const fetchImpl = async (_url, { signal }) => {
    calls++;
    if (hanging) return new Promise((_, reject) => {
      signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
    });
    return new Response(PNG, { headers: { "Content-Type": "image/png" } });
  };
  const h = harness(fetchImpl), { manifest } = await prepare(h.manager);
  const pending = h.manager.run(manifest.id, CONTEXT);
  await h.manager.pause(manifest.id, CONTEXT);
  await pending;
  assert.equal((await h.store.get(manifest.id, CONTEXT)).state, "interrupted");
  hanging = false;
  const reopened = download.createManager({ store: h.store, fetchImpl,
    storageManager: { estimate: async () => ({ quota: 1024 ** 3, usage: 0 }) },
    cryptoObject: crypto.webcrypto, origin: "https://app.test" });
  const ready = await reopened.run(manifest.id, CONTEXT);
  assert.equal(ready.state, "ready");
  assert.equal(ready.verifiedCount, ready.expectedCount);
  assert.ok(calls >= ready.expectedCount);
});

test("simultaneous identical runs share one downloader and deletion stays targeted", async () => {
  let calls = 0;
  const h = harness(async () => { calls++; return new Response(PNG, { headers: { "Content-Type": "image/png" } }); });
  const first = (await prepare(h.manager)).manifest;
  const second = (await prepare(h.manager)).manifest;
  const [a, b] = await Promise.all([h.manager.run(first.id, CONTEXT), h.manager.run(first.id, CONTEXT)]);
  assert.equal(a.state, "ready");
  assert.equal(b.state, "ready");
  assert.equal(calls, first.expectedCount);
  await h.manager.cancel(first.id, CONTEXT);
  assert.equal(h.caches.has(storage.cacheName(first.id)), false);
  assert.equal((await h.store.get(second.id, CONTEXT)).state, "preparing");
});

test("transient 429 and timeout retries are bounded", async () => {
  let calls = 0;
  const h = harness(async (_url, { signal }) => {
    calls++;
    if (calls === 1) return new Response("slow", { status: 429 });
    if (calls === 2) return new Promise((_, reject) => {
      signal.addEventListener("abort", () => reject(new DOMException("timeout", "AbortError")), { once: true });
    });
    return new Response(PNG, { headers: { "Content-Type": "image/png" } });
  });
  const { manifest } = await prepare(h.manager);
  const fast = download.createManager({ store: h.store, fetchImpl: async (_url, options) => {
    calls++;
    if (calls === 1) return new Response("slow", { status: 429 });
    if (calls === 2) return new Promise((_, reject) => {
      options.signal.addEventListener("abort", () => reject(new DOMException("timeout", "AbortError")), { once: true });
    });
    return new Response(PNG, { headers: { "Content-Type": "image/png" } });
  }, storageManager: { estimate: async () => ({ quota: 1024 ** 3, usage: 0 }) },
  cryptoObject: crypto.webcrypto, origin: "https://app.test", timeoutMs: 10 });
  const result = await fast.run(manifest.id, CONTEXT);
  assert.equal(result.state, "ready");
  assert.equal(calls, result.expectedCount + 2);
});
