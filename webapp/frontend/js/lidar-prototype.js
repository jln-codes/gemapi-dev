(async function (root) {
  "use strict";
  const { L, proj4, GeoTIFF, LidarPrototypeCore: core, LidarCanvas: canvas } = root;
  const $ = id => document.getElementById(id);
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const MAX_MEMORY_BLOCKS = core.MAX_BLOCKS;
  const REQUEST_DELAY_MS = 220;
  const state = { generation: 0, controller: null, timer: null, selection: [],
    base: null, catalog: null, blocks: new Map(), sampled: null, sampledSize: null,
    networkBytes: 0, cacheCounts: { MEMORY: 0, HIT: 0, WAIT: 0, MISS: 0 },
    currentBlocks: new Map(), map: null, overlay: null, disposed: false };

  function message(value, error = false) {
    $("status").textContent = value;
    $("status").classList.toggle("error", error);
  }
  function metric(name, value) { $("m-" + name).textContent = value; }
  const millis = value => Number.isFinite(value) ? `${Math.round(value)} ms` : "—";
  function memoryMetric() {
    const bytes = [...state.blocks.values()].reduce((sum, block) => sum + block.bytes, 0)
      + (state.sampled?.byteLength || 0);
    metric("memory", `${(bytes / 1048576).toFixed(1)} Mio`);
  }
  function cacheMetric() {
    metric("cache", Object.entries(state.cacheCounts).filter(([, count]) => count)
      .map(([source, count]) => `${source} ${count}`).join(" · ") || "—");
  }
  function assertCurrent(generation) {
    if (state.disposed || generation !== state.generation || state.controller?.signal.aborted) {
      throw new DOMException("Vue obsolète", "AbortError");
    }
  }
  function cancel() {
    state.generation++;
    clearTimeout(state.timer);
    state.controller?.abort(); state.controller = null;
  }
  function contextUrl(resource) {
    const base = state.base;
    if (!base || !state.catalog) throw new Error("Base métier absente.");
    const query = new URLSearchParams({ context_user_id: state.catalog.user_id,
      context_data_epoch: base.data_epoch, context_instance_id: state.catalog.instance_id });
    return `/api/bases/${base.business_database_id}/${resource}?${query}`;
  }
  async function jsonPost(resource, body, signal) {
    const response = await fetch(contextUrl(resource), { method: "POST", credentials: "same-origin",
      cache: "no-store", signal, headers: { "Content-Type": "application/json", "X-Digues-CSRF": "1" },
      body: JSON.stringify(body) });
    const received = await response.text();
    state.networkBytes += new TextEncoder().encode(received).byteLength;
    metric("network", `${(state.networkBytes / 1048576).toFixed(2)} Mio`);
    if (!response.ok) throw new Error(`${resource} : HTTP ${response.status}`);
    return JSON.parse(received);
  }
  function selected() {
    const lines = $("ensembles").value.split(/[\n,;]+/).map(value => value.trim()).filter(Boolean);
    if (!lines.length || lines.length > 1024) throw new Error("Sélection LiDAR trop volumineuse.");
    const rows = lines.map(line => {
      const match = /^(SE|AH)\s*:\s*([0-9a-f-]{36})$/i.exec(line);
      if (!match || !UUID.test(match[2])) throw new Error(`Identifiant invalide : ${line}`);
      return { type: match[1].toUpperCase(), id: match[2].toLowerCase() };
    });
    return [...new Map(rows.map(row => [`${row.type}:${row.id}`, row])).values()];
  }
  function projectedBounds() { return canvas.projectedBounds(state.map, proj4); }
  function recolor() {
    if (!state.sampled || !state.overlay?.canvas) return;
    const started = performance.now();
    const range = canvas.paint(state.overlay, core,
      { samples: state.sampled, ...state.sampledSize });
    metric("range", range ? `${range.min.toFixed(2)} – ${range.max.toFixed(2)} m` : "Aucune altitude valide");
    metric("color-time", millis(performance.now() - started));
  }
  async function render(generation) {
    assertCurrent(generation);
    state.overlay.reset();
    const started = performance.now();
    const sampled = await canvas.sampleView({ map: state.map, proj4, core,
      blocks: state.currentBlocks, mask: state.mask, assertCurrent: () => assertCurrent(generation) });
    metric("sample-time", millis(performance.now() - started));
    metric("projection-error", `${sampled.maximumError.toFixed(4)} m (points de contrôle)`);
    state.sampled = sampled.samples;
    state.sampledSize = { width: sampled.width, height: sampled.height };
    recolor(); memoryMetric();
  }
  function trimMemory(needed) {
    for (const key of state.blocks.keys()) {
      if (state.blocks.size <= MAX_MEMORY_BLOCKS &&
          [...state.blocks.values()].reduce((sum, b) => sum + b.bytes, 0) <= core.MAX_VIEW_BYTES) break;
      if (!needed.has(key)) state.blocks.delete(key);
    }
    while (state.blocks.size > MAX_MEMORY_BLOCKS ||
        [...state.blocks.values()].reduce((sum, b) => sum + b.bytes, 0) > core.MAX_VIEW_BYTES) {
      state.blocks.delete(state.blocks.keys().next().value);
    }
  }
  async function loadView() {
    cancel();
    if (!state.selection.length) return;
    state.overlay.clear(); state.sampled = null; memoryMetric();
    const generation = state.generation, controller = new AbortController();
    state.controller = controller;
    const started = performance.now();
    try {
      const body = { ensembles: state.selection,
        vue: { crs: "EPSG:2154", bbox: projectedBounds() } };
      body.vue.level = canvas.viewLevel(state.map, body.vue.bbox);
      const policyResponse = await fetch(contextUrl('lidar/policy'), {
        credentials:'same-origin',cache:'no-store',signal:controller.signal });
      if (!policyResponse.ok) throw new Error('Réglage du buffer LiDAR indisponible.');
      const policy = await policyResponse.json();
      assertCurrent(generation);
      if (policy.contract_version !== 1 || !Number.isSafeInteger(policy.policy_version) || policy.policy_version < 1)
        throw new Error('Réglage du buffer LiDAR invalide.');
      body.policy_version = policy.policy_version;
      const planStarted = performance.now();
      const plan = await jsonPost("lidar/blocks/v1/plan", body, controller.signal);
      assertCurrent(generation);
      metric("plan-time", millis(performance.now() - planStarted));
      core.validatePlan(plan);
      if (plan.policy_version !== body.policy_version) throw new Error('Révision du plan LiDAR incompatible.');
      state.mask = plan.mask;
      metric("planned", String(plan.blocks.length));
      const anomaly = plan.ensembles?.filter(row => row.status === "ANOMALY") || [];
      if (anomaly.length) message(`${anomaly.length} ensemble(s) en anomalie ; leurs blocs sont exclus.`, true);
      const needed = new Set(plan.blocks.map(block => block.id));
      state.currentBlocks = new Map();
      state.cacheCounts = { MEMORY: 0, HIT: 0, WAIT: 0, MISS: 0 };
      let loaded = 0, pending = plan.blocks.length, decodeMs = 0;
      const updateCounts = () => metric("loaded", `${loaded} / ${pending}`);
      updateCounts();
      if (!plan.blocks.length) {
        for (const name of ["fetch-time", "decode-time", "sample-time", "color-time"]) metric(name, "0 ms");
        metric("projection-error", "—");
        metric("range", "Aucune altitude valide");
        metric("total-time", millis(performance.now() - started));
        if (!anomaly.length) message("Aucun bloc LiDAR dans cette vue.");
        return;
      }
      const fetchStarted = performance.now();
      let cursor = 0;
      async function worker() {
        while (cursor < plan.blocks.length) {
          assertCurrent(generation);
          const block = plan.blocks[cursor++];
          let decoded = state.blocks.get(block.id);
          if (decoded) {
            state.blocks.delete(block.id); state.blocks.set(block.id, decoded);
            state.cacheCounts.MEMORY++;
          } else {
            const response = await fetch(contextUrl("lidar/blocks/v1/read"), {
              method: "POST", credentials: "same-origin", cache: "no-store",
              signal: controller.signal,
              headers: { "Content-Type": "application/json", "X-Digues-CSRF": "1" },
              body: JSON.stringify({ ...body, bloc: { id: block.id, ix: block.ix, iy: block.iy } }),
            });
            assertCurrent(generation);
            if (response.status === 204 && response.headers.get("X-Lidar-Status") === "NO_COVERAGE") {
              pending--; updateCounts(); continue;
            }
            if (!response.ok || response.headers.get("Content-Type")?.split(";")[0] !== "image/geotiff") {
              throw new Error(`Lecture de bloc : HTTP ${response.status}`);
            }
            const buffer = await response.arrayBuffer();
            assertCurrent(generation);
            state.networkBytes += buffer.byteLength;
            metric("network", `${(state.networkBytes / 1048576).toFixed(2)} Mio`);
            const decodeStarted = performance.now();
            decoded = await core.decodeBlock(buffer, block, GeoTIFF);
            assertCurrent(generation);
            decodeMs += performance.now() - decodeStarted;
            const source = response.headers.get("X-Cache");
            if (Object.hasOwn(state.cacheCounts, source)) state.cacheCounts[source]++;
            state.blocks.set(block.id, decoded);
            trimMemory(needed);
          }
          const key = core.key(block.ix, block.iy);
          if (!state.currentBlocks.has(key)) state.currentBlocks.set(key, []);
          state.currentBlocks.get(key).push(decoded);
          loaded++; pending--; updateCounts(); cacheMetric(); memoryMetric();
        }
      }
      await Promise.all(Array.from({ length: Math.min(3, plan.blocks.length) }, worker));
      assertCurrent(generation);
      metric("fetch-time", millis(performance.now() - fetchStarted));
      metric("decode-time", millis(decodeMs));
      await render(generation);
      assertCurrent(generation);
      metric("total-time", millis(performance.now() - started));
      if (!anomaly.length) message(plan.blocks.length
        ? `${plan.blocks.length} bloc(s) affiché(s), sans découpe des pixels.`
        : "Aucun bloc LiDAR dans cette vue.");
    } catch (error) {
      if (error.name !== "AbortError") {
        controller.abort(); // Stop sibling reads after the first failed block.
        message(error.message, true);
      }
    }
  }
  function schedule() {
    cancel();
    state.overlay?.clear();
    state.sampled = null;
    state.timer = setTimeout(loadView, REQUEST_DELAY_MS);
  }
  function setupMap() {
    const map = L.map("map", { zoomAnimation: false }).setView([46.5, 3], 6);
    state.map = map;
    state.overlay = canvas.createOverlay(map, L, "lidarPrototype", 350);
    map.on("movestart zoomstart", cancel);
    map.on("moveend zoomend resize", schedule);
    return map;
  }
  async function initialize() {
    if (!L || !proj4 || !GeoTIFF || !core || !canvas) throw new Error("Bibliothèques cartographiques absentes.");
    canvas.defineProjection(proj4);
    const [userResponse, catalogResponse] = await Promise.all([
      fetch("/api/auth/me", { credentials: "same-origin", cache: "no-store" }),
      fetch("/api/auth/databases", { credentials: "same-origin", cache: "no-store" }),
    ]);
    if (!userResponse.ok || !catalogResponse.ok) throw new Error("Session requise. Se connecter depuis /carte.");
    const user = await userResponse.json();
    state.catalog = await catalogResponse.json();
    if (user.id !== state.catalog.user_id) throw new Error("Contexte de session incohérent.");
    const bases = state.catalog.databases.filter(base => base.status === "active" &&
      base.permissions.includes("business.read"));
    if (!bases.length) throw new Error("Aucune base métier autorisée.");
    const select = $("business-base");
    for (const base of bases) select.add(new Option(`${base.structure_name} — ${base.display_name}`,
      base.business_database_id));
    const requested = new URL(location.href).searchParams.get("base");
    const chosen = bases.find(base => base.slug === requested || base.business_database_id === requested) || bases[0];
    select.value = chosen.business_database_id; state.base = chosen;
    const map = setupMap();
    select.addEventListener("change", () => {
      cancel(); state.base = bases.find(base => base.business_database_id === select.value);
      state.selection = []; state.blocks.clear(); state.currentBlocks.clear(); state.sampled = null;
      state.overlay.clear(); memoryMetric(); message("Saisir une sélection explicite pour cette base.");
    });
    $("selection-form").addEventListener("submit", async event => {
      event.preventDefault();
      try {
        cancel(); state.selection = []; state.selection = selected();
        state.blocks.clear(); state.currentBlocks.clear();
        state.overlay.clear(); memoryMetric();
        const generation = state.generation;
        const controller = new AbortController(); state.controller = controller;
        // L1b supplies only an initial location; all actual block authorization uses L2c.
        const masks = await jsonPost("lidar/masks/v1", { ensembles: state.selection }, controller.signal);
        assertCurrent(generation);
        const ready = masks.masks.filter(mask => mask.status === "READY" && mask.bbox);
        if (!ready.length) {
          message("Aucune géométrie contributrice exploitable pour cette sélection.", true);
          return;
        }
        const bbox = ready[0].bbox;
        const [lon, lat] = proj4("EPSG:2154", "EPSG:4326",
          [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]);
        map.setView([lat, lon], 18);
        schedule();
      } catch (error) { if (error.name !== "AbortError") message(error.message, true); }
    });
    $("opacity").addEventListener("input", () => {
      const value = Number($("opacity").value);
      $("opacity-label").value = `${value} %`;
      state.overlay.canvas.style.opacity = String(value / 100);
    });
    state.overlay.canvas.style.opacity = "0.68";
    try {
      const response = await fetch("/basemap/plan-ign.json", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const tilejson = await response.json();
      const template = tilejson.tiles?.[0];
      if (typeof template !== "string") throw new Error("URL des tuiles absente.");
      L.tileLayer(template, { attribution: tilejson.attribution || "© IGN",
        maxZoom: 20, maxNativeZoom: tilejson.maxzoom || 19 }).addTo(map);
      message("Saisir des UUID SE/AH, puis déplacer la carte pour explorer.");
    } catch (error) { message(`Fond PLAN IGN indisponible : ${error.message}`, true); }
    root.LidarPrototypeDemo = { map, state };
  }
  root.addEventListener("pagehide", () => {
    state.disposed = true; cancel(); state.blocks.clear(); state.currentBlocks.clear();
    state.sampled = null; state.map?.remove();
  }, { once: true });
  try { await initialize(); } catch (error) { message(error.message, true); }
})(window);
