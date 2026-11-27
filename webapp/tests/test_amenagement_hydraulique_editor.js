const assert = require("node:assert/strict");
const test = require("node:test");
const AmenagementHydrauliqueEditor = require("../frontend/js/amenagement-hydraulique-editor.js");
const EntityEditorShell = require("../frontend/js/entity-editor-shell.js");
const RelationSelector = require("../frontend/js/relation-selector.js");
const ReferenceData = require("../frontend/js/reference-data.js");
const MapLayerControls = require("../frontend/js/map-layer-controls.js");
const panelHarness = require("./panel-harness.js");

function element(document) {
  const handlers = {};
  const el = {
    ownerDocument: document, children: [], handlers, value: "", textContent: "", dataset: {},
    hidden: false, disabled: false, checked: false, classList: { toggle() {}, add() {}, remove() {} },
    append(...children) { this.children.push(...children); },
    setAttribute(name,value) {this[name]=value;},
    insertBefore(child,before){this.children.splice(this.children.indexOf(before),0,child);},
    replaceChildren() { this.children = []; },
    addEventListener(type, fn) { handlers[type] = fn; },
    focus() {}, show() { this.open = true; }, close() { this.open = false; },
    get selectedOptions() { return this.children.filter((child) => child.selected); },
    querySelector(selector) {
      if (selector === "input") return this.input || (this.input = element(document));
      return this.nodes?.[selector] || null;
    },
  };
  return el;
}

function harness() {
  const document = {
    createElement() { return element(document); },
    body: { append() {} },
  };
  const controls = element(document), legend = element(document);
  document.querySelector = (selector) =>
    selector === ".map-bottom-controls" ? controls : legend;
  const names = ["designation", "libelle", "commentaire", "date_debut",
    "date_fin", "troncon_ids", "zone_ids", "valid"];
  names.push("desordre_ids");
  let form = element(document);
  form.elements = Object.fromEntries(names.map((name) => [name, element(document)]));
  form.reportValidity = () => true;
  form.reset = () => names.forEach((name) => {
    form.elements[name].value = "";
    form.elements[name].checked = false;
  });
  let dialog = element(document);
  const roles = ["catalog", "status", "geometry-status", "save", "cancel",
    "draw", "confirm", "restore", "create-observation", "observations",
    "observations-section", "sous-objets-section", "sous-objets", "new"];
  dialog.nodes = {
    form, fieldset: element(document),
    ...Object.fromEntries(roles.map((name) => [`[data-role='${name}']`, element(document)])),
  };
  // The editor uses double-quoted selectors.
  roles.forEach((name) => {
    dialog.nodes[`[data-role="${name}"]`] = dialog.nodes[`[data-role='${name}']`];
  });
  let panelCreated = false;
  document.createElement = tag=>{const el=element(document);el.tagName=tag.toUpperCase();return el;};
  const map = {
    handlers: {},
    on(type, handler) { this.handlers[type] = handler; },
    off(type) { delete this.handlers[type]; },
    removeLayer() {},
  };
  const layer = {
    addTo() { return this; }, clearLayers() {}, addLayer() {},
    removeLayer() {}, eachLayer() {}, on() {}, getLatLng() { return { lng: 2, lat: 48 }; },
  };
  const L = { geoJSON: () => layer, circleMarker: () => element(document),
    marker: () => layer, polyline: () => layer, polygon: () => layer };
  const original = {
    type: "Feature", id: "v1", geometry: null,
    properties: { id: "v1", version: 1, troncon_ids: ["t1"], zone_ids: ["z1"], designation: "Bassin",
      desordre_ids: [],
      valid: false, troncons: [{ id: "t1", designation: "Tronçon 1",
        type_physique_id: "INDETERMINE", digue_id: null }], zones_stockage: [] },
  };
  const urls = [], writes = [], navigations=[];
  let deferred = null, conflict = false, confirmation = false, observedParent;
  const fetchJson = async (url) => {
    urls.push(url);
    if (url === "/api/amenagements-hydrauliques") return { type: "FeatureCollection", features: [original] };
    if (url === "/api/amenagements-hydrauliques/relations/options") return {
      troncons: [{ id: "t1", libelle: "Tronçon 1", valid: true },
        { id: "t2", libelle: "Tronçon 2", valid: true },
        { id: "t3", libelle: "Tronçon 3", valid: false }],
      zones: [{ id: "z1", libelle: "Zone 1", valid: true },
        { id: "z2", libelle: "Zone 2", valid: true }],
    };
    if (url.endsWith("/observations")) return { observations: [] };
    if (url.startsWith("/api/referentiels/")) {
      return { references: [{ id: "T", libelle: "Type AH", valid: true }] };
    }
    throw Error(url);
  };
  const root = {
    MapEntityNavigation:{open:(...args)=>navigations.push(args)},
    document, crypto: { randomUUID: () => "00000000-0000-4000-8000-000000000001" },
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
        properties: { ...original.properties,
          ...payload, id: "v1", version: options.method === "PUT" ? 2 : 1 },
      }) };
    },
  };
  panelHarness.installView(root,view=>{form=view.form;dialog=view.element;});
  const editor = AmenagementHydrauliqueEditor.create({
    root, map, L, fetchJson, referenceData: ReferenceData,
    entityEditor: EntityEditorShell, relationSelector: {
      ...RelationSelector,
      fillDisorders(select, choices, selected) {
        return RelationSelector.fill(select, choices.desordres || [], selected || []);
      },
    },
    layerControls: MapLayerControls,
    mutationController: {
      requestHeaders: async () => ({}),
      publishMutation: async () => {},
    },
    concurrency: {
      headers: (version) => ({ "If-Match": `"${version}"` }),
      httpError: (status, _body, message) =>
        Object.assign(new Error(message), { status }),
      isVersionConflict: (error) => error.status === 409
        && error.code !== "disorder-already-associated",
    },
    observationCreate: { open(parent) { observedParent = parent; } },
    panel: panelHarness(root),
  });
  return { editor, form, dialog, controls, legend, map, urls, writes, original, navigations,
    observedParent: () => observedParent,
    setDeferred(value) { deferred = value; },
    setConflict(value) { conflict = value; },
    setConfirmation(value) { confirmation = value; } };
}

test("AH has no drawing command and opens Observations on the regulatory parent", async () => {
  const h = harness();
  assert.equal(h.dialog.querySelector('[data-role="draw"]'),null);
  assert.equal(h.form.elements.geometry_type,undefined);
  await h.editor.openEdit("v1");
  const composition = h.dialog.nodes['[data-role="sous-objets"]'];
  const link=composition.children[1].children[1];
  assert.equal(link.textContent,"Tronçon 1");
  link.handlers.click();
  assert.deepEqual(h.navigations,[["troncons","t1"]]);
  h.dialog.nodes['[data-role="create-observation"]'].handlers.click();
  assert.deepEqual(h.observedParent(), {
    resource: "amenagements_hydrauliques", id: "v1", designation: "Bassin",
  });
});

test("AH creates without Zone, Tronçon or geometry and keeps a concurrent draft", async () => {
  const h = harness();
  await h.editor.beginCreate();
  h.form.elements.designation.value = "AH vide";
  h.form.handlers.input();
  await h.form.handlers.submit({ preventDefault() {} });
  const body = JSON.parse(h.writes.at(-1).options.body);
  assert.deepEqual(body.troncon_ids, []);
  assert.deepEqual(body.zone_ids, []);
  assert.equal(Object.hasOwn(body, "geometry"), false);
  await h.editor.openEdit("v1");
  h.form.elements.designation.value = "Brouillon";
  h.form.handlers.change();
  h.setConflict(true);
  await h.form.handlers.submit({ preventDefault() {} });
  assert.equal(h.editor.shell.state.conflict, true);
  assert.equal(h.form.elements.designation.value, "Brouillon");
});

test("AH submits only explicitly selected Zone and Tronçon links", async () => {
  const h = harness();
  await h.editor.beginCreate();
  h.form.elements.troncon_ids.children[0].selected = true;
  h.form.elements.zone_ids.children[1].selected = true;
  await h.form.handlers.submit({ preventDefault() {} });
  const body = JSON.parse(h.writes.at(-1).options.body);
  assert.deepEqual(body.troncon_ids, ["t1"]);
  assert.deepEqual(body.zone_ids, ["z2"]);
  assert.equal(Object.hasOwn(body, "geometry"), false);
});
