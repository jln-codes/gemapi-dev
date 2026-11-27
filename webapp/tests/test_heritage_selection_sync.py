from pathlib import Path
import shutil
import subprocess
import unittest


PROJECT_DIRECTORY = Path(__file__).resolve().parents[2]
FRONTEND_DIRECTORY = PROJECT_DIRECTORY / "webapp" / "frontend"


class HeritageSelectionSyncTest(unittest.TestCase):
    def test_preference_and_bidirectional_coordination(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")

        program = r'''
const assert = require("node:assert/strict");
const Sync = require("./webapp/frontend/js/heritage-selection-sync.js");

class Storage {
  constructor() { this.values = new Map(); }
  getItem(key) { return this.values.has(key) ? this.values.get(key) : null; }
  setItem(key, value) { this.values.set(key, value); }
}
class Checkbox {
  constructor() { this.checked = false; this.listeners = {}; }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  change(checked) { this.checked = checked; this.listeners.change(); }
}
function busPair() {
  const listeners = [new Map(), new Map()];
  const publications = [[], []];
  return [0, 1].map((index) => ({
    publications: publications[index],
    publish(type, payload) {
      publications[index].push({ type, payload });
      listeners[1 - index].get(type)?.(payload);
    },
    subscribe(type, callback) { listeners[index].set(type, callback); },
  }));
}
function selection(id) {
  return { systemeIds: [`s-${id}`], digueIds: [`d-${id}`], tronconIds: [`t-${id}`] };
}
function heritage(initial, onRemote = () => {}) {
  let current = initial;
  return {
    getSelection: () => current,
    applySelection(next, context) { current = next; onRemote(next, context); },
  };
}

const storage = new Storage();
assert.equal(Sync.readPreference(storage), true);
Sync.writePreference(false, storage);
assert.equal(Sync.readPreference(storage), false);
Sync.writePreference(true, storage);
assert.equal(Sync.readPreference(storage), true);

const [mapBus, dataBus] = busPair();
const mapCheckbox = new Checkbox();
const dataCheckbox = new Checkbox();
let mapSync;
let dataSync;
const mapHeritage = heritage(selection("map"), (next, context) => {
  mapSync.selectionChanged(next, context);
});
const dataHeritage = heritage(selection("data"), (next, context) => {
  dataSync.selectionChanged(next, context);
});
mapSync = Sync.createController({
  bus: mapBus, checkbox: mapCheckbox, heritageController: mapHeritage,
  storage, windowTarget: null,
});
dataSync = Sync.createController({
  bus: dataBus, checkbox: dataCheckbox, heritageController: dataHeritage,
  storage, windowTarget: null,
});

mapSync.selectionChanged(selection("map"), { origin: "user" });
assert.equal(mapBus.publications.length, 1);
assert.deepEqual(dataHeritage.getSelection(), selection("map"));
assert.equal(dataBus.publications.length, 0); // une application distante ne reboucle pas

dataSync.selectionChanged(selection("data-local"), { origin: "user" });
assert.equal(dataBus.publications.length, 1);
assert.deepEqual(mapHeritage.getSelection(), selection("data-local"));
assert.equal(mapBus.publications.length, 1);

mapCheckbox.change(false);
assert.equal(storage.getItem(Sync.PREFERENCE_KEY), "false");
mapSync.selectionChanged(selection("disabled"), { origin: "user" });
assert.equal(mapBus.publications.length, 1);

// Deux états locaux divergents sont conservés pendant la désactivation.
mapHeritage.applySelection(selection("map-diverged"), { origin: "remote" });
dataHeritage.applySelection(selection("data-diverged"), { origin: "remote" });
mapCheckbox.change(true);
assert.equal(storage.getItem(Sync.PREFERENCE_KEY), "true");
assert.deepEqual(dataHeritage.getSelection(), selection("map-diverged"));
assert.equal(mapBus.publications.length, 2);
assert.equal(dataBus.publications.length, 1);

// Même règle lorsque l’activation part de Base.
dataCheckbox.change(false);
mapHeritage.applySelection(selection("map-again"), { origin: "remote" });
dataHeritage.applySelection(selection("data-wins"), { origin: "remote" });
dataCheckbox.change(true);
assert.deepEqual(mapHeritage.getSelection(), selection("data-wins"));
assert.equal(dataBus.publications.length, 2);
assert.equal(mapBus.publications.length, 2);

const memoryOnlyCheckbox = new Checkbox();
const memoryOnlyBus = { publications: [], publish(type, payload) {
  this.publications.push({ type, payload });
}, subscribe() {} };
const memoryOnlySync = Sync.createController({
  bus: memoryOnlyBus,
  checkbox: memoryOnlyCheckbox,
  heritageController: heritage(selection("memory")),
  storage: null,
  windowTarget: null,
});
memoryOnlyCheckbox.change(false);
memoryOnlySync.selectionChanged(selection("memory"), { origin: "user" });
assert.equal(memoryOnlyBus.publications.length, 0);
'''
        subprocess.run([node, "-e", program], cwd=PROJECT_DIRECTORY, check=True)

    def test_selection_normalization(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")

        program = r'''
const assert = require("node:assert/strict");
const HeritagePanel = require("./webapp/frontend/js/heritage-panel.js");
const data = { systemes: [
  { id: "s1", digues: [{ id: "d1", troncons: [{ id: "t1" }, { id: "t2" }] }] },
  { id: "s2", digues: [{ id: "d2", troncons: [{ id: "t3" }] }] },
] };
const hidden = new Set(["t2"]);
assert.deepEqual(HeritagePanel.selectionFromVisibility(data, hidden), {
  systemeIds: ["s2"], digueIds: ["d2"], tronconIds: ["t1", "t3"],
});
assert.deepEqual(HeritagePanel.normalizeSelection({
  systemeIds: ["s1", "s1"], digueIds: [], tronconIds: ["t1"],
}), { systemeIds: ["s1"], digueIds: [], tronconIds: ["t1"] });
assert.equal(HeritagePanel.normalizeSelection({ tronconIds: ["t1"] }), null);
assert.equal(HeritagePanel.normalizeSelection({
  systemeIds: [], digueIds: [], tronconIds: [null],
}), null);
'''
        subprocess.run([node, "-e", program], cwd=PROJECT_DIRECTORY, check=True)

    def test_both_pages_load_settings_and_sync_before_their_entry_point(self):
        header = (FRONTEND_DIRECTORY / "js" / "app-header.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("Synchroniser les sélections patrimoine", header)
        self.assertEqual(header.count('id="sync-heritage-selection"'), 1)
        for name, entry_point in (("map.html", "map.js"), ("data.html", "data-view.js")):
            page = (FRONTEND_DIRECTORY / name).read_text(encoding="utf-8")
            with self.subTest(page=name):
                self.assertIn('src="/static/js/app-header.js"', page)
                self.assertLess(
                    page.index('src="/static/js/heritage-panel.js"'),
                    page.index('src="/static/js/heritage-selection-sync.js"'),
                )
                self.assertLess(
                    page.index('src="/static/js/heritage-selection-sync.js"'),
                    page.index(f'src="/static/js/{entry_point}"'),
                )


if __name__ == "__main__":
    unittest.main()
