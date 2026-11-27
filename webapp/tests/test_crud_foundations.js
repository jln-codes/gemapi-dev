const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const EntityEditorShell = require("../frontend/js/entity-editor-shell.js");
const RelationSelector = require("../frontend/js/relation-selector.js");
const ResourceConcurrency = require("../frontend/js/resource-concurrency.js");
const MobilierForm = require("../frontend/js/mobilier-form.js");
const VegetationForm = require("../frontend/js/vegetation-form.js");
const AmenagementHydrauliqueForm = require("../frontend/js/amenagement-hydraulique-form.js");
const frontend = path.resolve(__dirname, "../frontend");

function fakeSelect() {
  return {
    children: [],
    multiple: false,
    ownerDocument: {
      createElement() {
        return { value: "", textContent: "", selected: false };
      },
    },
    replaceChildren() { this.children = []; },
    append(option) { this.children.push(option); },
    get selectedOptions() { return this.children.filter((option) => option.selected); },
  };
}

function field(value = "") {
  return { value, checked: false };
}

function selectWith(values = [], selected = []) {
  const select = fakeSelect();
  RelationSelector.fill(select, values.map(id => ({ id, label: id })), selected);
  return select;
}

test("entity editor shell covers create, edit, dirty state and cancellation", () => {
  const shell = EntityEditorShell.create();
  shell.beginCreate({ objectType: "mobilier", data: { designation: "Brouillon" } });
  assert.equal(shell.state.mode, "create");
  assert.equal(shell.hasDraft(), true);

  const snapshot = { designation: "Serveur" };
  shell.beginEdit({ objectType: "desordre", objectId: "d1", data: snapshot });
  assert.equal(shell.hasDraft(), false);
  shell.state.data = { designation: "Modifié" };
  shell.markDirty();
  assert.equal(shell.hasDraft(), true);
  assert.equal(shell.cancel(), snapshot);
  assert.equal(shell.state.data, snapshot);
  assert.equal(shell.hasDraft(), false);
});

test("entity editor shell exposes pending hooks and confirms a save", () => {
  const calls = [];
  const shell = EntityEditorShell.create({}, {
    beforeSave: (context) => calls.push(["before", context]),
    afterSave: (context) => calls.push(["after", context]),
  });
  shell.beginEdit({ data: { designation: "Avant" } });
  shell.markDirty();
  shell.beginSave("request");
  assert.equal(shell.state.pending, true);
  shell.confirm({ designation: "Après" }, "response");
  assert.deepEqual(calls, [["before", "request"], ["after", "response"]]);
  assert.equal(shell.state.pending, false);
  assert.equal(shell.state.dirty, false);
  assert.deepEqual(shell.state.snapshot, { designation: "Après" });
});

test("entity editor shell keeps the draft on conflict", () => {
  const shell = EntityEditorShell.create();
  shell.beginEdit({ data: { designation: "Serveur" } });
  const draft = { designation: "Brouillon" };
  shell.state.data = draft;
  shell.markDirty();
  shell.beginSave();
  const conflict = new Error("Conflit");
  shell.fail(conflict, { conflict: true });
  assert.equal(shell.state.data, draft);
  assert.equal(shell.state.pending, false);
  assert.equal(shell.state.conflict, true);
  assert.equal(shell.state.error, conflict);
  assert.equal(shell.hasDraft(), true);
});

test("mandatory geometry rejects missing and incompatible drafts", () => {
  assert.throws(() => EntityEditorShell.requireGeometry({
    mode: "create", allowedTypes: ["Point"], label: "cet objet",
  }), /géométrie.*obligatoire/i);
  assert.throws(() => EntityEditorShell.requireGeometry({
    mode: "edit",
    currentGeometry: { type: "Point", coordinates: [2, 48] },
    geometryChanged: true,
    draftGeometry: null,
    allowedTypes: ["Point"],
  }), /géométrie.*obligatoire/i);
  assert.throws(() => EntityEditorShell.requireGeometry({
    mode: "create",
    geometryChanged: true,
    draftGeometry: { type: "Polygon", coordinates: [] },
    allowedTypes: ["LineString"],
    label: "ce cheminement",
  }), /LineString/);
});

test("mandatory geometry accepts a server geometry without marking it changed", () => {
  const geometry = { type: "LineString", coordinates: [[2, 48], [3, 49]] };
  assert.equal(EntityEditorShell.requireGeometry({
    mode: "edit", currentGeometry: geometry,
    geometryChanged: false, allowedTypes: ["LineString"],
  }), geometry);
});

test("relation selector renders a simple optional relation", () => {
  const select = fakeSelect();
  RelationSelector.fill(select, [{ id: 7, libelle: "Sept" }], [7], {
    multiple: false,
    emptyLabel: "Aucun",
  });
  assert.equal(select.multiple, false);
  assert.deepEqual(select.children.map((option) => option.value), ["", "7"]);
  assert.deepEqual(RelationSelector.values(select), ["7"]);
});

test("relation selector preserves server order for multiple relations", () => {
  const select = fakeSelect();
  const rows = [
    { id: "b", libelle: "B" },
    { id: "a", libelle: "A" },
    { id: "c", libelle: "C" },
  ];
  RelationSelector.fill(select, rows, ["a", "c"]);
  assert.equal(select.multiple, true);
  assert.deepEqual(select.children.map((option) => option.value), ["b", "a", "c"]);
  assert.deepEqual(RelationSelector.values(select), ["a", "c"]);
});

test("relation selector keeps the current value outside a contextual filter", () => {
  const select = fakeSelect();
  const rows = [{ id: "visible" }, { id: "current" }, { id: "hidden" }];
  RelationSelector.fill(select, rows, ["current"], {
    include: (row) => row.id === "visible",
    preserveSelectedOutsideFilter: true,
    label: (row) => row.id,
    outsideLabel: (label) => `${label} (actuel)`,
  });
  assert.deepEqual(select.children.map((option) => option.value), ["visible", "current"]);
  assert.equal(select.children[1].textContent, "current (actuel)");
  assert.deepEqual(RelationSelector.values(select), ["current"]);
});

test("relation selector does not invent ordering metadata", () => {
  const result = RelationSelector.choices([
    { id: "2", ordre: null }, { id: "1", ordre: null },
  ]);
  assert.deepEqual(result.map((choice) => choice.value), ["2", "1"]);
  assert.equal(result.every((choice) => !("order" in choice)), true);
});

test("remote relation state keeps selections outside search pages", () => {
  const state = RelationSelector.selectionState(
    [{ id: "a", label: "Premier" }], ["a"],
  );
  state.add({ id: "b", label: "Second", valid: false });
  state.merge([{ id: "a", label: "Premier relu" }]);
  assert.deepEqual(state.ids(), ["a", "b"]);
  assert.equal(state.rows()[0].label, "Premier relu");
  assert.equal(state.rows()[1].valid, false);
  assert.equal(state.isDirty(), true);
  state.remove("a");
  assert.deepEqual(state.ids(), ["b"]);
});

test("selected disorders are resolved in bounded batches and input order", async () => {
  const ids = Array.from({ length: 205 }, (_, index) => `id-${index}`);
  const calls = [];
  const result = await RelationSelector.loadSelected(async (url) => {
    const parsed = new URL(url, "https://digues.test");
    const batch = parsed.searchParams.getAll("ids");
    calls.push(batch);
    return {
      items: batch.filter(id => id !== "id-150").reverse()
        .map(id => ({ id, label: id })),
      missing_ids: batch.filter(id => id === "id-150"),
    };
  }, ids);
  assert.deepEqual(calls.map(batch => batch.length), [100, 100, 5]);
  assert.deepEqual(result.items.slice(0, 3).map(row => row.id), ids.slice(0, 3));
  assert.deepEqual(result.missing_ids, ["id-150"]);
});

test("shared disorder relations distinguish unchanged, emptied and create payloads", () => {
  const select = fakeSelect();
  RelationSelector.fill(select, [
    { id: "a", label: "Premier" }, { id: "b", label: "Second" },
  ], ["a"]);
  const unchanged = RelationSelector.assignChanged({}, "desordre_ids", select, ["a"]);
  assert.deepEqual(unchanged, {});
  select.children.forEach(option => { option.selected = false; });
  const emptied = RelationSelector.assignChanged({}, "desordre_ids", select, ["a"]);
  assert.deepEqual(emptied, { desordre_ids: [] });
  const created = RelationSelector.assignChanged({}, "desordre_ids", select, [], { force: true });
  assert.deepEqual(created, { desordre_ids: [] });
});

test("shared disorder loading preserves missing identifiers and does not filter valid false", async () => {
  const result = await RelationSelector.loadDisorders(async () => ({
    items: [{ id: "inactive", label: "À valider", valid: false }],
    missing_ids: ["missing"],
  }), ["inactive", "missing"]);
  assert.deepEqual(result.desordres, [
    { id: "inactive", label: "À valider", valid: false },
    { id: "missing", label: "Désordre introuvable — missing", missing: true },
  ]);
});

test("hydraulic ownership conflicts keep their actionable server message", () => {
  const error = ResourceConcurrency.httpError(409, { detail: {
    code: "disorder-already-associated",
    message: "Ce Désordre est déjà associé à un autre aménagement hydraulique.",
  } }, "Refus");
  assert.equal(error.code, "disorder-already-associated");
  assert.match(error.message, /déjà associé/);
  assert.equal(ResourceConcurrency.isVersionConflict(error), false);
  const versionConflict = ResourceConcurrency.httpError(409, { detail: {
    code: "version-conflict", message: "Version serveur",
  } }, "Refus");
  assert.equal(versionConflict.message, ResourceConcurrency.CONFLICT_MESSAGE);
  assert.equal(ResourceConcurrency.isVersionConflict(versionConflict), true);
  const geometryConflict = ResourceConcurrency.httpError(409, { detail: {
    code: "geometry-contract-changed", message: "Le contrat géométrique a changé. Votre brouillon est conservé.",
  } }, "Refus");
  assert.equal(geometryConflict.code, "geometry-contract-changed");
  assert.match(geometryConflict.message, /contrat géométrique/);
  assert.equal(ResourceConcurrency.isVersionConflict(geometryConflict), true);
});

test("the three shared forms omit unchanged disorders and serialize explicit removal", () => {
  for (const classId of ["mobilier", "equipements_mesure", "reseaux_techniques"]) {
    const form = { elements: {
      designation: field("Objet"), commentaire: field(), date_debut: field(),
      type_id: field("TYPE"), troncon_id: field(), valid: { checked: true },
      desordre_ids: selectWith(["d1"], ["d1"]),
    } };
    const feature = { properties: { id: "parent", desordre_ids: ["d1"] } };
    assert.deepEqual(MobilierForm.payload(
      form, MobilierForm.profile(classId), RelationSelector, null,
    ).desordre_ids, ["d1"]);
    assert.equal("desordre_ids" in MobilierForm.payload(
      form, MobilierForm.profile(classId), RelationSelector, feature,
    ), false);
    form.elements.desordre_ids.children[0].selected = false;
    assert.deepEqual(MobilierForm.payload(
      form, MobilierForm.profile(classId), RelationSelector, feature,
    ).desordre_ids, []);
  }

  const vegetationElements = Object.fromEntries([
    "famille_vegetation", "disposition", "sous_type", "designation", "commentaire",
    "date_debut", "date_fin", "etat_sanitaire_id", "classe_hauteur_id",
    "classe_diametre_id", "espece_id", "type_cote_id", "type_position_id",
    "densite", "hauteur_m", "diametre_cm", "contact_eau",
  ].map(name => [name, field({ famille_vegetation: "ARBRES", disposition: "ISOLE",
    sous_type: "ARBRE_SUR_PIED" }[name] || "")]));
  Object.assign(vegetationElements, {
    valid: { checked: true }, invasif: { checked: false },
    problematique_ids: selectWith(), troncon_ids: selectWith(),
    amenagement_hydraulique_ids: selectWith(), desordre_ids: selectWith(["d1"], ["d1"]),
  });
  const vegetation = { elements: vegetationElements };
  const feature = { properties: { id: "v1", desordre_ids: ["d1"] } };
  assert.equal("desordre_ids" in VegetationForm.payload(
    vegetation, null, RelationSelector, feature,
  ), false);
  vegetation.elements.desordre_ids.children[0].selected = false;
  assert.deepEqual(VegetationForm.payload(
    vegetation, null, RelationSelector, feature,
  ).desordre_ids, []);

  const amenagement = { elements: {
    type_id: field(), designation: field(), libelle: field(), commentaire: field(),
    date_debut: field(), date_fin: field(), valid: field(), troncon_ids: selectWith(), zone_ids: selectWith(),
    desordre_ids: selectWith(["d1"], ["d1"]),
  } };
  assert.equal("desordre_ids" in AmenagementHydrauliqueForm.payload(
    amenagement, null, RelationSelector, { properties: { id: "a1", desordre_ids: ["d1"] } },
  ), false);
  amenagement.elements.desordre_ids.children[0].selected = false;
  assert.deepEqual(AmenagementHydrauliqueForm.payload(
    amenagement, null, RelationSelector, { properties: { id: "a1", desordre_ids: ["d1"] } },
  ).desordre_ids, []);
});

test("the map loads and caches both foundations before its domain controller", () => {
  const page = fs.readFileSync(path.join(frontend, "map.html"), "utf8");
  const worker = fs.readFileSync(path.join(frontend, "service-worker.js"), "utf8");
  const shell = '/static/js/entity-editor-shell.js';
  const relations = '/static/js/relation-selector.js';
  const map = '/static/js/map.js';
  assert.ok(page.indexOf(shell) < page.indexOf(map));
  assert.ok(page.indexOf(relations) < page.indexOf(map));
  assert.equal(page.match(new RegExp(shell.replaceAll("/", "\\/"), "g")).length, 1);
  assert.equal(page.match(new RegExp(relations.replaceAll("/", "\\/"), "g")).length, 1);
  assert.ok(worker.includes(`"${shell}"`));
  assert.ok(worker.includes(`"${relations}"`));
});
