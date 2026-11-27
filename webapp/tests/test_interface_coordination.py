from pathlib import Path
import shutil
import subprocess
import unittest


PROJECT_DIRECTORY = Path(__file__).resolve().parents[2]
FRONTEND_DIRECTORY = PROJECT_DIRECTORY / "webapp" / "frontend"


class InterfaceCoordinationTest(unittest.TestCase):
    def test_presence_activity_navigation_lifecycle_and_duplicates(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")

        program = r'''
const assert = require("node:assert/strict");
const Coordination = require("./webapp/frontend/js/interface-coordination.js");

class EventTarget {
  constructor() { this.listeners = {}; }
  addEventListener(type, callback) { (this.listeners[type] ||= new Set()).add(callback); }
  removeEventListener(type, callback) { this.listeners[type]?.delete(callback); }
  dispatch(type, event = {}) {
    for (const callback of this.listeners[type] || []) callback(event);
  }
}
class Control extends EventTarget {
  click() {
    const event = { prevented: false, preventDefault() { this.prevented = true; } };
    this.dispatch("click", event);
    return event;
  }
}
class DocumentTarget extends EventTarget {
  constructor({ visible = false, focused = false } = {}) {
    super();
    this.visibilityState = visible ? "visible" : "hidden";
    this.focused = focused;
  }
  hasFocus() { return this.focused; }
}
class WindowTarget extends EventTarget {
  constructor() { super(); this.focusCalls = 0; this.openCalls = []; }
  focus() { this.focusCalls += 1; }
  open(...argumentsList) { this.openCalls.push(argumentsList); return {}; }
}
class Storage {
  constructor(initial = null) { this.values = new Map(initial ? [["digues-app:interface-open-new-tab-enabled", initial]] : []); }
  getItem(key) { return this.values.has(key) ? this.values.get(key) : null; }
  setItem(key, value) { this.values.set(key, String(value)); }
}
class BusNetwork {
  constructor() { this.buses = []; }
  create(source) {
    const listeners = new Map();
    const bus = {
      source, listeners, publications: [],
      publish: (type, payload) => {
        bus.publications.push({ type, payload });
        for (const peer of this.buses) {
          if (peer === bus) continue;
          for (const callback of peer.listeners.get(type) || []) {
            callback(payload, { type, source, payload });
          }
        }
      },
      subscribe(type, callback) {
        const callbacks = listeners.get(type) || new Set();
        callbacks.add(callback);
        listeners.set(type, callbacks);
        return () => callbacks.delete(callback);
      },
    };
    this.buses.push(bus);
    return bus;
  }
  inject(source, type, payload) {
    for (const bus of this.buses) {
      for (const callback of bus.listeners.get(type) || []) {
        callback(payload, { type, source, payload });
      }
    }
  }
}

const network = new BusNetwork();
let time = 1_000;
let nextTimerId = 0;
const timers = new Map();
const common = {
  now: () => time,
  setIntervalFn(callback) {
    const identifier = ++nextTimerId;
    timers.set(identifier, callback);
    return identifier;
  },
  clearIntervalFn(identifier) { timers.delete(identifier); },
  presenceTtlMs: 1_000,
};
function create(interfaceType, instanceId, documentOptions = {}) {
  const bus = network.create(interfaceType);
  const targetLink = new Control();
  targetLink.href = interfaceType === "map" ? "/base" : "/carte";
  const openInNewTabCheckbox = new Control();
  const documentTarget = new DocumentTarget(documentOptions);
  const windowTarget = new WindowTarget();
  const storage = new Storage("false");
  const controller = Coordination.createController({
    ...common,
    interfaceType,
    instanceId,
    bus,
    targetLink,
    openInNewTabCheckbox,
    storage,
    documentTarget,
    openInNewTabCheckbox,
    storage,
    windowTarget,
  });
  return {
    bus,
    controller,
    documentTarget,
    targetLink,
    timerId: nextTimerId,
    windowTarget,
  };
}

const map = create("map", "map-instance", { visible: true, focused: true });
assert.equal(map.bus.publications.some(({ type }) => type === Coordination.ACTIVITY_EVENT), true);
assert.equal(map.targetLink.click().prevented, false); // navigation native si Base absente

time = 1_010;
const dataA = create("data", "data-a");
time = 1_020;
const dataB = create("data", "data-b");
assert.equal(map.controller.getOtherInterfacePresence().lastActiveAt, null);

// Sans activité connue, le heartbeat le plus récent détermine la cible.
time = 1_030;
timers.get(dataB.timerId)();
assert.equal(map.controller.getOtherInterfacePresence().instanceId, "data-b");
time = 1_040;
timers.get(dataA.timerId)();
assert.equal(map.controller.getOtherInterfacePresence().instanceId, "data-a");

// Focus et retour visible annoncent une activité réelle.
time = 1_100;
dataA.documentTarget.visibilityState = "visible";
dataA.documentTarget.focused = true;
dataA.windowTarget.dispatch("focus");
assert.equal(map.controller.getOtherInterfacePresence().instanceId, "data-a");
assert.equal(map.controller.getOtherInterfacePresence().lastActiveAt, 1_100);

time = 1_200;
dataB.documentTarget.visibilityState = "visible";
const activityCount = dataB.bus.publications.filter(
  ({ type }) => type === Coordination.ACTIVITY_EVENT,
).length;
dataB.windowTarget.dispatch("focus");
assert.equal(dataB.bus.publications.filter(
  ({ type }) => type === Coordination.ACTIVITY_EVENT,
).length, activityCount);
dataB.documentTarget.visibilityState = "hidden";
dataB.documentTarget.focused = true;
dataB.documentTarget.dispatch("visibilitychange");
assert.equal(dataB.bus.publications.filter(
  ({ type }) => type === Coordination.ACTIVITY_EVENT,
).length, activityCount);
dataB.documentTarget.visibilityState = "visible";
dataB.documentTarget.focused = true;
dataB.documentTarget.dispatch("visibilitychange");
assert.equal(map.controller.getOtherInterfacePresence().instanceId, "data-b");

// Un heartbeat plus récent ne doit ni écraser lastActiveAt ni voler la priorité.
time = 1_300;
timers.get(dataA.timerId)();
const selectedAfterHeartbeat = map.controller.getOtherInterfacePresence();
assert.equal(selectedAfterHeartbeat.instanceId, "data-b");
assert.equal(selectedAfterHeartbeat.lastActiveAt, 1_200);

const targetedClick = map.targetLink.click();
assert.equal(targetedClick.prevented, false);
assert.equal(dataA.windowTarget.focusCalls, 0);
assert.equal(dataB.windowTarget.focusCalls, 0);
const reverseClick = dataA.targetLink.click();
assert.equal(reverseClick.prevented, false);
assert.equal(map.windowTarget.focusCalls, 0);

// Les activités invalides ou forgées sont ignorées.
network.inject("data", Coordination.ACTIVITY_EVENT, {
  interface: "data", instanceId: "data-a", timestamp: "plus tard",
});
network.inject("map", Coordination.ACTIVITY_EVENT, {
  interface: "data", instanceId: "forged", timestamp: 9_999,
});
network.inject("data", Coordination.ACTIVITY_EVENT, {
  interface: "data", instanceId: "unknown", timestamp: 9_999,
});
assert.equal(map.controller.getOtherInterfacePresence().instanceId, "data-b");

// La cible suivante est retenue lorsqu’une instance disparaît.
dataB.windowTarget.dispatch("pagehide");
assert.equal(map.controller.getOtherInterfacePresence().instanceId, "data-a");
dataA.windowTarget.dispatch("pagehide");
assert.equal(map.controller.getOtherInterfacePresence(), null);
assert.equal(map.targetLink.click().prevented, false);

// Un rechargement remplace l’identité, pageshow réannonce la même instance.
time = 1_310;
const reloaded = create("data", "data-reloaded");
assert.equal(map.controller.getOtherInterfacePresence().instanceId, "data-reloaded");
reloaded.windowTarget.dispatch("pagehide");
assert.equal(map.controller.getOtherInterfacePresence(), null);
reloaded.windowTarget.dispatch("pageshow");
assert.equal(map.controller.getOtherInterfacePresence().instanceId, "data-reloaded");

// Sans heartbeat ni événement de sortie reçu, une présence finit par expirer.
time += 1_001;
assert.equal(map.controller.getOtherInterfacePresence(), null);

map.controller.destroy();
dataA.controller.destroy();
dataB.controller.destroy();
reloaded.controller.destroy();
'''
        subprocess.run([node, "-e", program], cwd=PROJECT_DIRECTORY, check=True)

    def test_open_in_new_tab_preference_is_independent_from_presence(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")

        program = r'''
const assert = require("node:assert/strict");
const Coordination = require("./webapp/frontend/js/interface-coordination.js");
class EventTarget {
  constructor() { this.listeners = {}; }
  addEventListener(type, callback) { (this.listeners[type] ||= new Set()).add(callback); }
  removeEventListener(type, callback) { this.listeners[type]?.delete(callback); }
  dispatch(type, event = {}) { for (const callback of this.listeners[type] || []) callback(event); }
}
class Control extends EventTarget {
  constructor(href = "") { super(); this.href = href; this.checked = false; }
  click(overrides = {}) {
    const event = { ...overrides, prevented:false, preventDefault() { this.prevented = true; } };
    this.dispatch("click", event); return event;
  }
}
class Storage {
  constructor() { this.values = new Map(); }
  getItem(key) { return this.values.get(key) ?? null; }
  setItem(key, value) { this.values.set(key, String(value)); }
}
class WindowTarget extends EventTarget {
  constructor() { super(); this.name = ""; this.openCalls = []; this.focusCalls = 0; this.openedFocusCalls = 0; }
  open(...argumentsList) {
    this.openCalls.push(argumentsList);
    return { focus: () => { this.openedFocusCalls += 1; } };
  }
  focus() { this.focusCalls += 1; }
}
class DocumentTarget extends EventTarget {
  constructor() { super(); this.visibilityState = "hidden"; }
  hasFocus() { return false; }
}
const buses = [];
function bus(source) {
  const listeners = new Map();
  const result = {
    publish(type, payload) {
      for (const peer of buses) if (peer !== result) {
        for (const callback of peer.listeners.get(type) || []) callback(payload, { type, source, payload });
      }
    },
    subscribe(type, callback) {
      const callbacks = listeners.get(type) || new Set(); callbacks.add(callback); listeners.set(type, callbacks);
      return () => callbacks.delete(callback);
    },
    listeners,
  };
  buses.push(result); return result;
}
function create(interfaceType, instanceId, storage, href) {
  const targetLink = new Control(href); const checkbox = new Control();
  const windowTarget = new WindowTarget();
  const controller = Coordination.createController({
    interfaceType, instanceId, bus: bus(interfaceType), targetLink, openInNewTabCheckbox: checkbox,
    storage, windowTarget, documentTarget: new DocumentTarget(), setIntervalFn: () => 1,
    clearIntervalFn() {},
  });
  return { checkbox, controller, targetLink, windowTarget };
}
const storage = new Storage();
assert.equal(Coordination.readOpenInNewTabPreference(storage), true);
const map = create("map", "map-a", storage, "/base");
assert.equal(map.checkbox.checked, true);
assert.equal(map.controller.isOpenInNewTabEnabled(), true);
assert.equal(map.targetLink.click().prevented, true);
assert.equal(map.windowTarget.name, Coordination.WINDOW_NAMES.map);
assert.deepEqual(map.windowTarget.openCalls, [["/base", Coordination.WINDOW_NAMES.data]]);
assert.equal(map.windowTarget.openedFocusCalls, 1);

map.checkbox.checked = false; map.checkbox.dispatch("change");
assert.equal(storage.getItem(Coordination.OPEN_IN_NEW_TAB_PREFERENCE_KEY), "false");
assert.equal(map.targetLink.click().prevented, false);
assert.equal(map.windowTarget.openCalls.length, 1);

const data = create("data", "data-a", storage, "/carte");
assert.equal(data.checkbox.checked, false);
storage.setItem(Coordination.OPEN_IN_NEW_TAB_PREFERENCE_KEY, "true");
data.windowTarget.dispatch("storage", { key: Coordination.OPEN_IN_NEW_TAB_PREFERENCE_KEY });
assert.equal(data.checkbox.checked, true);
assert.equal(data.controller.isOpenInNewTabEnabled(), true);
assert.equal(map.targetLink.click().prevented, true);
assert.deepEqual(map.windowTarget.openCalls.at(-1), ["/base", Coordination.WINDOW_NAMES.data]);
assert.equal(map.windowTarget.openedFocusCalls, 2);

map.checkbox.checked = false; map.checkbox.dispatch("change");
assert.equal(map.targetLink.click().prevented, false);
assert.equal(map.windowTarget.openCalls.length, 2);

map.controller.destroy();
data.controller.destroy();
const isolatedStorage = new Storage();
const isolatedData = create("data", "data-alone", isolatedStorage, "/carte");
assert.equal(isolatedData.targetLink.click({ ctrlKey: true }).prevented, false);
assert.equal(isolatedData.windowTarget.openCalls.length, 0);
assert.equal(isolatedData.windowTarget.name, "");
assert.equal(isolatedData.targetLink.click().prevented, true);
assert.equal(isolatedData.windowTarget.name, Coordination.WINDOW_NAMES.data);
assert.deepEqual(isolatedData.windowTarget.openCalls, [["/carte", Coordination.WINDOW_NAMES.map]]);
assert.equal(isolatedData.windowTarget.openedFocusCalls, 1);
'''
        subprocess.run([node, "-e", program], cwd=PROJECT_DIRECTORY, check=True)

    def test_instance_id_generation_payload_validation_and_target_order(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")

        program = r'''
const assert = require("node:assert/strict");
const Coordination = require("./webapp/frontend/js/interface-coordination.js");
assert.equal(Coordination.createInstanceId({ randomUUID: () => "uuid-1" }), "uuid-1");
const values = new Uint32Array([1, 2, 3, 4]);
assert.equal(Coordination.createInstanceId({ getRandomValues(target) {
  target.set(values); return target;
} }), "00000001-00000002-00000003-00000004");
const fallback = Coordination.createInstanceId(null, () => 123, () => 0.5);
assert.match(fallback, /^3f-/);

assert.deepEqual(Coordination.normalizeActivity({
  interface: "map", instanceId: "map-1", timestamp: 10,
}), { interface: "map", instanceId: "map-1", timestamp: 10 });
for (const invalid of [null, {}, [],
  { interface: "other", instanceId: "x", timestamp: 1 },
  { interface: "map", instanceId: "", timestamp: 1 },
  { interface: "map", instanceId: "x", timestamp: "1" },
]) assert.equal(Coordination.normalizeActivity(invalid), null);

assert.deepEqual(Coordination.normalizePresence({
  interface: "map", instanceId: "map-1", timestamp: 10, requestReply: true,
}), { interface: "map", instanceId: "map-1", timestamp: 10, requestReply: true });
assert.deepEqual(Coordination.WINDOW_NAMES, {
  map: "digues-app-map", data: "digues-app-data",
});

const instances = [
  { instanceId: "b", lastSeenAt: 300, lastActiveAt: 200 },
  { instanceId: "a", lastSeenAt: 400, lastActiveAt: 100 },
];
assert.equal(instances.sort(Coordination.compareTargets)[0].instanceId, "b");
assert.equal([
  { instanceId: "a", lastSeenAt: 100, lastActiveAt: null },
  { instanceId: "b", lastSeenAt: 200, lastActiveAt: null },
].sort(Coordination.compareTargets)[0].instanceId, "b");
assert.equal([
  { instanceId: "b", lastSeenAt: 100, lastActiveAt: null },
  { instanceId: "a", lastSeenAt: 100, lastActiveAt: null },
].sort(Coordination.compareTargets)[0].instanceId, "a");
'''
        subprocess.run([node, "-e", program], cwd=PROJECT_DIRECTORY, check=True)

    def test_pages_and_pwa_shell_load_coordination_without_dual_preference(self):
        service_worker = (FRONTEND_DIRECTORY / "service-worker.js").read_text(
            encoding="utf-8"
        )
        css = (FRONTEND_DIRECTORY / "css" / "app.css").read_text(encoding="utf-8")
        module = (FRONTEND_DIRECTORY / "js" / "interface-coordination.js").read_text(
            encoding="utf-8"
        )
        header = (FRONTEND_DIRECTORY / "js" / "app-header.js").read_text(
            encoding="utf-8"
        )
        standalone = css.split("@media (display-mode: standalone)", 1)[1]
        self.assertNotIn(".map-page .view-switcher", standalone)
        self.assertEqual(
            service_worker.count('"/static/js/interface-coordination.js"'), 1
        )
        self.assertNotIn("sirs:dual-interface-enabled", module)
        self.assertIn('OPEN_IN_NEW_TAB_PREFERENCE_KEY = "digues-app:interface-open-new-tab-enabled"', module)
        self.assertNotIn("interface-focus-request", module)
        self.assertIn('map: globalThis.BusinessRuntime?.key("digues-app-map")', module)
        self.assertIn('data: globalThis.BusinessRuntime?.key("digues-app-data")', module)

        for page_name, interface_type, target, href, label in (
            ("map.html", "map", "data", "/base", "Base de données"),
            ("data.html", "data", "map", "/carte", "Cartographie"),
        ):
            page = (FRONTEND_DIRECTORY / page_name).read_text(encoding="utf-8")
            with self.subTest(page=page_name):
                self.assertNotIn("dual-interface-enabled", page)
                self.assertNotIn("Ouvrir Carte et Base séparément", page)
                self.assertIn(f'data-interface="{interface_type}"', page)
                self.assertIn('src="/static/js/app-header.js"', page)
                self.assertEqual(header.count('id="sync-heritage-selection"'), 1)
                self.assertEqual(header.count('id="open-interface-in-new-tab"'), 1)
                self.assertIn("Ouvrir Carte/Base dans un nouvel onglet", header)
                self.assertIn(f'switchHref: "{href}"', header)
                self.assertIn(f'switchTarget: "{target}"', header)
                self.assertIn(f'switchLabel: "{label}"', header)
                self.assertNotIn("view-switcher-menu", page)
                self.assertIn(
                    f'src="/static/js/interface-coordination.js" '
                    f'data-interface="{interface_type}"',
                    page,
                )


if __name__ == "__main__":
    unittest.main()
