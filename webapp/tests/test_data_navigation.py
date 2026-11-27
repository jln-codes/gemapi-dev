"""STAT-1, niveau A : DOM isolé par scénario, sans réseau/SQL/stockage mutable.

Les vrais data-view, TopNavigation, EntityNavigation et BaseDetailController vérifient
l'exclusion des vues, les retours et l'URL ; les chargements périphériques sont
doublés. Le veto de fermeture est injecté à la frontière du contrôleur, sans
prétendre qualifier l'édition ou le hors ligne. Groupe webapp, coût < 20 secondes.
"""

import json
import subprocess
import unittest


HARNESS = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const frontend = "./webapp/frontend/";
class Element {
  constructor(hidden = false) {
    this.hidden = hidden; this.children = []; this.listeners = {}; this.dataset = {};
    this.attributes = {}; this.textContent = ""; this.value = "";
    this.classes = new Set(); this.clientWidth = 1200;
    this.classList = {
      toggle: (name, active) => active ? this.classes.add(name) : this.classes.delete(name),
      add: name => this.classes.add(name), remove: name => this.classes.delete(name),
    };
  }
  set id(value) {this._id=value;elements.set("#"+value,this);}
  get id(){return this._id;}
  remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(child=>child!==this);}
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  dispatchEvent(event) { for (const callback of this.listeners[event.type] || []) callback(event); }
  click() { this.dispatchEvent({ type: "click", target: this }); }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name]; }
  querySelector() { return null; }
  append(...items) {for(const item of items){item.parentElement=this;this.children.push(item);}}
  replaceChildren(...items) { this.children = items; }
  contains(target) { return this === target || this.children.some(child => child.contains(target)); }
  focus() { document.activeElement = this; }
}
const header = new Element();
require(frontend + "js/app-header.js").render(header, "data");
const markup = fs.readFileSync(frontend + "data.html", "utf8") + header.innerHTML;
const elements = new Map();
for (const tag of markup.matchAll(/<[a-z][\w-]*\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
  const element = new Element(/\bhidden\b/.test(tag[0]));
  for (const attr of tag[0].matchAll(/([\w-]+)="([^"]*)"/g)) element.setAttribute(attr[1], attr[2]);
  elements.set("#" + tag[1], element);
}
const main = new Element(), viewport = new Element();
elements.set("main", main); elements.set(".app-header", header);
header.querySelector = () => viewport;
const document = new Element();
for (const element of elements.values()) element.ownerDocument = document;
document.querySelector = selector => elements.get(selector) || null;
document.createElement = tag => {const el=new Element();el.tagName=tag.toUpperCase();el.ownerDocument=document;return el;};
document.body = new Element();
Object.assign(globalThis, { document, window: globalThis, innerHeight: 800 });
const windowEvents = new Element();
globalThis.addEventListener = (...args) => windowEvents.addEventListener(...args);
globalThis.CustomEvent = class { constructor(type, options) { this.type = type; this.detail = options.detail; } };
globalThis.ResizeObserver = class { observe() {} };
const initialUrl = new URL("http://example.test/base?base=demo" + __SEARCH__);
globalThis.location = initialUrl;
const urlWrites = [];
globalThis.history = { state: { preserved: true }, replaceState(state, _, url) {
  assert.deepEqual(state, { preserved: true }); urlWrites.push(url);
  globalThis.location = new URL(url, location);
} };
const requests = [];
globalThis.BusinessRuntime = {
  can: () => true, urlMode: () => "object", isActive: () => true,
  fetch: async url => { requests.push(url); return {ok: true, json: async () => ({crs:{label:"CRS test"}})}; },
};
globalThis.fetch = async url => { requests.push(url); return {ok:true,json:async()=>({show_uuid:false})}; };
globalThis.EntityNavigation = require(frontend + "js/entity-navigation.js");
globalThis.TopNavigation = require(frontend + "js/top-navigation.js");
globalThis.AppHeader = {positionMenu() {}};
const exportedDatasets = [];
globalThis.DataGeoPackage = {
  createController: () => ({setDataset: value => exportedDatasets.push(value)}),
  createCsvController: () => ({setDataset: value => exportedDatasets.push(value)}),
};
let builderOptions, heritageOptions, detailController, navigation;
let closeAllowed = true, closeAttempts = 0;
globalThis.DataViewBuilder = {createController(options) {
  builderOptions = options;
  return {setOpen: open => {elements.get("#data-view-builder-workspace").hidden = !open;},
    refresh: async () => {}, setHiddenTronconIds() {}};
} };
globalThis.HeritagePanel = {createController(options) {
  heritageOptions = options;
  const panel = elements.get("#heritage-panel");
  return {panel, toggleButton: elements.get("#toggle-heritage"), closeButton: elements.get("#close-heritage"),
    setOpen: open => {panel.hidden = !open;}, load: async () => {}};
} };
globalThis.HeritageSelectionSync = {createController: () => ({})};
globalThis.InterPageBus = {};
globalThis.ResourceMutationController = {};
globalThis.DataResourceInvalidators = {register() {}};
globalThis.AiPanel = {createController() {
  const panel = elements.get("#ai-panel");
  return {panel, toggleButton: elements.get("#toggle-ai"), closeButton: elements.get("#close-ai"),
    setOpen: open => {panel.hidden = !open;}};
} };
globalThis.SirsImport = {createController() {}};
const realNavigation = TopNavigation.createController;
TopNavigation.createController = options => (navigation = realNavigation(options));
const realDetail = require(frontend + "js/base-detail-controller.js");
globalThis.EntityFormView=require(frontend+"js/entity-form-view.js");
let pendingDetail;
globalThis.BaseDetailController = {createController(options) {
  // This suite exercises central navigation, not an editor or its security.
  detailController = realDetail.createController({...options, createEditor: () => ({
    close: () => true, hasDraft: () => false, getState: () => ({}), isBlocked: () => false,
  }), fetchJson: async url => {
    requests.push(url);
    if (pendingDetail) await pendingDetail;
    if(url.endsWith('/options'))return {references:{},troncons:[],digues:[],amenagements:[]};
    // Canonical Tronçon reads are GeoJSON Features, including an absent shape.
    const id=url.split("/").at(-1);
    return {type:'Feature',id,geometry:null,properties:{id,version:1,designation:'Objet test',
      type_physique_id:'INDETERMINE',type_physique_libelle:'Indéterminé',valid:false}};
  }});
  const close = detailController.close;
  detailController.close = () => {closeAttempts++; return closeAllowed && close();};
  return detailController;
} };
vm.runInThisContext(fs.readFileSync(frontend + "js/data-view.js", "utf8"));
const click = id => elements.get("#" + id).click();
const centralPanels = {
  statistics: "data-statistics-view", views: "data-view-builder-workspace",
  detail: "data-entity-detail", sql: "queries-view",
};
function expectMode(mode) {
  assert.equal(main.dataset.centralView, mode);
  for (const [candidate, id] of Object.entries(centralPanels)) {
    assert.equal(elements.get("#" + id).hidden, mode !== candidate, id);
  }
  for (const [candidate, id] of [["statistics","show-statistics"],["views","toggle-views"],["sql","toggle-queries"]]) {
    assert.equal(elements.get("#" + id).getAttribute("aria-expanded"), String(mode === candidate), id);
  }
  assert.equal(navigation.getState().queries, mode === "sql");
  assert.equal(main.classes.has("queries-open"), mode === "sql");
  assert.equal(location.searchParams.get("base"), "demo");
}
const id = "00000000-0000-4000-8000-000000000701";
function openEntity(entityClass = "troncons", entityId = id) {
  main.dispatchEvent(new CustomEvent("digues-app:open-entity", {detail:{entityClass, entityId}}));
}
const settle = () => new Promise(resolve => setImmediate(resolve));
(async () => { __SCENARIO__ })().catch(error => {console.error(error);process.exitCode = 1;});
'''


class DataNavigationTest(unittest.TestCase):
    def run_scenario(self, scenario, search=""):
        program = HARNESS.replace("__SEARCH__", json.dumps(search)).replace("__SCENARIO__", scenario)
        subprocess.run(["node", "-e", program], check=True, capture_output=True, text=True, timeout=10)

    def test_statistics_default_views_and_explicit_return_preserve_base(self):
        self.run_scenario(r'''
expectMode("statistics");
await settle();
assert.equal(elements.has("#data-disorders-view"), false);
assert.deepEqual(requests.sort(), ["/api/base-summary", "/api/config"]);
click("toggle-views"); expectMode("views");
click("show-statistics"); expectMode("statistics");
click("show-statistics"); expectMode("statistics");
click("toggle-views"); click("toggle-views"); expectMode("statistics");
assert.deepEqual(urlWrites, []);
''')

    def test_deep_link_opens_detail_then_returns_to_statistics(self):
        self.run_scenario(r'''
expectMode("detail"); // onShow is immediate, before the asynchronous detail read.
assert.equal(detailController.getSource(), "statistics");
await settle();
assert.equal(detailController.getState().id, id);
assert.equal(location.searchParams.get("entity"), "troncons");
assert.equal(location.searchParams.get("id"), id);
click("data-entity-detail-back"); expectMode("statistics");
assert.equal(location.search, "?base=demo");
''', "&entity=troncons&id=00000000-0000-4000-8000-000000000701")

    def test_malformed_links_leave_statistics_active(self):
        for search in ("&entity=troncons", "&entity=unknown&id=no", "&entity=troncons&id=no"):
            with self.subTest(search=search):
                self.run_scenario('expectMode("statistics"); assert.equal(detailController.getState().open, false);', search)

    def test_detail_returns_to_each_origin_including_nested_navigation(self):
        self.run_scenario(r'''
for (const [origin, button] of [["statistics","show-statistics"],["views","toggle-views"],
  ["sql","toggle-queries"]]) {
  click(button); expectMode(origin);
  openEntity(); expectMode("detail");
  await settle();
  assert.equal(detailController.getSource(), origin);
  openEntity("troncons", "00000000-0000-4000-8000-000000000702");
  await settle(); expectMode("detail");
  assert.equal(detailController.getSource(), origin);
  click("data-entity-detail-back"); expectMode(origin);
  click("show-statistics"); expectMode("statistics");
}
// Builder cells retain their return destination.
click("toggle-views"); builderOptions.onOpenEntity("troncons", id);
await settle(); click("data-entity-detail-back"); expectMode("views");
''')

    def test_sql_and_statistics_are_exclusive_and_panels_stay_compatible(self):
        self.run_scenario(r'''
click("toggle-views"); click("toggle-queries"); expectMode("sql");
click("toggle-ai"); expectMode("sql");
assert.equal(navigation.getState().ai, true);
click("show-statistics"); expectMode("statistics");
assert.equal(navigation.getState().ai, true);
click("toggle-queries"); expectMode("sql");
click("close-queries"); expectMode("statistics");
click("toggle-queries"); click("toggle-queries"); expectMode("statistics");
click("toggle-queries"); click("toggle-heritage"); expectMode("statistics");
assert.equal(navigation.getState().heritage, true);
click("toggle-views"); expectMode("views");
click("toggle-queries"); click("toggle-views"); expectMode("views");
assert.deepEqual(urlWrites, []);
''')

    def test_explicit_destinations_respect_detail_close_veto(self):
        self.run_scenario(r'''
openEntity(); await settle(); expectMode("detail");
closeAllowed = false;
for (const button of ["show-statistics", "toggle-views", "toggle-queries"]) {
  click(button); expectMode("detail");
  assert.equal(location.searchParams.get("id"), id);
}
assert.equal(closeAttempts, 3);
closeAllowed = true;
click("toggle-queries"); expectMode("sql");
openEntity(); await settle(); click("show-statistics"); expectMode("statistics");
openEntity(); await settle(); click("toggle-views"); expectMode("views");
''')

    def test_late_detail_response_cannot_reopen_detail_or_restore_its_url(self):
        self.run_scenario(r'''
let release;
pendingDetail = new Promise(resolve => {release = resolve;});
openEntity(); expectMode("detail");
click("show-statistics"); expectMode("statistics");
release(); await settle(); expectMode("statistics");
assert.equal(location.search, "?base=demo");
assert.equal(detailController.getState().open, false);
''')


if __name__ == "__main__":
    unittest.main()
