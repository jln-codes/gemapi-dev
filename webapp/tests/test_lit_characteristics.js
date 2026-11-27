/* CRUD-LIT-1, niveau A : occurrences, précision, OCC et réponses tardives.
 * Aucune base mutable. Le transport est simulé ici ; PostgreSQL qualifie la
 * transaction et les permissions séparément. Coût attendu inférieur à 1 s. */
const assert = require("node:assert/strict");
const test = require("node:test");
const Characteristics = require("../frontend/js/lit-characteristics.js");
const ReferenceData = require("../frontend/js/reference-data.js");

class Element {
  constructor(tag, document) {
    this.tagName = tag;
    this.ownerDocument = document;
    this.children = [];
    this.listeners = new Map();
    this.dataset = {};
    this.classList = { add() {} };
    this.value = "";
    this.checked = false;
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(key, value) { this[key] = value; }
  addEventListener(type, handler) { this.listeners.set(type, handler); }
  reportValidity() { return true; }
  focus() { this.focused = true; }
  find(predicate) {
    if (predicate(this)) return this;
    for (const child of this.children) {
      const match = child.find?.(predicate);
      if (match) return match;
    }
    return null;
  }
}

const parent = (version = 1, values = {}) => ({ type: "Feature", geometry: null,
  properties: { id: "00000000-0000-4000-8000-000000000001", version,
    largeurs_lit: [], pentes_lit: [], domanialites_lit: [],
    regimes_ecoulement_lit: [], occupations_riveraines_lit: [], ...values } });

function harness({ initial = parent(), load, onChanged } = {}) {
  const document = { createElement: tag => new Element(tag, document) };
  const container = document.createElement("section");
  let feature = initial, sequence = 1, dirty = false, mode = "ok";
  const requests = [], published = [], messages = [], changed = [], pending = [];
  const root = { crypto: { randomUUID: () => `00000000-0000-4000-8000-${String(++sequence).padStart(12, "0")}` },
    async fetch(path, options) {
      requests.push({ path, ...options });
      if (typeof mode === "function") return mode(path, options);
      if (mode === "network") throw new Error("Réseau indisponible");
      if (typeof mode === "number") return { ok: false, status: mode,
        json: async () => ({ detail: "Valeur refusée" }) };
      const body = JSON.parse(options.body);
      const family = path.split("/caracteristiques/")[1].split("/")[0];
      const collection = Characteristics.FAMILIES[family].collection;
      const row = options.method === "POST" ? body : { ...feature.properties[collection][0], ...body };
      const result = parent(feature.properties.version + 1, { ...feature.properties,
        version: feature.properties.version + 1,
        [collection]: options.method === "POST" ? [...feature.properties[collection], row] : [row] });
      result.mutation = { resource: "troncons-lit", ids: [feature.properties.id] };
      return { ok: true, status: 200, json: async () => result };
    } };
  const controller = Characteristics.create({ root, container, getParent: () => feature,
    fetchJson: async () => ({}), isParentDirty: () => dirty,
    referenceData: { load: load || (async () => []), fillSelect: ReferenceData.fillSelect },
    onChanged: async result => { feature = result; changed.push(result); await onChanged?.(result); },
    onPending: value => pending.push(value),
    setStatus: (message, error) => messages.push({ message, error }),
    mutationController: { requestHeaders: async () => ({ "X-Origin": "test" }),
      publishMutation: async mutation => published.push(mutation) },
  });
  return { controller, container, requests, published, messages, changed, pending, root,
    get feature() { return feature; },
    setFeature(value) { feature = value; }, setMode(value) { mode = value; },
    setDirty(value) { dirty = value; },
    field(name) { return container.find(node => node.name === name); },
  };
}

test("cinq familles : champs propres, catalogues canoniques et aucun dessin autonome", () => {
  assert.equal(Object.keys(Characteristics.FAMILIES).length, 5);
  assert.deepEqual(Characteristics.FAMILIES["occupations-riveraines"].fields.map(field => field.name),
    ["type_occupation_id", "rive_id"]);
  assert.deepEqual(ReferenceData.CATALOGS.domanialitesLit, ["/api/referentiels/domanialites-lit", "references"]);
  assert.deepEqual(ReferenceData.CATALOGS.typesZoneAtterrissementLit,
    ["/api/referentiels/types-zone-atterrissement-lit", "references"]);
  for (const name of Object.keys(Characteristics.FAMILIES)) {
    assert.equal(Characteristics.fields(name).some(field => field.name === "geometry"), false);
    assert.equal(Characteristics.fields(name).some(field => field.name === "segment_id"), false);
  }
});

test("les décimaux conservent précision et zéro ; NULL reste distinct de faux", () => {
  const number = "12345678901234567890.12345678901234567890";
  assert.equal(Characteristics.decimal(number, "Largeur"), number);
  assert.equal(Characteristics.decimal("0", "Largeur"), "0");
  assert.equal(Characteristics.decimal("", "Largeur"), null);
  for (const invalid of [false, 0, "NaN", "Infinity", "1,25", "x"])
    assert.throws(() => Characteristics.decimal(invalid, "Largeur"), /décimal/);
  const payload = Characteristics.buildPayload("largeurs", {
    largeur_moyenne: number, pr_debut: "0", pr_fin: "", valid: false,
    source_class: "forbidden", geometry: {}, segment_id: "other-parent",
  }, { id: "new-id" });
  assert.equal(payload.largeur_moyenne, number);
  assert.equal(payload.pr_debut, "0");
  assert.equal(payload.pr_fin, null);
  assert.equal(payload.valid, false);
  assert.equal("source_class" in payload || "geometry" in payload || "segment_id" in payload, false);
});

test("PUT différentiel conserve les repérages historiques non édités", () => {
  const original = { id: "old", largeur_moyenne: "0.000", designation: "Avant", valid: false,
    pr_debut: "0", pr_fin: null, systeme_rep_id: "sys", borne_debut_id: "borne",
    borne_debut_distance: "12.345678901234567890", borne_debut_aval: false,
    position_debut_ewkt: "SRID=3950;POINT(1 2)" };
  const values = { ...original, designation: "Après" };
  assert.deepEqual(Characteristics.buildPayload("largeurs", values, { original }), { designation: "Après" });
  assert.deepEqual(Characteristics.buildPayload("largeurs", { ...values, largeur_moyenne: "" }, { original }),
    { largeur_moyenne: null, designation: "Après" });
  assert.throws(() => Characteristics.buildPayload("pentes", {
    date_debut: "2026-05-02", date_fin: "2026-05-01", valid: true,
  }, { id: "id" }), /date de fin/);
});

test("deux occurrences identiques restent distinctes dans le parent et les requêtes", async () => {
  const h = harness();
  for (let i = 0; i < 2; i++) {
    assert.equal(await h.controller.open("largeurs"), true);
    h.field("largeur_moyenne").value = "0";
    await h.controller.save();
  }
  assert.equal(h.requests.length, 2);
  const bodies = h.requests.map(request => JSON.parse(request.body));
  assert.notEqual(bodies[0].id, bodies[1].id);
  assert.equal(bodies[0].largeur_moyenne, bodies[1].largeur_moyenne);
  assert.equal(h.feature.properties.largeurs_lit.length, 2);
  assert.equal(h.requests[0].headers["If-Match"], '"1"');
  assert.equal(h.requests[1].headers["If-Match"], '"2"');
  assert.equal(h.published.length, 2);
  assert.deepEqual(h.pending, [true, false, true, false, true, false, true, false]);
});

test("un rejet 409/422 conserve la saisie, la version parent et l'identité d'opération", async () => {
  const h = harness();
  await h.controller.open("pentes");
  h.field("pente_moyenne").value = "2.50";
  h.setMode(409);
  await h.controller.save();
  assert.equal(h.controller.hasDraft(), true);
  assert.equal(h.field("pente_moyenne").value, "2.50");
  h.setMode(422);
  await h.controller.save();
  assert.equal(h.requests[1].headers["Operation-Id"], h.requests[0].headers["Operation-Id"]);
  assert.equal(h.requests[1].headers["If-Match"], '"1"');
  h.setMode("ok");
  await h.controller.save();
  assert.equal(h.requests[2].headers["Operation-Id"], h.requests[0].headers["Operation-Id"]);
  assert.equal(h.controller.hasDraft(), false);
});

test("un résultat réseau incertain est rejoué à l'identique avant toute autre valeur", async () => {
  const h = harness();
  await h.controller.open("largeurs");
  h.field("largeur_moyenne").value = "1.5";
  h.setMode("network");
  await h.controller.save();
  h.field("largeur_moyenne").value = "2.5";
  await h.controller.save();
  assert.equal(h.requests.length, 1);
  assert.match(h.messages.at(-1).message, /non confirmé/);
  h.field("largeur_moyenne").value = "1.5";
  h.setMode("ok");
  await h.controller.save();
  assert.equal(h.requests[1].body, h.requests[0].body);
  assert.equal(h.requests[1].headers["Operation-Id"], h.requests[0].headers["Operation-Id"]);
});

test("une réponse tardive de catalogue annulé ne rouvre pas le formulaire", async () => {
  let release;
  const h = harness({ load: () => new Promise(resolve => { release = resolve; }) });
  const load = h.controller.open("domanialites");
  assert.equal(h.controller.hasDraft(), true);
  assert.equal(h.controller.reset(), true);
  release([{ id: "type-1", libelle: "Public" }]);
  assert.equal(await load, false);
  assert.equal(h.controller.hasDraft(), false);
  assert.equal(h.field("type_id"), null);
});

test("les mises à jour parent concurrentes ne remplacent ni version ni valeurs du brouillon", async () => {
  const h = harness();
  await h.controller.open("largeurs");
  h.field("largeur_moyenne").value = "0";
  h.setFeature(parent(3));
  assert.equal(h.controller.render(h.feature), false);
  assert.equal(h.field("largeur_moyenne").value, "0");
  h.setMode(409);
  await h.controller.save();
  assert.equal(h.requests[0].headers["If-Match"], '"1"');
  assert.equal(h.controller.hasDraft(), true);
});

test("brouillon parent, permissions et nettoyage empêchent les mutations interdites", async () => {
  const h = harness();
  h.setDirty(true);
  assert.equal(await h.controller.open("largeurs"), false);
  h.setDirty(false);
  h.root.BusinessRuntime = { can: () => false, key: () => "base", isActive: () => true };
  assert.equal(await h.controller.open("largeurs"), false);
  delete h.root.BusinessRuntime;
  await h.controller.open("largeurs");
  h.controller.dispose();
  assert.equal(h.container.children.length, 0);
  assert.equal(await h.controller.save(), null);
  assert.equal(h.requests.length, 0);
});

test("la consultation sans permission d'édition conserve les détails et les références historiques", () => {
  const h = harness({ initial: parent(1, { largeurs_lit: [{ id: "old", valid: false,
    largeur_moyenne: "0", commentaire: "Note conservée", date_debut: "2026-01-01",
    systeme_rep_id: "systeme-conserve", borne_debut_aval: false }] }) });
  h.root.BusinessRuntime = { can: () => false };
  h.controller.render(h.feature);
  assert.ok(h.container.find(node => node.textContent === "Note conservée"));
  assert.ok(h.container.find(node => node.textContent === "systeme-conserve"));
  assert.equal(h.container.find(node => node.tagName === "button" && node.textContent === "Modifier"), null);
});

test("une réponse d'écriture après fermeture n'écrase pas une autre fiche", async () => {
  const h = harness();
  await h.controller.open("largeurs");
  let release;
  h.setMode(() => new Promise(resolve => { release = resolve; }));
  const writing = h.controller.save();
  await new Promise(resolve => setImmediate(resolve));
  const body = JSON.parse(h.requests[0].body);
  h.controller.dispose();
  release({ ok: true, json: async () => parent(2, { largeurs_lit: [body] }) });
  assert.equal(await writing, null);
  assert.equal(h.changed.length, 0);
  assert.equal(h.published.length, 0);
});

test("un échec d'affichage après confirmation n'empêche pas la publication", async () => {
  const h = harness({ onChanged: () => { throw new Error("Vue interrompue"); } });
  await h.controller.open("largeurs");
  assert.ok(await h.controller.save());
  assert.equal(h.controller.hasDraft(), false);
  assert.equal(h.published.length, 1);
  assert.match(h.messages.at(-1).message, /enregistrée.*incomplète/);
});
