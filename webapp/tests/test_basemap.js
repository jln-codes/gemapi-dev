const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const frontend = path.resolve(__dirname, "../frontend");
const source = fs.readFileSync(path.join(frontend, "js/basemap.js"), "utf8");

function basemapWith(fetch, timers = {}) {
  const window = {
    location: { href: "https://app.example.test/carte" },
    setTimeout: timers.setTimeout || setTimeout,
    clearTimeout: timers.clearTimeout || clearTimeout,
    BusinessRuntime: timers.businessRuntime,
  };
  vm.runInNewContext(source, { window, fetch, URL, AbortController, console: { warn() {} } });
  return window.Basemap;
}

test("public TileJSON uses the instance proxy independently of a later identity", async () => {
  let requests = 0;
  const basemap = basemapWith(async url => {
    requests++;
    assert.equal(url, "/basemap/plan-ign.json");
    return { ok: true, json: async () => ({ tiles: [
      "/basemap/tiles/v1/{z}/{x}/{y}.webp"], minzoom: 0, maxzoom: 18 }) };
  }, {
    businessRuntime: { async fetch() { throw new Error("A public fond needs no identity"); } },
  });
  const descriptor = await basemap.loadStrict("/basemap/plan-ign.json");
  assert.equal(requests, 1);
  assert.equal(descriptor.url, "https://app.example.test/basemap/tiles/v1/{z}/{x}/{y}.webp");
});

test("TileJSON provides the tile template and zoom limits", async () => {
  let requested;
  const tilejson = {
    tiles: ["https://example.test/france/{z}/{x}/{y}.png"],
    minzoom: 0,
    maxzoom: 15,
    scheme: "tms",
    attribution: "France tiles",
  };
  const basemap = basemapWith(async (url) => {
    requested = url;
    return { ok: true, json: async () => tilejson };
  });
  const layer = await basemap.load("https://example.test/france.json");
  assert.equal(requested, "https://example.test/france.json");
  assert.equal(layer.url, "https://example.test/france/{z}/{x}/{y}.png");
  assert.equal(layer.options.minZoom, 0);
  assert.equal(layer.options.maxNativeZoom, 15);
  assert.equal(layer.options.maxZoom, 16);
  assert.equal(layer.options.tms, true);
  assert.equal(layer.options.attribution, "France tiles");
  assert.equal(tilejson.minzoom, 0);
  assert.equal(tilejson.maxzoom, 15);
});

test("root-relative TileJSON and tiles use the application's origin", async () => {
  let requested;
  const basemap = basemapWith(async (url) => {
    requested = url;
    return { ok: true, json: async () => ({
      tiles: ["/basemap/tiles/2026-09-abcd/{z}/{x}/{y}.webp"],
      minzoom: 0,
      maxzoom: 18,
      scheme: "xyz",
      attribution: "© IGN — PLAN.IGN",
    }) };
  });
  const layer = await basemap.load("/basemap/plan-ign.json");
  assert.equal(requested, "/basemap/plan-ign.json");
  assert.equal(layer.url, "https://app.example.test/basemap/tiles/2026-09-abcd/{z}/{x}/{y}.webp");
  assert.equal(layer.options.minZoom, 0);
  assert.equal(layer.options.maxNativeZoom, 18);
  assert.equal(layer.options.maxZoom, 19);
  assert.equal(layer.options.attribution, "© IGN — PLAN.IGN");
  const print = fs.readFileSync(path.join(frontend, "js/map-print.js"), "utf8");
  assert.match(print, /crossOrigin: "anonymous"/);
  assert.match(print, /allowTaint: false/);
  assert.match(print, /useCORS: true/);
  assert.match(print, /TILE_LOAD_TIMEOUT_MS = 60000/);
});

test("each raster is overzoomed by exactly one Leaflet level", async () => {
  const tilejson = { tiles: ["/tiles/{z}/{x}/{y}.webp"], minzoom: 2, maxzoom: 13 };
  const basemap = basemapWith(async () => ({ ok: true, json: async () => tilejson }));
  const layer = await basemap.load("/basemap/france.json");
  assert.equal(layer.options.minZoom, 2);
  assert.equal(layer.options.maxNativeZoom, 13);
  assert.equal(layer.options.maxZoom, 14);
  assert.equal(tilejson.minzoom, 2);
  assert.equal(tilejson.maxzoom, 13);
});

test("document-relative tile templates use the TileJSON document URL", async () => {
  const basemap = basemapWith(async () => ({
    ok: true,
    url: "https://tiles.example.test/basemap/france.json",
    json: async () => ({ tiles: ["tiles/{z}/{x}/{y}.webp"] }),
  }));
  const layer = await basemap.load("/basemap/france.json");
  assert.equal(layer.url, "https://tiles.example.test/basemap/tiles/{z}/{x}/{y}.webp");
  assert.equal(layer.options.maxZoom, 19);
  assert.equal(layer.options.maxNativeZoom, undefined);
});

test("absent, inaccessible, invalid and unusable TileJSON retain the OSM fallback", async () => {
  const absent = basemapWith(() => { throw new Error("fetch should not run"); });
  assert.equal(await absent.load(), absent.fallback);
  assert.equal(await absent.load(""), absent.fallback);
  assert.equal(absent.fallback.options.maxZoom, 19);
  assert.equal(absent.fallback.options.maxNativeZoom, undefined);
  const cases = [
    async () => { throw new Error("offline"); },
    async () => ({ ok: false, status: 503 }),
    async () => ({ ok: true, json: async () => { throw new SyntaxError("invalid JSON"); } }),
    async () => ({ ok: true, json: async () => ({ tiles: [] }) }),
    async () => ({ ok: true, json: async () => ({ tiles: ["invalid"] }) }),
  ];
  for (const fetch of cases) {
    const basemap = basemapWith(fetch);
    assert.equal(await basemap.load("https://example.test/tiles.json"), basemap.fallback);
  }
});

test("strict TileJSON loading reports provider failure without silently choosing OSM", async () => {
  const basemap = basemapWith(async () => ({ ok: false, status: 503 }));
  await assert.rejects(basemap.loadStrict("/basemap/ign-plan.json"), /HTTP 503/);
  assert.equal(await basemap.load("/basemap/ign-plan.json"), basemap.fallback);
});

test("a TileJSON request that never resolves times out and uses OSM", async () => {
  let fireTimeout;
  let timeoutMs;
  let aborted = false;
  const basemap = basemapWith((_url, { signal }) => {
    signal.addEventListener("abort", () => { aborted = true; });
    return new Promise(() => {});
  }, {
    setTimeout(callback, ms) { fireTimeout = callback; timeoutMs = ms; return 1; },
    clearTimeout() {},
  });
  const loading = basemap.load("/basemap/france.json");
  assert.equal(timeoutMs, 8000);
  fireTimeout();
  assert.equal(await loading, basemap.fallback);
  assert.equal(aborted, true);
});

test("invalid zoom metadata and non-HTTP tile templates use OSM", async () => {
  const invalid = [
    { minzoom: 15, maxzoom: 0 },
    { minzoom: "0", maxzoom: 15 },
    { minzoom: NaN },
    { maxzoom: Infinity },
    { maxzoom: null },
  ];
  for (const zooms of invalid) {
    const basemap = basemapWith(async () => ({
      ok: true, json: async () => ({ tiles: ["/tiles/{z}/{x}/{y}"], ...zooms }),
    }));
    assert.equal(await basemap.load("/basemap/france.json"), basemap.fallback);
  }
  for (const template of ["javascript:{z}/{x}/{y}", "file:///tiles/{z}/{x}/{y}"]) {
    const basemap = basemapWith(async () => ({
      ok: true, json: async () => ({ tiles: [template] }),
    }));
    assert.equal(await basemap.load("/basemap/france.json"), basemap.fallback);
  }
});

test("an /api/config error keeps selector startup and legacy fallback available", async () => {
  const mapSource = fs.readFileSync(path.join(frontend, "js/map.js"), "utf8");
  const start = mapSource.indexOf("async function loadFrontendConfig()");
  const end = mapSource.indexOf("\nfunction inputText", start);
  assert.ok(start >= 0 && end > start);
  for (const [index, fetchJson] of [
    async () => { throw new Error("HTTP 500"); },
    async () => null,
  ].entries()) {
    const warnings = [];
    const basemap = basemapWith(() => { throw new Error("fetch should not run"); });
    const starts = [];
    const context = {
      window: { Basemap: basemap, OfflineBasemapUi: { create() {
        if (index === 1) throw new Error("IndexedDB unavailable");
        return { attachSelector() {}, start() {} };
      } }, BasemapSelector: { create(options) {
        assert.equal(options.basemapApi, basemap);
        return { async start(url) { starts.push(url); options.onChange(basemap.fallback); } };
      } } },
      fetchJson,
      document: { body: { classList: { toggle() {} } }, querySelector() { return {}; } },
      console: { warn: (...args) => warnings.push(args) },
      L: {},
      map: {},
    };
    const result = await vm.runInNewContext(
      `let showUuid = true; let basemap; let basemapSelector; let offlineBasemapUi; let mapPrintPending = false; let mapPrintAbortController = null; let territoireAdministratifGeoJSON = {features: []}; ${mapSource.slice(start, end)}\n` +
        "loadFrontendConfig().then(() => ({ showUuid, basemap }))",
      context,
    );
    assert.equal(result.showUuid, false);
    assert.equal(result.basemap, basemap.fallback);
    assert.deepEqual(starts, [undefined]);
    assert.match(warnings[0][0], /Configuration frontend indisponible/);
    if (index === 1) assert.match(warnings[1][0], /Stockage cartographique local indisponible/);
  }
});

test("map and print use the resolved TileJSON layer without a z13 dependency", () => {
  const map = fs.readFileSync(path.join(frontend, "js/map.js"), "utf8");
  const print = fs.readFileSync(path.join(frontend, "js/map-print.js"), "utf8");
  assert.match(map, /BasemapSelector\.create\(/);
  assert.match(map, /basemapSelector\.start\(config\.basemap_tilejson_url\)/);
  assert.match(map, /tileOptions: selectedBasemap\.options/);
  assert.match(print, /\.\.\.tileOptions/);
  assert.doesNotMatch(print, /fond OpenStreetMap|tuile OpenStreetMap/);
  assert.match(print, /fond de carte n'a pas pu être chargé/);
  assert.doesNotMatch(source + map + print, /z13|z15/);
});
