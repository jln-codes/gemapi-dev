(function (root, factory) {
  const defaultView = typeof module === "object" && module.exports
    ? require("./data-view-default.js") : root.DataViewDefault;
  const api = factory(defaultView);
  if (typeof module === "object" && module.exports) { module.exports = api; return; }
  api.mount(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function (DefaultView) {
  "use strict";
  const CURSOR = /^(0|[1-9][0-9]{0,18})$/;
  function canCoordinateReconnect(detail, runtime) {
    return !["authentication-required", "permission-denied"].includes(detail?.reason)
      && (!runtime || runtime.isActive());
  }
  function createConnectivityMonitor({
    root = globalThis,
    documentTarget = root.document,
    fetchImpl = root.fetch.bind(root),
    endpoint = "/api/resource-events/head",
    contextKey = null,
    timeoutMs = 5000,
    onlineIntervalMs = 45000,
    offlineDelays = [5000, 15000, 30000, 60000],
    random = Math.random,
    now = Date.now,
    setTimeoutFn = root.setTimeout.bind(root),
    clearTimeoutFn = root.clearTimeout.bind(root),
    onState = () => {},
    onReconnect = () => {},
  } = {}) {
    let state = { status: "unknown", offline: null, checkedAt: null, reason: "initial" };
    let stopped = true, timer = null, active = null, controller = null;
    let sequence = 0, committedSequence = 0, consecutiveFailures = 0;
    const listeners = [];
    const snapshot = () => ({ ...state });
    const visible = () => !documentTarget || documentTarget.visibilityState !== "hidden";
    function clearTimer() {
      if (timer !== null) clearTimeoutFn(timer);
      timer = null;
    }
    function publish(previousStatus) {
      if (previousStatus === state.status) return;
      const current = snapshot();
      onState(current);
      if (previousStatus === "offline" && current.status === "online") onReconnect(current);
    }
    function commit(token, status, reason, checkedAt = now()) {
      if (stopped || token < committedSequence) return false;
      committedSequence = token;
      const previousStatus = state.status;
      state = { status, offline: status === "offline" ? true : status === "online" ? false : null,
        checkedAt: status === "checking" ? state.checkedAt : checkedAt, reason };
      if (status === "online") consecutiveFailures = 0;
      else if (status === "offline") consecutiveFailures = previousStatus === "offline" ? consecutiveFailures + 1 : 1;
      publish(previousStatus);
      return true;
    }
    function beginEvidence() { return ++sequence; }
    function jitter(delay) { return Math.round(delay * (0.9 + Math.max(0, Math.min(1, random())) * 0.2)); }
    function schedule() {
      clearTimer();
      if (stopped || !visible() || active) return;
      const delay = state.status === "offline"
        ? offlineDelays[Math.min(Math.max(0, consecutiveFailures - 1), offlineDelays.length - 1)]
        : onlineIntervalMs;
      timer = setTimeoutFn(() => { timer = null; void check("periodic"); }, jitter(delay));
    }
    function confirmOnline(token = beginEvidence(), reason = "backend-response") {
      const changed = commit(token, "online", reason);
      schedule();
      return changed;
    }
    function confirmOffline(token = beginEvidence(), reason = "transport-error") {
      const changed = commit(token, "offline", reason);
      schedule();
      return changed;
    }
    function signalForProbe() {
      controller = new root.AbortController();
      const timeout = root.AbortSignal?.timeout?.(timeoutMs);
      return timeout && root.AbortSignal?.any
        ? root.AbortSignal.any([controller.signal, timeout]) : timeout || controller.signal;
    }
    function check(reason = "manual") {
      if (stopped) return Promise.resolve(snapshot());
      clearTimer();
      if (active) return active;
      const token = beginEvidence();
      if (state.status === "unknown") commit(token, "checking", reason, null);
      const signal = signalForProbe();
      active = (async () => {
        try {
          const response = await fetchImpl(endpoint, { cache: "no-store", credentials: "same-origin", signal,
            connectivityObservation: false });
          if (response.status === 401) confirmOnline(token, "authentication-required");
          else if (response.status === 403) confirmOnline(token, "permission-denied");
          else if (!response.ok) confirmOffline(token, `backend-http-${response.status}`);
          else {
            let payload;
            try { payload = await response.json(); } catch (_) { /* classified below */ }
            if (!CURSOR.test(payload?.cursor || "") || BigInt(payload.cursor) > 9223372036854775807n) {
              confirmOffline(token, "invalid-probe-response");
            } else confirmOnline(token, "probe-ok");
          }
        } catch (error) {
          if (!stopped && error?.name !== "AbortError") {
            confirmOffline(token, error?.name === "TimeoutError" ? "timeout" : "transport-error");
          }
        } finally {
          controller = null;
          active = null;
          schedule();
        }
        return snapshot();
      })();
      return active;
    }
    function listen(target, type, callback) {
      target?.addEventListener?.(type, callback);
      listeners.push(() => target?.removeEventListener?.(type, callback));
    }
    function start() {
      if (!stopped) return active || Promise.resolve(snapshot());
      stopped = false;
      const wake = event => { if (event?.type !== "visibilitychange" || visible()) void check(event?.type || "wake"); };
      const pause = () => { if (!visible()) clearTimer(); };
      listen(root, "online", wake);
      listen(root, "offline", wake);
      listen(root, "focus", wake);
      listen(root, "pageshow", wake);
      listen(documentTarget, "visibilitychange", event => { pause(); wake(event); });
      return check("startup");
    }
    function stop() {
      if (stopped) return;
      stopped = true;
      clearTimer();
      controller?.abort(); controller = null;
      while (listeners.length) listeners.pop()();
    }
    function observeStart(observation) {
      if (stopped || observation?.scope !== "business"
          || (contextKey !== null && observation.contextKey !== contextKey)) return null;
      return { token: beginEvidence(), contextKey: observation.contextKey,
        generation: observation.generation, startedAt: observation.startedAt };
    }
    function observeResult(handle, observation) {
      if (!handle || stopped || handle.token < committedSequence
          || handle.contextKey !== observation?.contextKey
          || handle.generation !== observation.generation
          || (contextKey !== null && observation.contextKey !== contextKey)) return false;
      if (observation.outcome === "response") {
        if (observation.status === 401) return confirmOnline(handle.token, "authentication-required");
        if (observation.status === 403) return confirmOnline(handle.token, "permission-denied");
        if (observation.status >= 200 && observation.status < 400) {
          return confirmOnline(handle.token, `business-http-${observation.status}`);
        }
        if (observation.status >= 500 || [408, 429].includes(observation.status)) {
          void check(`business-http-${observation.status}`);
          return false;
        }
        if (observation.status >= 400 && observation.status < 500) {
          return confirmOnline(handle.token, `business-http-${observation.status}`);
        }
        return false;
      }
      if (["transport-error", "timeout"].includes(observation.outcome)) {
        void check(`business-${observation.outcome}`);
      }
      return false;
    }
    return { start, stop, check, beginEvidence, confirmOnline, confirmOffline, observeStart, observeResult,
      getState: snapshot,
      isActive: () => !stopped, hasActiveProbe: () => active !== null };
  }

  function mount(root) {
  const store = root.LocalProjection.createStore();
  const businessRuntime = root.BusinessRuntime;
  const networkFetch = businessRuntime?.fetch || root.fetch.bind(root);
  const connectivityContextKey = businessRuntime ? (businessRuntime.legacy ? "legacy" : businessRuntime.context?.key) : null;
  let disabled = false, heritage = null;
  let projectionError = null;
  let noticeDismissed = false, noticeDiagnosis = null, offlineScopeDiagnosis = null;
  let serial = Promise.resolve();
  let detailSequence = 0;
  const OFFLINE_PREPARED_MESSAGE = "Hors connexion — périmètre préparé uniquement ; fraîcheur non garantie. Photos et fichiers enregistrés sur cet appareil disponibles, sans confirmation serveur. Fond de carte et autres fichiers Photo non préparés.";
  const OFFLINE_EMPTY_MESSAGE = "Hors connexion — aucun périmètre préparé ; aucune donnée métier locale disponible.";
  const STORAGE_UNAVAILABLE_MESSAGE = "Stockage local indisponible — utilisation connectée uniquement.";
  const networkError = error => error instanceof TypeError || error?.name === "TimeoutError";
  const connectivity = createConnectivityMonitor({ root, documentTarget: root.document, fetchImpl: networkFetch,
    contextKey: connectivityContextKey,
    onState: detail => {
      root.dispatchEvent(new CustomEvent("digues-app:network-state", { detail }));
      if (detail.status === "online" && noticeDiagnosis?.startsWith("offline:")) {
        status("Serveur accessible — consultation connectée.");
      }
    },
    onReconnect: detail => {
      if (!canCoordinateReconnect(detail, businessRuntime)) return;
      root.dispatchEvent(new CustomEvent("digues-app:backend-reconnected", { detail: {
        ...detail, previousStatus: "offline", contextKey: connectivityContextKey,
        generation: businessRuntime?.generation?.() ?? null,
      } }));
    } });
  const detachConnectivityObserver = businessRuntime?.setConnectivityObserver?.({
    start: observation => connectivity.observeStart(observation),
    result: (handle, observation) => connectivity.observeResult(handle, observation),
  }) || (() => {});
  const isOffline = () => connectivity.getState().status === "offline";
  function dialogStatus(message) {
    const element = document.querySelector("#offline-status");
    if (element) element.textContent = message;
  }
  function projectionErrorMessage() {
    return `Projection à actualiser (${projectionError.code}) : ${projectionError.message}`;
  }
  function noticeKey() {
    if (disabled) return `storage-unavailable:${isOffline()}`;
    if (projectionError) return `projection:${isOffline()}:${projectionError.code}:${projectionError.message}`;
    return isOffline() ? `offline:${offlineScopeDiagnosis}` : null;
  }
  function noticeStatus(message) {
    let panel = document.querySelector(".offline-status-panel");
    if (!panel) {
      panel = document.createElement("div"); panel.className = "offline-status-panel";
      const notice = document.createElement("p"); notice.id = "offline-notice";
      notice.setAttribute("role", "status");
      const close = document.createElement("button"); close.type = "button";
      close.className = "offline-status-close icon-button";
      close.setAttribute("aria-label", "Fermer l’information hors ligne"); close.textContent = "×";
      close.addEventListener("click", () => {
        noticeDismissed = true;
        panel.hidden = true;
        document.querySelector("#open-business-session")?.focus({ preventScroll: true });
      });
      panel.append(notice, close);
      (document.querySelector(".header-notifications") || document.querySelector("main") || document.body).append(panel);
    }
    const diagnosis = noticeKey();
    if (diagnosis !== noticeDiagnosis) {
      noticeDiagnosis = diagnosis;
      noticeDismissed = false;
    }
    const notice = panel.querySelector("#offline-notice");
    if (notice.textContent !== message) notice.textContent = message;
    panel.hidden = !diagnosis || noticeDismissed;
  }
  function status(message) {
    dialogStatus(message);
    noticeStatus(disabled ? STORAGE_UNAVAILABLE_MESSAGE : projectionError ? projectionErrorMessage() : message);
  }
  const scopeStatus = ({ scope, records }) => scope
    ? `Périmètre local disponible : ${scope.tronconIds.length} tronçon(s), ${records.length} objet(s).`
    : "Aucun périmètre préparé";
  function showOfflineStatus(view) {
    if (disabled) return status(STORAGE_UNAVAILABLE_MESSAGE);
    if (projectionError) return status(projectionErrorMessage());
    offlineScopeDiagnosis = view.scope ? "prepared" : "empty";
    noticeStatus(view.scope ? OFFLINE_PREPARED_MESSAGE : OFFLINE_EMPTY_MESSAGE);
    dialogStatus(scopeStatus(view));
  }
  function persistenceFailed(error) {
    disabled = true;
    projectionError = { code: "storage-error", message: error.message };
    status(STORAGE_UNAVAILABLE_MESSAGE);
    console.error("Projection locale indisponible (aucune suppression automatique)", error);
  }
  async function inspect() {
    if (disabled) return { scope: null, records: [] };
    try { return await store.inspect(); }
    catch (error) { persistenceFailed(error); return { scope: null, records: [] }; }
  }
  async function consultation() {
    const view = await inspect();
    if (disabled) return view;
    const creations = (await store.listOperations()).filter(o => o.resource === "observations" && o.action === "create");
    const ids = new Set(creations.map(o => o.resourceId));
    const base = [...view.records.filter(r => r.resource !== "observations" || !ids.has(r.id)),
      ...creations.map(root.LocalProjection.localCreation)];
    const known = new Set(base.filter(r => r.resource === "observations").map(r => r.id));
    for (const operation of (await store.listOperations()).filter(o => o.resource === "photos" && o.action === "create")) {
      const context = root.LocalProjection.photoObservationContext(operation);
      if (context && !known.has(context.id)) { base.push(context); known.add(context.id); }
    }
    return { ...view, records: base };
  }
  async function preparedParent(parent) {
    if (!["desordres", "troncons"].includes(parent?.resource)) return false;
    const { records, scope } = await inspect();
    return !!scope && records.some(r => r.resource === parent.resource && r.id === parent.id && !r.detached);
  }
  const belongsTo = (row, resource, id) => row.resource === "observations"
    && (attributes(row).parent?.canonical_resource || attributes(row).parent?.resource) === resource
    && attributes(row).parent.id === id;
  async function refreshScopeStatus() {
    const element = document.querySelector("#offline-status");
    if (!element || disabled) return;
    const view = await inspect();
    if (disabled) return;
    element.textContent = scopeStatus(view);
  }
  async function request(url, options = {}) {
    // Actual network failure, not navigator.onLine, determines fallback.
    const evidence = connectivity.beginEvidence();
    try {
      const response = await networkFetch(url, { ...options, connectivityObservation: false,
        signal: options.signal || AbortSignal.timeout(12000) });
      if (response.status >= 500 || [408, 429].includes(response.status)) void connectivity.check(`http-${response.status}`);
      else connectivity.confirmOnline(evidence, `http-${response.status}`);
      return response;
    } catch (error) {
      if (networkError(error)) connectivity.confirmOffline(evidence,
        error?.name === "TimeoutError" ? "timeout" : "transport-error");
      throw error;
    }
  }
  async function snapshot(ids) {
    const query = new URLSearchParams(ids.map(id => ["troncon_id", id]));
    const response = await request(`/api/offline-scope?${query}`, { cache: "no-store" });
    const value = await response.json();
    if (!response.ok) throw Object.assign(new Error(value.detail?.message || value.detail || `Préparation impossible (${response.status})`),
      { code: value.detail?.code === "scope-too-large" ? "scope-too-large" : "http-error", status: response.status });
    return value;
  }
  function exclusive(work) {
    // Web Locks avoids identical reads between tabs. IDB CAS remains the safety
    // boundary when Web Locks is unavailable (or a page closes during a request).
    const run = () => root.navigator.locks
      ? root.navigator.locks.request(root.BusinessRuntime?.coordinationKey("field-projection") || "digues-app:field-projection", work) : work();
    const result = serial.then(run);
    serial = result.catch(() => {});
    return result;
  }
  async function prepare(ids) {
    if (root.BusinessRuntime && !root.BusinessRuntime.can('offline.prepare')) throw new Error('Préparation hors ligne non autorisée.');
    if (!Array.isArray(ids) || !ids.length || ids.length > 20) {
      throw new Error("Sélectionnez de 1 à 20 tronçons dans Patrimoine.");
    }
    return exclusive(async () => {
      const previous = await inspect();
      if (disabled) throw new Error("Stockage local indisponible");
      const value = await snapshot(ids);
      let applied;
      try {
        applied = await store.replace(value, previous.scope?.revision || null);
      } catch (error) { if (error.code !== "pending-operations") persistenceFailed(error); throw error; }
      if (!applied) throw new Error("Le périmètre a changé dans un autre onglet ; recommencez.");
      projectionError = null;
      status(`Périmètre conservé : ${ids.length} tronçon(s), ${value.records.length} objets. Observations, Photos en préparation et modifications textuelles disponibles localement.`);
      return value;
    });
  }
  async function integrate(entry) {
    return exclusive(async () => {
      const current = await inspect();
      if (!current.scope || disabled || BigInt(entry.cursor) <= BigInt(current.scope.cursor)) return;
      // The journal is ordered; refresh the bounded graph in one authoritative
      // snapshot, including new/deleted/moved children and relational labels.
      try {
        await store.markStale();
      } catch (error) { persistenceFailed(error); return; }
      let value;
      try { value = await snapshot(current.scope.tronconIds); }
      catch (error) {
        projectionError = { code: networkError(error) ? "network-error" : error.code || "http-error", message: error.message };
        status(projectionErrorMessage());
        // A too-large scope must not prevent journal/UI delivery. Its durable
        // cursor remains unchanged; only pinned intentions may recover separately.
        if (projectionError.code === "scope-too-large") return;
        throw error;
      }
      if (!root.LocalProjection.validCursor(value.cursor)) {
        persistenceFailed(new Error("Curseur d’instantané invalide")); return;
      }
      if (BigInt(value.cursor) < BigInt(entry.cursor)) throw new Error("Instantané antérieur à l’événement");
      let applied;
      try {
        applied = await store.replace(value, current.scope.revision);
      } catch (error) {
        persistenceFailed(error); return;
      }
      if (!applied) {
        // CAS failed: another tab may have cleared/reprepared or integrated it.
        const latest = await inspect();
        if (latest.scope && BigInt(latest.scope.cursor) < BigInt(entry.cursor)) {
          throw new Error("Projection modifiée simultanément ; reprise du journal nécessaire");
        }
      }
      projectionError = null;
      status("Projection locale actualisée.");
      root.ResourceMutationController?.retryInvalidations();
    });
  }
  async function recoverPinned(operation, response) {
    if (disabled || (projectionError?.code !== "scope-too-large" && !(operation.action === "create" && response?.mutation))) {
      throw new Error("Projection non intégrée : replay différé.");
    }
    return exclusive(async () => {
      const result = await request(`/api/offline-scope/pinned/${operation.resource}/${encodeURIComponent(operation.resourceId)}`, { cache: "no-store" });
      if (!result.ok) throw new Error(`Récupération ciblée impossible (${result.status})`);
      const recovery = await result.json();
      const row = recovery.records?.find(row => row.resource === operation.resource && row.id === operation.resourceId);
      const confirmedVersion = response?.mutation?.versions?.[operation.resourceId];
      if (confirmedVersion && row && row.serverVersion < confirmedVersion) {
        throw new Error("Récupération antérieure au reçu autoritaire");
      }
      if (operation.action === "create" && confirmedVersion && (!row
          || row.payload.parent?.id !== operation.changes.parent.id
          || row.payload.parent?.resource !== operation.changes.parent.resource)) {
        throw new Error("Création confirmée introuvable ou parent incohérent : intention conservée.");
      }
      try { await store.integratePinned(operation.operationId, recovery); }
      catch (error) { persistenceFailed(error); throw error; }
      // Missing on the authoritative server is also meaningful (future delete).
      // Neither this read nor an HTTP receipt advances the whole-scope cursor.
      return row || null;
    });
  }
  const attributes = row => row.payload.properties || row.payload;
  function tree(records, scope) {
    const trunks = records.filter(r => r.resource === "troncons" && scope.tronconIds.includes(r.id)).map(attributes);
    const digues = records.filter(r => r.resource === "digues").map(r => ({ ...attributes(r),
      troncons: trunks.filter(t => t.digue_id === r.id) })).filter(d => d.troncons.length);
    const systemes = records.filter(r => r.resource === "systemes").map(r => ({ ...attributes(r),
      digues: digues.filter(d => d.systeme_endiguement_id === r.id) })).filter(s => s.digues.length);
    const represented = new Set(systemes.flatMap(s => s.digues.flatMap(d => d.troncons.map(t => t.id))));
    // An AH-only root or a Digue without SE is still prepared. No synthetic
    // parent identity/version is introduced into the offline business graph.
    return { systemes, troncons_hors_se: trunks.filter(t => !represented.has(t.id)) };
  }
  function home(records, query) {
    const hidden = new Set(query.hidden_troncon_ids || []);
    const byId = (resource, id) => records.find(r => r.resource === resource && r.id === id);
    const rows = [];
    const disorders = records.filter(r => r.resource === "desordres").sort((a, b) => {
      const left = attributes(a).designation || "", right = attributes(b).designation || "";
      return left < right ? -1 : left > right ? 1 : a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
    });
    for (const disorder of disorders) {
      const entities = [{ class: "desordres", id: disorder.id, attributes: attributes(disorder) }];
      const related = attributes(disorder).troncon_ids || [];
      // Same predicate as data_views._patrimoine_filter_sql: exclude if ANY
      // related trunk is hidden; keep all authoritative relational labels.
      if (related.some(id => hidden.has(id))) continue;
      for (const id of related) {
        const troncon = byId("troncons", id);
        const digue = troncon && byId("digues", attributes(troncon).digue_id);
        const systeme = digue && byId("systemes", attributes(digue).systeme_endiguement_id);
        for (const row of [systeme, digue, troncon]) if (row && !entities.some(e => e.id === row.id && e.class === row.resource)) {
          entities.push({ class: row.resource, id: row.id, attributes: attributes(row) });
        }
      }
      rows.push({ entities, values: ["systemes", "digues", "troncons", "desordres"].map(resource =>
        [...new Set(entities.filter(e => e.class === resource)
          .map(e => resource === "desordres" ? e.attributes.designation : e.attributes.libelle)
          .filter(value => value != null))].sort().join(" · ")) });
    }
    const offset = query.offset || 0, size = query.page_size || 500;
    return { root: "desordres", granularity: "desordres", columns: DefaultView.columns(),
      rows: rows.slice(offset, offset + size), total: rows.length, offset,
      page_size: size, has_previous: offset > 0, has_next: offset + size < rows.length };
  }
  async function local(url, options, preparedView = null) {
    const { records: allRecords, scope } = preparedView || await consultation();
    // A Photo intent can retain a minimal Observation label outside the scope.
    // It is inspectable in the local detail, never an authoritative GET fallback.
    const records = allRecords.filter(row => !row.detached && row.source !== "photo-context");
    if (!scope) throw new Error("Hors connexion : aucun périmètre préparé sur cet appareil.");
    const path = new URL(url, location.href).pathname;
    if (path === "/api/systemes-endiguement") return tree(records, scope);
    if (["/api/troncons", "/api/desordres"].includes(path)) {
      const resource = path.split("/")[2];
      return { type: "FeatureCollection", features: records.filter(r => r.resource === resource
        && (resource !== "troncons" || scope.tronconIds.includes(r.id))).map(r => r.payload) };
    }
    if (path === "/api/data/desordres/query") return home(records, JSON.parse(options.body || "{}"));
    if (path === "/api/data/view-catalog") return DefaultView.catalog();
    if (path === "/api/data/view-query") {
      const query = JSON.parse(options.body || "{}");
      if (!DefaultView.matches(query)) {
        throw new Error("Les vues personnalisées nécessitent le serveur ; seule la vue initiale des désordres est disponible hors connexion.");
      }
      if (!Number.isSafeInteger(query.offset ?? 0) || (query.offset ?? 0) < 0
          || !Number.isSafeInteger(query.page_size ?? 500) || (query.page_size ?? 500) < 1
          || (query.page_size ?? 500) > 2000) throw new Error("Pagination de vue invalide.");
      return home(records, query);
    }
    const match = path.match(/^\/api\/(systemes-endiguement|digues|troncons|desordres|observations)\/([^/]+)(\/observations)?$/);
    if (match) {
      const resource = match[1] === "systemes-endiguement" ? "systemes" : match[1];
      const id = decodeURIComponent(match[2]);
      const row = records.find(r => r.resource === resource && r.id === id);
      if (row) {
        if (match[3]) return { parent: { resource, id },
          observations: records.filter(r => belongsTo(r, resource, id)).map(r => r.payload) };
        return row.payload;
      }
    }
    throw new Error("Indisponible hors connexion : donnée non préparée ou fonctionnalité non prise en charge (vues SQL, référentiels, fichiers Photo serveur non conservés sur cet appareil). ");
  }
  async function fetchRead(url, options = {}) {
    const method = (options.method || "GET").toUpperCase();
    const localObservation = method === "GET" && url.match(/^\/api\/observations\/([^/?]+)$/);
    if (localObservation && !disabled) {
      try {
        const operation = (await store.listOperations()).find(o => o.resource === "observations" && o.action === "create"
          && o.resourceId === decodeURIComponent(localObservation[1]));
        if (operation) return new Response(JSON.stringify(root.LocalProjection.localCreation(operation).payload), {
          headers: { "Content-Type": "application/json", "X-Digues-App-Local-Projection": "1", "X-Digues-App-Local-Creation": "1" },
        });
      } catch (error) { persistenceFailed(error); }
    }
    const business = /^\/api\/(systemes-endiguement|digues|troncons|desordres|observations)(\/|$)/.test(url);
    const dataRead = url === "/api/data/view-catalog" || url === "/api/data/desordres/query"
      || url === "/api/data/view-query";
    const read = method === "GET" || (method === "POST"
      && ["/api/data/desordres/query", "/api/data/view-query"].includes(url));
    if (!read) {
      try {
        if (url === "/api/data/view-query") await root.ResourceEventTransport?.ensureReadBaseline();
        return await networkFetch(url, options);
      }
      catch (error) {
        if (!networkError(error)) throw error;
        if (!business && url !== "/api/data/view-query") throw error;
        throw new Error(url === "/api/data/view-query"
          ? "Les vues personnalisées nécessitent le serveur ; elles ne sont pas disponibles hors connexion."
          : "Réseau indisponible : enregistrement impossible. Conservez ce brouillon dans cet onglet et réessayez en ligne.");
      }
    }
    const wasOffline = isOffline();
    const readEvidence = connectivity.beginEvidence();
    try {
      // After a startup without network/head, acquire the journal baseline
      // BEFORE the first successful business HTTP read, not afterwards.
      if (business || dataRead) await root.ResourceEventTransport?.ensureReadBaseline();
      const response = await request(url, options);
      // HTTP errors never authorize a local fallback. Transient server errors
      // are classified separately by the authoritative connectivity probe.
      if (wasOffline && !isOffline()) {
        status(disabled ? STORAGE_UNAVAILABLE_MESSAGE : projectionError ? projectionErrorMessage()
          : response.ok ? "Serveur accessible — consultation connectée."
          : `Serveur accessible — erreur HTTP ${response.status}, sans recours au cache local.`);
      }
      return response;
    } catch (error) {
      if (!networkError(error)) throw error;
      if (!business && !dataRead) throw error;
      if (!isOffline()) connectivity.confirmOffline(readEvidence,
        error?.name === "TimeoutError" ? "timeout" : "transport-error");
      const view = await consultation();
      showOfflineStatus(view);
      const payload = await local(url, options, view);
      return new Response(JSON.stringify(payload), { headers: { "Content-Type": "application/json", "X-Digues-App-Local-Projection": "1" } });
    }
  }
  async function showDetail(resource, id) {
    const sequence = ++detailSequence;
    const { records, observationReferences } = await consultation();
    if (sequence !== detailSequence) return;
    const row = records.find(value => value.resource === resource && value.id === String(id));
    if (!row) throw new Error("Fiche non préparée hors connexion");
    let dialog = document.querySelector("#offline-detail");
    if (!dialog) { dialog = document.createElement("dialog"); dialog.id = "offline-detail"; document.body.append(dialog); }
    dialog.dataset.resource = resource; dialog.dataset.id = String(id);
    dialog.dataset.localCreate = String(row.source === "local-create");
    dialog.replaceChildren();
    const title = document.createElement("h2"); title.textContent = row.source === "local-create"
      ? `Observation enregistrée localement — ${root.OfflineOperations.statusLabel(row.payload.localStatus)}` : row.detached
      ? "Objet sorti du périmètre serveur — état antérieur conservé pour une intention locale"
      : row.source === "photo-context" ? "Contexte Observation conservé pour une Photo locale — relire le serveur avant toute écriture"
      : "État serveur conservé localement"; dialog.append(title);
    const list = document.createElement("dl");
    for (const [key, value] of Object.entries(attributes(row))) {
      if (value == null || typeof value === "object") continue;
      const label = document.createElement("dt"), content = document.createElement("dd");
      label.textContent = key; content.textContent = String(value); list.append(label, content);
    }
    if (resource === "desordres") {
      const labels = (attributes(row).troncon_ids || []).map(tronconId => {
        const trunk = records.find(value => value.resource === "troncons" && value.id === tronconId);
        if (!trunk) return "Rattachement non préparé";
        const value = attributes(trunk);
        return [value.systeme_endiguement_libelle, value.digue_libelle, value.libelle].join(" / ");
      });
      const label = document.createElement("dt"), content = document.createElement("dd");
      label.textContent = "Système / Digue / Tronçon"; content.textContent = labels.join(" · ");
      list.append(label, content);
    }
    dialog.append(list);
    if (attributes(row).parent) {
      const context = document.createElement("p");
      const parent = attributes(row).parent;
      context.textContent = `Rattachée à : ${parent.resource} — ${parent.designation || parent.id}`;
      dialog.append(context);
    }
    if (["desordres", "troncons"].includes(resource) && !row.detached && (!root.BusinessRuntime || (root.BusinessRuntime.can('business.create') && root.BusinessRuntime.can('offline.write')))) {
      const add = document.createElement("button"); add.textContent = "+ Observation";
      add.onclick = () => root.ObservationCreate.open({ resource, id: row.id, designation: attributes(row).designation || attributes(row).libelle });
      dialog.append(add);
    }
    if (row.source === "local-create") {
      if (row.editable) {
        const edit = document.createElement("button"); edit.textContent = "Modifier la création locale";
        edit.onclick = () => root.ObservationCreate.edit({ resourceId: row.id, operationId: row.operationId,
          parentContext: row.payload.parent, changes: row.payload }); dialog.append(edit);
        const abandon = document.createElement("button"); abandon.textContent = "Abandonner la création locale";
        abandon.onclick = async () => {
          const children = (await store.listOperations()).filter(o => o.resource === "photos"
            && o.dependsOn?.[0]?.resource === "observations" && o.dependsOn[0].id === row.id);
          const question = children.length
            ? `Abandonner cette observation et ses ${children.length} Photo(s) locales en préparation ? Leurs fichiers et métadonnées locales seront supprimés.`
            : "Abandonner cette observation jamais envoyée au serveur ?";
          if (!confirm(question)) return;
          if (children.length) await store.discardObservationWithPhotos(row.operationId, children.map(o => o.operationId));
          else await store.removeOperation(row.operationId, true);
          dialog.close();
        }; dialog.append(abandon);
      }
    }
    if (resource === "observations" && root.PhotoOffline) {
      const gallery = document.createElement("section"); gallery.className = "offline-photo-gallery"; dialog.append(gallery);
      await root.PhotoOffline.mount(gallery, attributes(row));
    }
    const fields = root.OfflineOperations?.FIELDS[resource];
    if (attributes(row).offline_read_only) {
      const notice = document.createElement("p");
      notice.textContent = "Parent historique typé : consultation disponible ; édition avec connexion requise.";
      dialog.append(notice);
    }
    if (fields && !attributes(row).offline_read_only && !row.detached && row.source !== "local-create" && row.source !== "photo-context"
        && (!root.BusinessRuntime || (root.BusinessRuntime.can('business.update') && root.BusinessRuntime.can('offline.write')))) {
      let existing = null;
      if(resource === "observations") existing=(await store.listOperations()).find(operation=>
        operation.resource===resource && operation.resourceId===String(id) && operation.action==="update") || null;
      if(sequence!==detailSequence)return;
      const draftValues={...attributes(row),...existing?.changes};
      const descriptor=resource === "observations" ? root.ObservationForm.composition(draftValues)
        : root.EntityFormView.fromFields({classId:resource,singular:"Attributs hors ligne"},
          fields.map(key=>[key,key,"textarea",true]));
      const view=root.EntityFormView.create({root,document,descriptor,id:"offline-attributes",onClose:()=>dialog.close(),onCancel:()=>dialog.close()});
      const form=view.form; form.id="offline-attributes-form";
      title.remove();
      view.sync({mode:"edit",objectId:String(id)});
      view.setSectionNotice("identification", "Enregistrer conserve une intention locale jusqu’à confirmation du serveur.");
      let controls;
      if(resource === "observations") {
        controls=new Map();
        for(const [key,label,kind,,catalog,labelKey] of root.ObservationForm.fields(draftValues)) {
          const control=view.controls.get(key);
          if(kind==="select") root.ReferenceData.fillSelect(control,observationReferences?.[catalog]||[],draftValues[key],`Sans ${label.toLowerCase()}`,
            existing?.changes && Object.hasOwn(existing.changes,key) && existing.changes[key]!==attributes(row)[key] ? null:draftValues[labelKey]);
          else control.value=draftValues[key]??"";
          controls.set(key,control);
        }
        view.controls.get("parent_label").value=root.ObservationForm.parentText(draftValues);
        if (!observationReferences) view.setSectionNotice("classification", "Référentiels incomplets. Repréparez le périmètre pour modifier la suite ou l’urgence.", { warning: true });
      } else {
        controls=Object.fromEntries(view.controls);
        for(const key of fields)controls[key].value=draftValues[key]||"";
      }
      const save=view.save,feedback=view.status;
      if (existing && (existing.attempts > 0 || existing.status !== "pending")) {
        form.querySelectorAll("input, select, textarea").forEach(control => { control.disabled = true; });
        save.disabled = true;
        feedback.textContent = "Intention déjà tentée : saisie conservée pour réconciliation, sans modification automatique.";
      }
      form.onsubmit = async event => {
        event.preventDefault(); if (save.disabled) return; save.disabled = true;
        try {
          const changes = resource === "observations" ? root.ObservationForm.changes(controls, attributes(row))
            : Object.fromEntries(Object.entries(controls).map(([key, input]) => [key, input.value.trim() || null])
              .filter(([key, value]) => value !== (attributes(row)[key] ?? null)));
          if (!Object.keys(changes).length) {
            if (existing) {
              await store.removeOperation(existing.operationId, true);
              existing = null;
              feedback.textContent = "Intention locale non envoyée annulée.";
            } else feedback.textContent = "Aucune modification.";
            return;
          }
          let result;
          if (existing) {
            existing = await store.editUpdate(existing.operationId, changes);
            await root.OfflineOperationsRuntime.synchronize(true);
            const current = (await store.listOperations()).find(operation => operation.operationId === existing.operationId);
            result = { status: current?.status || "synced" };
          } else result = await root.OfflineOperationsRuntime.submit({ resource, resourceId: String(id),
            baseVersion: row.serverVersion, changes });
          feedback.textContent = result.status === "synced" ? "Synchronisé — état confirmé par le serveur. Rouvrez la fiche pour le consulter."
            : `Enregistré localement — ${root.OfflineOperations.statusLabel(result.status)}. Intention conservée dans Outils → Consultation hors ligne.`;
        } catch (error) { feedback.textContent = error.message; }
        finally { save.disabled = false; }
      };
      dialog.append(view.element);
    }
    renderObservations(dialog, records, resource, id);
    if(!dialog.querySelector(".entity-form")) { const close = document.createElement("button"); close.textContent = "Fermer"; close.onclick = () => dialog.close(); dialog.append(close); }
    dialog.showModal();
  }
  function renderObservations(dialog, records, resource, id) {
    let list = dialog.querySelector(".offline-observation-list");
    if (!list) { list = document.createElement("section"); list.className = "offline-observation-list"; dialog.append(list); }
    list.replaceChildren();
    records.filter(r => belongsTo(r, resource, String(id))).forEach(observation => {
      const button = document.createElement("button"); button.textContent = `Observation : ${attributes(observation).designation || observation.id}${observation.source === "local-create" ? " — en attente de synchronisation" : ""}`;
      button.onclick = () => { dialog.close(); void showDetail("observations", observation.id); }; list.append(button);
    });
  }
  async function showParent(parent) {
    const dialog = document.querySelector("#offline-detail");
    if (dialog?.open && dialog.dataset.resource === parent.resource && dialog.dataset.id === parent.id) {
      const { records } = await consultation();
      // Refresh only children: a parent text draft may predate + Observation.
      if (dialog.open && dialog.dataset.resource === parent.resource && dialog.dataset.id === parent.id) {
        renderObservations(dialog, records, parent.resource, parent.id);
      }
    } else await showDetail(parent.resource, parent.id);
  }
  async function consultIfOffline(resource, id) {
    const { records } = await consultation();
    if (records.some(r => r.resource === resource && r.id === String(id) && r.source === "local-create")) {
      await showDetail(resource, id); return true;
    }
    const prepared = records.some(row => row.resource === resource && row.id === String(id));
    if (!prepared && !isOffline()) return false;
    try {
      if (!isOffline()) {
        // Prepared maps can lose the backend while already open, without any
        // navigator.onLine change. Test the authoritative detail, not the hint.
        const response = await fetchRead(`/api/${resource}/${encodeURIComponent(id)}`);
        if (!response.headers.has("X-Digues-App-Local-Projection")) return false;
      }
      await showDetail(resource, id);
    } catch (error) { status(error.message); }
    return true;
  }
  async function refreshConfirmed() {
    const dialog = document.querySelector("#offline-detail");
    if (!dialog?.open || dialog.dataset.localCreate !== "true" || document.querySelector("#observation-create-dialog")?.open) return;
    const id = dialog.dataset.id;
    if ((await store.listOperations()).some(o => o.resourceId === id)) return;
    const row = await store.read("observations", id);
    if (dialog.open && dialog.dataset.id === id && dialog.dataset.localCreate === "true" && row) {
      await showDetail("observations", id);
    }
  }
  root.OfflineRuntime = { store, inspect, consultation, preparedParent, prepare, integrate, recoverPinned, fetch: fetchRead, networkError,
    getProjectionError: () => projectionError,
    isOffline, getNetworkState: () => connectivity.getState(), checkConnectivity: reason => connectivity.check(reason),
    showDetail, showParent, consultIfOffline, attachHeritage: controller => { heritage = controller; },
    refreshScopeStatus, refreshConfirmed,
    initialCursor: async () => (await inspect()).scope?.cursor || null,
  };
  document.querySelector("#prepare-offline")?.addEventListener("click", async event => {
    event.currentTarget.disabled = true;
    status("Préparation du périmètre en cours…");
    try { await prepare(heritage?.getSelection().tronconIds); }
    catch (error) { status(error.message); }
    finally { document.querySelector("#prepare-offline").disabled = false; }
  });
  document.querySelector("#clear-offline")?.addEventListener("click", async () => {
    if (!confirm("Supprimer le périmètre local ? Aucune donnée serveur ni aucun brouillon ne sera supprimé.")) return;
    try {
      await exclusive(() => store.clear());
      disabled = false;
      projectionError = null;
      dialogStatus("Périmètre local supprimé.");
      offlineScopeDiagnosis = "empty";
      noticeStatus(isOffline() ? OFFLINE_EMPTY_MESSAGE : "Périmètre local supprimé.");
    }
    catch (error) { if (error.code === "pending-operations") status(error.message); else persistenceFailed(error); }
  });
  const modal = document.querySelector("#offline-consultation-modal");
  const closeModalButton = document.querySelector("#close-offline-consultation-modal");
  let returnFocusTarget = null;
  function closeModal() {
    if (!modal || modal.hidden) return;
    modal.hidden = true;
    const target = returnFocusTarget;
    returnFocusTarget = null;
    target?.focus();
  }
  function openModal(trigger) {
    if (!modal) return;
    returnFocusTarget = trigger.closest(".tools-menu")?.querySelector("#toggle-tools-menu") || trigger;
    modal.hidden = false;
    void refreshScopeStatus();
    root.dispatchEvent(new CustomEvent("digues-app:offline-dialog-open"));
    closeModalButton?.focus();
  }
  document.querySelectorAll('[data-tool-action="offline-consultation"]').forEach((trigger) => {
    trigger.addEventListener("click", () => openModal(trigger));
  });
  closeModalButton?.addEventListener("click", closeModal);
  modal?.addEventListener("click", (event) => {
    if (event.target === modal) closeModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && modal && !modal.hidden) closeModal();
  });
  root.addEventListener("digues-app:context-closed", () => {
    detachConnectivityObserver();
    connectivity.stop();
  }, { once: true });
  void connectivity.start();
  void inspect().then(({ scope }) => {
    if (!disabled && !isOffline()) status(scope ? `Périmètre local disponible : ${scope.tronconIds.length} tronçon(s).` : "Aucun périmètre préparé");
  });
  }
  return { canCoordinateReconnect, createConnectivityMonitor, mount };
});
