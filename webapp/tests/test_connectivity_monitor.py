import subprocess
import unittest


HARNESS = r'''
const assert = require("node:assert/strict");
const { canCoordinateReconnect, createConnectivityMonitor } = require("./webapp/frontend/js/offline-consultation.js");
class Target {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, callback) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(callback);
  }
  removeEventListener(type, callback) { this.listeners.get(type)?.delete(callback); }
  dispatch(type) { for (const callback of [...(this.listeners.get(type) || [])]) callback({ type }); }
}
function environment(fetchImpl, options = {}) {
  const root = new Target(), documentTarget = new Target(), timers = new Map();
  let timerSequence = 0, clock = 1000;
  documentTarget.visibilityState = "visible";
  Object.assign(root, {
    document: documentTarget, fetch: fetchImpl, AbortController, AbortSignal,
    setTimeout(callback, delay) { const id = ++timerSequence; timers.set(id, { callback, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
  });
  const states = [], reconnects = [];
  const monitor = createConnectivityMonitor({ root, documentTarget, fetchImpl,
    random: () => 0.5, now: () => ++clock, ...options,
    onState: state => states.push(state), onReconnect: state => reconnects.push(state) });
  return { root, documentTarget, timers, states, reconnects, monitor };
}
'''


class ConnectivityMonitorTest(unittest.TestCase):
    def test_reconnection_requires_a_live_authorized_context(self):
        subprocess.run(["node", "-e", HARNESS + r'''
const active = { isActive: () => true };
assert.equal(canCoordinateReconnect({ reason: "probe-ok" }, active), true);
assert.equal(canCoordinateReconnect({ reason: "business-http-204" }, active), true);
assert.equal(canCoordinateReconnect({ reason: "authentication-required" }, active), false);
assert.equal(canCoordinateReconnect({ reason: "permission-denied" }, active), false);
assert.equal(canCoordinateReconnect({ reason: "probe-ok" }, { isActive: () => false }), false);
'''], check=True)

    def test_lifecycle_browser_hints_foreground_and_background(self):
        subprocess.run(["node", "-e", HARNESS + r'''
(async () => {
  let available = true, calls = 0;
  const fetchImpl = async (url, options) => {
    calls++;
    assert.equal(url, "/api/resource-events/head");
    assert.equal(options.cache, "no-store");
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.connectivityObservation, false);
    assert.ok(options.signal);
    if (!available) throw new TypeError("Failed to fetch");
    return { ok: true, status: 200, json: async () => ({ cursor: "12" }) };
  };
  const h = environment(fetchImpl);
  assert.deepEqual(h.monitor.getState(), { status: "unknown", offline: null, checkedAt: null, reason: "initial" });
  await h.monitor.start();
  assert.equal(calls, 1);
  assert.deepEqual(h.states.map(state => [state.status, state.offline]), [["checking", null], ["online", false]]);
  assert.equal(h.reconnects.length, 0, "startup is not a reconnection");
  assert.equal([...h.timers.values()][0].delay, 45000);

  // navigator.onLine is deliberately irrelevant: the browser event only wakes a real probe.
  h.root.navigator = { onLine: true };
  available = false;
  const [periodicId, periodicTimer] = [...h.timers.entries()][0];
  h.timers.delete(periodicId); periodicTimer.callback();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.monitor.getState().status, "offline");
  assert.equal([...h.timers.values()][0].delay, 5000);
  const beforeOfflineHint = calls;
  h.root.dispatch("offline");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls, beforeOfflineHint + 1);
  assert.equal([...h.timers.values()][0].delay, 15000);

  available = true;
  h.root.dispatch("online");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.monitor.getState().status, "online");
  assert.equal(h.reconnects.length, 1);

  const beforeFocus = calls;
  h.root.dispatch("focus");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls, beforeFocus + 1);
  assert.equal(h.reconnects.length, 1, "online confirmations are deduplicated");
  h.documentTarget.visibilityState = "hidden";
  h.documentTarget.dispatch("visibilitychange");
  assert.equal(h.timers.size, 0);
  h.documentTarget.visibilityState = "visible";
  h.documentTarget.dispatch("visibilitychange");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.monitor.getState().status, "online");
  assert.equal(h.timers.size, 1);
  const beforePageShow = calls;
  h.root.dispatch("pageshow");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls, beforePageShow + 1);

  h.monitor.stop();
  assert.equal(h.timers.size, 0);
  assert.ok([...h.root.listeners.values()].every(group => group.size === 0));
  assert.ok([...h.documentTarget.listeners.values()].every(group => group.size === 0));
})().catch(error => { console.error(error); process.exitCode = 1; });
'''], check=True)

    def test_mutualization_stale_results_http_timeout_and_deduplication(self):
        subprocess.run(["node", "-e", HARNESS + r'''
(async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const h = environment(() => pending);
  const first = h.monitor.start();
  const second = h.monitor.check("simultaneous");
  assert.strictEqual(first, second);
  assert.equal(h.monitor.hasActiveProbe(), true);
  release({ ok: true, status: 200, json: async () => ({ cursor: "1" }) });
  await first;
  assert.equal(h.monitor.getState().status, "online");

  const oldRequest = h.monitor.beginEvidence();
  const newerRequest = h.monitor.beginEvidence();
  h.monitor.confirmOnline(newerRequest, "newer-success");
  assert.equal(h.monitor.confirmOffline(oldRequest, "late-failure"), false);
  assert.equal(h.monitor.getState().status, "online");
  const publications = h.states.length;
  h.monitor.confirmOnline(h.monitor.beginEvidence(), "same-status");
  assert.equal(h.states.length, publications, "same stable state must not be republished");
  h.monitor.stop();

  async function classify(responseOrError) {
    const sample = environment(async () => {
      if (responseOrError instanceof Error) throw responseOrError;
      return responseOrError;
    });
    await sample.monitor.start();
    const state = sample.monitor.getState();
    sample.monitor.stop();
    return state;
  }
  assert.equal((await classify({ ok: false, status: 401, json: async () => ({}) })).status, "online");
  assert.equal((await classify({ ok: false, status: 403, json: async () => ({}) })).status, "online");
  assert.deepEqual(await classify({ ok: false, status: 503, json: async () => ({}) }),
    { status: "offline", offline: true, checkedAt: 1001, reason: "backend-http-503" });
  assert.equal((await classify({ ok: true, status: 200, json: async () => ({ cursor: "bad" }) })).reason,
    "invalid-probe-response");
  const timeout = new DOMException("Timed out", "TimeoutError");
  const timedOut = await classify(timeout);
  assert.equal(timedOut.status, "offline");
  assert.equal(timedOut.reason, "timeout");

  const actualTimeout = environment((_url, { signal }) => new Promise((resolve, reject) => {
    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
  }), { timeoutMs: 5 });
  await actualTimeout.monitor.start();
  assert.equal(actualTimeout.monitor.getState().reason, "timeout");
  actualTimeout.monitor.stop();
})().catch(error => { console.error(error); process.exitCode = 1; });
'''], check=True)

    def test_central_observations_are_classified_without_recursion_or_stale_overwrite(self):
        subprocess.run(["node", "-e", HARNESS + r'''
(async () => {
  let probeResponse = { ok: true, status: 200, json: async () => ({ cursor: "1" }) };
  let releaseProbe = null, probeCalls = 0, syncCalls = 0;
  const fetchImpl = async (_url, options) => {
    probeCalls++;
    assert.equal(options.connectivityObservation, false, "the authoritative probe must not observe itself");
    if (releaseProbe) return new Promise(resolve => { releaseProbe.resolve = resolve; });
    return probeResponse;
  };
  const h = environment(fetchImpl, { contextKey: "context-A" });
  await h.monitor.start();
  assert.equal(h.monitor.getState().status, "online");
  const metadata = (overrides = {}) => ({ scope: "business", contextKey: "context-A",
    generation: 1, startedAt: 100, ...overrides });
  const result = (outcome, overrides = {}) => ({ outcome, contextKey: "context-A",
    generation: 1, startedAt: 100, completedAt: 200, ...overrides });
  const setOffline = reason => h.monitor.confirmOffline(h.monitor.beginEvidence(), reason);

  for (const [status, reason] of [[200, "business-http-200"], [401, "authentication-required"],
    [403, "permission-denied"]]) {
    setOffline("fixture");
    const handle = h.monitor.observeStart(metadata());
    h.monitor.observeResult(handle, result("response", { status }));
    assert.equal(h.monitor.getState().status, "online");
    assert.equal(h.monitor.getState().reason, reason);
  }

  for (const status of [404, 422]) {
    setOffline("fixture");
    const handle = h.monitor.observeStart(metadata());
    h.monitor.observeResult(handle, result("response", { status }));
    assert.equal(h.monitor.getState().status, "online");
    assert.equal(h.monitor.getState().reason, `business-http-${status}`);
  }

  // Ambiguous server/transport failures wake one authoritative probe and do
  // not impose their own state while that probe is pending.
  for (const observation of [result("response", { status: 408 }),
    result("response", { status: 429 }), result("response", { status: 500 }),
    result("response", { status: 502 }), result("transport-error"), result("timeout")]) {
    h.monitor.confirmOnline(h.monitor.beginEvidence(), "fixture-online");
    releaseProbe = {};
    const before = probeCalls;
    const handle = h.monitor.observeStart(metadata());
    h.monitor.observeResult(handle, observation);
    assert.equal(h.monitor.getState().status, "online");
    assert.equal(h.monitor.hasActiveProbe(), true);
    assert.equal(probeCalls, before + 1);
    probeResponse = { ok: false, status: 503, json: async () => ({}) };
    releaseProbe.resolve(probeResponse); releaseProbe = null;
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(h.monitor.getState().status, "offline");
  }

  const beforeAbort = probeCalls;
  const abortHandle = h.monitor.observeStart(metadata());
  h.monitor.observeResult(abortHandle, result("abort"));
  assert.equal(probeCalls, beforeAbort);

  assert.equal(h.monitor.observeStart(metadata({ contextKey: "context-B" })), null);
  assert.equal(h.monitor.observeStart(metadata({ scope: "peripheral" })), null);
  const oldGeneration = h.monitor.observeStart(metadata({ generation: 1 }));
  assert.equal(h.monitor.observeResult(oldGeneration,
    result("response", { status: 200, generation: 2 })), false);

  setOffline("fixture");
  const old = h.monitor.observeStart(metadata({ startedAt: 1 }));
  const recent = h.monitor.observeStart(metadata({ startedAt: 2 }));
  h.monitor.observeResult(recent, result("response", { status: 200, startedAt: 2 }));
  const callsBeforeStale = probeCalls;
  const reconnectsBeforeStale = h.reconnects.length;
  assert.equal(h.monitor.observeResult(old, result("response", { status: 500, startedAt: 1 })), false);
  assert.equal(h.monitor.getState().status, "online");
  assert.equal(probeCalls, callsBeforeStale);
  assert.equal(h.reconnects.length, reconnectsBeforeStale, "stale evidence must not trigger a late recovery");

  const publications = h.states.length;
  const reconnects = h.reconnects.length;
  const duplicate = h.monitor.observeStart(metadata());
  h.monitor.observeResult(duplicate, result("response", { status: 200 }));
  assert.equal(h.states.length, publications);
  assert.equal(h.reconnects.length, reconnects);
  assert.equal(syncCalls, 0, "connectivity observations must not start business synchronization");
  h.monitor.stop();
})().catch(error => { console.error(error); process.exitCode = 1; });
'''], check=True)

    def test_browser_consumers_only_wake_for_the_current_confirmed_context(self):
        subprocess.run(["node", "-e", r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
class Target {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, callback, options = {}) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    if (options.once) {
      const once = event => { this.removeEventListener(type, once); callback(event); };
      this.listeners.get(type).add(once);
    } else this.listeners.get(type).add(callback);
  }
  removeEventListener(type, callback) { this.listeners.get(type)?.delete(callback); }
  dispatch(type, detail = undefined) {
    for (const callback of [...(this.listeners.get(type) || [])]) callback({ type, detail });
  }
}
function runtime() {
  return { legacy: false, context: { key: "context-A" }, generation: () => 7,
    isActive: () => true, can: () => true, coordinationKey: name => name,
    apiUrl: value => value, fetch: async () => { throw new Error("unexpected fetch"); } };
}
function execute(path, root) {
  root.globalThis = root;
  vm.runInNewContext(fs.readFileSync(path, "utf8"), root, { filename: path });
}
const current = { contextKey: "context-A", generation: 7 };
const stale = { contextKey: "context-B", generation: 7 };

(async () => {
  const mutationRoot = Object.assign(new Target(), {
    ResourceInvalidation: require("./webapp/frontend/js/resource-invalidation.js"),
    ResourceEvents: require("./webapp/frontend/js/resource-events.js"),
    InterPageBus: { subscribe() { return () => {}; }, publish() {} },
    InterfaceCoordination: { instanceId: "instance-A" }, BusinessRuntime: runtime(),
  });
  execute("./webapp/frontend/js/resource-mutation.js", mutationRoot);
  let invalidationRetries = 0;
  mutationRoot.ResourceMutationController.retryInvalidations = () => { invalidationRetries++; };
  mutationRoot.dispatch("online"); mutationRoot.dispatch("digues-app:backend-reconnected", stale);
  assert.equal(invalidationRetries, 0);
  mutationRoot.dispatch("digues-app:backend-reconnected", current);
  assert.equal(invalidationRetries, 1);
  mutationRoot.dispatch("digues-app:context-closed");
  mutationRoot.dispatch("digues-app:backend-reconnected", current);
  assert.equal(invalidationRetries, 1, "closed contexts remove the invalidation wake listener");

  const sources = [];
  class SSE {
    constructor() { sources.push(this); }
    addEventListener(_type, callback) { this.receive = callback; }
    close() { this.closed = true; }
  }
  const transportRoot = Object.assign(new Target(), {
    ResourceInvalidation: require("./webapp/frontend/js/resource-invalidation.js"),
    ResourceMutationController: { handleRemoteMutation: async () => ({ accepted: true }), retryInvalidations() {} },
    OfflineRuntime: { initialCursor: async () => "0", integrate: async () => {}, networkError: () => false },
    BusinessRuntime: { ...runtime(), fetch: async url => url.endsWith("/head")
      ? { ok: true, json: async () => ({ cursor: "0" }) }
      : { ok: true, json: async () => ({ events: [], nextCursor: "0", hasMore: false }) } },
    EventSource: SSE, AbortSignal, console, setTimeout, clearTimeout,
  });
  execute("./webapp/frontend/js/resource-server-transport.js", transportRoot);
  transportRoot.ResourceEventTransport.start();
  for (let index = 0; index < 20 && sources.length < 1; index++) await new Promise(resolve => setImmediate(resolve));
  assert.equal(sources.length, 1);
  transportRoot.dispatch("online"); transportRoot.dispatch("digues-app:backend-reconnected", stale);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(sources.length, 1);
  transportRoot.dispatch("digues-app:backend-reconnected", current);
  for (let index = 0; index < 20 && sources.length < 2; index++) await new Promise(resolve => setImmediate(resolve));
  assert.equal(sources.length, 2); assert.equal(sources[0].closed, true);
  assert.equal(transportRoot.ResourceEventTransport.getCursor(), "0");
  transportRoot.dispatch("digues-app:context-closed");
  transportRoot.dispatch("digues-app:backend-reconnected", current);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(sources.length, 2, "closed contexts remove the transport wake listener");
  transportRoot.ResourceEventTransport.stop();

  const timers = new Map(); let timerId = 0;
  const operationsRoot = Object.assign(new Target(), {
    OfflineRuntime: { store: { listOperations: async () => [] }, refreshConfirmed: async () => {} },
    ResourceEventTransport: { synchronize: async () => "0" },
    ResourceMutationController: { requestHeaders: async () => ({}), publishMutation: async () => {} },
    BusinessRuntime: runtime(), navigator: { locks: { request: async (_name, _options, work) => work({}) } },
    document: { querySelector: () => null }, AbortSignal, crypto, console,
    setTimeout(callback, delay) { const id = ++timerId; timers.set(id, { callback, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
  });
  execute("./webapp/frontend/js/offline-operations.js", operationsRoot);
  for (let index = 0; index < 20 && timers.size < 1; index++) await new Promise(resolve => setImmediate(resolve));
  let replayWakes = 0;
  operationsRoot.OfflineOperationsRuntime.synchronize = async force => {
    assert.notEqual(force, true, "reconnection must preserve replay backoff"); replayWakes++;
  };
  operationsRoot.dispatch("online"); operationsRoot.dispatch("digues-app:backend-reconnected", stale);
  assert.equal(replayWakes, 0);
  operationsRoot.dispatch("digues-app:backend-reconnected", current);
  for (let index = 0; index < 20 && replayWakes < 1; index++) await new Promise(resolve => setImmediate(resolve));
  assert.equal(replayWakes, 1);
  operationsRoot.dispatch("digues-app:context-closed");
  operationsRoot.dispatch("digues-app:backend-reconnected", current);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(replayWakes, 1, "closed contexts remove the replay wake listener");
})().catch(error => { console.error(error); process.exitCode = 1; });
'''], check=True)


if __name__ == "__main__":
    unittest.main()
