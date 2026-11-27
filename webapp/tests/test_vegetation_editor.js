const assert = require("node:assert/strict");
const test = require("node:test");
const VegetationEditor = require("../frontend/js/vegetation-editor.js");
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
  return el;
}

function harness({ tracking = false } = {}) {
  const document = {
    createElement() { return element(document); },
    body: { append() {} },
  };
  const controls = element(document), legend = element(document);
  document.querySelector = (selector) =>
    selector === ".map-bottom-controls" ? controls : legend;
  const names = ["designation", "commentaire", "date_debut", "date_fin",
    "famille_vegetation", "disposition", "sous_type", "invasif", "contact_eau",
    "densite", "hauteur_m", "diametre_cm", "type_cote_id", "type_position_id",
    "problematique_ids", "troncon_ids", "amenagement_hydraulique_ids",
    "desordre_ids",
    "etat_sanitaire_id", "classe_hauteur_id", "classe_diametre_id",
    "espece_id",
    "geometry_type", "valid"];
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
    "observations-section", "new"];
  dialog.nodes = {
    form, fieldset: element(document),
    ...Object.fromEntries(roles.map((name) => [`[data-role='${name}']`, element(document)])),
  };
  // The editor uses double-quoted selectors.
  roles.forEach((name) => {
    dialog.nodes[`[data-role="${name}"]`] = dialog.nodes[`[data-role='${name}']`];
  });
  for (const [context, field] of Object.entries({ sous_type: "sous_type" })) {
    const container = element(document);
    container.querySelector = (selector) => selector === "select" ? form.elements[field] : null;
    dialog.nodes[`[data-context='${context}']`] = container;
  }
  document.createElement = tag => {const el=element(document);el.tagName=tag.toUpperCase();return el;};
  const map = {
    handlers: {},
    on(type, handler) { this.handlers[type] = handler; },
    off(type) { delete this.handlers[type]; },
    removeLayer() {},
  };
  const layer = {
    addTo() { return this; }, clearLayers() {}, addLayer() {},
    removeLayer() {}, eachLayer() {}, on() {},
  };
  const L = { geoJSON: () => layer, circleMarker: () => element(document),
    marker: () => layer, polyline: () => layer, polygon: () => layer };
  const original = {
    type: "Feature", id: "v1", geometry: { type: "Point", coordinates: [2, 48] },
    properties: { id: "v1", version: 1, famille_vegetation: "ARBRES",
      disposition: "ISOLE", sous_type: "ARBRE_SUR_PIED", invasif: false,
      troncon_ids: [], amenagement_hydraulique_ids: [], desordre_ids: [],
      designation: "Arbre", valid: true },
  };
  const urls = [], writes = [];
  let deferred = null, conflict = false, failure = null, confirmation = false;
  const fetchJson = async (url) => {
    urls.push(url);
    if (url === "/api/vegetation") return { type: "FeatureCollection", features: [original] };
    if (url === "/api/vegetation/relations/options") return {
      troncons: [{ id: "t1", libelle: "Tronçon 1", valid: true },
        { id: "t2", libelle: "Tronçon 2", valid: true }],
      amenagements_hydrauliques: [{ id: "a1", libelle: "AH 1", valid: true }],
    };
    if (url.endsWith("/observations")) return { observations: [] };
    if (url === "/api/referentiels/problematiques-vegetation") {
      return { references: [
        { id: "DAN", libelle: "Dangereuse", valid: true },
        { id: "GEN", libelle: "Gênante", valid: true },
      ] };
    }
    if (url.startsWith("/api/referentiels/")) {
      return { references: [{ id: "R1", libelle: "Référence", valid: true }] };
    }
    throw Error(url);
  };
  const root = {
    document, crypto: { randomUUID: () => "00000000-0000-4000-8000-000000000001" },
    ObservationParentList: { load: async () => {} },
    addEventListener() {}, confirm: () => confirmation,
    fetch: async (url, options = {}) => {
      writes.push({ url, options });
      if (!options.method) return { ok: true, json: async () => original };
      if (deferred) await deferred.promise;
      if (conflict) return { ok: false, status: 409,
        json: async () => ({ detail: "Conflit" }) };
      if (failure) return { ok: false, status: failure.status,
        json: async () => ({ detail: failure.message }) };
      const payload = JSON.parse(options.body);
      return { ok: true, status: 201, json: async () => ({
        ...original, geometry: payload.geometry || original.geometry,
        properties: { ...original.properties,
          ...payload, id: "v1", version: options.method === "PUT" ? 2 : 1 },
      }) };
    },
  };
  let trackingOptions = null;
  if (tracking) root.PwaGeometryTracking = {
    createWidget(options) {
      trackingOptions = options;
      return { cancel() {}, refresh() {}, setDisabled() {}, isActive: () => false };
    },
  };
  panelHarness.installView(root,view=>{form=view.form;dialog=view.element;});
  const editor = VegetationEditor.create({
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
      httpError: (status, body, message) =>
        Object.assign(new Error(body?.detail || message), { status }),
    },
    observationCreate: { open() {} },
    panel: panelHarness(root),
  });
  return { editor, form, dialog, controls, legend, map, urls, writes, original,
    trackingOptions: () => trackingOptions,
    setDeferred(value) { deferred = value; },
    setConflict(value) { conflict = value; },
    setFailure(value) { failure = value; },
    setConfirmation(value) { confirmation = value; } };
}

test("Végétation builds the three GeoJSON geometries", () => {
  const vertices = [{ lng: 2, lat: 48 }, { lng: 3, lat: 48 }, { lng: 3, lat: 49 }];
  assert.deepEqual(VegetationEditor.geometryFromVertices("Point", vertices),
    { type: "Point", coordinates: [2, 48] });
  assert.deepEqual(VegetationEditor.geometryFromVertices("LineString", vertices),
    { type: "LineString", coordinates: [[2, 48], [3, 48], [3, 49]] });
  assert.deepEqual(VegetationEditor.geometryFromVertices("Polygon", vertices),
    { type: "Polygon", coordinates: [[[2, 48], [3, 48], [3, 49], [2, 48]]] });
});

test("a temporary family detour retains the tree subtype and other draft values", async () => {
  const h = harness();
  await h.editor.beginCreate();
  h.form.elements.famille_vegetation.value = "ARBRES";
  h.form.elements.famille_vegetation.handlers.change();
  assert.equal(h.dialog.nodes["[data-context='sous_type']"].hidden, false);
  h.form.elements.sous_type.value = "SOUCHE";
  h.form.elements.designation.value = "Brouillon conservé";
  h.form.elements.famille_vegetation.value = "HERBACEES";
  h.form.elements.famille_vegetation.handlers.change();
  assert.equal(h.dialog.nodes["[data-context='sous_type']"].hidden, false);
  assert.equal(h.form.elements.sous_type.value, "SOUCHE");
  assert.equal(h.form.elements.designation.value, "Brouillon conservé");
  h.form.elements.famille_vegetation.value = "ARBRES";
  h.form.elements.famille_vegetation.handlers.change();
  assert.equal(h.form.elements.sous_type.value, "SOUCHE");
});

test("Végétation refuses a creation without geometry and keeps the draft", async () => {
  const h = harness();
  await h.editor.beginCreate();
  h.form.elements.famille_vegetation.value = "ARBRES";
  h.form.elements.disposition.value = "ISOLE";
  h.form.elements.sous_type.value = "ARBRE_SUR_PIED";
  h.form.elements.designation.value = "Arbre sans point";

  await h.form.handlers.submit({ preventDefault() {} });

  assert.equal(h.writes.filter(({ options }) => options.method === "POST").length, 0);
  assert.equal(h.editor.shell.state.mode, "create");
  assert.equal(h.form.elements.famille_vegetation.value, "ARBRES");
  assert.equal(h.form.elements.designation.value, "Arbre sans point");
  assert.match(h.dialog.nodes['[data-role="status"]'].textContent, /géométrie.*obligatoire/i);
});

test("Végétation selects direct relations, saves once and preserves edit draft on conflict", async () => {
  const h = harness();
  await h.editor.load();
  assert.equal(h.controls.children.length, 0);
  assert.match(h.legend.children[0].innerHTML, /point.*line.*polygon/);
  await h.editor.beginCreate();
  assert.equal(h.editor.shell.state.mode, "create");
  h.form.elements.famille_vegetation.value = "ARBRES";
  h.form.elements.disposition.value = "ISOLE";
  h.form.elements.sous_type.value = "ARBRE_SUR_PIED";
  h.form.elements.invasif.checked = true;
  h.form.elements.troncon_ids.children.find((item) => item.value === "t2").selected = true;
  h.form.elements.amenagement_hydraulique_ids.children[0].selected = true;
  h.form.elements.problematique_ids.children[0].selected = true;
  h.form.elements.designation.value = "Haie";
  h.dialog.nodes['[data-role="draw"]'].handlers.click();
  h.map.handlers.click({ latlng: { lng: 2.2, lat: 48.2 } });
  h.form.handlers.input();
  assert.equal(h.editor.shell.hasDraft(), true);
  let release;
  h.setDeferred({ promise: new Promise((resolve) => { release = resolve; }) });
  const submit = h.form.handlers.submit({ preventDefault() {} });
  assert.equal(h.editor.shell.state.pending, true);
  await h.form.handlers.submit({ preventDefault() {} });
  release();
  await submit;
  assert.equal(h.writes.filter(({ options }) => options.method === "POST").length, 1);
  const created = JSON.parse(h.writes.at(-1).options.body);
  assert.deepEqual(created.troncon_ids, ["t2"]);
  assert.deepEqual(created.amenagement_hydraulique_ids, ["a1"]);
  assert.deepEqual(created.problematique_ids, ["DAN"]);
  assert.equal(created.invasif, true);
  assert.equal(Object.hasOwn(created, "parcelle_gestion_id"), false);
  assert.equal(h.editor.shell.state.pending, false);
  await h.editor.openEdit("v1");
  h.form.elements.designation.value = "Brouillon";
  h.form.handlers.change();
  assert.equal(h.editor.shell.hasDraft(), true);
  h.setDeferred(null);
  h.setConflict(true);
  await h.form.handlers.submit({ preventDefault() {} });
  assert.equal(h.writes.at(-1).options.headers["If-Match"], '"1"');
  assert.ok(h.writes.at(-1).options.headers["Operation-Id"]);
  assert.equal(h.editor.shell.state.conflict, true);
  assert.equal(h.form.elements.designation.value, "Brouillon");
  h.dialog.nodes['[data-role="cancel"]'].handlers.click();
  assert.equal(h.dialog.open, true);
  h.setConfirmation(true);
  h.dialog.nodes['[data-role="cancel"]'].handlers.click();
  assert.equal(h.dialog.open, false);
});

test("Végétation keeps attributes, relations and geometry after a GEO-2 error", async () => {
  const h = harness();
  await h.editor.beginCreate();
  h.form.elements.famille_vegetation.value = "ARBRES";
  h.form.elements.disposition.value = "ISOLE";
  h.form.elements.sous_type.value = "ARBRE_SUR_PIED";
  h.form.elements.designation.value = "Brouillon à corriger";
  h.form.elements.troncon_ids.children.find((item) => item.value === "t2").selected = true;
  h.dialog.nodes['[data-role="draw"]'].handlers.click();
  h.map.handlers.click({ latlng: { lng: 2.2, lat: 48.2 } });
  h.setFailure({ status: 422, message: "La géométrie est invalide." });

  await h.form.handlers.submit({ preventDefault() {} });

  assert.equal(h.editor.shell.state.mode, "create");
  assert.equal(h.editor.shell.state.pending, false);
  assert.equal(h.form.elements.designation.value, "Brouillon à corriger");
  assert.deepEqual(RelationSelector.values(h.form.elements.troncon_ids), ["t2"]);
  assert.equal(h.dialog.nodes['[data-role="geometry-status"]'].textContent, "");
  assert.equal(JSON.parse(h.writes.find(({ options }) => options.method === "POST").options.body).geometry.type, "Point");
  assert.match(h.dialog.nodes['[data-role="status"]'].textContent, /géométrie est invalide/i);
  assert.equal(h.writes.filter(({ options }) => options.method === "POST").length, 1);

  h.setFailure(null);
  await h.form.handlers.submit({ preventDefault() {} });
  assert.equal(h.writes.filter(({ options }) => options.method === "POST").length, 2);
  assert.equal(h.editor.shell.state.mode, "edit");
});

test("Végétation confirms map geometry before sending it and permits a type change", async () => {
  const h = harness();
  await h.editor.beginCreate();
  h.form.elements.famille_vegetation.value = "ARBRES";
  h.form.elements.disposition.value = "LIGNE";
  h.form.elements.sous_type.value = "ARBRE_SUR_PIED";
  h.form.elements.geometry_type.value = "LineString";
  h.dialog.nodes['[data-role="draw"]'].handlers.click();
  h.map.handlers.click({ latlng: { lng: 2, lat: 48 } });
  h.map.handlers.click({ latlng: { lng: 3, lat: 49 } });
  h.dialog.nodes['[data-role="confirm"]'].handlers.click();
  await h.form.handlers.submit({ preventDefault() {} });
  assert.deepEqual(JSON.parse(h.writes.at(-1).options.body).geometry, {
    type: "LineString", coordinates: [[2, 48], [3, 49]],
  });
  await h.editor.openEdit("v1");
  h.form.elements.geometry_type.value = "Polygon";
  h.form.elements.geometry_type.handlers.change();
  h.form.elements.disposition.value = "SURFACE";
  h.dialog.nodes['[data-role="draw"]'].handlers.click();
  for (const [lng, lat] of [[2, 48], [3, 48], [3, 49]]) {
    h.map.handlers.click({ latlng: { lng, lat } });
  }
  h.dialog.nodes['[data-role="confirm"]'].handlers.click();
  await h.form.handlers.submit({ preventDefault() {} });
  assert.equal(JSON.parse(h.writes.at(-1).options.body).geometry.type, "Polygon");
  assert.equal(h.writes.at(-1).options.method, "PUT");
});

test("tracé GPS remplace seulement la géométrie du brouillon et garde la version ouverte", async () => {
  const h = harness({ tracking: true });
  h.original.geometry = { type: "LineString", coordinates: [[2, 48], [2.001, 48]] };
  h.original.properties.disposition = "LIGNE";
  await h.editor.openEdit("v1");
  const before = h.trackingOptions().getOriginal();
  assert.deepEqual(before, h.original.geometry);
  h.trackingOptions().onConfirm({ type: "LineString",
    coordinates: [[2, 48], [2.002, 48]] });
  assert.equal(h.writes.filter(({ options }) => options.method).length, 0);
  assert.equal(h.editor.shell.hasDraft(), true);
  await h.form.handlers.submit({ preventDefault() {} });
  const write = h.writes.at(-1);
  assert.equal(write.options.method, "PUT");
  assert.equal(write.options.headers["If-Match"], '"1"');
  assert.deepEqual(JSON.parse(write.options.body).geometry,
    { type: "LineString", coordinates: [[2, 48], [2.002, 48]] });
  assert.equal(JSON.parse(write.options.body).designation, undefined,
    "an unchanged designation is omitted from the patch");
  assert.equal(h.form.elements.designation.value, "Arbre");
});
