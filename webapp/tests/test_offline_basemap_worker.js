const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const storeModule = require("../frontend/js/offline-basemap-store.js");

const source = fs.readFileSync(path.join(__dirname, "../frontend/service-worker.js"), "utf8");
const ID = "11111111-1111-4111-8111-111111111111";

function worker({ context = "context-A", available = true, revokeDuringRead = false } = {}) {
  const handlers = new Map();
  let networkCalls = 0;
  const names = new Set(["gemapi-shell-old", "gemapi-shell-v1", "digues-basemap-package-" + ID]);
  const caches = { async keys() { return [...names]; }, async delete(name) { return names.delete(name); } };
  const self = { location: { href: "https://app.test/service-worker.js?v=v1", origin: "https://app.test" },
    clients: { async claim() {}, async get() { return { postMessage(_message, ports) {
      ports[0].postMessage({ contextKey: context });
    } }; } },
    addEventListener(name, callback) { handlers.set(name, callback); } };
  const module = { ...storeModule, createStore: () => ({
    async resolve(id, key, tile) {
      if (revokeDuringRead) context = null;
      return available && id === ID && key === "context-A" && tile === "0/0/0"
        ? new Response("tile", { headers: { "Content-Type": "image/png" } }) : null;
    },
  }) };
  vm.runInNewContext(source, { self, URL, Response, MessageChannel, setTimeout, clearTimeout, caches,
    importScripts(url) { assert.equal(url, "/static/js/offline-basemap-store.js"); self.OfflineBasemapStore = module; },
    fetch() { networkCalls++; throw new Error("The worker must not fetch local tiles."); } });
  async function request(url) {
    let promise;
    handlers.get("fetch")({ request: { method: "GET", url, mode: "same-origin" }, clientId: "client-1",
      respondWith(value) { promise = value; } });
    return promise;
  }
  return { request, calls: () => networkCalls, names,
    async activate() { let task; handlers.get("activate")({ waitUntil(value) { task = value; } }); await task; } };
}

test("synthetic local route serves only the manifest tile and never falls back to network", async () => {
  const runtime = worker();
  const tile = await runtime.request(`https://app.test/__offline-basemaps/${ID}/0/0/0`);
  assert.equal(tile.status, 200);
  assert.equal(await tile.text(), "tile");
  const missing = await runtime.request(`https://app.test/__offline-basemaps/${ID}/1/1/0`);
  assert.equal(missing.status, 404);
  assert.equal(runtime.calls(), 0);
});

test("malformed paths, query strings and wrong context are rejected locally", async () => {
  const runtime = worker({ context: "context-B" });
  assert.equal((await runtime.request(`https://app.test/__offline-basemaps/${ID}/0/0/0`)).status, 404);
  assert.equal((await runtime.request(`https://app.test/__offline-basemaps/${ID}/0/0/0?url=evil`)).status, 400);
  assert.equal((await runtime.request(`https://app.test/__offline-basemaps/${ID}/0/01/0`)).status, 400);
  assert.equal(runtime.calls(), 0);
});

test("service worker update deletes old shell versions but preserves local basemap packages", async () => {
  const runtime = worker();
  await runtime.activate();
  assert.equal(runtime.names.has("gemapi-shell-old"), false);
  assert.equal(runtime.names.has("gemapi-shell-v1"), true);
  assert.equal(runtime.names.has("digues-basemap-package-" + ID), true);
});

test("revocation during asynchronous cache reading refuses the late tile", async () => {
  const runtime = worker({ revokeDuringRead: true });
  assert.equal((await runtime.request(`https://app.test/__offline-basemaps/${ID}/0/0/0`)).status, 403);
  assert.equal(runtime.calls(), 0);
});
