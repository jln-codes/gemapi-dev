(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) { module.exports = api; return; }
  root.OfflineOperations = api;
  const engine = api.createEngine({
    store: root.OfflineRuntime.store,
    catchUp: () => root.ResourceEventTransport.synchronize(),
    recoverPinned: (operation, response) => root.OfflineRuntime.recoverPinned(operation, response),
    headers: () => root.ResourceMutationController.requestHeaders(),
    publish: event => root.ResourceMutationController.publishMutation(event, { applyLocally: true }),
    integrated: async () => {
      await root.OfflineRuntime.refreshConfirmed();
      await root.ObservationCreate?.refreshConfirmed();
      await root.PhotoOffline?.refreshVisible();
    },
    fetchImpl: (url, options) => (root.BusinessRuntime?.fetch || root.fetch.bind(root))(url, { ...options, offlineWrite: true }), locks: root.navigator.locks,
  });
  root.OfflineOperationsRuntime = engine;
  let timer, ticking = false, wakeRequested = false, stopped = false;
  const reconnect = event => {
    const runtime = root.BusinessRuntime;
    const key = runtime ? (runtime.legacy ? "legacy" : runtime.context?.key) : null;
    if (runtime && (!runtime.isActive() || event.detail?.contextKey !== key
        || event.detail?.generation !== runtime.generation())) return;
    clearTimeout(timer); void tick();
  };
  root.addEventListener("digues-app:context-closed", () => {
    stopped = true; clearTimeout(timer);
    root.removeEventListener("digues-app:backend-reconnected", reconnect);
  }, { once: true });
  async function tick() {
    if (stopped) return;
    if (ticking) { wakeRequested = true; return; }
    ticking = true; clearTimeout(timer);
    try {
      try { await engine.synchronize(); } catch (_) { /* summary retains diagnostic */ }
      await render();
    } finally {
      ticking = false;
      if (stopped) return;
      if (wakeRequested) { wakeRequested = false; void tick(); }
      else timer = setTimeout(tick, 15000);
    }
  }
  async function render() {
    const host = document.querySelector("#offline-operations");
    if (!host) return;
    try {
      await root.OfflineRuntime.refreshConfirmed();
      await root.ObservationCreate?.refreshConfirmed();
      const summary = await engine.summary();
      for (const name of ["pending", "syncing", "synced", "conflict", "failed"]) {
        const count = host.querySelector(`[data-count="${name}"]`);
        if (count) count.textContent = String(summary[name]);
      }
      const message = host.querySelector("[data-summary-message]");
      if (message) message.textContent = summary.message;
      const list = host.querySelector("[data-intentions]"); list.replaceChildren();
      for (const operation of summary.operations) {
        const item = document.createElement("details"), label = document.createElement("summary"), content = document.createElement("pre");
        const photoState = operation.resource === "photos" && operation.action === "create"
          ? await root.OfflineRuntime.store.photoEligibility(operation.operationId) : null;
        label.textContent = operation.action === "update-localisation"
          ? `Localisation Photo — ${operation.resourceId} — ${api.statusLabel(operation.status)}` : photoState
          ? `Photo en préparation — ${operation.changes.libelle || operation.changes.designation || operation.resourceId} — ${api.photoReasonLabel(photoState.reason)}`
          : `${operation.resource} ${operation.resourceId} — ${api.statusLabel(operation.status)}`;
        content.textContent = JSON.stringify({ baseVersion: operation.baseVersion, changes: operation.changes,
          dependsOn: operation.dependsOn, message: operation.message || "Intention locale conservée, distincte de l’état serveur." }, null, 2);
        item.append(label, content);
        if (operation.resource === "observations" && operation.action === "create") {
          const open = document.createElement("button"); open.type = "button"; open.textContent = "Ouvrir l’observation locale";
          open.onclick = () => root.OfflineRuntime.showDetail("observations", operation.resourceId);
          item.append(open);
        }
        if (photoState) {
          const open = document.createElement("button"); open.type = "button"; open.textContent = "Ouvrir la Photo locale";
          open.onclick = async () => {
            const parent = (await root.OfflineRuntime.store.read("observations", operation.changes.observation_id))?.payload
              || { id: operation.changes.observation_id, designation: operation.observationContext.designation,
                version: operation.observationContext.observedVersion };
            root.PhotoOffline.open(parent, operation);
          };
          item.append(open);
          const parent = document.createElement("button"); parent.type = "button";
          parent.textContent = "Voir l’Observation liée";
          parent.onclick = () => root.OfflineRuntime.showDetail("observations", operation.changes.observation_id);
          item.append(parent);
        }
        if (operation.resource === "photos" && operation.action === "update-localisation") {
          const open = document.createElement("button"); open.type = "button"; open.textContent = "Voir la localisation locale";
          open.onclick = async () => {
            const photoCreate = summary.operations.find(o => o.resource === "photos" && o.action === "create"
              && o.resourceId === operation.resourceId);
            const confirmed = await root.OfflineRuntime.store.confirmedPhotos(operation.observationId);
            const photo = confirmed.find(item => item.photo.id === operation.resourceId)?.photo
              || (photoCreate ? root.LocalProjection.localPhoto(photoCreate) : null);
            const parentRow = await root.OfflineRuntime.store.read("observations", operation.observationId);
            const observation = parentRow?.payload || { id: operation.observationId,
              designation: photoCreate?.observationContext?.designation || operation.observationId };
            if (photo) await root.PhotoLocalisation.openOffline(photo, observation, () => render(), operation);
          };
          item.append(open);
        }
        const photoUnknown = operation.resource === "photos" && operation.attempts > 0
          && !(operation.action === "update-localisation" && ["conflict", "failed"].includes(operation.status));
        if (!photoUnknown && (["conflict", "failed"].includes(operation.status)
            || (["create", "update-localisation"].includes(operation.action) && !operation.attempts && operation.status === "pending"))) {
          const discard = document.createElement("button"); discard.type = "button"; discard.textContent = "Abandonner cette intention";
          discard.onclick = async () => {
            const children = operation.resource === "observations" && operation.action === "create"
              ? summary.operations.filter(o => o.resource === "photos" && o.dependsOn?.[0]?.resource === "observations"
                && o.dependsOn[0].id === operation.resourceId) : [];
            if (!confirm(children.length
              ? `Abandonner cette Observation et ses ${children.length} Photo(s) locales ? Leurs fichiers et métadonnées seront supprimés.`
              : photoState ? "Abandonner cette Photo locale, son fichier et ses métadonnées ? Le serveur ne sera pas modifié."
                : "Abandonner définitivement cette intention locale ? Le serveur ne sera pas modifié.")) return;
            if (children.length) await root.OfflineRuntime.store.discardObservationWithPhotos(operation.operationId,
              children.map(o => o.operationId));
            else await engine.discard(operation.operationId);
            await root.PhotoOffline?.refreshVisible(); await render();
          }; item.append(discard);
        }
        list.append(item);
      }
      await root.PhotoOffline?.refreshVisible();
    } catch (error) {
      const message = host.querySelector("[data-summary-message]");
      if (message) message.textContent = `Queue indisponible : ${error.message}`;
    }
  }
  const host = document.querySelector("#offline-operations");
  if (host) {
    const retry = host.querySelector("[data-synchronize-now]");
    retry.onclick = async () => { retry.disabled = true; try { await engine.synchronize(true); } finally { await render(); retry.disabled = false; } };
  }
  root.addEventListener("digues-app:offline-dialog-open", () => { void render(); });
  root.addEventListener("digues-app:backend-reconnected", reconnect);
  root.addEventListener("pagehide", () => clearTimeout(timer));
  root.addEventListener("pageshow", event => { if (event.persisted) void tick(); });
  void tick();
})(globalThis, function () {
  "use strict";
  const FIELDS = Object.freeze({ desordres: ["designation", "commentaire", "lieu_dit"], observations:
    ["designation", "date", "evolution", "suite_apporter_id", "suite", "urgence_id", "nombre_desordres"] });
  const photoReasonLabel = reason => ({ "media-required": "Fichier à ajouter",
    "parent-pending": "Observation en attente", "parent-syncing": "Observation en cours de synchronisation",
    "parent-confirmed": "Observation confirmée — intégration locale en attente",
    "parent-conflict": "Observation en conflit", "parent-failed": "Observation en échec",
    "parent-abandoned": "Observation abandonnée — vérification nécessaire",
    "parent-unverified": "Observation non vérifiée", ready: "En attente de synchronisation",
    "photo-pending": "Photo en attente de synchronisation", "photo-syncing": "Photo en cours de synchronisation",
    "photo-confirmed": "Confirmation Photo en cours", "photo-conflict": "Conflit de création Photo",
    "photo-failed": "Échec de création Photo", "photo-unverified": "Photo non confirmée",
    "photo-version-unknown": "Version Photo inconnue", "references-stale": "Références modifiées — revue nécessaire",
    "references-missing": "Références préparées indisponibles",
    confirmed: "Confirmée — actualisation en cours", conflict: "Conflit à examiner", failed: "Erreur à examiner" })[reason] || reason;
  const statusLabel = status => ({ pending: "en attente de synchronisation", syncing: "synchronisation en cours",
    confirmed: "confirmé serveur — actualisation locale en attente", conflict: "conflit — intervention nécessaire",
    failed: "échec — intervention nécessaire", synced: "synchronisé" })[status] || status;
  function supported(resource, changes) {
    return !!FIELDS[resource] && !!changes && Object.keys(changes).length > 0
      && Object.entries(changes).every(([key, value]) => FIELDS[resource].includes(key)
        && (value === null || (resource === "observations" && key === "nombre_desordres"
          ? Number.isSafeInteger(value) && value >= 0 && value <= 2147483647 : typeof value === "string")));
  }
  function createEngine({ store, catchUp, recoverPinned, fetchImpl, headers, publish, integrated = async () => {}, locks, now = Date.now }) {
    let running, synced = 0, message = "", retryAt = 0, failures = 0;
    const verifiedMedia = new Set();
    async function summary() {
      const operations = await store.listOperations();
      return { operations, synced, message,
        pending: operations.filter(o => ["pending", "confirmed"].includes(o.status)).length,
        syncing: operations.filter(o => o.status === "syncing").length,
        conflict: operations.filter(o => o.status === "conflict").length,
        failed: operations.filter(o => o.status === "failed").length };
    }
    async function barrier(operation, response) {
      const cursor = await catchUp();
      const { scope } = await store.inspect();
      if (!scope || BigInt(scope.cursor) < BigInt(cursor)) {
        if (operation && recoverPinned) { await recoverPinned(operation, response); return; }
        throw new Error("Projection non intégrée : replay différé.");
      }
    }
    async function finish(operation, response) {
      // Persist success before further network work. Crash/reload will finish the
      // same receipt, never infer a new version from submitted changes.
      await store.updateOperation(operation.operationId, { status: "confirmed", response: { mutation: response.mutation },
        message: "Succès serveur confirmé ; intégration du journal en cours." });
      await publish(response.mutation);
      await barrier(operation, response);
      if (operation.action === "create") {
        const row = await store.read(operation.resource, operation.resourceId);
        const parent = row?.payload?.parent;
        if (!row || row.serverVersion < response.mutation.versions[operation.resourceId]
            || parent?.id !== operation.changes.parent.id || parent?.resource !== operation.changes.parent.resource) {
          if (!recoverPinned) throw new Error("Projection de la création non intégrée.");
          await recoverPinned(operation, response);
        }
      }
      await store.removeOperation(operation.operationId);
      synced += 1;
      try { await integrated(); } catch (_) { /* confirmed; UI may refresh on the next tick */ }
    }
    async function photoRequest(url, options = {}) {
      const response = await fetchImpl(url, { credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(120000),
        ...options, headers: { ...await headers(), ...options.headers } });
      let payload;
      try { payload = await response.json(); } catch (_) { /* 2xx without a receipt is unknown */ }
      if (!response.ok || !payload) {
        const error = new Error(payload?.detail?.message || (typeof payload?.detail === "string" ? payload.detail : `Réponse non confirmée (${response.status})`));
        error.status = response.ok ? 502 : response.status; error.code = payload?.detail?.code; throw error;
      }
      return payload;
    }
    async function finishPhoto(operation, response) {
      if (operation.status !== "confirmed") await store.confirmPhoto(operation.operationId, response);
      await publish(response.mutation);
      // This Photo has a durable authoritative copy in photoMedia. It does not
      // extend the five-resource scope or move its cursor on a targeted receipt.
      await catchUp();
      await store.completePhoto(operation.operationId);
      synced++;
      try { await integrated(); } catch (_) { /* real receipt retained in photoMedia */ }
    }
    async function runPhoto(operation, force) {
      if (["conflict", "failed"].includes(operation.status) || (!force && operation.retryAt > now())) return;
      try {
        if (operation.status === "confirmed") { await finishPhoto(operation, operation.response); return; }
        const eligibility = await store.photoEligibility(operation.operationId);
        if (!eligibility.eligible) return;
        await catchUp();
        let media = await store.readPhotoMedia(operation.mediaLocalRef);
        if (!media) throw new Error("Fichier local absent ; intention conservée.");
        const post = async () => {
          const claim = await store.claimPhotoPost(operation.operationId);
          if (!claim) return null; // edited/discarded during a network request
          operation = claim.operation;
          return photoRequest("/api/photos", { method: "POST", headers: { "Content-Type": "application/json", "Operation-Id": operation.operationId },
            body: JSON.stringify(claim.payload) });
        };
        // Reconcile a possibly committed POST before consulting/renewing its media.
        if (operation.immutableIntent) {
          try {
            const response = await post();
            if (response) await finishPhoto(operation, response);
            return;
          } catch (error) {
            if (!["upload-not-found", "upload-expired"].includes(error.code) || ![404, 410].includes(error.status)) throw error;
          }
        }
        const url = `/api/photo-uploads/${operation.resourceId}/${media.sha256}?observation_id=${encodeURIComponent(operation.changes.observation_id)}`;
        let prepared;
        try { prepared = await photoRequest(url); }
        catch (error) { if (![404, 410].includes(error.status)) throw error; }
        if (!prepared) {
          // Verify once per loaded engine before uploading persisted bytes. Retries
          // reuse the result; restarting verifies again without changing the hash.
          if (!verifiedMedia.has(media.mediaLocalRef)) {
            const digest = await crypto.subtle.digest("SHA-256", await media.blob.arrayBuffer());
            const actual = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, "0")).join("");
            if (actual !== media.sha256) throw Object.assign(new Error("Intégrité du fichier local invalide ; original conservé pour diagnostic."), { status: 422 });
            verifiedMedia.add(media.mediaLocalRef);
          }
          if (!await store.beginPhotoUpload(operation.operationId)) return;
          prepared = await photoRequest(url + "&filename=" + encodeURIComponent(media.originalFilename), {
            method: "PUT", headers: { "Content-Type": media.mimeType }, body: media.blob });
        }
        if (!await store.bindPhotoUpload(operation.operationId, prepared)) return;
        const response = await post();
        if (response) await finishPhoto(operation, response);
      } catch (error) {
        const latest = (await store.listOperations()).find(o => o.operationId === operation.operationId);
        if (!latest) return;
        const temporary = !error.status || error.status >= 500 || [408, 429].includes(error.status);
        await store.updateOperation(operation.operationId, { status: latest.status === "confirmed" ? "confirmed"
          : temporary ? "pending" : error.status === 409 ? "conflict" : "failed",
          retryAt: temporary ? now() + 15000 : 0, message: error.message });
        // Each Photo is independent: its refusal/backoff does not starve siblings.
      }
    }
    async function finishPhotoLocalisation(operation, response) {
      if (operation.status !== "confirmed") await store.confirmPhotoLocalisation(operation.operationId, response);
      await publish(response.mutation);
      await catchUp();
      await store.completePhotoLocalisation(operation.operationId);
      synced++;
      try { await integrated(); } catch (_) { /* real Photo receipt was durably integrated */ }
    }
    async function runPhotoLocalisation(operation, force) {
      if (["conflict", "failed"].includes(operation.status) || (!force && operation.retryAt > now())) return;
      try {
        if (operation.status === "confirmed") { await finishPhotoLocalisation(operation, operation.response); return; }
        const eligibility = await store.photoLocalisationEligibility(operation.operationId);
        if (!eligibility.eligible) {
          if (eligibility.reason === "references-stale") await store.updateOperation(operation.operationId,
            { status: "conflict", message: "Les références préparées ont changé : vérifiez le repérage local." });
          return;
        }
        await catchUp();
        const claimed = await store.claimPhotoLocalisation(operation.operationId);
        if (!claimed) return;
        operation = claimed;
        const response = await photoRequest(`/api/photos/${encodeURIComponent(operation.resourceId)}/localisation`, {
          method: "PUT", headers: { "Content-Type": "application/json", "If-Match": `"${operation.baseVersion}"`,
            "Operation-Id": operation.operationId,
            ...(operation.referenceRevision ? { "Reference-Revision": operation.referenceRevision } : {}) },
          body: JSON.stringify(operation.changes),
        });
        await finishPhotoLocalisation(operation, response);
      } catch (error) {
        const latest = (await store.listOperations()).find(o => o.operationId === operation.operationId);
        if (!latest) return;
        const temporary = !error.status || error.status >= 500 || [408, 429].includes(error.status);
        await store.updateOperation(operation.operationId, { status: latest.status === "confirmed" ? "confirmed"
          : temporary ? "pending" : error.status === 409 ? "conflict" : "failed",
          retryAt: temporary ? now() + 15000 : 0, message: error.message });
      }
    }
    async function run(force) {
      const operations = await store.listOperations();
      if (!operations.some(o => ["pending", "syncing", "confirmed"].includes(o.status))) {
        // Only the explicit recovery action needs this barrier without a queue.
        // The automatic idle tick must not consume bootstrap events before the
        // page has installed its consumers and loaded its initial state.
        if (force) await catchUp();
        return;
      }
      for (let operation of operations) {
        // Photos without a verified local original remain valid LOT 4B intentions.
        if (operation.resource === "photos" && operation.action === "create") {
          await runPhoto(operation, force); continue;
        }
        if (operation.resource === "photos" && operation.action === "update-localisation") {
          await runPhotoLocalisation(operation, force); continue;
        }
        if (["conflict", "failed"].includes(operation.status)) continue;
        if (!force && operation.retryAt > now()) break;
        await barrier(operation, operation.response);
        if (operation.status === "confirmed") { await finish(operation, operation.response); continue; }
        // Do NOT reject based only on the updated projection: it may contain this
        // operation's own committed write whose HTTP response was lost.
        operation = await store.claimOperation(operation.operationId);
        if (!operation) continue; // edited/abandoned while the journal was loading
        const creating = operation.action === "create";
        let response, payload;
        try {
          response = await fetchImpl(creating ? "/api/observations" : `/api/${operation.resource}/${encodeURIComponent(operation.resourceId)}`, {
            method: creating ? "POST" : "PUT", credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(12000),
            headers: { "Content-Type": "application/json", ...creating ? {} : { "If-Match": `"${operation.baseVersion}"` },
              "Operation-Id": operation.operationId, ...await headers() },
            body: JSON.stringify(operation.changes),
          });
          try { payload = await response.json(); }
          catch (error) {
            // A proxy may return HTML for a definitive 4xx. It must not become
            // an endless "network" retry. A malformed 2xx remains unknown.
            if (response.ok) throw error;
            payload = { detail: `HTTP ${response.status}` };
          }
        } catch (error) {
          await store.updateOperation(operation.operationId, { status: "pending", message: "Résultat inconnu ; retry avec le même Operation-Id.", retryAt: now() + 15000 });
          throw error;
        }
        if (!response.ok) {
          const conflict = response.status === 409 && (creating || payload.detail?.code === "version-conflict");
          const temporary = response.status >= 500 || [408, 429].includes(response.status);
          await store.updateOperation(operation.operationId, { status: temporary ? "pending" : conflict ? "conflict" : "failed",
            message: typeof payload.detail === "string" ? payload.detail : payload.detail?.message || `HTTP ${response.status}`,
            retryAt: temporary ? now() + 15000 : 0 });
          if (temporary) throw new Error(`Serveur indisponible (${response.status})`);
          if (conflict) await barrier(operation);
          continue;
        }
        if (!payload.mutation?.eventId || payload.mutation.resource !== operation.resource
            || !payload.mutation.ids?.includes(operation.resourceId)
            || !Number.isSafeInteger(payload.mutation.versions?.[operation.resourceId])) {
          throw new Error("Reçu autoritaire incomplet ; Operation-Id conservé pour réconciliation.");
        }
        if (creating && (payload.id !== operation.resourceId || payload.version !== 1
            || payload.parent?.id !== operation.changes.parent.id || payload.parent?.resource !== operation.changes.parent.resource
            || payload.mutation.action !== "create" || payload.mutation.versions[operation.resourceId] !== 1)) {
          throw new Error("Reçu de création incohérent ; intention conservée.");
        }
        await finish(operation, payload);
      }
    }
    function synchronize(force = false) {
      if (running) return running;
      if (!locks) { message = "Web Locks indisponible : replay désactivé, intentions conservées."; return Promise.resolve(); }
      if (!force && retryAt > now()) return Promise.resolve();
      if (globalThis.BusinessRuntime && (!globalThis.BusinessRuntime.isActive() || !globalThis.BusinessRuntime.can("offline.write"))) return Promise.resolve();
      running = locks.request(globalThis.BusinessRuntime?.coordinationKey("offline-replay") || "digues-app:offline-replay", { ifAvailable: true }, async lock => {
        if (!lock) return;
        try { await run(force); failures = 0; retryAt = 0; message = ""; }
        catch (error) { failures += 1; retryAt = now() + Math.min(300000, 15000 * 2 ** Math.min(failures - 1, 5)); message = `Reprise différée : ${error.message}`; }
      }).finally(() => { running = null; });
      return running;
    }
    async function submit(intention) {
      if (globalThis.BusinessRuntime && !globalThis.BusinessRuntime.can('offline.write')) throw new Error('Écriture hors ligne non autorisée.');
      if (!locks) throw new Error("Écriture durable indisponible sans Web Locks ; brouillon conservé.");
      if (!supported(intention.resource, intention.changes)) throw new Error("Cette opération nécessite une connexion.");
      const operation = await store.enqueue(intention);
      await synchronize(true);
      const current = (await store.listOperations()).find(o => o.operationId === operation.operationId);
      return { operationId: operation.operationId, status: current?.status || "synced", operation: current };
    }
    async function submitCreate(intention) {
      if (globalThis.BusinessRuntime && !globalThis.BusinessRuntime.can('offline.write')) throw new Error('Écriture hors ligne non autorisée.');
      if (!locks) throw new Error("Écriture durable indisponible sans Web Locks ; brouillon conservé.");
      const operation = await store.enqueueCreate(intention);
      await synchronize(true);
      const current = (await store.listOperations()).find(o => o.operationId === operation.operationId);
      return { operationId: operation.operationId, status: current?.status || "synced", operation: current };
    }
    return { submit, submitCreate, synchronize, summary, supported: (resource, changes) => !!locks && supported(resource, changes),
      discard: id => store.removeOperation(id, true) };
  }
  return { createEngine, supported, FIELDS, statusLabel, photoReasonLabel };
});
