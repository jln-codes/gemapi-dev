const test = require("node:test");
const assert = require("node:assert/strict");
const Related = require("../frontend/js/disorder-related-objects.js");

class ClassList {
  constructor() { this.values = new Set(); }
  add(...values) { values.forEach((value) => this.values.add(value)); }
}

class Element {
  constructor(documentTarget) {
    this.ownerDocument = documentTarget;
    this.children = [];
    this.listeners = {};
    this.dataset = {};
    this.classList = new ClassList();
    this.hidden = false;
    this.disabled = false;
    this.isConnected = true;
    this.textContent = "";
  }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  setAttribute(name, value) { this[name] = String(value); }
}

const documentTarget = { createElement: () => new Element(documentTarget) };
const runtime = { isActive: () => true };
const id = (suffix) => `00000000-0000-4000-8000-000000000${suffix}`;
const item = (resource, category, suffix, label) => ({
  resource, category, id: id(suffix), label, valid: true,
});

test("pagination preserves previous selections and groups categories", async () => {
  const container = new Element(documentTarget);
  const calls = [];
  const navigation = [];
  const controller = Related.createController({
    container,
    documentTarget,
    runtime: () => runtime,
    isCurrent: () => true,
    fetchJson: async (url) => {
      calls.push(url);
      return calls.length === 1
        ? { items: [item("troncons", "Tronçons", "101", "T1")], has_more: true, next_cursor: "next" }
        : { items: [item("mobilier", "Mobilier", "102", "M1")], has_more: false, next_cursor: null };
    },
    onNavigate: async (selected) => navigation.push(selected.id),
  });
  await controller.open(id("999"));
  await controller.loadMore();
  const groups = container.children[2];
  assert.equal(groups.children.length, 2);
  assert.match(calls[1], /cursor=next/);
  const firstButton = groups.children[0].children[1].children[0].children[0];
  await firstButton.listeners.click();
  assert.deepEqual(navigation, [id("101")]);
  assert.equal(container.children[3].hidden, true);
});

test("a late response cannot overwrite another disorder", async () => {
  const container = new Element(documentTarget);
  const resolvers = [];
  const controller = Related.createController({
    container,
    documentTarget,
    runtime: () => runtime,
    isCurrent: () => true,
    fetchJson: () => new Promise((resolve) => resolvers.push(resolve)),
    onNavigate: async () => {},
  });
  const first = controller.open(id("901"));
  const second = controller.open(id("902"));
  resolvers[0]({ items: [item("troncons", "Tronçons", "101", "Ancien")], has_more: false });
  resolvers[1]({ items: [item("mobilier", "Mobilier", "102", "Actuel")], has_more: false });
  await Promise.all([first, second]);
  const groups = container.children[2];
  assert.equal(groups.children.length, 1);
  assert.equal(groups.children[0].children[0].textContent, "Mobilier");
});

test("represented relationships are omitted by resource and identity without losing distinct objects", async () => {
  const container = new Element(documentTarget);
  let onlyDuplicate = false;
  const controller = Related.createController({
    container, documentTarget, runtime: () => runtime,
    isRepresented: row => row.resource === "troncons" && row.id === id("101"),
    fetchJson: async () => ({ items: onlyDuplicate
      ? [item("troncons", "Tronçons", "101", "Déjà sélectionné")]
      : [item("troncons", "Tronçons", "101", "Déjà sélectionné"),
        item("mobilier", "Mobilier", "101", "Même UUID, autre famille"),
        item("troncons", "Tronçons", "102", "Autre relation")], has_more: false }),
    onNavigate: async () => {},
  });
  await controller.open(id("999"));
  const groups = container.children[2];
  assert.equal(groups.children.length, 2);
  assert.equal(groups.children[0].children[1].children.length, 1);
  assert.equal(groups.children[1].children[1].children.length, 1);
  assert.equal(container.hidden, false);
  onlyDuplicate = true;
  await controller.invalidate({ resource: "troncons" });
  assert.equal(groups.children.length, 0);
  assert.equal(container.hidden, true);
});

test("only mutations from inverse resources trigger a reload", async () => {
  const container = new Element(documentTarget);
  let calls = 0;
  const controller = Related.createController({
    container,
    documentTarget,
    runtime: () => runtime,
    isCurrent: () => true,
    fetchJson: async () => ({ items: [], has_more: false, next_cursor: null, call: ++calls }),
    onNavigate: async () => {},
  });
  await controller.open(id("999"));
  assert.equal(controller.invalidate({ resource: "photos" }), false);
  await controller.invalidate({ resource: "ouvrages-particuliers" });
  assert.equal(calls, 2);
});
