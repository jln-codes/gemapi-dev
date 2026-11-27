const assert = require("node:assert/strict");
const test = require("node:test");
const storeModule = require("../frontend/js/offline-basemap-store.js");

const PACKAGE_ID = "11111111-1111-4111-8111-111111111111";
const TERRITORY_ID = "22222222-2222-4222-8222-222222222222";
const CONTEXT_KEY = "instance:user:base:epoch";
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg==", "base64");
function spec(overrides = {}) {
  return { contextKey: CONTEXT_KEY, basemapId: "ign-plan", territoryId: TERRITORY_ID,
    bounds: [2, 45, 3, 46], zoomMin: 0, zoomMax: 1, generation: "generation-1",
    sourceTemplate: "/basemap/ign-plan/tiles/generation-1/{z}/{x}/{y}.png", format: "png",
    expectedKeys: ["0/0/0", "1/1/0"], ...overrides };
}
function persistence() {
  const data = Object.fromEntries(["packages", "pointers", "catalogues"].map((name) => [name, new Map()]));
  const key = { packages: "id", pointers: "key", catalogues: "contextKey" };
  return {
    async one(name, id) { return data[name].get(id); },
    async all(name) { return [...data[name].values()]; },
    async write(name, value) { data[name].set(value[key[name]], value); return value; },
    async remove(name, id) { data[name].delete(id); },
    async removePackage(id, pointerKey) {
      if (data.pointers.get(pointerKey)?.packageId === id) data.pointers.delete(pointerKey);
      data.packages.delete(id);
    },
    async mutate(id, callback, pointerKey) {
      const next = callback(data.packages.get(id));
      data.packages.set(id, next);
      if (pointerKey) data.pointers.set(pointerKey, { key: pointerKey, packageId: id });
      return next;
    },
  };
}
function fakeCaches() {
  const all = new Map();
  return {
    all,
    async open(name) {
      if (!all.has(name)) all.set(name, new Map());
      const items = all.get(name);
      return {
        async put(url, response) { items.set(url, response.clone()); },
        async match(url) { return items.get(url)?.clone(); },
        async delete(url) { return items.delete(url); },
      };
    },
    async delete(name) { return all.delete(name); },
    async keys() { return [...all.keys()]; },
  };
}
function harness() {
  const records = persistence(), cacheStorage = fakeCaches();
  let nextId = 1;
  const create = () => storeModule.createStore({ persistence: records, cacheStorage,
    origin: "https://app.test", uuid: () => `${String(nextId++).padStart(8, "0")}-1111-4111-8111-111111111111` });
  return { create, records, cacheStorage };
}
const pngResponse = () => new Response(PNG, { headers: { "Content-Type": "image/png" } });

test("local paths and manifest reject traversal, outside matrices and excessive plans", () => {
  assert.deepEqual(storeModule.parseLocalPath(`/__offline-basemaps/${PACKAGE_ID}/0/0/0`).key, "0/0/0");
  for (const path of ["/__offline-basemaps/../0/0/0", `/__offline-basemaps/${PACKAGE_ID}/20/99999999/0`,
    `/__offline-basemaps/${PACKAGE_ID}/1/01/0`, `/__offline-basemaps/${PACKAGE_ID}/1/0/0.png`]) {
    assert.equal(storeModule.parseLocalPath(path), null);
  }
  assert.throws(() => storeModule.validateSpec(spec({ expectedKeys: ["0/0/0", "0/0/0"] })));
  assert.throws(() => storeModule.validateSpec(spec({ sourceTemplate: "https://evil.test/{z}/{x}/{y}.png" })));
  assert.throws(() => storeModule.validateSpec(spec({ format: "jpeg" })));
  assert.throws(() => storeModule.validateSpec(spec({ expectedKeys: ["2/0/0"] })));
});

test("a package becomes ready only after every native tile is valid", async () => {
  const h = harness(), store = h.create();
  const created = await store.create(spec());
  assert.equal(created.state, "preparing");
  await store.putTile(created.id, spec().contextKey, "0/0/0", pngResponse());
  let verified = await store.inspect(created.id, spec().contextKey, { activate: true });
  assert.equal(verified.state, "preparing");
  assert.equal(verified.verifiedCount, 1);
  await store.putTile(created.id, spec().contextKey, "1/1/0", pngResponse());
  verified = await store.inspect(created.id, spec().contextKey, { activate: true });
  assert.equal(verified.state, "ready");
  assert.equal(verified.verifiedCount, 2);
  assert.equal(verified.storedBytes, 2 * PNG.length);
  assert.equal(await store.activePointer(spec().contextKey, "ign-plan", TERRITORY_ID), created.id);
  assert.equal((await store.resolve(created.id, spec().contextKey, "1/1/0")).headers.get("content-type"), "image/png");
  assert.equal(await store.resolve(created.id, spec().contextKey, "1/0/0"), null);
  assert.equal((await h.create().get(created.id, spec().contextKey)).state, "ready");
});

test("corruption downgrades a ready package and never returns invalid bytes", async () => {
  const h = harness(), store = h.create(), created = await store.create(spec({ expectedKeys: ["0/0/0"] }));
  await store.putTile(created.id, spec().contextKey, "0/0/0", pngResponse());
  await store.inspect(created.id, spec().contextKey, { activate: true });
  const cache = await h.cacheStorage.open(storeModule.cacheName(created.id));
  await cache.put(storeModule.localTileUrl("https://app.test", created.id, "0/0/0"),
    new Response("invalid", { headers: { "Content-Type": "image/png" } }));
  assert.equal(await store.resolve(created.id, spec().contextKey, "0/0/0"), null);
  assert.equal((await store.get(created.id, spec().contextKey)).state, "incomplete");
});

test("contexts, caches and deletion are independent", async () => {
  const h = harness(), store = h.create();
  const first = await store.create(spec({ expectedKeys: ["0/0/0"] }));
  const second = await store.create(spec({ contextKey: "other:user:base:epoch", expectedKeys: ["0/0/0"] }));
  await store.putTile(first.id, spec().contextKey, "0/0/0", pngResponse());
  await store.putTile(second.id, "other:user:base:epoch", "0/0/0", pngResponse());
  await store.inspect(first.id, spec().contextKey, { activate: true });
  await store.inspect(second.id, "other:user:base:epoch", { activate: true });
  await assert.rejects(store.get(first.id, "other:user:base:epoch"));
  await store.remove(first.id, spec().contextKey);
  assert.equal(h.cacheStorage.all.has(storeModule.cacheName(first.id)), false);
  assert.equal(h.cacheStorage.all.has(storeModule.cacheName(second.id)), true);
  assert.equal((await store.list("other:user:base:epoch")).length, 1);
  assert.equal(await store.activePointer(spec().contextKey, "ign-plan", TERRITORY_ID), null);
});

test("invalid MIME or image signature cannot enter the cache", async () => {
  const h = harness(), store = h.create(), created = await store.create(spec());
  await assert.rejects(store.putTile(created.id, spec().contextKey, "0/0/0",
    new Response("<html>", { headers: { "Content-Type": "image/png" } })));
  await assert.rejects(store.putTile(created.id, spec().contextKey, "0/0/0",
    new Response(PNG, { headers: { "Content-Type": "text/html" } })));
  assert.equal(await (await h.cacheStorage.open(storeModule.cacheName(created.id))).match(
    storeModule.localTileUrl("https://app.test", created.id, "0/0/0")), undefined);
});

test("recovery marks abandoned downloads interrupted and cleans targeted deletions", async () => {
  const h = harness(), store = h.create();
  const interrupted = await store.create(spec({ expectedKeys: ["0/0/0"] }));
  const deleting = await store.create(spec({ expectedKeys: ["0/0/0"] }));
  await store.setState(interrupted.id, CONTEXT_KEY, "downloading");
  await h.records.mutate(deleting.id, (row) => ({ ...row, state: "deleting" }));
  await h.cacheStorage.open(storeModule.cacheName(deleting.id));
  const remaining = await h.create().recover(CONTEXT_KEY);
  assert.equal(remaining.length, 1);
  assert.equal(remaining[0].state, "interrupted");
  assert.equal(h.cacheStorage.all.has(storeModule.cacheName(deleting.id)), false);
});

test("recovery removes only orphan basemap caches, not shell or another context", async () => {
  const h = harness(), store = h.create();
  const other = await store.create(spec({ contextKey: "other:user:base:epoch" }));
  const orphan = "digues-basemap-package-33333333-3333-4333-8333-333333333333";
  await h.cacheStorage.open(storeModule.cacheName(other.id));
  await h.cacheStorage.open(orphan);
  await h.cacheStorage.open("digues-shell-v1");
  await store.recover(CONTEXT_KEY);
  assert.equal(h.cacheStorage.all.has(orphan), false);
  assert.equal(h.cacheStorage.all.has(storeModule.cacheName(other.id)), true);
  assert.equal(h.cacheStorage.all.has("digues-shell-v1"), true);
});

test("a new generation never displaces the ready pointer before verification", async () => {
  const h = harness(), store = h.create();
  const old = await store.create(spec({ expectedKeys: ["0/0/0"] }));
  await store.putTile(old.id, CONTEXT_KEY, "0/0/0", pngResponse());
  await store.inspect(old.id, CONTEXT_KEY, { activate: true });
  const next = await store.create(spec({ generation: "generation-2", expectedKeys: ["0/0/0", "1/1/0"] }));
  await store.putTile(next.id, CONTEXT_KEY, "0/0/0", pngResponse());
  await store.inspect(next.id, CONTEXT_KEY, { activate: true });
  assert.equal(await store.activePointer(CONTEXT_KEY, "ign-plan", TERRITORY_ID), old.id);
  assert.equal((await store.resolve(old.id, CONTEXT_KEY, "0/0/0")).status, 200);
  await store.putTile(next.id, CONTEXT_KEY, "1/1/0", pngResponse());
  await store.inspect(next.id, CONTEXT_KEY, { activate: true });
  assert.equal(await store.activePointer(CONTEXT_KEY, "ign-plan", TERRITORY_ID), next.id);
  await store.remove(old.id, CONTEXT_KEY);
  assert.equal((await store.resolve(next.id, CONTEXT_KEY, "1/1/0")).status, 200);
});
