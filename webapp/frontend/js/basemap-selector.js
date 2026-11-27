(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.BasemapSelector = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  const ID = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;

  function normalizeCatalogue(payload) {
    if (payload?.contract_version !== 1 || !Array.isArray(payload.basemaps)) {
      throw new Error("Contrat du catalogue de fonds invalide.");
    }
    const seen = new Set();
    const entries = payload.basemaps.map((entry) => {
      const zoom = entry?.zoom;
      const url = entry?.delivery?.url;
      if (!ID.test(entry?.id || "") || seen.has(entry.id) ||
          typeof entry.name !== "string" || !entry.name.trim() ||
          !Number.isSafeInteger(entry.order) ||
          entry.delivery?.adapter !== "tilejson" ||
          typeof url !== "string" ||
          !/^\/basemap\/[a-z0-9/-]+\.json$/.test(url) ||
          !Number.isSafeInteger(zoom?.native_min) ||
          !Number.isSafeInteger(zoom?.native_max) ||
          !Number.isSafeInteger(zoom?.display_max) ||
          zoom.native_min < 0 || zoom.native_min > zoom.native_max ||
          zoom.display_max !== zoom.native_max + 1 ||
          typeof entry.attribution !== "string") {
        throw new Error("Entrée de fond cartographique invalide.");
      }
      seen.add(entry.id);
      return entry;
    }).sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
    if (payload.default_basemap_id !== null && payload.default_basemap_id !== undefined &&
        !seen.has(payload.default_basemap_id)) {
      throw new Error("Fond par défaut absent du catalogue.");
    }
    return { entries, defaultId: payload.default_basemap_id || entries[0]?.id || null };
  }

  function create({ map, L, container, basemapApi, documentObject, embedded = false,
    runtime, fetchImpl, offlineSource, onChange = () => {}, logger = console } = {}) {
    if (!map || !L?.tileLayer || !container || !basemapApi?.loadStrict || !basemapApi?.load) {
      throw new Error("Sélecteur de fonds incomplet.");
    }
    const win = runtime || (typeof window !== "undefined" ? window : globalThis);
    const doc = documentObject || win.document;
    const request = fetchImpl || ((url, options) => win.BusinessRuntime?.fetch
      ? win.BusinessRuntime.fetch(url, options) : win.fetch(url, options));
    const local = offlineSource || { getState: async () => null };
    let entries = [];
    let defaultId = null;
    let packages = {};
    let active = null;
    let activeLayer = null;
    let activeTileError = null;
    let offline = false;
    let issue = "";
    let scaleIssue = "";
    let selectionEpoch = 0;
    let refreshEpoch = 0;
    let legacyUrl = null;

    const trigger = doc.createElement("button");
    trigger.type = "button";
    trigger.className = "basemap-selector-toggle";
    trigger.setAttribute("aria-controls", "basemap-selector-options");
    trigger.setAttribute("aria-haspopup", "true");
    const panel = doc.createElement("div");
    panel.id = "basemap-selector-options";
    panel.className = "basemap-selector-options";
    panel.setAttribute("role", "group");
    panel.setAttribute("aria-label", "Fonds de carte disponibles");
    panel.hidden = true;
    const status = doc.createElement("p");
    status.className = "basemap-selector-status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    status.hidden = true;
    container.append(trigger, panel, status);
    if (embedded) {
      trigger.hidden = true;
      panel.hidden = false;
    }
    L.DomEvent?.disableClickPropagation?.(container);
    L.DomEvent?.disableScrollPropagation?.(container);
    container.addEventListener("pointerdown", (event) => event.stopPropagation());

    function showNotice() {
      const message = issue || scaleIssue;
      status.textContent = message;
      status.hidden = !message || !panel.hidden;
    }

    function setIssue(message) {
      issue = message || "";
      showNotice();
    }

    function updateScaleNotice() {
      const entry = active?.entry;
      scaleIssue = entry && map.getZoom() < entry.zoom.native_min
        ? `${entry.name} n’est pas disponible sous le zoom ${entry.zoom.native_min}. Dézoomez avec un autre fond ou zoomez pour l’afficher.`
        : "";
      showNotice();
    }

    function setOpen(open) {
      panel.hidden = embedded ? false : !open;
      trigger.setAttribute("aria-expanded", String(open));
      showNotice();
    }

    function render() {
      trigger.textContent = `Fond : ${active?.name || "aucun"}`;
      trigger.setAttribute("aria-label", `Choisir un fond de carte. Actif : ${active?.name || "aucun"}`);
      panel.replaceChildren();
      for (const entry of entries) {
        const ready = !offline || Boolean(packages[entry.id]);
        const button = doc.createElement("button");
        button.type = "button";
        button.className = "basemap-selector-choice";
        button.disabled = !ready;
        button.setAttribute("aria-pressed", String(active?.id === entry.id));
        button.textContent = `${active?.id === entry.id ? "✓ " : ""}${entry.name}`;
        const availability = doc.createElement("small");
        availability.textContent = offline
          ? ready ? packages[entry.id]?.partial ? "Couverture partielle hors ligne" : "Disponible hors ligne"
            : "Hors ligne indisponible"
          : packages[entry.id] ? "Disponible aussi hors ligne" : "En ligne";
        button.append(availability);
        button.addEventListener("click", () => { void select(entry.id, { remember: true }); });
        panel.append(button);
      }
      trigger.disabled = entries.length === 0;
      if (trigger.disabled) setOpen(false);
      updateScaleNotice();
    }

    function clearActive() {
      selectionEpoch++;
      if (activeLayer) {
        if (activeTileError) activeLayer.off?.("tileerror", activeTileError);
        map.removeLayer(activeLayer);
      }
      activeLayer = null;
      activeTileError = null;
      active = null;
      onChange(null);
      render();
    }

    function install(entry, descriptor, fallbackName = "OpenStreetMap (secours)") {
      const center = map.getCenter();
      const previousZoom = map.getZoom();
      const layer = L.tileLayer(descriptor.url, { ...descriptor.options, pane: "tilePane" });
      const tileError = () => {
        if (activeLayer === layer) {
          setIssue(`Certaines tuiles de ${entry?.name || "ce fond"} sont indisponibles. Les couches métier restent visibles.`);
        }
      };
      layer.on?.("tileerror", tileError);
      if (activeLayer) {
        if (activeTileError) activeLayer.off?.("tileerror", activeTileError);
        map.removeLayer(activeLayer);
      }
      activeLayer = layer;
      activeTileError = tileError;
      active = { ...descriptor, id: entry?.id || null,
        name: entry?.name || fallbackName, entry: entry || null,
        capabilities: entry?.capabilities || null };
      setIssue("");
      map.setMaxZoom(descriptor.options.maxZoom);
      if (previousZoom > descriptor.options.maxZoom) {
        map.setView(center, descriptor.options.maxZoom, { animate: false });
      }
      layer.addTo(map);
      onChange(active);
      render();
      setOpen(false);
      return active;
    }

    async function installFallback(message) {
      if (offline) {
        clearActive();
        setIssue(message || "Aucun fond cartographique préparé hors connexion.");
        return null;
      }
      const descriptor = await basemapApi.load(legacyUrl);
      install(null, descriptor, descriptor === basemapApi.fallback
        ? "OpenStreetMap (secours)" : "Fond configuré (secours)");
      setIssue(message || "Aucun fond publié ; OpenStreetMap est utilisé en secours.");
      return active;
    }

    function validDescriptor(entry, descriptor, localPackage = false) {
      const options = descriptor?.options;
      if (options?.minZoom !== entry.zoom.native_min ||
          options?.maxNativeZoom !== entry.zoom.native_max ||
          options?.maxZoom !== entry.zoom.display_max ||
          typeof descriptor.url !== "string") {
        throw new Error("Zooms du TileJSON incompatibles avec le catalogue.");
      }
      const resolved = new URL(descriptor.url, win.location.href);
      const prefix = localPackage ? "/__offline-basemaps/" : "/basemap/";
      if (resolved.origin !== win.location.origin || !resolved.pathname.startsWith(prefix) ||
          resolved.search || resolved.hash) {
        throw new Error("Gabarit de tuiles hors du service cartographique autorisé.");
      }
      const tileUrl = resolved.href.replace(/%7B/gi, "{").replace(/%7D/gi, "}");
      return { url: tileUrl, partial: Boolean(descriptor.partial), options: { ...options,
        minZoom: entry.zoom.native_min, maxNativeZoom: entry.zoom.native_max,
        maxZoom: entry.zoom.display_max, attribution: entry.attribution } };
    }

    const preferenceKey = win.BusinessRuntime?.key?.("basemap-choice-v1") || null;
    async function select(id, { remember = false } = {}) {
      const entry = entries.find((item) => item.id === id);
      if (!entry || (offline && !packages[id])) return false;
      const epoch = ++selectionEpoch;
      try {
        const descriptor = offline ? packages[id] : await basemapApi.loadStrict(entry.delivery.url);
        if (epoch !== selectionEpoch) return false;
        const resolved = validDescriptor(entry, descriptor, offline);
        install(entry, resolved);
        if (remember && preferenceKey) {
          try { win.localStorage?.setItem(preferenceKey, id); }
          catch (_) { setIssue("Fond changé ; préférence locale indisponible."); }
        }
        return true;
      } catch (error) {
        if (epoch !== selectionEpoch) return false;
        logger.warn(`Fond ${id} indisponible`, error);
        setIssue(`${entry.name} est indisponible. ${active
          ? `Le fond ${active.name} est conservé.` : "Aucun fond n’est affiché."}`);
        return false;
      }
    }

    async function readCatalogue() {
      const response = await request("/api/basemaps", { cache: "no-store", credentials: "same-origin" });
      if (!response.ok) throw new Error(`Catalogue HTTP ${response.status}`);
      const payload = await response.json();
      const catalogue = normalizeCatalogue(payload);
      try { await local.saveCatalogue?.(payload); } catch (error) {
        logger.warn("Catalogue local non enregistré", error);
      }
      return catalogue;
    }

    async function readOfflineState() {
      const state = await local.getState();
      if (!state) return null;
      const catalogue = normalizeCatalogue(state.catalogue);
      return { ...catalogue, packages: state.packages || {} };
    }

    async function refresh() {
      const epoch = ++refreshEpoch;
      selectionEpoch++; // A catalogue change supersedes an in-flight TileJSON selection.
      const networkStatus = win.OfflineRuntime?.getNetworkState?.()?.status;
      const nowOffline = networkStatus === "offline" ||
        (networkStatus !== "online" && win.navigator?.onLine === false);
      let next;
      let nextPackages = {};
      try {
        if (nowOffline) {
          const state = await readOfflineState();
          next = state || { entries: [], defaultId: null };
          nextPackages = state?.packages || {};
        } else {
          next = await readCatalogue();
          try { nextPackages = (await readOfflineState())?.packages || {}; } catch (error) {
            logger.warn("État local des fonds indisponible", error);
          }
        }
      } catch (error) {
        if (epoch !== refreshEpoch) return;
        logger.warn("Catalogue des fonds indisponible", error);
        if (!nowOffline && active && !offline) {
          setIssue("Catalogue des fonds indisponible ; le fond actif est conservé.");
          return;
        }
        entries = [];
        packages = {};
        offline = nowOffline;
        clearActive();
        await installFallback(nowOffline
          ? "Catalogue des fonds indisponible hors connexion."
          : "Catalogue des fonds indisponible ; fond de secours utilisé.");
        return;
      }
      if (epoch !== refreshEpoch) return;
      selectionEpoch++;
      const wasOffline = offline;
      offline = nowOffline;
      entries = next.entries;
      defaultId = next.defaultId;
      packages = nextPackages;
      const currentEntry = entries.find((entry) => entry.id === active?.id);
      if (active && (!currentEntry || wasOffline !== offline ||
          JSON.stringify(currentEntry) !== JSON.stringify(active.entry) ||
          offline && (!packages[active.id] || packages[active.id].url !== active.url))) clearActive();
      if (active && currentEntry) {
        active.entry = currentEntry;
        active.name = currentEntry.name;
        render();
        return;
      }
      render();
      let remembered = null;
      try { remembered = preferenceKey && win.localStorage?.getItem(preferenceKey); } catch (_) { /* optional preference */ }
      const preferred = currentEntry?.id || remembered || defaultId;
      const candidate = entries.find((entry) => entry.id === preferred &&
        (!offline || packages[entry.id])) || entries.find((entry) => !offline || packages[entry.id]);
      if (candidate && await select(candidate.id)) return;
      await installFallback(offline
        ? "Aucun paquet de fond cartographique n’est prêt hors connexion."
        : entries.length ? "Fond publié indisponible ; fond de secours utilisé."
          : "Aucun fond publié ; OpenStreetMap est utilisé en secours.");
    }

    trigger.addEventListener("click", () => setOpen(panel.hidden));
    container.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || panel.hidden) return;
      event.preventDefault();
      setOpen(false);
      trigger.focus();
    });
    const outside = (event) => { if (!container.contains(event.target)) setOpen(false); };
    doc.addEventListener("pointerdown", outside);
    const connectivity = () => { void refresh(); };
    win.addEventListener?.("online", connectivity);
    win.addEventListener?.("offline", connectivity);
    win.addEventListener?.("digues-app:network-state", connectivity);
    map.on("zoomend", updateScaleNotice);
    render();

    return {
      async start(tilejsonUrl) { legacyUrl = tilejsonUrl; await refresh(); return active; },
      refresh,
      async updateLocalAvailability() {
        if (offline) return refresh();
        try { packages = (await readOfflineState())?.packages || {}; render(); }
        catch (error) { logger.warn("Disponibilité locale indisponible", error); }
      },
      select,
      getActive: () => active,
      getAvailable: () => entries.map((entry) => ({ id: entry.id, name: entry.name,
        online: !offline, offlineReady: Boolean(packages[entry.id]) })),
      destroy() {
        selectionEpoch++;
        refreshEpoch++;
        doc.removeEventListener("pointerdown", outside);
        win.removeEventListener?.("online", connectivity);
        win.removeEventListener?.("offline", connectivity);
        win.removeEventListener?.("digues-app:network-state", connectivity);
        map.off("zoomend", updateScaleNotice);
        clearActive();
        container.replaceChildren();
      },
    };
  }

  return { create, normalizeCatalogue };
});
