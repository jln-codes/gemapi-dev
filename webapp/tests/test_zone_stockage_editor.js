const assert = require("node:assert/strict");
const test = require("node:test");
const ZoneEditor = require("../frontend/js/zone-stockage-editor.js");
const EntityEditorShell = require("../frontend/js/entity-editor-shell.js");
const RelationSelector = require("../frontend/js/relation-selector.js");
const ReferenceData = require("../frontend/js/reference-data.js");
const panelHarness = require("./panel-harness.js");
const ResourceInvalidation = require("../frontend/js/resource-invalidation.js");

test("une mutation de Zone invalide Carte et Base", () => {
  const resolution = ResourceInvalidation.resolveInvalidation("zones-stockage");
  assert.equal(resolution.knownResource, true);
  assert.deepEqual(resolution.units,
    ["heritage-tree", "relation-options", "data-query-results", "open-detail", "map-feature-store"]);
});

function element(document) {
  const handlers = {};
  return {
    ownerDocument: document, children: [], handlers, value: "", textContent: "", dataset: {},
    hidden: false, disabled: false, classList: { toggle() {}, add() {}, remove() {} },
    append(...children) { this.children.push(...children); },
    setAttribute(name,value) {this[name]=value;},
    insertBefore(child,before){this.children.splice(this.children.indexOf(before),0,child);},
    replaceChildren() { this.children = []; },
    addEventListener(type, fn) { handlers[type] = fn; },
    focus() {},
    get selectedOptions() { return this.children.filter(child => child.selected); },
    querySelector(selector) { return this.nodes?.[selector] || null; },
  };
}

function harness() {
  const document = { createElement() {} };
  const names = ["type_id", "designation", "libelle", "commentaire", "date_debut",
    "date_fin", "fonctionnement_id", "capacite_stockage", "superficie",
    "profondeur_moyenne", "collectivite_competente_id", "amenagement_hydraulique_id",
    "reseau_hydraulique_ids", "valid", "geometry_type"];
  let form = element(document);
  form.elements = Object.fromEntries(names.map(name => [name, element(document)]));
  form.reportValidity = () => true;
  form.reset = () => names.forEach(name => { form.elements[name].value = ""; });
  let dialog = element(document);
  const roles = ["catalog", "status", "geometry-status", "save", "cancel", "draw",
    "confirm", "restore", "create-observation", "observations", "sous-objets-section",
    "sous-objets", "new"];
  dialog.nodes = { form, fieldset: element(document),
    ".geometry-editor": element(document),
    ...Object.fromEntries(roles.map(name => [`[data-role='${name}']`, element(document)])) };
  roles.forEach(name => { dialog.nodes[`[data-role="${name}"]`] = dialog.nodes[`[data-role='${name}']`]; });
  document.createElement = tag=>{const el=element(document);el.tagName=tag.toUpperCase();return el;};
  const map = { handlers: {}, on(type, fn) { this.handlers[type] = fn; },
    off(type) { delete this.handlers[type]; }, removeLayer() {} };
  const layer = { addTo() { return this; }, clearLayers() {}, addLayer() {},
    removeLayer() {}, eachLayer() {}, on() {} };
  const L = { geoJSON: () => layer, polygon: () => layer, polyline: () => layer,
    marker: () => layer, circleMarker: () => layer };
  const historical = { type: "Feature", geometry: { type: "LineString", coordinates: [[2, 48], [3, 49]] },
    properties: { id: "zone-1", version: 1, designation: "Historique", valid: false,
      geometry_status: "HISTORIQUE_NON_CONFORME", reseau_hydraulique_ids: [], organes: [] } };
  const writes = [];
  const fetchJson = async url => {
    if (url === "/api/zones-stockage") return { type: "FeatureCollection", features: [] };
    if (url === "/api/zones-stockage/relations/options") return {
      amenagements: [], reseaux: [], organismes: [] };
    if (url.startsWith("/api/referentiels/")) return { references: [] };
    if (url.endsWith("/observations")) return { observations: [] };
    throw Error(url);
  };
  const root = { document, crypto: { randomUUID: () => "00000000-0000-4000-8000-000000000001" },
    ObservationParentList: { load: async () => {} }, addEventListener() {}, confirm: () => true,
    fetch: async (url, options = {}) => {
      if (!options.method) return { ok: true, json: async () => historical };
      writes.push({ url, options });
      const payload = JSON.parse(options.body);
      return { ok: true, json: async () => ({ ...historical,
        geometry: payload.geometry || historical.geometry,
        properties: { ...historical.properties, ...payload, version: 2 },
      }) };
    } };
  panelHarness.installView(root,view=>{form=view.form;dialog=view.element;});
  const editor = ZoneEditor.create({ root, map, L, fetchJson,
    referenceData: ReferenceData, entityEditor: EntityEditorShell,
    relationSelector: RelationSelector,
    mutationController: { requestHeaders: async () => ({}), publishMutation: async () => {} },
    concurrency: { headers: version => ({ "If-Match": `"${version}"` }),
      httpError: (_status, _body, message) => new Error(message), isVersionConflict: () => false },
    observationCreate: { open() {} },
    layerControls: { categoryStyle: () => ({ point: {}, path: {}, polygon: {} }), createBusinessLayerToggle() {} },
    panel: panelHarness(root) });
  return { editor, form, dialog, map, writes };
}

test("Zone creation requires a confirmed Polygon and uses existing map drawing", async () => {
  const h = harness();
  await h.editor.beginCreate();
  await h.form.handlers.submit({ preventDefault() {} });
  assert.equal(h.writes.length, 0);
  h.dialog.nodes['[data-role="draw"]'].handlers.click();
  for (const [lng, lat] of [[2, 48], [2.001, 48], [2.001, 48.001]]) {
    h.map.handlers.click({ latlng: { lng, lat } });
  }
  h.dialog.nodes['[data-role="confirm"]'].handlers.click();
  await h.form.handlers.submit({ preventDefault() {} });
  assert.equal(h.writes.length, 1);
  assert.deepEqual(JSON.parse(h.writes[0].options.body).geometry, {
    type: "Polygon", coordinates: [[[2, 48], [2.001, 48], [2.001, 48.001], [2, 48]]],
  });
});

test("editing an historical Zone leaves its geometry untouched", async () => {
  const h = harness();
  await h.editor.openEdit("zone-1");
  h.form.elements.commentaire.value = "Qualification";
  await h.form.handlers.submit({ preventDefault() {} });
  assert.equal(h.writes.length, 1);
  assert.equal(Object.hasOwn(JSON.parse(h.writes[0].options.body), "geometry"), false);
});
