(function (root, factory) {
  const store = root.OfflineBasemapStore || (typeof module === "object" && module.exports
    ? require("./offline-basemap-store.js") : null);
  const download = root.OfflineBasemapDownload || (typeof module === "object" && module.exports
    ? require("./offline-basemap-download.js") : null);
  const api = factory(root, store, download);
  if (typeof module === "object" && module.exports) module.exports = api;
  root.OfflineBasemapUi = api;
})(typeof window !== "undefined" ? window : globalThis, function (root, storage, download) {
  "use strict";
  const STATE_LABELS = Object.freeze({ preparing: "préparation", downloading: "téléchargement",
    interrupted: "interrompu", incomplete: "incomplet", ready: "prêt", error: "erreur",
    obsolete: "obsolète", deleting: "suppression" });

  function mapBounds(map) {
    const bounds = map.getBounds();
    return [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()];
  }

  function localPackages(entries, manifests, map) {
    const result = {};
    const zoom = map.getZoom();
    const center = map.getCenter();
    for (const entry of entries) {
      if (entry.offline?.status !== "available" || entry.offline.retention !== "authorized") continue;
      const nativeZoom = Math.min(zoom, entry.zoom.native_max);
      if (nativeZoom < entry.zoom.native_min) continue;
      let viewport;
      try { viewport = download.planTiles({ bounds: mapBounds(map), zoomMin: nativeZoom, zoomMax: nativeZoom }); }
      catch (_) { viewport = null; }
      const n = 2 ** nativeZoom;
      const latitude = Math.max(-85.05112878, Math.min(85.05112878, center.lat));
      const centerKey = `${nativeZoom}/${Math.min(n - 1, Math.max(0, Math.floor((center.lng + 180) / 360 * n)))}/` +
        `${Math.min(n - 1, Math.max(0, Math.floor((1 - Math.asinh(Math.tan(latitude * Math.PI / 180)) / Math.PI) / 2 * n)))}`;
      const candidates = manifests.filter((row) => row.state === "ready" && row.basemapId === entry.id &&
        row.zoomMin <= nativeZoom && row.zoomMax >= nativeZoom).map((row) => {
        const keys = new Set(row.expectedKeys);
        const full = Boolean(viewport?.count && viewport.keys.every((key) => keys.has(key)));
        return { row, full, center: keys.has(centerKey) };
      }).filter((candidate) => candidate.full || candidate.center);
      candidates.sort((a, b) => Number(b.full) - Number(a.full) ||
        a.row.expectedCount - b.row.expectedCount || a.row.generation.localeCompare(b.row.generation) ||
        a.row.id.localeCompare(b.row.id));
      const chosen = candidates[0];
      if (!chosen) continue;
      result[entry.id] = { url: `/__offline-basemaps/${chosen.row.id}/{z}/{x}/{y}`,
        partial: !chosen.full,
        options: { minZoom: entry.zoom.native_min, maxNativeZoom: entry.zoom.native_max,
          maxZoom: entry.zoom.display_max } };
    }
    return result;
  }

  function create({ map, basemapApi, getTerritory = () => null, runtime = root,
    documentObject = runtime.document, store = storage.createStore(), manager,
    fetchImpl = runtime.fetch.bind(runtime), logger = console } = {}) {
    if (!map || !basemapApi?.loadStrict || !documentObject) throw new Error("Préparation des fonds indisponible.");
    const owner = runtime.BusinessRuntime;
    const ownerKey = owner?.legacy ? "legacy" : owner?.context?.key;
    let closed = false;
    const contextKey = () => !closed && owner === runtime.BusinessRuntime && owner?.isActive()
      ? ownerKey : null;
    const isCurrent = (key) => Boolean(key && key === contextKey());
    function assertCurrent(key) {
      if (!isCurrent(key)) throw new DOMException("Contexte cartographique fermé.", "AbortError");
    }
    const scopedFetch = async (...args) => {
      assertCurrent(ownerKey);
      const response = await fetchImpl(...args);
      assertCurrent(ownerKey);
      return response;
    };
    let progressTimer = null;
    const dl = manager || download.createManager({ store, fetchImpl: scopedFetch, origin: runtime.location.origin,
      cryptoObject: runtime.crypto, storageManager: runtime.navigator.storage,
      onProgress: () => {
        if (progressTimer) return;
        progressTimer = setTimeout(() => { progressTimer = null; void renderPackages(); }, 250);
      } });
    const modal = documentObject.querySelector("#offline-basemaps-modal");
    const form = modal?.querySelector("form");
    const choices = modal?.querySelector("[data-basemap-choices]");
    const preview = modal?.querySelector("[data-basemap-preview]");
    const packageList = modal?.querySelector("[data-basemap-packages]");
    const status = modal?.querySelector("[data-basemap-status]");
    const closeButton = modal?.querySelector("[data-basemap-close]");
    let selector = null, catalogue = null;
    const online = () => runtime.OfflineRuntime?.getNetworkState?.()?.status === "online" ||
      (runtime.OfflineRuntime?.getNetworkState?.()?.status !== "offline" && runtime.navigator.onLine !== false);
    const tell = (message) => { if (status) status.textContent = message; };
    const notify = () => { if (contextKey()) void selector?.updateLocalAvailability?.(); };

    async function getState() {
      const key = contextKey();
      if (!key || closed) return null;
      const saved = await store.getCatalogue(key);
      if (!saved || !isCurrent(key)) return null;
      const manifests = await store.list(key);
      if (!isCurrent(key)) return null;
      return { catalogue: saved, packages: localPackages(saved.basemaps || [], manifests, map) };
    }
    async function saveCatalogue(payload) {
      const key = contextKey();
      if (!key || closed) return;
      await store.saveCatalogue(key, payload);
      if (!isCurrent(key)) return;
      catalogue = payload;
      if (!modal.hidden) await render();
    }
    async function renderPackages() {
      const key = contextKey();
      if (!key || !packageList) return;
      const rows = await store.list(key);
      if (!isCurrent(key)) return;
      packageList.replaceChildren();
      for (const row of rows.sort((a, b) => a.basemapId.localeCompare(b.basemapId) || a.id.localeCompare(b.id))) {
        const item = documentObject.createElement("div");
        item.className = "offline-basemap-package";
        const label = documentObject.createElement("span");
        const name = catalogue?.basemaps?.find((entry) => entry.id === row.basemapId)?.name || row.basemapId;
        label.textContent = `${name} · z${row.zoomMin}–${row.zoomMax} · ${STATE_LABELS[row.state] || row.state} · ${row.verifiedCount}/${row.expectedCount} tuiles · ${(row.storedBytes / 1048576).toFixed(1)} Mio`;
        item.append(label);
        for (const [action, caption] of [["resume", "Reprendre"], ["pause", "Suspendre"],
          ["update", "Mettre à jour"], ["delete", "Supprimer"]]) {
          const permission = catalogue?.basemaps?.find((entry) => entry.id === row.basemapId)?.offline;
          const authorized = permission?.status === "available" && permission.retention === "authorized";
          if (action === "resume" && (!authorized || !["interrupted", "incomplete", "error", "preparing"].includes(row.state)) ||
              action === "pause" && row.state !== "downloading" ||
              action === "update" && (!authorized || row.state !== "ready")) continue;
          const button = documentObject.createElement("button");
          button.type = "button"; button.dataset.action = action; button.dataset.id = row.id;
          button.textContent = action === "delete" && row.state === "downloading" ? "Annuler" : caption;
          item.append(button);
        }
        packageList.append(item);
      }
      if (!rows.length) packageList.textContent = "Aucun fond enregistré sur cet appareil.";
      notify();
    }
    async function render() {
      const key = contextKey();
      if (!key) { tell("Contexte métier indisponible."); return; }
      catalogue = online() ? catalogue || await store.getCatalogue(key) : await store.getCatalogue(key);
      if (!isCurrent(key)) return;
      choices.replaceChildren();
      const authorized = catalogue?.basemaps?.filter((entry) => entry.offline?.status === "available" &&
        entry.offline.retention === "authorized") || [];
      for (const entry of authorized) {
        const label = documentObject.createElement("label");
        const input = documentObject.createElement("input");
        input.type = "checkbox"; input.name = "basemap"; input.value = entry.id;
        label.append(input, ` ${entry.name} (z${entry.offline.min_zoom}–${entry.offline.max_zoom})`);
        choices.append(label);
      }
      form.querySelector('[type="submit"]').disabled = !authorized.length || !online();
      if (!authorized.length) tell("Aucun fond n’est autorisé pour la préparation locale. Les droits de conservation IGN restent à confirmer.");
      else if (!online()) tell("Connectez-vous pour préparer de nouveaux fonds. Les paquets prêts restent consultables.");
      await renderPackages();
    }
    async function run(id, key = contextKey()) {
      try {
        assertCurrent(key);
        tell("Téléchargement en cours. Vous pouvez le suspendre et le reprendre plus tard.");
        const result = await dl.run(id, key);
        if (!isCurrent(key)) return;
        tell(result.state === "ready" ? "Paquet vérifié et prêt hors connexion." : "Téléchargement suspendu ; le paquet reste incomplet.");
      } catch (error) { logger.warn("Téléchargement cartographique interrompu", error); tell(error.message); }
      await renderPackages();
    }
    async function prepare(event) {
      event.preventDefault();
      if (!online()) { tell("Connexion nécessaire pour préparer un fond."); return; }
      const key = contextKey();
      if (!key) { tell("Contexte métier indisponible."); return; }
      const selected = [...form.querySelectorAll('input[name="basemap"]:checked')].map((input) =>
        catalogue.basemaps.find((entry) => entry.id === input.value)).filter(Boolean);
      if (!selected.length) { tell("Sélectionnez au moins un fond."); return; }
      const territory = getTerritory();
      const useTerritory = form.elements.zone.value === "territory";
      if (useTerritory && !territory?.geometry) { tell("Territoire administratif non disponible."); return; }
      const geometry = useTerritory ? territory.geometry : null;
      const bounds = useTerritory ? geometryBounds(geometry) : mapBounds(map);
      const zoomMin = Number(form.elements.zoomMin.value), zoomMax = Number(form.elements.zoomMax.value);
      const plans = [];
      try {
        for (const entry of selected) {
          if (zoomMin < entry.offline.min_zoom || zoomMax > entry.offline.max_zoom) {
            throw new Error(`Plage de zoom incompatible avec ${entry.name}.`);
          }
          const plan = download.planTiles({ bounds, geometry, zoomMin, zoomMax });
          const estimate = await download.estimateStorage(plan, entry.tile_format, runtime.navigator.storage);
          assertCurrent(key);
          plans.push({ entry, plan, estimate });
        }
      } catch (error) { tell(error.message); return; }
      const count = plans.reduce((sum, row) => sum + row.plan.count, 0);
      const bytes = plans.reduce((sum, row) => sum + row.estimate.estimatedBytes, 0);
      preview.textContent = plans.map(({ entry, plan }) => `${entry.name} : ${Object.entries(plan.byZoom).map(([z, n]) => `z${z} ${n}`).join(", ")}`).join(" · ") +
        ` · Total ${count} tuiles, environ ${(bytes / 1048576).toFixed(1)} Mio. ` +
        `Espace navigateur estimé : ${plans[0].estimate.freeEstimate === null ? "inconnu" :
          `${(plans[0].estimate.freeEstimate / 1048576).toFixed(0)} Mio`}, réserve ${
          (plans[0].estimate.safetyReserve / 1048576).toFixed(0)} Mio. Cette estimation ne garantit pas l’espace réellement disponible.`;
      const storage = plans[0].estimate;
      if (plans.some((row) => row.estimate.risk === "high") ||
          storage.freeEstimate !== null && bytes + storage.safetyReserve > storage.freeEstimate) {
        tell("Réserve de sécurité insuffisante pour tous les fonds choisis : réduisez la zone ou les zooms."); return;
      }
      if (!runtime.confirm(`Préparer ${count} tuiles cartographiques sur cet appareil ?`)) return;
      await dl.requestPersistence().catch(() => false);
      for (const { entry } of plans) {
        try {
          assertCurrent(key);
          const descriptor = await basemapApi.loadStrict(entry.delivery.url);
          assertCurrent(key);
          const territoryId = useTerritory && /^[0-9a-f-]{36}$/i.test(territory.id || "")
            ? territory.id : useTerritory ? TERRITORY_ID : VIEW_TERRITORY_ID;
          const previous = (await store.list(key)).filter((row) => row.state === "ready" && row.basemapId === entry.id &&
            row.territoryId === territoryId && row.zoomMin === zoomMin && row.zoomMax === zoomMax &&
            JSON.stringify(row.bounds) === JSON.stringify(bounds));
          assertCurrent(key);
          const { manifest } = await dl.prepare({ entry, descriptor, contextKey: key,
            territoryId,
            bounds, geometry, zoomMin, zoomMax });
          assertCurrent(key);
          await run(manifest.id, key);
          assertCurrent(key);
          if ((await store.get(manifest.id, key)).state === "ready") {
            for (const old of previous) if (old.id !== manifest.id) {
              assertCurrent(key);
              await store.remove(old.id, key);
            }
          }
        } catch (error) { logger.warn("Préparation cartographique impossible", error); tell(error.message); }
      }
      await renderPackages();
    }
    async function packageAction(event) {
      const button = event.target.closest("button[data-action]");
      if (!button || !packageList.contains(button)) return;
      const id = button.dataset.id, key = contextKey();
      if (!key) return;
      button.disabled = true;
      try {
        if (button.dataset.action === "resume") { if (!online()) throw new Error("Connexion nécessaire pour reprendre."); void run(id); return; }
        if (button.dataset.action === "pause") await dl.pause(id, key);
        if (button.dataset.action === "delete") { if (runtime.confirm("Supprimer ce paquet cartographique de cet appareil ?")) await dl.cancel(id, key); }
        if (button.dataset.action === "update") {
          if (!online()) throw new Error("Connexion nécessaire pour mettre à jour.");
          const row = await store.get(id, key);
          assertCurrent(key);
          const entry = catalogue?.basemaps?.find((item) => item.id === row.basemapId);
          if (!entry) throw new Error("Fond absent du catalogue actuel.");
          const descriptor = await basemapApi.loadStrict(entry.delivery.url);
          assertCurrent(key);
          const { manifest } = await dl.prepare({ entry, descriptor, contextKey: key,
            territoryId: row.territoryId, bounds: row.bounds,
            plannedKeys: row.expectedKeys, zoomMin: row.zoomMin, zoomMax: row.zoomMax });
          assertCurrent(key);
          await run(manifest.id, key);
          assertCurrent(key);
          if ((await store.get(manifest.id, key)).state === "ready") {
            assertCurrent(key);
            await store.remove(id, key);
          }
        }
      } catch (error) { tell(error.message); }
      await renderPackages();
    }
    const swMessage = (event) => {
      if (event.data?.type === "DIGUES_BASEMAP_CONTEXT" && event.ports?.[0]) {
        event.ports[0].postMessage({ contextKey: closed ? null : contextKey() || null });
      }
    };
    const mapChanged = () => notify();
    const networkChanged = () => { if (!modal.hidden) void render(); };
    const close = () => { modal.hidden = true; };
    function contextClosed() {
      if (closed) return;
      closed = true; close(); clearTimeout(progressTimer);
      selector?.destroy();
      runtime.navigator.serviceWorker?.removeEventListener("message", swMessage);
      map.off("moveend", mapChanged); map.off("zoomend", mapChanged);
      runtime.removeEventListener("digues-app:network-state", networkChanged);
      runtime.removeEventListener("online", networkChanged); runtime.removeEventListener("offline", networkChanged);
      if (ownerKey) void store.list(ownerKey).then((rows) => Promise.all(rows.filter((row) => dl.isRunning(row.id))
        .map((row) => dl.pause(row.id, ownerKey)))).catch((error) => logger.warn("Arrêt des fonds locaux impossible", error));
    }
    function start() {
      runtime.navigator.serviceWorker?.addEventListener("message", swMessage);
      map.on("moveend", mapChanged); map.on("zoomend", mapChanged);
      runtime.addEventListener("digues-app:network-state", networkChanged);
      runtime.addEventListener("online", networkChanged); runtime.addEventListener("offline", networkChanged);
      runtime.addEventListener("digues-app:context-closed", contextClosed, { once: true });
      form.addEventListener("submit", (event) => { void prepare(event); });
      packageList.addEventListener("click", (event) => { void packageAction(event); });
      closeButton.addEventListener("click", close);
      modal.addEventListener("click", (event) => { if (event.target === modal) close(); });
      runtime.document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !modal.hidden) close(); });
      const key = contextKey();
      if (key) void store.recover(key).then(notify).catch((error) => logger.warn("Reprise des paquets locaux impossible", error));
    }
    return { start, attachSelector: (value) => { selector = value; }, getState, saveCatalogue,
      async open() {
        if (!contextKey()) return;
        modal.hidden = false;
        try { await render(); } catch (error) { logger.warn("Paquets cartographiques indisponibles", error); tell("Stockage cartographique local indisponible."); }
        closeButton.focus();
      }, close };
  }

  const VIEW_TERRITORY_ID = "00000000-0000-4000-8000-000000000001";
  const TERRITORY_ID = "00000000-0000-4000-8000-000000000002";
  function geometryBounds(geometry) {
    const points = [];
    function visit(value) {
      if (!Array.isArray(value)) return;
      if (typeof value[0] === "number") { points.push(value); return; }
      value.forEach(visit);
    }
    visit(geometry?.coordinates);
    if (!points.length) throw new Error("Emprise du territoire invalide.");
    return [Math.min(...points.map((p) => p[0])), Math.min(...points.map((p) => p[1])),
      Math.max(...points.map((p) => p[0])), Math.max(...points.map((p) => p[1]))];
  }
  return { create, localPackages, geometryBounds };
});
