const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { PANES, install } = require("../webapp/frontend/js/leaflet-layer-order.js");

function fakeMap() {
  const panes = new Map([
    ["tilePane", { style: {} }],
    ["overlayPane", { style: {} }],
    ["markerPane", { style: {} }],
    ["popupPane", { style: {} }],
  ]);
  return {
    panes,
    getPane(name) { return panes.get(name); },
    createPane(name) {
      const pane = { style: {} };
      panes.set(name, pane);
      return pane;
    },
  };
}

test("installe un ordre canonique indépendant de l'activation des couches", () => {
  const map = fakeMap();
  install(map);
  const z = key => Number(map.getPane(PANES[key].name).style.zIndex);

  assert.ok(z("BASEMAP") < z("LIDAR"));
  assert.ok(z("LIDAR") < z("WEATHER"));
  assert.ok(z("LIDAR") < z("HYDROMETRY"));
  assert.ok(z("WEATHER") < z("BUSINESS_VECTORS"));
  assert.ok(z("HYDROMETRY") < z("BUSINESS_VECTORS"));
  assert.ok(z("BUSINESS_VECTORS") < z("BUSINESS_MARKERS"));
  assert.ok(z("BUSINESS_MARKERS") < z("POPUPS"));
});

test("réserve les interactions aux couches qui doivent rester cliquables", () => {
  const map = fakeMap();
  install(map);

  assert.equal(map.getPane(PANES.LIDAR.name).style.pointerEvents, "none");
  assert.equal(map.getPane(PANES.WEATHER.name).style.pointerEvents, "auto");
  assert.equal(map.getPane(PANES.HYDROMETRY.name).style.pointerEvents, "auto");
  assert.equal(map.getPane(PANES.BUSINESS_VECTORS.name).style.pointerEvents, "auto");
  assert.equal(map.getPane(PANES.BUSINESS_MARKERS.name).style.pointerEvents, "auto");
});

test("réapplique le contrat sans créer deux fois les panes", () => {
  const map = fakeMap();
  install(map);
  const panes = new Map(map.panes);
  install(map);
  for (const [name, pane] of panes) assert.equal(map.getPane(name), pane);
});

test("échoue si un pane standard Leaflet manque", () => {
  const map = fakeMap();
  map.panes.delete("overlayPane");
  assert.throws(() => install(map), /Pane Leaflet standard absent : overlayPane/);
});

test("Carte et le shell PWA chargent le contrat avant ses consommateurs", () => {
  const root = path.join(__dirname, "..");
  const page = fs.readFileSync(path.join(root, "webapp/frontend/map.html"), "utf8");
  const prototype = fs.readFileSync(
    path.join(root, "webapp/backend/digues_webapp/lidar_prototype.html"), "utf8",
  );
  const worker = fs.readFileSync(path.join(root, "webapp/frontend/service-worker.js"), "utf8");
  const mapSource = fs.readFileSync(path.join(root, "webapp/frontend/js/map.js"), "utf8");
  const order = page.indexOf('/static/js/leaflet-layer-order.js');

  assert.ok(order >= 0);
  assert.ok(order < page.indexOf('/static/js/lidar-canvas.js'));
  assert.ok(order < page.indexOf('/static/js/map.js'));
  assert.ok(prototype.indexOf('/static/js/leaflet-layer-order.js')
    < prototype.indexOf('/static/js/lidar-canvas.js'));
  assert.match(worker, /"\/static\/js\/leaflet-layer-order\.js"/);
  assert.match(mapSource, /pane: HYDROMETRY_PANE/);
  assert.match(mapSource, /window\.LeafletLayerOrder\.install\(map\)/);
});
