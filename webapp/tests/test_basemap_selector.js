const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { create, normalizeCatalogue } = require("../frontend/js/basemap-selector.js");

const definitions = [
  ["ign-epure", "PLAN.IGN Épuré", 10, 0, 18],
  ["ign-plan", "PLANIGNV2", 20, 0, 19],
  ["ign-ortho", "BD ORTHO IGN", 30, 6, 19],
  ["other-raster", "Autre fond", 40, 0, 17],
];
function entry([id, name, order, nativeMin, nativeMax]) {
  return { id, name, order, delivery: { adapter: "tilejson", url: `/basemap/${id}.json` },
    zoom: { native_min: nativeMin, native_max: nativeMax, display_max: nativeMax + 1 },
    attribution: `© ${name}`, capabilities: { browser_print: true } };
}
function catalogue(ids = ["ign-epure"], defaultId = ids[0]) {
  return { contract_version: 1, default_basemap_id: defaultId,
    basemaps: definitions.filter(([id]) => ids.includes(id)).map(entry) };
}

class Element {
  constructor(tag = "div") {
    this.tag = tag;
    this.children = [];
    this.listeners = new Map();
    this.attributes = {};
    this.hidden = false;
    this.textContent = "";
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(callback);
  }
  removeEventListener(name, callback) {
    this.listeners.set(name, (this.listeners.get(name) || []).filter((item) => item !== callback));
  }
  fire(name, event = {}) {
    for (const callback of this.listeners.get(name) || []) callback({ target: this, ...event });
  }
  contains(node) { return this === node || this.children.some((child) => child.contains?.(node)); }
  focus() { this.focused = true; }
}

function harness(initial = catalogue()) {
  const root = new Element();
  const doc = new Element("document");
  doc.createElement = (tag) => new Element(tag);
  const listeners = new Map();
  const win = { navigator: { onLine: true }, location: { href: "https://app.test/carte", origin: "https://app.test" },
    document: doc, addEventListener(name, callback) { listeners.set(name, callback); },
    removeEventListener(name) { listeners.delete(name); } };
  const mapListeners = new Map();
  const businessLayer = { business: true };
  const map = { layers: new Set([businessLayer]), center: [45, 2], zoom: 8, maxZoom: 19,
    getCenter() { return [...this.center]; }, getZoom() { return this.zoom; },
    setMaxZoom(value) { this.maxZoom = value; if (this.zoom > value) this.zoom = value; },
    setView(center, zoom) { this.center = [...center]; this.zoom = zoom; },
    removeLayer(layer) { this.layers.delete(layer); },
    on(name, callback) { mapListeners.set(name, callback); },
    off(name) { mapListeners.delete(name); },
    zoomTo(zoom) { this.zoom = zoom; mapListeners.get("zoomend")?.(); } };
  const layers = [];
  const L = { tileLayer(url, options) {
    const handlers = new Map();
    const layer = { url, options, on(name, callback) { handlers.set(name, callback); return this; },
      off(name) { handlers.delete(name); return this; },
      fire(name) { handlers.get(name)?.(); },
      addTo(target) { target.layers.add(this); return this; } };
    layers.push(layer);
    return layer;
  }, DomEvent: { disableClickPropagation() {}, disableScrollPropagation() {} } };
  let published = initial;
  const requested = [];
  const fetchImpl = async (url) => { requested.push(url); return { ok: true, json: async () => published }; };
  const tilejson = [];
  let failTilejson = null;
  const basemapApi = {
    fallback: { url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png", options: { maxZoom: 19 } },
    async load() { return this.fallback; },
    async loadStrict(url) {
      tilejson.push(url);
      if (failTilejson === url) throw new Error("HTTP 503");
      const selected = published.basemaps.find((item) => item.delivery.url === url);
      return { url: `https://app.test/basemap/${selected.id}/tiles/{z}/{x}/{y}.png`,
        options: { minZoom: selected.zoom.native_min, maxNativeZoom: selected.zoom.native_max,
          maxZoom: selected.zoom.display_max, attribution: selected.attribution } };
    },
  };
  let local = null;
  const changes = [];
  const selector = create({ map, L, container: root, documentObject: doc, runtime: win,
    fetchImpl, basemapApi, offlineSource: { async getState() { return local; } },
    onChange: (value) => changes.push(value), logger: { warn() {} } });
  return { selector, root, doc, win, map, businessLayer, layers, listeners, basemapApi,
    requested, tilejson, changes, publish(value) { published = value; },
    fail(url) { failTilejson = url; }, localState(value) { local = value; } };
}

test("catalogue validates the public contract and sorts a fourth configured fund", () => {
  const payload = catalogue(["other-raster", "ign-ortho", "ign-plan", "ign-epure"]);
  payload.basemaps.reverse();
  assert.deepEqual(normalizeCatalogue(payload).entries.map(({ id }) => id),
    ["ign-epure", "ign-plan", "ign-ortho", "other-raster"]);
  assert.throws(() => normalizeCatalogue({ ...payload, default_basemap_id: "missing" }));
  assert.throws(() => normalizeCatalogue({ ...payload, basemaps: [payload.basemaps[0], payload.basemaps[0]] }));
  assert.throws(() => normalizeCatalogue({ ...payload, basemaps: [{ ...payload.basemaps[0],
    delivery: { adapter: "tilejson", url: "https://evil.test/tiles.json" } }] }));
});

test("removed territorial catalogues are rejected; global catalogues are context independent", () => {
 const payload=catalogue();
 assert.equal(normalizeCatalogue(payload,'base-a').entries.length,1);
 assert.equal(normalizeCatalogue(payload,'base-b').entries.length,1);
 payload.basemaps[0].delivery.url='/api/bases/30000000-0000-4000-8000-000000000001/basemaps/ign-epure.json';
 assert.throws(()=>normalizeCatalogue(payload),/invalide/);
});

test("one fund starts as default and selector uses only published entries", async () => {
  const h = harness();
  await h.selector.start("/basemap/plan-ign.json");
  assert.equal(h.selector.getActive().id, "ign-epure");
  assert.equal(h.root.children[1].children.length, 1);
  assert.deepEqual(h.requested, ["/api/basemaps"]);
  assert.equal(h.root.children[1].children[0].attributes["aria-pressed"], "true");
  assert.equal(await h.selector.select("ign-plan"), false);
  assert.equal(h.map.layers.has(h.businessLayer), true);
});

test("three funds switch immediately without changing map, center or business layers", async () => {
  const h = harness(catalogue(["ign-epure", "ign-plan", "ign-ortho"]));
  await h.selector.start();
  const first = h.layers[0];
  assert.equal(await h.selector.select("ign-plan"), true);
  assert.equal(h.map.layers.has(first), false);
  assert.equal(h.map.layers.has(h.businessLayer), true);
  assert.deepEqual(h.map.center, [45, 2]);
  assert.equal(h.map.zoom, 8);
  assert.equal(h.map.maxZoom, 20);
  assert.equal(h.selector.getActive().options.maxNativeZoom, 19);
  assert.match(h.selector.getActive().url, /\/\{z\}\/\{x\}\/\{y\}\.png$/);
  assert.equal(h.selector.getActive().options.attribution, "© PLANIGNV2");
  assert.equal(h.map.layers.size, 2);
  assert.deepEqual(h.root.children[1].children.map((button) => button.textContent.slice(0, 1)), ["P", "✓", "B"]);
  assert.equal(h.changes.at(-1).id, "ign-plan");
});

test("a fourth fund is selectable through the same catalogue contract", async () => {
  const h = harness(catalogue(["ign-epure", "ign-plan", "ign-ortho", "other-raster"]));
  await h.selector.start();
  assert.equal(h.root.children[1].children.length, 4);
  assert.equal(await h.selector.select("other-raster"), true);
  assert.equal(h.selector.getActive().id, "other-raster");
  assert.equal(h.selector.getActive().options.maxZoom, 18);
});

test("overzoom is Leaflet-only and ortho below z6 is explicitly blank", async () => {
  const h = harness(catalogue(["ign-epure", "ign-plan", "ign-ortho"]));
  await h.selector.start();
  assert.equal(h.layers[0].options.maxNativeZoom, 18);
  assert.equal(h.layers[0].options.maxZoom, 19);
  h.map.zoomTo(19);
  await h.selector.select("ign-ortho");
  assert.equal(h.layers.at(-1).options.minZoom, 6);
  assert.equal(h.layers.at(-1).options.maxNativeZoom, 19);
  assert.equal(h.layers.at(-1).options.maxZoom, 20);
  assert.equal(h.map.zoom, 19);
  h.map.zoomTo(5);
  assert.match(h.root.children[2].textContent, /pas disponible sous le zoom 6/);
  assert.equal(h.map.zoom, 5);
  h.map.zoomTo(20);
  assert.equal(h.root.children[2].hidden, true);
  await h.selector.select("ign-epure");
  assert.equal(h.map.zoom, 19);
  assert.equal(h.map.layers.has(h.businessLayer), true);
});

test("removing the active fund picks the declared fallback on refresh", async () => {
  const h = harness(catalogue(["ign-epure", "ign-plan", "ign-ortho"]));
  await h.selector.start();
  await h.selector.select("ign-plan");
  h.publish(catalogue(["ign-epure", "ign-ortho"]));
  await h.selector.refresh();
  assert.equal(h.selector.getActive().id, "ign-epure");
  assert.equal(h.root.children[1].children.length, 2);
  assert.equal(h.map.layers.size, 2);
});

test("a pending TileJSON cannot activate a fund removed by catalogue refresh", async () => {
  const h = harness(catalogue(["ign-epure", "ign-plan"]));
  await h.selector.start();
  let complete;
  h.basemapApi.loadStrict = () => new Promise((resolve) => { complete = resolve; });
  const pending = h.selector.select("ign-plan");
  h.publish(catalogue(["ign-epure"]));
  await h.selector.refresh();
  complete({ url: "https://app.test/basemap/ign-plan/tiles/{z}/{x}/{y}.png",
    options: { minZoom: 0, maxNativeZoom: 19, maxZoom: 20 } });
  assert.equal(await pending, false);
  assert.equal(h.selector.getActive().id, "ign-epure");
});

test("TileJSON failure keeps prior fund; tile failures report once without mixing funds", async () => {
  const h = harness(catalogue(["ign-epure", "ign-plan"]));
  await h.selector.start();
  h.fail("/basemap/ign-plan.json");
  assert.equal(await h.selector.select("ign-plan"), false);
  assert.equal(h.selector.getActive().id, "ign-epure");
  assert.match(h.root.children[2].textContent, /Le fond PLAN.IGN Épuré est conservé/);
  h.layers[0].fire("tileerror");
  const message = h.root.children[2].textContent;
  h.layers[0].fire("tileerror");
  assert.equal(h.root.children[2].textContent, message);
  assert.equal(h.map.layers.size, 2);
});

test("a TileJSON cannot redirect Leaflet to an arbitrary provider URL", async () => {
  const h = harness();
  await h.selector.start();
  h.localState({ catalogue: catalogue(), packages: {
    "ign-epure": { url: "https://outside.test/tiles/{z}/{x}/{y}.webp",
      options: { minZoom: 0, maxNativeZoom: 18, maxZoom: 19 } },
  } });
  h.win.navigator.onLine = false;
  await h.selector.refresh();
  assert.equal(h.selector.getActive(), null);
  assert.equal(h.map.layers.has(h.businessLayer), true);
  assert.deepEqual(h.requested, ["/api/basemaps"]);
});

test("offline without a package makes no network request and exposes package availability", async () => {
  const h = harness(catalogue(["ign-epure", "ign-plan"]));
  h.win.navigator.onLine = false;
  await h.selector.start();
  assert.deepEqual(h.requested, []);
  assert.equal(h.selector.getActive(), null);
  assert.match(h.root.children[2].textContent, /Aucun paquet/);
  h.localState({ catalogue: catalogue(["ign-epure", "ign-plan"]), packages: {
    "ign-plan": { url: "https://app.test/__offline-basemaps/pkg/{z}/{x}/{y}.png",
      options: { minZoom: 0, maxNativeZoom: 19, maxZoom: 20 } },
  } });
  await h.selector.refresh();
  assert.equal(h.selector.getActive().id, "ign-plan");
  assert.match(h.selector.getActive().url, /\/\{z\}\/\{x\}\/\{y\}\.png$/);
  assert.equal(h.root.children[1].children[0].disabled, true);
  assert.equal(h.root.children[1].children[1].disabled, false);
  assert.deepEqual(h.requested, []);
  assert.deepEqual(h.tilejson, []);
});

test("catalogue outage is distinct from unavailable tiles and retains online OSM fallback", async () => {
  const h = harness({ invalid: true });
  await h.selector.start();
  assert.equal(h.selector.getActive().id, null);
  assert.match(h.root.children[2].textContent, /Catalogue des fonds indisponible/);
});

test("a later catalogue outage preserves the selected fund", async () => {
  const h = harness(catalogue(["ign-epure", "ign-plan"]));
  await h.selector.start();
  await h.selector.select("ign-plan");
  const layer = h.layers.at(-1);
  h.publish({ invalid: true });
  await h.selector.refresh();
  assert.equal(h.selector.getActive().id, "ign-plan");
  assert.equal(h.map.layers.has(layer), true);
  assert.match(h.root.children[2].textContent, /fond actif est conservé/);
});

test("keyboard, touch and outside interactions stay inside the selector", async () => {
  const h = harness();
  await h.selector.start();
  const [toggle, panel] = h.root.children;
  toggle.fire("click");
  assert.equal(panel.hidden, false);
  assert.equal(toggle.attributes["aria-expanded"], "true");
  let stopped = false;
  h.root.fire("pointerdown", { stopPropagation() { stopped = true; } });
  assert.equal(stopped, true);
  h.root.fire("keydown", { key: "Escape", preventDefault() {} });
  assert.equal(panel.hidden, true);
  assert.equal(toggle.focused, true);
  toggle.fire("click");
  h.doc.fire("pointerdown", { target: new Element() });
  assert.equal(panel.hidden, true);
});

test("map page loads selector before map and PWA shell includes it", () => {
  const frontend = path.resolve(__dirname, "../frontend");
  const page = fs.readFileSync(path.join(frontend, "map.html"), "utf8");
  const shell = fs.readFileSync(path.join(frontend, "service-worker.js"), "utf8");
  const selectorScript = 'data-business-src="/static/js/basemap-selector.js"';
  assert.equal(page.match(/id="basemap-selector"/g)?.length, 1);
  assert.ok(page.indexOf(selectorScript) < page.indexOf('data-business-src="/static/js/map.js"'));
  assert.equal(shell.match(/"\/static\/js\/basemap-selector\.js"/g)?.length, 1);
});
