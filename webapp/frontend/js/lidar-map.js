/* LiDAR thematic layer. Visible patrimoine, scoped L2c transport, local numeric blocks. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LidarMap = api;
})(globalThis, function () {
  "use strict";
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const GRID = "epsg2154-050cm-512-v1";
  const MAX_BLOCKS = 128, MAX_MEMORY_BYTES = 24 * 1024 * 1024;
  // Bounding boxes only preselect candidates. L2c remains the authority for
  // exact masks, contributors and read permissions; no spatial parent is inferred.
  function visibleEnsembles({ hierarchy, geometries }, bbox, bufferM = 25) {
    if (!Array.isArray(hierarchy?.systemes) || geometries?.version !== 1 ||
        geometries.crs?.srid !== 2154 || !Array.isArray(geometries.troncons) ||
        !Array.isArray(geometries.zones_stockage)) {
      throw new Error("Emprises patrimoniales LiDAR indisponibles.");
    }
    const digues = new Map();
    for (const systeme of hierarchy.systemes) {
      for (const digue of systeme.digues || []) {
        if (digues.has(digue.id) && digues.get(digue.id) !== systeme.id) {
          throw new Error("Parent patrimonial ambigu.");
        }
        digues.set(digue.id, systeme.id);
      }
    }
    const selected = new Map();
    function add(type, id) {
      if (!UUID.test(id || "")) throw new Error("Identité patrimoniale LiDAR invalide.");
      selected.set(`${type}:${id.toLowerCase()}`, { type, id: id.toLowerCase() });
    }
    for (const row of [...geometries.troncons, ...geometries.zones_stockage]) {
      const box = row.bbox;
      if (!box) continue; // No usable extent: never invent a geometry.
      if (!Array.isArray(box) || box.length !== 4 || !box.every(Number.isFinite) ||
          box[0] > box[2] || box[1] > box[3]) throw new Error("Emprise patrimoniale invalide.");
      // Include the authoritative installation buffer in
      // the viewport prefilter so objects just outside the view are not lost.
      if (box[0] - bufferM > bbox[2] || box[2] + bufferM < bbox[0] ||
          box[1] - bufferM > bbox[3] || box[3] + bufferM < bbox[1]) continue;
      if (row.digue_id) add("SE", digues.get(row.digue_id));
      if (row.amenagement_hydraulique_id) add("AH", row.amenagement_hydraulique_id);
    }
    if (selected.size > 1024) throw new Error("Trop d’ensembles pour une demande LiDAR bornée.");
    return [...selected.values()].sort((a, b) =>
      a.type.localeCompare(b.type) || a.id.localeCompare(b.id));
  }
  function create({ map, L, proj4, geotiff, core, canvas, runtime, getPatrimoine,
    offline = globalThis.OfflineRuntime, storage = globalThis.OfflineBasemapStore,
    documentObject = document, navigatorObject = navigator, onMetrics = () => {} }) {
    if (!map || !runtime?.context || !runtime.can("business.read") || !L || !proj4 ||
        !geotiff || !core || !canvas || !storage?.createLidarStore || typeof getPatrimoine !== "function") {
      throw new Error("Contexte cartographique LiDAR indisponible.");
    }
    const $ = id => documentObject.getElementById(id);
    const widget = $("lidar-legend-item"), toggle = $("lidar-toggle"), controls = $("lidar-legend-controls");
    const status = $("lidar-status"), rangeOutput = $("lidar-range");
    if (!widget || !toggle || !controls) throw new Error("Légende LiDAR absente.");
    canvas.defineProjection(proj4);
    const store = storage.createLidarStore({ core });
    const contextKey = runtime.context.key;
    let patrimoine = null;
    const state = { enabled: false, selection: [], memory: new Map(), current: new Map(),
      sampled: null, generation: 0, controller: null, timer: null, overlay: null,
      networkBytes: 0, lastPlan: null, disposed: false,
      metrics: { planned: 0, loaded: 0, pending: 0, planMs: 0, readMs: 0,
        decodeMs: 0, sampleMs: 0, colorMs: 0, totalMs: 0, networkBytes: 0,
        memoryBytes: 0, cache: { MEMORY: 0, LOCAL: 0, HIT: 0, WAIT: 0, MISS: 0 } } };
    function emit() {
      state.metrics.networkBytes = state.networkBytes;
      state.metrics.memoryBytes = [...state.memory.values()].reduce((sum, row) => sum + row.bytes, 0)
        + (state.sampled?.samples.byteLength || 0);
      onMetrics({ ...state.metrics, cache: { ...state.metrics.cache } });
    }
    function say(message, error = false) {
      status.textContent = message;
      status.classList.toggle("error", error);
    }
    function knownOffline() {
      return navigatorObject.onLine === false || offline?.isOffline?.() === true;
    }
    function current(token) {
      if (state.disposed || !state.enabled || token !== state.generation ||
          state.controller?.signal.aborted || !runtime.isActive()) {
        throw new DOMException("Vue obsolète", "AbortError");
      }
    }
    function cancel() {
      state.generation++;
      clearTimeout(state.timer);
      state.controller?.abort(); state.controller = null;
    }
    function clear() { state.overlay?.clear(); state.sampled = null; state.range = null; state.current.clear(); rangeOutput.textContent = "Aucune altitude"; emit(); }
    function trim(needed) {
      for (const key of state.memory.keys()) {
        if (memoryBytes() <= MAX_MEMORY_BYTES && state.memory.size <= MAX_BLOCKS) break;
        if (!needed.has(key)) state.memory.delete(key);
      }
      while (memoryBytes() > MAX_MEMORY_BYTES || state.memory.size > MAX_BLOCKS) state.memory.delete(state.memory.keys().next().value);
    }
    function memoryBytes() { return [...state.memory.values()].reduce((sum, block) => sum + block.bytes, 0); }
    function recolor() {
      if (!state.sampled || !state.overlay) return;
      const start = performance.now();
      const chosen = canvas.paint(state.overlay, core, state.sampled);
      state.range = chosen;
      rangeOutput.textContent = chosen ? `${chosen.min.toFixed(2)} – ${chosen.max.toFixed(2)} m` :
        "Aucune altitude valide";
      state.metrics.colorMs = performance.now() - start;
      emit();
    }
    async function post(resource, body, signal) {
      const response = await runtime.fetch(`/api/lidar/blocks/v1/${resource}`, {
        method: "POST", cache: "no-store", signal,
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      return response;
    }
    async function planView(body, token, signal) {
      if (knownOffline()) {
        const local = await store.findPlan(contextKey, null, body.vue.bbox, body.vue.level);
        current(token);
        if (!local) return null;
        state.selection = local.ensembles.map(({ type, id }) => ({ type, id }));
        return { ...local, blocks: local.blocks.filter(block =>
          block.bbox[0] < body.vue.bbox[2] && block.bbox[2] > body.vue.bbox[0] &&
          block.bbox[1] < body.vue.bbox[3] && block.bbox[3] > body.vue.bbox[1]) };
      }
      const response = await post("plan", body, signal);
      if (!response.ok) throw new Error(`Planification LiDAR : HTTP ${response.status}`);
      const text = await response.text();
      state.networkBytes += new TextEncoder().encode(text).byteLength;
      const plan = JSON.parse(text);
      current(token);
      core.validatePlan(plan);
      if (plan.policy_version !== body.policy_version || !Number.isSafeInteger(plan.buffer_m) || plan.buffer_m < 5 || plan.buffer_m > 100)
        throw new Error('Révision du plan LiDAR incompatible.');
      try { await store.putPlan(contextKey, state.selection, body.vue.bbox, plan); }
      catch (error) { say(`Plan local non conservé : ${error.message}`, true); }
      return plan;
    }
    async function readBlock(block, body, token, signal) {
      let local = null;
      try { local = await store.getBlock(contextKey, block); }
      catch (error) { say(`Stockage local indisponible : ${error.message}`, true); }
      current(token);
      if (local) {
        if (local.status === "NO_COVERAGE") return { status: "NO_COVERAGE" };
        try {
          const decoded = await core.decodeBlock(local.bytes, block, geotiff);
          current(token);
          state.metrics.cache.LOCAL++;
          return { status: local.status, decoded };
        } catch (_) {
          await store.dropBlock(contextKey, block);
          if (knownOffline()) return { status: "MISSING" };
        }
      }
      if (knownOffline()) return { status: "MISSING" };
      const response = await post("read", { ...body,
        bloc: { id: block.id, ix: block.ix, iy: block.iy } }, signal);
      current(token);
      if (response.status === 204 && response.headers.get("X-Lidar-Status") === "NO_COVERAGE") {
        try { await store.putBlock(contextKey, block, null, "NO_COVERAGE"); } catch (_) { /* local best effort */ }
        return { status: "NO_COVERAGE" };
      }
      const dataStatus = response.headers.get("X-Lidar-Status");
      if (!response.ok || response.headers.get("Content-Type")?.split(";")[0] !== "image/geotiff" ||
          !["DATA", "NO_DATA"].includes(dataStatus)) {
        throw new Error(`Bloc LiDAR : HTTP ${response.status}`);
      }
      const bytes = await response.arrayBuffer();
      state.networkBytes += bytes.byteLength;
      current(token);
      const start = performance.now();
      const decoded = await core.decodeBlock(bytes, block, geotiff);
      state.metrics.decodeMs += performance.now() - start;
      current(token);
      try { await store.putBlock(contextKey, block, bytes, dataStatus); }
      catch (error) { say(`Bloc affiché mais non conservé localement : ${error.message}`, true); }
      const origin = response.headers.get("X-Cache");
      if (Object.hasOwn(state.metrics.cache, origin)) state.metrics.cache[origin]++;
      return { status: dataStatus, decoded };
    }
    async function load() {
      cancel();
      if (!state.enabled) return;
      clear();
      state.selection = [];
      const token = state.generation, controller = new AbortController();
      state.controller = controller;
      const started = performance.now();
      try {
        const body = { ensembles: [],
          vue: { crs: "EPSG:2154", bbox: canvas.projectedBounds(map, proj4) } };
        body.vue.level = canvas.viewLevel(map, body.vue.bbox);
        if (!knownOffline()) {
          say("Lecture des emprises patrimoniales…");
          const source = patrimoine || await getPatrimoine({ signal: controller.signal });
          current(token);
          patrimoine = source;
          const policyResponse = await runtime.fetch('/api/lidar/policy', { cache: 'no-store', signal: controller.signal });
          if (!policyResponse.ok) throw new Error('Réglage du buffer LiDAR indisponible.');
          const policy = await policyResponse.json();
          current(token);
          if (policy.contract_version !== 1 || !Number.isSafeInteger(policy.policy_version) || policy.policy_version < 1 || !Number.isSafeInteger(policy.buffer_m) || policy.buffer_m < 5 || policy.buffer_m > 100)
            throw new Error('Buffer LiDAR invalide.');
          body.policy_version = policy.policy_version;
          state.selection = visibleEnsembles(source, body.vue.bbox, policy.buffer_m);
          if (!state.selection.length) { say("Aucun SE/AH dans cette fenêtre cartographique."); return; }
          body.ensembles = state.selection;
        }
        say(knownOffline() ? "Lecture des blocs locaux…" : "Chargement du relief LiDAR…");
        const planStart = performance.now();
        const plan = await planView(body, token, controller.signal);
        current(token);
        state.metrics.planMs = performance.now() - planStart;
        if (!plan) { say("Données LiDAR non disponibles hors connexion pour cette zone."); return; }
        core.validatePlan(plan);
        body.ensembles = state.selection;
        state.lastPlan = plan;
        body.policy_version = plan.policy_version;
        state.metrics.planned = plan.blocks.length;
        state.metrics.loaded = 0;
        state.metrics.pending = plan.blocks.length;
        state.metrics.decodeMs = 0;
        state.metrics.cache = { MEMORY: 0, LOCAL: 0, HIT: 0, WAIT: 0, MISS: 0 };
        const anomaly = plan.ensembles?.filter(row => row.status === "ANOMALY") || [];
        if (!plan.blocks.length) {
          say(anomaly.length ? `${anomaly.length} ensemble(s) en anomalie ; aucun bloc acquis.` :
            plan.ensembles?.every(row => row.status === "EMPTY") ?
              "Aucun relief contributif pour la sélection." : "Aucun bloc LiDAR dans cette vue.",
          Boolean(anomaly.length));
          emit(); return;
        }
        const needed = new Set(plan.blocks.map(block => block.id));
        const readStart = performance.now();
        let cursor = 0, missing = 0, noCoverage = 0, noData = 0, failures = 0;
        const worker = async () => {
          while (cursor < plan.blocks.length) {
            current(token);
            const block = plan.blocks[cursor++];
            try {
              let decoded = state.memory.get(block.id);
              let blockStatus = "DATA";
              if (decoded) {
                state.memory.delete(block.id); state.memory.set(block.id, decoded);
                state.metrics.cache.MEMORY++;
                blockStatus = decoded.lidarStatus || "DATA";
              } else {
                const result = await readBlock(block, body, token, controller.signal);
                blockStatus = result.status;
                decoded = result.decoded;
              }
              current(token);
              if (blockStatus === "MISSING") missing++;
              else if (blockStatus === "NO_COVERAGE") noCoverage++;
              else if (decoded) {
                if (blockStatus === "NO_DATA") noData++;
                decoded.lidarStatus = blockStatus;
                state.memory.set(block.id, decoded);
                trim(needed);
                const key = core.key(block.ix, block.iy);
                if (!state.current.has(key)) state.current.set(key, []);
                state.current.get(key).push(decoded);
                state.metrics.loaded++;
              }
            } catch (error) {
              if (error.name === "AbortError") throw error;
              failures++;
            }
            state.metrics.pending--;
            emit();
          }
        };
        await Promise.all(Array.from({ length: Math.min(3, plan.blocks.length) }, worker));
        current(token);
        state.metrics.readMs = performance.now() - readStart;
        if (state.current.size) {
          state.overlay.reset();
          const sampleStart = performance.now();
          state.sampled = await canvas.sampleView({ map, proj4, core,
            blocks: state.current, mask: plan.mask, assertCurrent: () => current(token) });
          state.metrics.sampleMs = performance.now() - sampleStart;
          recolor();
        }
        current(token);
        state.metrics.totalMs = performance.now() - started;
        const completeness = await store.inspectPlan(contextKey, plan)
          .catch(() => ({ ready: false }));
        const partial = missing || failures || !completeness.ready || plan.partialView;
        say(partial ? `${failures ? "Erreur de chargement ; " : ""}${state.metrics.loaded}/${plan.blocks.length} bloc(s) affiché(s) ; cache local incomplet${
          knownOffline() ? " hors connexion" : ""}.` :
          noCoverage === plan.blocks.length ? "Absence de couverture IGN dans cette vue." :
            noData === plan.blocks.length || !state.range ? "Blocs reçus, sans altitude utile dans cette vue." :
            anomaly.length ? `Relief affiché ; ${anomaly.length} ensemble(s) en anomalie.` :
              `${state.metrics.loaded} bloc(s) LiDAR affiché(s) ; disponibles localement.`,
        Boolean(failures || anomaly.length));
        emit();
      } catch (error) {
        if (error.name !== "AbortError") say(error.message, true);
      }
    }
    function schedule() {
      cancel();
      clear();
      if (state.enabled) {
        say("Chargement du relief LiDAR…");
        state.timer = setTimeout(load, 220);
      }
    }
    function invalidatePatrimoine() {
      patrimoine = null;
      schedule();
    }
    function deactivate() {
      cancel();
      state.enabled = false;
      state.overlay?.remove(); state.overlay = null;
      state.memory.clear(); clear();
      say("Relief désactivé.");
    }
    function activate() {
      state.enabled = true;
      state.overlay = canvas.createOverlay(map, L);
      state.overlay.canvas.style.opacity = String(Number($("lidar-opacity").value) / 100);
      controls.hidden = false;
      invalidatePatrimoine();
    }
    toggle.addEventListener("change", () => {
      if (toggle.checked) activate(); else { controls.hidden = true; deactivate(); }
    });
    $("lidar-opacity").addEventListener("input", () => {
      const value = Number($("lidar-opacity").value);
      $("lidar-opacity-label").value = `${value} %`;
      if (state.overlay) state.overlay.canvas.style.opacity = String(value / 100);
    });
    map.on("movestart zoomstart", cancel);
    map.on("moveend zoomend resize", schedule);
    const onNetwork = () => { if (state.enabled) invalidatePatrimoine(); };
    globalThis.addEventListener("digues-app:network-state", onNetwork);
    globalThis.addEventListener("online", onNetwork);
    globalThis.addEventListener("offline", onNetwork);
    function dispose() {
      if (state.disposed) return;
      deactivate(); state.disposed = true;
      map.off("movestart zoomstart", cancel);
      map.off("moveend zoomend resize", schedule);
      globalThis.removeEventListener("digues-app:network-state", onNetwork);
      globalThis.removeEventListener("online", onNetwork);
      globalThis.removeEventListener("offline", onNetwork);
    }
    globalThis.addEventListener("digues-app:context-closed", dispose, { once: true });
    globalThis.addEventListener("pagehide", dispose, { once: true });
    widget.hidden = false;
    return { state, invalidatePatrimoine, schedule, dispose };
  }
  return { create, visibleEnsembles, GRID, MAX_BLOCKS };
});
