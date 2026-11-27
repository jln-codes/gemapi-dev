const assert = require("node:assert/strict");
const test = require("node:test");
const PrestationEditor = require("../frontend/js/prestation-editor.js");
const EntityEditorShell = require("../frontend/js/entity-editor-shell.js");
const RelationSelector = require("../frontend/js/relation-selector.js");
const ReferenceData = require("../frontend/js/reference-data.js");
const MapLayerControls = require("../frontend/js/map-layer-controls.js");
const panelHarness = require("./panel-harness.js");

function element(document) {
  const handlers = {};
  return {
    ownerDocument: document, children: [], handlers, value: "", textContent: "", dataset: {},
    hidden: false, disabled: false, checked: false, classList: { toggle() {}, add() {}, remove() {} },
    append(...children) { this.children.push(...children); },
    setAttribute(name,value) { this[name]=value; },
    insertBefore(child,before) {this.children.splice(this.children.indexOf(before),0,child);},
    replaceChildren() { this.children = []; },
    addEventListener(type, fn) { handlers[type] = fn; },
    focus() {}, show() { this.open = true; }, close() { this.open = false; },
    get selectedOptions() { return this.children.filter((child) => child.selected); },
    querySelector(selector) {
      if (selector === "input") return this.input || (this.input = element(document));
      return this.nodes?.[selector] || null;
    },
  };
}

function harness() {
  const document = { createElement() { return element(document); }, body: { append() {} } };
  const controls = element(document), legend = element(document);
  document.querySelector = (selector) => selector === ".map-bottom-controls" ? controls : legend;
  const families = ["desordres", "ouvrages_hydrauliques", "ouvrages_particuliers",
    "mobilier", "equipements_mesure", "reseaux_techniques", "cheminements",
    "vegetation", "amenagements_hydrauliques"];
  const names = ["designation", "libelle", "commentaire", "date_debut", "date_fin",
    "cout_global", "cout_metre", "realisation_interne", "valid",
    "type_prestation_id", "troncon_ids", "intervenant_ids", "geometry_type",
    ...families.map((family) => `objects_${family}`)];
  let form = element(document);
  form.elements = Object.fromEntries(names.map((name) => [name, element(document)]));
  form.reportValidity = () => true;
  form.reset = () => names.forEach((name) => { form.elements[name].value = "";
    form.elements[name].checked = false; });
  let dialog = element(document);
  const roles = ["catalog", "status", "geometry-status", "save", "cancel", "draw",
    "confirm", "restore", "create-observation", "observations", "observations-section",
    "new"];
  dialog.nodes = { form, fieldset: element(document),
    ...Object.fromEntries(roles.map((name) => [`[data-role='${name}']`, element(document)])) };
  roles.forEach((name) => {
    dialog.nodes[`[data-role="${name}"]`] = dialog.nodes[`[data-role='${name}']`];
  });
  document.createElement = tag => {const el=element(document);el.tagName=tag.toUpperCase();return el;};
  const map = { handlers: {}, on(type, handler) { this.handlers[type] = handler; },
    off(type) { delete this.handlers[type]; }, removeLayer() {} };
  const layer = { addTo() { return this; }, clearLayers() {}, addLayer() {},
    removeLayer() {}, eachLayer() {}, on() {}, getLatLng() { return { lng: 2, lat: 48 }; } };
  const L = { geoJSON: () => layer, circleMarker: () => element(document),
    marker: () => layer, polyline: () => layer, polygon: () => layer };
  const original = { type: "Feature", id: "p1", geometry: null,
    properties: { id: "p1", version: 1,
      type_prestation_id: "OLD", type_libelle: "Ancien type",
      troncon_ids: ["t1"], intervenant_ids: [], object_ids: { desordres: ["d1"] },
      designation: "Historique", date_fin: null, valid: false } };
  const urls = [], writes = [];
  let deferred = null, conflict = false, confirmation = false, obsParent;
  const fetchJson = async (url) => {
    urls.push(url);
    if (url === "/api/prestations") return { type: "FeatureCollection", features: [original] };
    if (url === "/api/prestations/relations/options") return {
      troncons: [{ id: "t1", libelle: "T1", valid: true },
        { id: "t2", libelle: "T2", valid: true }],
      desordres: [{ id: "d1", libelle: "D1", valid: true },
        { id: "d2", libelle: "D2", valid: true }],
      amenagements_hydrauliques: [{ id: "a1", libelle: "A1", valid: true }],
      contacts: [{ id: "c1", libelle: "Contact", valid: true }],
    };
    if (url.endsWith("/observations")) return { observations: [] };
    if (url.startsWith("/api/referentiels/")) return {
      references: [{ id: "T", libelle: "Type", valid: true }] };
    throw Error(url);
  };
  const root = { document, crypto: { randomUUID: () => "00000000-0000-4000-8000-000000000001" },
    ObservationParentList: { load: async () => {} },
    addEventListener() {}, confirm: () => confirmation,
    fetch: async (url, options = {}) => {
      writes.push({ url, options });
      if (!options.method) return { ok: true, json: async () => original };
      if (deferred) await deferred.promise;
      if (conflict) return { ok: false, status: 409,
        json: async () => ({ detail: "Conflit" }) };
      const payload = JSON.parse(options.body);
      return { ok: true, status: 201, json: async () => ({
        ...original, geometry: payload.geometry || original.geometry,
        properties: { ...original.properties, ...payload, id: "p1",
          version: options.method === "PUT" ? 2 : 1 },
      }) };
    } };
  panelHarness.installView(root,view=>{form=view.form;dialog=view.element;});
  const editor = PrestationEditor.create({ root, map, L, fetchJson,
    referenceData: ReferenceData, entityEditor: EntityEditorShell,
    relationSelector: RelationSelector,
    layerControls: MapLayerControls,
    mutationController: { requestHeaders: async () => ({}), publishMutation: async () => {} },
    concurrency: { headers: (version) => ({ "If-Match": `"${version}"` }),
      httpError: (status, _body, message) => Object.assign(new Error(message), { status }) },
    observationCreate: { open(parent) { obsParent = parent; } },
    panel: panelHarness(root) });
  return { editor, form, dialog, map, writes, original,
    obsParent: () => obsParent, setDeferred(value) { deferred = value; },
    setConflict(value) { conflict = value; }, setConfirmation(value) { confirmation = value; } };
}

test("Prestation builds the three allowed geometries", () => {
  const vertices = [{ lng: 2, lat: 48 }, { lng: 3, lat: 48 }, { lng: 3, lat: 49 }];
  assert.deepEqual(PrestationEditor.geometryFromVertices("Point", vertices),
    { type: "Point", coordinates: [2, 48] });
  assert.deepEqual(PrestationEditor.geometryFromVertices("LineString", vertices),
    { type: "LineString", coordinates: [[2, 48], [3, 48], [3, 49]] });
  assert.deepEqual(PrestationEditor.geometryFromVertices("Polygon", vertices),
    { type: "Polygon", coordinates: [[[2, 48], [3, 48], [3, 49], [2, 48]]] });
});

test("native creation selects explicit relations, stays pending once, and requires completion date", async () => {
  const h = harness();
  await h.editor.load();
  await h.editor.beginCreate();
  assert.equal(h.editor.shell.state.mode, "create");
  h.form.elements.date_fin.value = "2026-02-01";
  h.form.elements.type_prestation_id.value = "T";
  h.form.elements.cout_global.value = "0";
  h.form.elements.realisation_interne.value = "false";
  h.form.elements.troncon_ids.children[0].selected = true;
  h.form.elements.troncon_ids.children[1].selected = true;
  h.form.elements.objects_desordres.children[0].selected = true;
  h.form.elements.objects_amenagements_hydrauliques.children[0].selected = true;
  h.form.elements.intervenant_ids.children[0].selected = true;
  h.form.handlers.input();
  assert.equal(h.editor.shell.hasDraft(), true);
  let release;
  h.setDeferred({ promise: new Promise((resolve) => { release = resolve; }) });
  const submit = h.form.handlers.submit({ preventDefault() {} });
  assert.equal(h.editor.shell.state.pending, true);
  await h.form.handlers.submit({ preventDefault() {} });
  release(); await submit;
  const writes = h.writes.filter(({ options }) => options.method === "POST");
  assert.equal(writes.length, 1);
  const body = JSON.parse(writes[0].options.body);
  assert.deepEqual(body.troncon_ids, ["t1", "t2"]);
  assert.deepEqual(body.object_ids.desordres, ["d1"]);
  assert.deepEqual(body.object_ids.amenagements_hydrauliques, ["a1"]);
  assert.deepEqual(body.intervenant_ids, ["c1"]);
  assert.equal(body.realisation_interne, false);
  assert.equal(body.cout_global, 0);
  assert.equal(body.source_class, undefined);
  assert.equal(body.id, "00000000-0000-4000-8000-000000000001");
  assert.equal(writes[0].options.headers["Operation-Id"], body.id);
  assert.equal(h.editor.shell.state.pending, false);
});

test("an imported prestation uses the business type and keeps its draft on conflict", async () => {
  const h = harness();
  await h.editor.load();
  await h.editor.openEdit("p1");
  assert.equal(h.form.elements.type_prestation_id.disabled, false);
  assert.equal(h.form.elements.date_fin.required, false);
  assert.equal(h.form.elements.type_prestation_id.children.at(-1).value, "OLD");
  h.dialog.nodes['[data-role="create-observation"]'].handlers.click();
  assert.deepEqual(h.obsParent(), { resource: "prestations", id: "p1", designation: "Historique" });
  h.form.elements.designation.value = "Brouillon";
  h.form.elements.troncon_ids.children[1].selected = true;
  h.form.handlers.change();
  h.setConflict(true);
  await h.form.handlers.submit({ preventDefault() {} });
  assert.equal(h.editor.shell.state.conflict, true);
  assert.equal(h.form.elements.designation.value, "Brouillon");
  const body = JSON.parse(h.writes.at(-1).options.body);
  assert.equal(body.type_prestation_id, "OLD");
  assert.equal(body.date_fin, undefined);
  assert.equal(h.writes.at(-1).options.headers["If-Match"], '"1"');
  assert.ok(h.writes.at(-1).options.headers["Operation-Id"]);
  h.dialog.nodes['[data-role="cancel"]'].handlers.click();
  assert.equal(h.dialog.open, true);
  h.setConfirmation(true);
  h.dialog.nodes['[data-role="cancel"]'].handlers.click();
  assert.equal(h.dialog.open, false);
});

test("geometry drawing confirms replacement, with Point, LineString and Polygon", async () => {
  for (const [type, coordinates] of [
    ["Point", [[2, 48]]], ["LineString", [[2, 48], [3, 49]]],
    ["Polygon", [[2, 48], [3, 48], [3, 49]]],
  ]) {
    const h = harness();
    await h.editor.beginCreate();
    h.form.elements.date_fin.value = "2026-02-01";
    h.form.elements.geometry_type.value = type;
    h.dialog.nodes['[data-role="draw"]'].handlers.click();
    for (const [lng, lat] of coordinates) h.map.handlers.click({ latlng: { lng, lat } });
    if (type !== "Point") h.dialog.nodes['[data-role="confirm"]'].handlers.click();
    await h.form.handlers.submit({ preventDefault() {} });
    assert.equal(JSON.parse(h.writes.at(-1).options.body).geometry.type, type);
  }
});
