(function (root, factory) {
  const validation = typeof module === "object" && module.exports
    ? require("./resource-invalidation.js") : root.ResourceInvalidation;
  const api = factory(validation);
  if (typeof module === "object" && module.exports) module.exports = api;
  else {
    root.ResourceServerTransport = api;
    root.ResourceEventTransport = api.createTransport({
      receive: root.ResourceMutationController.handleRemoteMutation,
      projection: root.OfflineRuntime,
    });
    const reconnect = event => {
      const runtime = root.BusinessRuntime;
      const key = runtime?.context?.key || null;
      if (runtime && (!runtime.isActive() || event.detail?.contextKey !== key
          || event.detail?.generation !== runtime.generation())) return;
      root.ResourceEventTransport.wake();
    };
    root.addEventListener("digues-app:backend-reconnected", reconnect);
    root.addEventListener("digues-app:context-closed", () => {
      root.removeEventListener("digues-app:backend-reconnected", reconnect);
    }, { once: true });
    root.addEventListener("pagehide", () => root.ResourceEventTransport.stop());
    root.addEventListener("pageshow", (event) => {
      if (event.persisted) root.ResourceEventTransport.start();
    });
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function (Validation) {
  "use strict";
  const ROOT = "/api/resource-events";
  function cursor(value) {
    return typeof value === "string" && /^(0|[1-9][0-9]{0,18})$/.test(value)
      && BigInt(value) <= 9223372036854775807n;
  }
  function createTransport({ receive, projection, fetchImpl = globalThis.BusinessRuntime?.fetch || globalThis.fetch.bind(globalThis),
    EventSourceImpl = globalThis.EventSource, retryMs = 2000,
    delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    onError = (error) => console.warn("Synchronisation serveur interrompue ; reprise automatique.", error),
  }) {
    let reconnectPending = false;
    let maintenance = false;
    let retryAttempt = 0;
    const retryDelay = () => Math.min(retryMs * 2 ** Math.min(retryAttempt++, 4), 30000);
    let lastCursor = null;
    let source = null;
    let generation = 0;
    let running = false;
    let initialState = Promise.resolve();
    let chain = Promise.resolve();
    let baselineRead = null;
    async function json(path) {
      const response = await fetchImpl(ROOT + path, { cache: "no-store", credentials: "same-origin",
        signal: globalThis.AbortSignal?.timeout?.(12000) });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        if (response.status === 503 && body?.code === "BUSINESS_DATABASE_MAINTENANCE") {
          maintenance = true;
          throw new Error(body.detail);
        }
        throw new Error(`Journal indisponible (${response.status})`);
      }
      const data = await response.json();
      maintenance = false; // Only a successful business journal read clears it.
      retryAttempt = 0;
      return data;
    }
    // Capture BEFORE HTTP state reads. Retry rather than silently choosing a later
    // cursor after stale initial data has already been loaded.
    const bootstrap = (async () => {
      const integrated = await projection?.initialCursor();
      if (cursor(integrated)) { lastCursor = integrated; return lastCursor; }
      for (;;) {
        if (globalThis.BusinessRuntime && !globalThis.BusinessRuntime.isActive()) return null;
        try {
          const head = await json("/head");
          if (!cursor(head.cursor)) throw new Error("Curseur initial invalide");
          lastCursor = head.cursor;
          return lastCursor;
        } catch (error) {
          if (globalThis.BusinessRuntime && !globalThis.BusinessRuntime.isActive()) return null;
          onError(error);
          // Offline consultation may start without a server. A new (unprepared)
          // page still captures a head before any later server synchronization.
          if (projection && projection.networkError(error)) return null;
          await delay(retryDelay());
        }
      }
    })();

    async function ensureReadBaseline() {
      await bootstrap;
      if (lastCursor !== null) return lastCursor;
      if (!baselineRead) baselineRead = (async () => {
        const head = await json("/head");
        if (!cursor(head.cursor)) throw new Error("Curseur initial invalide");
        lastCursor = head.cursor;
        return lastCursor;
      })().finally(() => { baselineRead = null; });
      return baselineRead;
    }

    async function accept(entry) {
      if (!cursor(entry?.cursor) || !Validation.normalizeMutationPayload(entry.event)) {
        throw new Error("Événement serveur invalide ; curseur conservé");
      }
      if (BigInt(entry.cursor) <= BigInt(lastCursor)) return;
      const result = await receive(entry.event);
      if (result?.accepted !== true && !["duplicate-event", "local-origin"].includes(result?.reason)) {
        throw new Error("Événement non accepté ; curseur conservé");
      }
      // UI delivery is not held hostage by persistence. Own-origin and BC
      // duplicates must nevertheless integrate before advancing this cursor.
      await projection?.integrate(entry);
      // Acceptance = validated and scheduled by the existing dispatcher. Handler
      // failures remain isolated by the invalidation registry (not write failures).
      lastCursor = entry.cursor;
      retryAttempt = 0;
    }

    async function catchUp(token) {
      for (;;) {
        const page = await json(`?after=${encodeURIComponent(lastCursor)}&limit=100`);
        if (token !== generation) return;
        if (!Array.isArray(page.events) || typeof page.hasMore !== "boolean"
            || !cursor(page.nextCursor)) throw new Error("Page de journal invalide");
        let previous = lastCursor;
        for (const entry of page.events) {
          if (!cursor(entry.cursor) || BigInt(entry.cursor) <= BigInt(previous)) {
            throw new Error("Ordre du journal invalide");
          }
          await accept(entry);
          previous = entry.cursor;
        }
        if (page.nextCursor !== lastCursor || (page.hasMore && !page.events.length)) {
          throw new Error("Pagination du journal invalide");
        }
        if (!page.hasMore) return;
      }
    }

    async function connect(token) {
      try {
        await bootstrap;
        await initialState;
        await chain;
        if (token !== generation) return;
        await ensureReadBaseline();
        await synchronize();
        if (token !== generation) return;
        const streamUrl = `${ROOT}/stream?after=${encodeURIComponent(lastCursor)}`;
        source = new EventSourceImpl(globalThis.BusinessRuntime?.apiUrl(streamUrl) || streamUrl);
        source.addEventListener("resource-mutated", (message) => {
          if (token !== generation) return;
          chain = chain.then(() => {
            if (token !== generation) return;
            return accept({ cursor: message.lastEventId, event: JSON.parse(message.data) });
          }).catch((error) => reconnect(token, error));
        });
        source.addEventListener("maintenance", () => {
          if (token !== generation) return;
          maintenance = true;
          reconnect(token, new Error("Base en maintenance ; suivi d’import disponible pour les administrateurs autorisés."));
        });
        source.onerror = () => reconnect(token);
      } catch (error) { reconnect(token, error); }
    }

    function reconnect(token, error) {
      if (token !== generation || !running) return;
      source?.close(); source = null;
      const next = ++generation;
      reconnectPending = true;
      if (error) onError(error);
      // Do not trust EventSource's automatic Last-Event-ID (delivery != acceptance).
      // Recreate it after REST catch-up using only our accepted cursor.
      void delay(retryDelay()).then(() => {
        if (next !== generation || !running) return;
        reconnectPending = false;
        void connect(next);
      });
    }
    function start(ready) {
      if (ready) initialState = Promise.resolve(ready);
      if (running) return;
      running = true;
      void connect(++generation);
    }
    function stop() {
      running = false; generation += 1; reconnectPending = false;
      source?.close(); source = null;
    }
    function wake() {
      // Control-plane success does not prove that business maintenance ended.
      if (!running || maintenance || reconnectPending) return;
      source?.close(); source = null;
      void connect(++generation);
    }
    async function synchronize() {
      await ensureReadBaseline();
      const work = chain.catch(() => {}).then(async () => {
        let token = generation;
        await catchUp(token);
        // The first successful HTTP response after an outage can itself wake
        // the transport. Complete the same serialized barrier on that new
        // generation, once; stopping or closing the context still aborts it.
        if (token !== generation && running
            && (!globalThis.BusinessRuntime || globalThis.BusinessRuntime.isActive())) {
          token = generation;
          await catchUp(token);
        }
        if (token !== generation) throw new Error("Reconnexion en cours ; reprise différée");
        // A degraded scope may lag behind an accepted transport cursor. Retry
        // from its durable state even if this journal page contains no new event.
        const integrated = await projection?.initialCursor();
        if (cursor(integrated) && BigInt(integrated) < BigInt(lastCursor)) {
          await projection.integrate({ cursor: lastCursor });
        }
        globalThis.ResourceMutationController?.retryInvalidations();
        return lastCursor;
      });
      chain = work.catch(() => {});
      return work;
    }
    return { bootstrap, start, stop, wake, synchronize, ensureReadBaseline, getCursor: () => lastCursor };
  }
  return { createTransport };
});
