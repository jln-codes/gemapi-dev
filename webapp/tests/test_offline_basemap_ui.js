const assert = require("node:assert/strict");
const test = require("node:test");
const { localPackages, geometryBounds } = require("../frontend/js/offline-basemap-ui.js");
const fs = require("node:fs");
const vm = require("node:vm");

function controllerFixture() {
  const events = new Map();
  const node = { hidden: true, addEventListener() {}, removeEventListener() {},
    querySelector() { return this; }, replaceChildren() {}, focus() {} };
  let active = true;
  const runtime = { document: { querySelector: () => node, addEventListener() {}, removeEventListener() {} },
    location: { origin: "http://localhost" }, navigator: { onLine: true, storage: {},
      serviceWorker: { addEventListener: (name, fn) => events.set(name, fn), removeEventListener() {} } },
    addEventListener: (name, fn) => events.set(name, fn), removeEventListener() {},
    BusinessRuntime: { context: { key: "authorized-context" }, isActive: () => active }, fetch: async () => new Response() };
  const sandbox = { ...runtime, console, setTimeout, clearTimeout, DOMException,
    OfflineBasemapStore: {}, OfflineBasemapDownload: {} };
  sandbox.window = sandbox;
  vm.runInNewContext(fs.readFileSync(require.resolve("../frontend/js/offline-basemap-ui.js"), "utf8"), sandbox);
  const store = { getCatalogue: async () => ({ basemaps: [] }), list: async () => [], recover: async () => {}, saveCatalogue: async () => {} };
  const controller = sandbox.OfflineBasemapUi.create({ map: { on() {}, off() {} },
    basemapApi: { loadStrict() {} }, store, manager: { isRunning: () => false } });
  return { controller, store, runtime: sandbox, events, expire: () => { active = false; } };
}

test("browser default runtime initializes and never returns an expired service worker context", async () => {
  const fixture = controllerFixture();
  fixture.controller.start();
  const responses = [];
  const request = () => fixture.events.get("message")({ data: { type: "DIGUES_BASEMAP_CONTEXT" },
    ports: [{ postMessage: value => responses.push(value.contextKey) }] });
  request();
  fixture.expire();
  request();
  assert.deepEqual(responses, ["authorized-context", null]);
});

test("late local catalogue cannot reactivate a closed or replaced identity", async () => {
  for (const change of [f => f.expire(), f => { f.runtime.BusinessRuntime = { context: { key: "other" }, isActive: () => true }; }]) {
    const fixture = controllerFixture();
    let finish;
    fixture.store.getCatalogue = () => new Promise(resolve => { finish = resolve; });
    const pending = fixture.controller.getState();
    change(fixture);
    finish({ basemaps: [] });
    assert.equal(await pending, null);
  }
});

const ID_A = "11111111-1111-4111-8111-111111111111";
const ID_B = "22222222-2222-4222-8222-222222222222";
const entries = [
  { id: "ign-epure", zoom: { native_min: 0, native_max: 18, display_max: 19 },
    offline: { status: "available", retention: "authorized" } },
  { id: "ign-plan", zoom: { native_min: 0, native_max: 19, display_max: 20 },
    offline: { status: "available", retention: "authorized" } },
  { id: "ign-ortho", zoom: { native_min: 6, native_max: 19, display_max: 20 },
    offline: { status: "available", retention: "authorized" } },
];
function map(zoom, west = -1, east = 1) {
  return { getZoom: () => zoom, getCenter: () => ({ lat: 0, lng: 0 }),
    getBounds: () => ({ getWest: () => west, getEast: () => east,
      getSouth: () => -1, getNorth: () => 1 }) };
}
function manifest(basemapId, id, zoom, keys, state = "ready") {
  return { id, basemapId, zoomMin: zoom, zoomMax: zoom, expectedKeys: keys,
    expectedCount: keys.length, state, generation: "generation-1" };
}

test("local package resolution selects full viewport independently of insertion order", () => {
  const full = manifest("ign-epure", ID_A, 1, ["1/0/0", "1/1/0", "1/0/1", "1/1/1"]);
  const partial = manifest("ign-epure", ID_B, 1, ["1/1/1"]);
  const first = localPackages(entries, [partial, full], map(1))["ign-epure"];
  const second = localPackages(entries, [full, partial], map(1))["ign-epure"];
  assert.equal(first.url, second.url);
  assert.match(first.url, new RegExp(ID_A));
  assert.equal(first.partial, false);
});

test("partial coverage is disclosed, absent coverage is unavailable, and z6 ortho minimum is respected", () => {
  const partial = manifest("ign-epure", ID_B, 1, ["1/1/1"]);
  const resolved = localPackages(entries, [partial], map(1));
  assert.equal(resolved["ign-epure"].partial, true);
  assert.equal(resolved["ign-plan"], undefined);
  assert.equal(resolved["ign-ortho"], undefined);
  assert.equal(localPackages(entries, [manifest("ign-ortho", ID_A, 6, ["6/32/32"])], map(5))["ign-ortho"], undefined);
});

test("offline z19/z20 use only native z18/z19 packages and legal lock hides revoked entries", () => {
  const world = { getZoom: () => 20, getCenter: () => ({ lat: 0, lng: 0 }),
    getBounds: () => ({ getWest: () => -0.0001, getEast: () => 0.0001,
      getSouth: () => -0.0001, getNorth: () => 0.0001 }) };
  const packageRow = manifest("ign-plan", ID_A, 19, ["19/262144/262144"]);
  const result = localPackages(entries, [packageRow], world);
  assert.equal(result["ign-plan"].options.maxNativeZoom, 19);
  assert.equal(result["ign-plan"].options.maxZoom, 20);
  assert.equal(localPackages([{ ...entries[1], offline: { status: "planned", retention: "review-required" } }],
    [packageRow], world)["ign-plan"], undefined);
});

test("territory geometry bounding box is stable for narrow multipolygons", () => {
  assert.deepEqual(geometryBounds({ type: "MultiPolygon", coordinates: [
    [[[1, 2], [1.01, 2], [1, 2.1], [1, 2]]],
    [[[2, 3], [2.01, 3], [2, 3.1], [2, 3]]],
  ] }), [1, 2, 2.01, 3.1]);
});
