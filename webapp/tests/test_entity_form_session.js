const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const EntityFormSession = require("../frontend/js/entity-form-session.js");
const EntityEditorShell = require("../frontend/js/entity-editor-shell.js");
const panelHarness = require("./panel-harness.js");

test("le socle est chargé avant le pilote et inclus dans le cache PWA", () => {
  const frontend = path.join(__dirname, "../frontend");
  const page = fs.readFileSync(path.join(frontend, "map.html"), "utf8");
  const worker = fs.readFileSync(path.join(frontend, "service-worker.js"), "utf8");
  const shell = "/static/js/entity-editor-shell.js";
  const session = "/static/js/entity-form-session.js";
  const geometry = "/static/js/entity-geometry-draft.js";
  const editor = "/static/js/ouvrage-particulier-editor.js";
  assert.ok(page.indexOf(shell) < page.indexOf(session));
  assert.ok(page.indexOf(session) < page.indexOf(editor));
  assert.ok(page.indexOf(geometry) < page.indexOf(editor));
  assert.ok(page.indexOf(geometry) < page.indexOf("/static/js/vegetation-editor.js"));
  assert.equal(page.split(session).length - 1, 1);
  assert.equal(page.split(geometry).length - 1, 1);
  assert.ok(worker.includes(`"${session}"`));
  assert.ok(worker.includes(`"${geometry}"`));
});

function harness() {
  const listeners = new Map();
  let confirm = false;
  const root = {
    crypto: require("node:crypto").webcrypto,
    addEventListener(name, listener) { listeners.set(name, listener); },
    removeEventListener(name, listener) {
      if (listeners.get(name) === listener) listeners.delete(name);
    },
    confirm: () => confirm,
  };
  const shell = EntityEditorShell.create();
  const element = { removed: false, remove() { this.removed = true; } };
  const statuses = [], pending = [];
  let dirty = false, discarded = 0, disposed = 0;
  const session = EntityFormSession.create({
    root, shell, panel: panelHarness(root), element,
    objectType: "ouvrages-particuliers", label: "Ouvrage particulier",
    isDirty: () => dirty, hasActiveDraft: () => false,
    onDiscard: () => { discarded++; },
    setPending: value => pending.push(value),
    setStatus: (message, error) => statuses.push({ message, error }),
    onDispose: () => { disposed++; },
  });
  const form = { reportValidity: () => true };
  const event = { preventDefault() {} };
  return { root, shell, element, session, listeners, statuses, pending, form, event,
    setDirty(value) { dirty = value; }, setConfirm(value) { confirm = value; },
    discarded: () => discarded, disposed: () => disposed };
}

test("la session ignore une lecture tardive et protège l'abandon du brouillon", async () => {
  const h = harness();
  const first = h.session.begin("create", null, () => {});
  assert.equal(h.shell.state.mode, "create");
  h.setDirty(true);
  assert.equal(h.session.close(), false);
  assert.equal(h.session.isCurrent(first), true);
  let release;
  const delayed = new Promise(resolve => { release = resolve; });
  let rendered = false;
  const read = h.session.load(first, () => delayed, () => { rendered = true; }, () => {});
  h.setConfirm(true);
  assert.equal(h.session.close(), true);
  const second = h.session.begin("edit", "o1", () => {});
  assert.equal(h.shell.state.mode, "read");
  release({ id: "obsolete" });
  assert.equal(await read, null);
  assert.equal(rendered, false);
  assert.equal(h.session.isCurrent(second), true);
  assert.equal(h.discarded(), 1);
});

// FORM-CORE-3, niveau A (< 1 s) : aucun transport/stockage mutable.
// Les promesses contrôlées reproduisent les réponses tardives et ambiguës.
test("une identité d'objet ou de famille différente invalide la génération courante", () => {
  const h = harness();
  const token = h.session.begin("edit", "o1", () => {});
  assert.equal(h.shell.state.objectId, "o1");
  assert.equal(h.session.isCurrent(token), true);
  h.shell.setContext({ objectType: "autre", objectId: "o1" });
  assert.equal(h.session.isCurrent(token), false);
});

test("une réponse de référentiel ne remplace pas une saisie commencée pendant son attente", async () => {
  const h = harness();
  const token = h.session.begin("create", null, () => {});
  let release, rendered = false;
  const loading = h.session.load(token, () => new Promise(resolve => { release = resolve; }),
    () => { rendered = true; }, () => {});
  h.setDirty(true); h.session.changed();
  assert.equal(h.session.close(), false, "un référentiel en attente n'autorise pas l'abandon silencieux");
  release({ choices: ["valeur par défaut tardive"] });
  assert.equal(await loading, null);
  assert.equal(rendered, false);
  assert.equal(h.shell.state.dirty, true);
  assert.match(h.statuses.at(-1).message, /saisie est conservée/);
});

test("seul le dernier chargement de la même session peut installer ses résultats", async () => {
  const h = harness();
  const token = h.session.begin("create", null, () => {});
  let release, value;
  const first = h.session.load(token, () => new Promise(resolve => { release = resolve; }),
    result => { value = result; }, () => {});
  await h.session.load(token, async () => "actuel", result => { value = result; }, () => {});
  release("ancien"); await first;
  assert.equal(value, "actuel");
});

test("un POST au résultat inconnu ne peut pas être soumis une seconde fois ou abandonné", async () => {
  const h = harness(); h.session.begin("create", null, () => {}); h.setDirty(true);
  let sends = 0;
  const operation = { form: h.form, payload: () => ({ designation: "unique" }),
    send() { sends++; throw new TypeError("NetworkError"); }, validate() {}, onSaved() {} };
  await h.session.save(h.event, operation);
  assert.equal(h.shell.state.uncertain, true);
  await h.session.save(h.event, operation);
  h.setConfirm(true); assert.equal(h.session.close(), false);
  assert.equal(sends, 1);
  assert.equal(h.shell.state.snapshot, null);
});

test("le rejeu autorisé conserve intention, UUID et version ; une autre commande est refusée", async () => {
  const h = harness(); h.session.begin("edit", "o1", () => {});
  const initial = { properties: { id: "o1", version: 7 } };
  h.shell.beginEdit({ objectType: "ouvrages-particuliers", objectId: "o1", snapshot: initial });
  let sends = 0, body = { designation: "conservée" }; const attempts = [];
  const operation = { form: h.form, retryUncertain: true, payload: () => body,
    send(value, context) {
      sends++; attempts.push({ value, ...context });
      if (sends === 1) throw new TypeError("Réponse perdue");
      return { properties: { id: "o1", version: 8 } };
    }, validate: result => EntityFormSession.validateReceipt(result, { id: "o1", version: 7 }), onSaved() {} };
  await h.session.save(h.event, operation);
  assert.equal(h.shell.hasDraft(), true, "le résultat inconnu reste protégé des invalidations");
  body = { designation: "différente" }; await h.session.save(h.event, operation);
  assert.equal(sends, 1); assert.equal(h.shell.state.snapshot, initial);
  body = { designation: "conservée" }; await h.session.save(h.event, operation);
  assert.equal(sends, 2);
  assert.ok(attempts[0].operationId);
  assert.equal(attempts[0].operationId, attempts[1].operationId);
  assert.equal(attempts[1].snapshot.properties.version, 7);
  assert.deepEqual(attempts[0].value, attempts[1].value);
  assert.equal(h.shell.state.snapshot.properties.version, 8);
  assert.equal(h.shell.state.uncertain, false);
});

test("403 interdit une nouvelle opération sans perdre la saisie ni le snapshot", async () => {
  const h = harness(); h.session.begin("create", null, () => {}); h.setDirty(true);
  let sends = 0;
  const operation = { form: h.form, payload: () => ({ designation: "brouillon" }),
    send() { sends++; throw Object.assign(new Error("Interdit"), { status: 403 }); },
    validate() {}, onSaved() {} };
  await h.session.save(h.event, operation); await h.session.save(h.event, operation);
  assert.equal(sends, 1); assert.equal(h.shell.state.forbidden, true);
  assert.equal(h.shell.state.uncertain, false); assert.equal(h.session.close(), false);
});

test("la confirmation ne devient pas un échec d'écriture si son actualisation locale échoue", async () => {
  const h = harness(); h.session.begin("create", null, () => {});
  const result = { properties: { id: "o1", version: 1 } };
  await h.session.save(h.event, { form: h.form, payload: () => ({}), send: () => result,
    validate() {}, onSaved() { throw new Error("Actualisation impossible"); } });
  assert.equal(h.shell.state.mode, "edit"); assert.equal(h.shell.state.objectId, "o1");
  assert.equal(h.shell.state.snapshot, result); assert.equal(h.shell.state.uncertain, false);
  assert.match(h.statuses.at(-1).message, /Enregistré.*incomplète/);
});

test("après création confirmée le mode dépend du droit réel de mise à jour", async () => {
  const h = harness(); h.root.BusinessRuntime = { can: permission => permission !== "business.update" };
  h.session.begin("create", null, () => {});
  await h.session.save(h.event, { form: h.form, payload: () => ({}),
    send: () => ({ properties: { id: "o1", version: 1 } }), validate() {}, onSaved() {} });
  assert.equal(h.shell.state.mode, "read"); assert.equal(h.shell.state.objectId, "o1");
});

test("Base partage refus d'abandon, classification d'erreurs et validation nominative des reçus", () => {
  assert.equal(EntityFormSession.mayLeave({ uncertain: true, dirty: true, confirm: () => true }), false);
  assert.equal(EntityFormSession.mayLeave({ dirty: true, confirm: () => false }), false);
  assert.deepEqual(EntityFormSession.failureState({ status: 422 }), { conflict: false, forbidden: false, uncertain: false });
  assert.equal(EntityFormSession.failureState({ status: 503 }).uncertain, true);
  for (const properties of [{ id: "autre", version: 9 }, { id: "o1", version: 7 }, { id: "o1", version: 0 }]) {
    assert.throws(() => EntityFormSession.validateReceipt({ properties }, { id: "o1", version: 7 }), /incohérent/);
  }
});

test("la session garde les erreurs 409/422 et bloque une seconde soumission", async () => {
  const h = harness();
  h.session.begin("edit", "o1", () => {});
  h.shell.beginEdit({ objectType: "ouvrages-particuliers", objectId: "o1",
    snapshot: { properties: { id: "o1", version: 1 } } });
  let release;
  const delayed = new Promise(resolve => { release = resolve; });
  let sends = 0;
  const operationIds = [];
  const options = {
    form: h.form, payload: () => ({ designation: "Brouillon" }),
    async send(body, context) {
      sends++;
      operationIds.push(context.operationId);
      assert.deepEqual(body, { designation: "Brouillon" });
      assert.equal(context.snapshot.properties.version, 1);
      await delayed;
      throw Object.assign(new Error("Conflit"), { status: 409 });
    },
    validate() {}, onSaved() {},
  };
  const first = h.session.save(h.event, options);
  await h.session.save(h.event, options);
  assert.equal(sends, 1);
  assert.equal(h.shell.state.pending, true);
  assert.equal(h.session.close(), false);
  release();
  await first;
  assert.equal(h.shell.state.conflict, true);
  assert.equal(h.shell.state.snapshot.properties.version, 1);
  assert.match(h.statuses.at(-1).message, /Conflit/);
  await h.session.save(h.event, {
    ...options, send: async (_body, context) => {
      operationIds.push(context.operationId);
      throw Object.assign(new Error("Invalide"), { status: 422 });
    },
  });
  assert.equal(h.shell.state.conflict, false);
  assert.equal(h.shell.state.snapshot.properties.version, 1);
  assert.match(h.statuses.at(-1).message, /Invalide/);
  assert.equal(operationIds[0], operationIds[1], "une commande identique conserve son identité d'opération");
});

test("une vue en lecture ou une permission retirée ne lance aucune mutation", async () => {
  const h = harness();
  h.session.begin("edit", "o1", () => {});
  let sends = 0;
  const options = { form: h.form, payload: () => ({}),
    send: () => { sends++; }, validate() {}, onSaved() {} };
  h.shell.beginRead({ objectType: "ouvrages-particuliers" });
  await h.session.save(h.event, options);
  h.shell.beginEdit({ objectType: "ouvrages-particuliers", objectId: "o1" });
  h.root.BusinessRuntime = { can: () => false, isActive: () => true };
  await h.session.save(h.event, options);
  assert.equal(sends, 0);
});

test("la session invalide les écritures tardives et retire ses ressources", async () => {
  const h = harness();
  h.session.begin("create", null, () => {});
  h.setDirty(true);
  const unload = { prevented: false, preventDefault() { this.prevented = true; } };
  h.listeners.get("beforeunload")(unload);
  assert.equal(unload.prevented, true);
  let release;
  const delayed = new Promise(resolve => { release = resolve; });
  const submit = h.session.save(h.event, { form: h.form, payload: () => ({}),
    send: () => delayed, validate() { throw Error("Ne doit pas être appelé"); },
    onSaved() { throw Error("Ne doit pas être appelé"); },
  });
  h.session.dispose();
  release({ properties: { id: "o1", version: 1 } });
  assert.equal(await submit, null);
  assert.equal(h.listeners.has("beforeunload"), false);
  assert.equal(h.disposed(), 1);
  assert.equal(h.element.removed, true);
  assert.equal(h.session.begin("create", null, () => {}), null);
});

test("une réponse d'ancienne session ne bloque ni n'efface une nouvelle sauvegarde", async () => {
  const h = harness();
  h.session.begin("create", null, () => {});
  let release;
  const delayed = new Promise(resolve => { release = resolve; });
  const options = { form: h.form, payload: () => ({}),
    validate() {}, onSaved() {} };
  const oldSave = h.session.save(h.event, { ...options, send: () => delayed });
  h.session.panelSession.discard();
  h.session.begin("create", null, () => {});
  const saved = { geometry: { type: "Point", coordinates: [2, 48] },
    properties: { id: "new", version: 1 } };
  await h.session.save(h.event, { ...options, send: async () => saved });
  release({ properties: { id: "old", version: 1 } });
  assert.equal(await oldSave, null);
  assert.equal(h.shell.state.objectId, "new");
  assert.equal(h.shell.state.snapshot.properties.id, "new");
  assert.equal(h.shell.state.pending, false);
});

test("la confirmation serveur ne permet pas un second POST pendant l'actualisation", async () => {
  const h = harness();
  h.session.begin("create", null, () => {});
  let release;
  const refresh = new Promise(resolve => { release = resolve; });
  let sends = 0;
  const result = { properties: { id: "o1", version: 1 },
    geometry: { type: "Point", coordinates: [2, 48] } };
  const options = { form: h.form, payload: () => ({}),
    send: async () => { sends++; return result; }, validate() {},
    onSaved: () => {
      assert.equal(h.shell.hasDraft(), false, "le reçu confirmé n'est pas un brouillon pour l'invalidateur");
      assert.equal(h.session.canEdit(), false, "les actions attendent la clôture de l'actualisation");
      assert.equal(h.session.close(), false);
      return refresh;
    } };
  const first = h.session.save(h.event, options);
  await Promise.resolve();
  assert.equal(h.shell.state.mode, "edit");
  await h.session.save(h.event, options);
  assert.equal(sends, 1);
  release();
  await first;
  assert.equal(h.shell.state.pending, false);
});

test("un chargement actif et un changement de base interdisent toute nouvelle écriture", async () => {
  const h = harness();
  const token = h.session.begin("create", null, () => {});
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const read = h.session.load(token, () => gate, () => {}, () => {});
  let sends = 0;
  const operation = { form: h.form, payload: () => ({}),
    send() { sends++; return { properties: { id: 'x', version: 1 } }; },
    validate() {}, onSaved() {} };
  await h.session.save(h.event, operation);
  assert.equal(sends, 0);
  release(); await read;
  h.root.BusinessRuntime = { can: () => true, isActive: () => true };
  assert.equal(h.session.isCurrent(token), false);
  await h.session.save(h.event, operation);
  assert.equal(sends, 0);
});
