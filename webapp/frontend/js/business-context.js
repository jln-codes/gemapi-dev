(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.BusinessContext = api;
})(globalThis, function () {
  "use strict";
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const SLUG = /^[a-z0-9][a-z0-9_-]{0,62}$/;
  const URL_MODES = new Set(["minimal", "position", "object"]);
  function resolveDatabaseId(catalog, selection) {
    if (typeof selection !== "string" || !SLUG.test(selection)) throw new Error("Sélection de base invalide.");
    // Slugs win if a transitional UUID happens to be a valid slug too.
    const bySlug = catalog.databases.find(item => item.slug === selection);
    const base = bySlug || (UUID.test(selection)
      ? catalog.databases.find(item => item.business_database_id === selection) : null);
    if (!base) throw new Error("Base non autorisée.");
    return base.business_database_id;
  }
  function createContext(catalog, databaseId) {
    const base = catalog.databases.find(item => item.business_database_id === databaseId);
    if (!base || base.status !== "active" || !base.permissions.includes("business.read")) throw new Error("Base non autorisée.");
    if (typeof base.slug !== "string" || !SLUG.test(base.slug)) throw new Error("Slug de base invalide.");
    const identity = [catalog.instance_id, catalog.user_id, base.business_database_id, base.data_epoch];
    if (!identity.every(value => typeof value === "string" && UUID.test(value))) throw new Error("Identité de contexte invalide.");
    return Object.freeze({ instance_id: identity[0], user_id: identity[1], business_database_id: identity[2],
      slug: base.slug, data_epoch: identity[3], key: identity.join(":"), permissions: Object.freeze([...base.permissions]),
      display_name: base.display_name, structure_name: base.structure_name,
      role_code: base.role_code });
  }
  function createRuntime({ context = null, legacy = false, fetchImpl, origin, onUnauthorized = () => {}, isCurrent = () => true,
    storage = globalThis.localStorage }) {
    if (!legacy && !context) throw new Error("Contexte métier requis.");
    if (legacy && context) throw new Error("Modes exclusifs.");
    let active = true, generation = 1;
    let connectivityObserver = null;
    const controller = new AbortController();
    const objectUrls = new Set();
    const connectivityContextKey = legacy ? "legacy" : context.key;
    const key = name => legacy ? name : `${name}:${context.key}`;
    const urlModeKey = legacy ? null : `digues-app:url-content-v1:${context.instance_id}:${context.user_id}`;
    function urlMode() {
      try {
        const value = urlModeKey && storage?.getItem(urlModeKey);
        return URL_MODES.has(value) ? value : "position";
      } catch (_) { return "position"; }
    }
    function setUrlMode(mode) {
      if (!URL_MODES.has(mode)) throw new Error("Mode d’adresse invalide.");
      if (!urlModeKey || !storage) throw new Error("Préférence indisponible dans ce mode.");
      storage.setItem(urlModeKey, mode);
      if (typeof globalThis.dispatchEvent === "function" && typeof globalThis.CustomEvent === "function") {
        globalThis.dispatchEvent(new CustomEvent("digues-app:url-mode-changed", { detail: { mode } }));
      }
    }
    function observeStart(observer, metadata) {
      try { return observer?.start?.(Object.freeze(metadata)) ?? null; }
      catch (_) { return null; }
    }
    function observeResult(observer, handle, result) {
      try { observer?.result?.(handle, Object.freeze(result)); }
      catch (_) { /* Connectivity diagnostics must never alter the request contract. */ }
    }
    function assertActive() {
      if (!active || !isCurrent()) throw new DOMException("Contexte fermé", "AbortError");
    }
    function apiUrl(input) {
      assertActive();
      const url = new URL(input, origin);
      if (url.origin !== origin || !url.pathname.startsWith("/api/")
          || url.pathname.startsWith("/api/auth/") || ["/api/config", "/api/health"].includes(url.pathname)) return input;
      if (legacy) {
        if (url.pathname.startsWith("/api/bases/")) throw new Error("Route multi-base interdite en legacy.");
        return input;
      }
      const prefix = `/api/bases/${context.business_database_id}`;
      if (url.pathname.startsWith("/api/bases/")) {
        if (!url.pathname.startsWith(prefix + "/")) throw new Error("URL d’une autre base refusée.");
      } else url.pathname = prefix + url.pathname.slice(4);
      url.searchParams.set("context_user_id", context.user_id);
      url.searchParams.set("context_data_epoch", context.data_epoch);
      url.searchParams.set("context_instance_id", context.instance_id);
      return url.pathname + url.search;
    }
    async function fetchScoped(input, options = {}) {
      const captured = generation;
      let url = apiUrl(input);
      if (options.offlineWrite && !legacy) {
        const target = new URL(url, origin); target.searchParams.set('context_offline_write', '1');
        url = target.pathname + target.search;
      }
      const headers = new Headers(options.headers);
      const target = new URL(url, origin);
      const sameOrigin = target.origin === origin;
      if (sameOrigin && !["GET", "HEAD"].includes((options.method || "GET").toUpperCase())) headers.set("X-Digues-CSRF", "1");
      const signal = options.signal ? AbortSignal.any([options.signal, controller.signal]) : controller.signal;
      const requestOptions = { ...options, headers, signal, credentials: "same-origin" };
      delete requestOptions.connectivityObservation;
      const observer = options.connectivityObservation === false ? null : connectivityObserver;
      const startedAt = Date.now();
      const metadata = {
        contextKey: connectivityContextKey, generation: captured, startedAt,
        method: (options.method || "GET").toUpperCase(), pathname: target.pathname,
        scope: sameOrigin && target.pathname.startsWith("/api/")
          && !target.pathname.startsWith("/api/auth/")
          && !["/api/config", "/api/health"].includes(target.pathname) ? "business" : "peripheral",
      };
      const handle = observeStart(observer, metadata);
      let observed = false;
      try {
        const response = await fetchImpl(url, requestOptions);
        assertActive();
        if (captured !== generation) throw new DOMException("Réponse d’un ancien contexte", "AbortError");
        observeResult(observer, handle, {
          ...metadata, outcome: "response", status: response.status,
          completedAt: Date.now(),
        });
        observed = true;
        if (!legacy && sameOrigin && response.status === 401) { onUnauthorized(); throw new DOMException("Session expirée", "AbortError"); }
        for (const method of ['json', 'text', 'blob', 'arrayBuffer']) {
          const read = response[method].bind(response);
          response[method] = async (...args) => { const result = await read(...args); assertActive(); return result; };
        }
        return response;
      } catch (error) {
        if (!observed) observeResult(observer, handle, {
          ...metadata,
          outcome: error?.name === "TimeoutError" ? "timeout"
            : error?.name === "AbortError" ? "abort"
              : error instanceof TypeError ? "transport-error" : "error",
          completedAt: Date.now(),
        });
        throw error;
      }
    }
    function setConnectivityObserver(observer) {
      if (observer !== null && (typeof observer !== "object"
          || typeof observer.start !== "function" || typeof observer.result !== "function")) {
        throw new TypeError("Observateur de connectivité invalide");
      }
      connectivityObserver = observer;
      return () => { if (connectivityObserver === observer) connectivityObserver = null; };
    }
    function pageUrl(path) {
      const url = new URL(path, origin);
      if (!legacy) url.searchParams.set("base", context.slug);
      return url.pathname + url.search + url.hash;
    }
    const databaseName = key("digues-app-field-projection");
    const coordinationKey = name => key(`digues-app:${name}`);
    return Object.freeze({ context, legacy, key, coordinationKey, apiUrl, pageUrl, fetch: fetchScoped,
      urlModeKey, urlMode, setUrlMode,
      setConnectivityObserver,
      databaseName,
      can: permission => legacy || context.permissions.includes(permission),
      assertActive, isActive: () => active && isCurrent(), generation: () => generation,
      objectUrl: blob => { assertActive(); const url = URL.createObjectURL(blob); objectUrls.add(url); return url; },
      revokeObjectUrl: url => { objectUrls.delete(url); URL.revokeObjectURL(url); },
      stop: () => { active = false; generation++; controller.abort(); objectUrls.forEach(url => URL.revokeObjectURL(url)); objectUrls.clear(); } });
  }
  return { resolveDatabaseId, createContext, createRuntime };
});
