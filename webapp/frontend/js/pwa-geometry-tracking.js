(function (root, factory) {
  const geometry = typeof module === "object" && module.exports ? require("./pwa-geometry.js") : root.PwaGeometry;
  const api = factory(geometry);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PwaGeometryTracking = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (Geometry) {
  "use strict";

  const MAX_POINTS = 10000;
  const MAX_AGE_MS = 60000;
  const MAX_FUTURE_MS = 30000;
  const MAX_SPEED_MPS = 40;

  const geometryFromPoints = Geometry.geometryFromPoints;

  function createController({ geolocation, settings, now = () => Date.now(), onChange = () => {},
    onConfirm = () => false }) {
    const empty = () => ({ phase: "idle", type: null, points: [], accuracy: null,
      warning: null, validationError: null, geometry: null, rawGeometry: null,
      correctedGeometry: null, correction: null, correctionWarning: null,
      choice: "raw", editedGeometries: {}, geometryRevision: 0, pending: false });
    let state = empty(), unsubscribe = null, generation = 0;
    const snapshot = () => Geometry.clone(state);
    function publish(next) { state = next; onChange(snapshot()); }
    function release() { generation++; const previous = unsubscribe; unsubscribe = null; previous?.(); }
    function cancel() {
      if (state.pending) return false;
      release(); publish(empty()); return true;
    }
    function pause() {
      if (state.phase !== "recording") return;
      release(); publish({ ...state, phase: "paused", warning: null });
    }
    function suspend() { if (state.phase === "recording") pause(); else release(); }
    function restore(draft) {
      if (state.phase !== "idle" || !draft || !["LineString", "Polygon"].includes(draft.type)
          || !Array.isArray(draft.points)) return false;
      release();
      let next = { ...empty(), type: draft.type, points: Geometry.clone(draft.points),
        accuracy: draft.points.at(-1)?.accuracy ?? null, phase: "paused" };
      if (draft.schemaVersion === 2 && draft.phase === "adjusting") {
        next = { ...next, phase: "adjusting", ...Geometry.clone({
          geometry: draft.geometry, rawGeometry: draft.rawGeometry,
          correctedGeometry: draft.correctedGeometry, choice: draft.choice,
          correction: draft.correction, correctionWarning: draft.correctionWarning,
          editedGeometries: draft.editedGeometries, geometryRevision: draft.geometryRevision }) };
        try { Geometry.validate(next.geometry, [next.type]); }
        catch (error) { next.validationError = error.message; }
      } else if (draft.phase === "preview") {
        // Explicitly requested compatibility: retain old, recoverable GPS tracks.
        try { next = { ...next, ...Geometry.prepare(draft.type, draft.points, settings), phase: "adjusting" }; }
        catch (error) { next.validationError = error.message; }
      }
      publish(next); return true;
    }
    function receive(position, token) {
      if (generation !== token || state.phase !== "recording") return;
      const accuracy = Number.isFinite(position?.accuracy) ? position.accuracy : null;
      const last = state.points.at(-1), timestamp = position?.timestamp, time = now();
      let warning = null;
      if (!Number.isFinite(timestamp) || timestamp < time - MAX_AGE_MS
          || timestamp > time + MAX_FUTURE_MS || last && timestamp <= last.timestamp) warning = "Position datée ignorée.";
      else if (accuracy == null || accuracy > settings.loadSettings().tracking.maxAccuracyMeters) warning = "Précision insuffisante.";
      else if (last && settings.haversineMeters(last, position)
          > Math.max(50, MAX_SPEED_MPS * ((timestamp - last.timestamp) / 1000))) warning = "Déplacement aberrant ignoré.";
      else if (!settings.shouldAcceptPosition(position, last, settings.loadSettings())) warning = "Position trop proche ignorée.";
      else if (state.points.length >= MAX_POINTS) warning = "Limite de positions atteinte. Terminez le relevé.";
      if (warning) { publish({ ...state, accuracy, warning }); return; }
      publish({ ...state, accuracy, warning: null, validationError: null, points: [...state.points, { ...position }] });
    }
    function subscribe() {
      const token = ++generation;
      try {
        const stop = geolocation.subscribe(position => receive(position, token), error => {
          if (token !== generation || state.phase !== "recording") return;
          if (["PERMISSION_DENIED", "UNSUPPORTED", "NOT_STANDALONE"].includes(error?.code)) {
            release(); publish({ ...state, phase: "error", warning: error.message });
          } else publish({ ...state, warning: "Signal GPS perdu. Relevé conservé." });
        });
        if (token === generation && state.phase === "recording") unsubscribe = stop; else stop();
      } catch (error) { release(); publish({ ...state, phase: "error", warning: error.message }); }
    }
    function start(type) {
      if (state.phase !== "idle" || !["LineString", "Polygon"].includes(type)) return false;
      if (!geolocation.isStandalone()) { publish({ ...state, phase: "error", warning: "Tracé GPS réservé à la PWA installée." }); return false; }
      release(); publish({ ...empty(), phase: "recording", type }); subscribe();
      return state.phase === "recording";
    }
    function resume() {
      if (state.phase !== "paused") return;
      if (!geolocation.isStandalone()) { publish({ ...state, phase: "error", warning: "Tracé GPS réservé à la PWA installée." }); return; }
      publish({ ...state, phase: "recording", warning: null }); subscribe();
    }
    function finish() {
      if (!["recording", "paused"].includes(state.phase)) return false;
      release();
      try {
        const prepared = Geometry.prepare(state.type, state.points, settings);
        publish({ ...state, ...prepared, phase: "adjusting", warning: null,
          editedGeometries: {}, geometryRevision: state.geometryRevision + 1 });
        return true;
      } catch (error) {
        // Insufficient points: stop and retain the acquisition in pause.
        publish({ ...state, phase: "paused", validationError: error.message }); return false;
      }
    }
    function editGeometry(geometry) {
      if (state.phase !== "adjusting" || state.pending) return false;
      let validationError = null;
      try { Geometry.validate(geometry, [state.type]); } catch (error) { validationError = error.message; }
      const copy = Geometry.clone(geometry);
      publish({ ...state, geometry: copy, validationError, warning: null,
        editedGeometries: { ...state.editedGeometries, [state.choice]: copy },
        geometryRevision: state.geometryRevision + 1 });
      return true;
    }
    function select(choice) {
      if (state.phase !== "adjusting" || state.pending || !["raw", "corrected"].includes(choice)
          || choice === "corrected" && !state.correctedGeometry) return false;
      const geometry = state.editedGeometries[choice]
        || (choice === "raw" ? state.rawGeometry : state.correctedGeometry);
      let validationError = null;
      try { Geometry.validate(geometry, [state.type]); } catch (error) { validationError = error.message; }
      publish({ ...state, choice, geometry: Geometry.clone(geometry), validationError,
        geometryRevision: state.geometryRevision + 1 }); return true;
    }
    async function confirm() {
      if (state.phase !== "adjusting" || state.pending) return false;
      try { Geometry.validate(state.geometry, [state.type]); }
      catch (error) { publish({ ...state, validationError: error.message }); return false; }
      const token = generation;
      publish({ ...state, pending: true, validationError: null, warning: null });
      try {
        if (await onConfirm(Geometry.clone(state.geometry)) !== true) throw new Error("La géométrie n’a pas été acceptée. Le relevé est conservé.");
        if (token !== generation) throw new Error("Le contexte a changé. Le relevé est conservé.");
        publish({ ...state, pending: false }); cancel(); return true;
      } catch (error) {
        publish({ ...state, pending: false, warning: error.message || "Validation impossible." }); return false;
      }
    }
    return { start, pause, resume, finish, editGeometry, select, confirm, cancel, suspend, restore, getState: snapshot };
  }

  function createWidget({ root, map, L, container, ownerId = container.id, geometryType, getOriginal, onConfirm,
    getDraft = getOriginal, restoreDraft = onConfirm,
    draftContext = () => null,
    onState = () => {},
    canStart = () => true, geolocation = root.PwaGeolocation,
    settings = root.PwaMapSettings }) {
    const document = root.document;
    const section = document.createElement("div");
    section.className = "gps-geometry-tracking";
    section.dataset.gpsOwner = ownerId;
    section.setAttribute("role", "group");
    section.setAttribute("aria-label", "Relevé GPS");
    section.tabIndex = -1;
    section.innerHTML = `<div class="gps-geometry-controls">
      <button type="button" class="secondary geometry-method-button" data-gps="start">Tracé GPS</button>
      <button type="button" data-gps="pause" hidden>Pause</button>
      <button type="button" data-gps="finish" hidden>Terminer</button>
      <button type="button" data-gps="confirm" hidden>Valider</button>
      <button type="button" data-gps="cancel" class="secondary" hidden>Annuler</button>
      <button type="button" data-gps="restore" hidden>Reprendre le relevé</button>
      <button type="button" data-gps="abandon" class="secondary" hidden>Abandonner le relevé</button>
    </div><label data-gps="choice-label" class="gps-geometry-choice" hidden>Tracé
      <select data-gps="choice" aria-label="Tracé à ajuster">
        <option value="corrected">Corrigé</option><option value="raw">Brut</option>
      </select></label><p data-gps="error" role="alert" hidden></p>
    <p data-gps="status" role="status" aria-live="polite"></p>`;
    container.append(section);
    const button = (name) => section.querySelector(`[data-gps='${name}']`);
    const ownerForm = container.closest?.("form");
    let formLocked = false, previousFormInert = false;
    function lockForm(active) {
      if (!ownerForm || active === formLocked) return;
      if (active) { previousFormInert = Boolean(ownerForm.inert); ownerForm.inert = true; }
      else ownerForm.inert = previousFormInert;
      formLocked = active;
    }
    let previewLayer = null, originalLayer = null, correctedLayer = null, disabled = false;
    let centeredOnFirstPoint = false, drawnGeometry = null, baselineGeometry = null;
    let editing = false, previousDoubleClickZoom = false;
    const drafts = root.PwaGeometryDrafts;
    const runtime = root.BusinessRuntime;
    const store = drafts?.createStore({ indexedDB: root.indexedDB, runtime });
    let activeContext = null, savedDraft = null, invalidDraft = null, loadedKey = null;
    let loadGeneration = 0, loadingDraft = false, writeQueue = Promise.resolve(), lastWriteSignature = null;
    let destroyed = false, storageConflict = false;
    let storageWarning = store ? null : "Relevé non sauvegardé localement dans ce contexte.";
    function context() {
      const target = draftContext();
      if (!store || !runtime?.isActive() || !target || typeof root.DiguesGpsTerritoryKey !== "string") return null;
      const result = { ...target, type: geometryType(), contextKey: runtime.context.key,
        territoryKey: root.DiguesGpsTerritoryKey };
      result.key = drafts.draftKey(result);
      return result.key ? result : null;
    }
    function mismatch(draft, target = context()) {
      if (!target || draft.territoryKey !== target.territoryKey) return "Le territoire a changé.";
      if (draft.type !== target.type) return "Le type de géométrie a changé.";
      if (draft.version !== target.version) return "La version de l’objet a changé.";
      return null;
    }
    function enqueue(work) {
      writeQueue = writeQueue.catch(() => {}).then(work).catch(error => {
        if (error.code === "GPS_DRAFT_CONFLICT") storageConflict = true;
        storageWarning = `Stockage local indisponible : ${error.message || "erreur"}`;
        render();
      });
      return writeQueue;
    }
    function persist(state) {
      if (!activeContext || !store || state.phase === "idle") return;
      const signature = `${state.phase}:${state.points.length}:${state.points.at(-1)?.timestamp || ""}:${state.geometryRevision}`;
      if (signature === lastWriteSignature) return;
      lastWriteSignature = signature;
      activeContext.revision += 1;
      const draft = { schemaVersion: 2, ...activeContext, phase: state.phase,
        points: state.points.map(point => ({ longitude: point.longitude,
          latitude: point.latitude, timestamp: point.timestamp, accuracy: point.accuracy })),
        baselineGeometry: Geometry.clone(baselineGeometry),
        geometry: state.geometry, rawGeometry: state.rawGeometry,
        correctedGeometry: state.correctedGeometry, correction: state.correction,
        correctionWarning: state.correctionWarning, choice: state.choice,
        editedGeometries: state.editedGeometries, geometryRevision: state.geometryRevision };
      const key = activeContext.key;
      enqueue(() => store.write(key, draft));
    }
    function removeActive() {
      if (!activeContext || !store) return;
      const key = activeContext.key;
      const trackId = activeContext.trackId;
      const revision = activeContext.revision;
      activeContext = null;
      lastWriteSignature = null;
      savedDraft = null;
      invalidDraft = null;
      enqueue(() => store.remove(key, trackId, revision));
    }
    function clearLayers() {
      previewLayer?.disableEdit?.();
      // Restore Leaflet's target registry before removing owned paths: removing
      // them from the temporary registry would resurrect their saved targets.
      if (editing) {
        map.editTools?.unblockEvents?.();
        if (previousDoubleClickZoom) map.doubleClickZoom?.enable();
        editing = false;
      }
      if (previewLayer) map.removeLayer(previewLayer);
      if (originalLayer) map.removeLayer(originalLayer);
      if (correctedLayer) map.removeLayer(correctedLayer);
      previewLayer = originalLayer = correctedLayer = null;
      drawnGeometry = null;
    }
    function draw(state) {
      const signature = state.phase === "adjusting" ? JSON.stringify([state.choice, state.geometry]) : null;
      if (editing && signature === drawnGeometry) {
        if (state.pending) previewLayer.disableEdit?.();
        else if (!previewLayer.editEnabled?.()) previewLayer.enableEdit?.(map);
        return;
      }
      clearLayers();
      if (state.phase === "idle") return;
      const original = baselineGeometry || getOriginal();
      if (original && ["LineString", "Polygon"].includes(original.type)) {
        originalLayer = L.geoJSON({ type: "Feature", geometry: original, properties: {} }, {
          style: { color: "#475569", weight: 5, dashArray: "8 7", fillOpacity: 0.04 },
          interactive: false,
        }).addTo(map);
      }
      if (state.phase === "adjusting" && state.geometry) {
        const coordinates = state.type === "Polygon" ? state.geometry.coordinates[0].slice(0, -1) : state.geometry.coordinates;
        const positions = coordinates.map(([lng, lat]) => [lat, lng]);
        const style = { color: state.choice === "corrected" ? "#168064" : "#e87c16", weight: 5, fillOpacity: 0.18 };
        previewLayer = (state.type === "Polygon" ? L.polygon(positions, style) : L.polyline(positions, style)).addTo(map);
        if (state.choice === "corrected") {
          correctedLayer = L.polyline(state.rawGeometry.coordinates[0].map(([lng, lat]) => [lat, lng]),
            { color: "#e87c16", weight: 3, dashArray: "7 6", interactive: false }).addTo(map);
        }
        previousDoubleClickZoom = Boolean(map.doubleClickZoom?.enabled());
        map.doubleClickZoom?.disable();
        map.editTools?.blockEvents?.();
        editing = true;
        drawnGeometry = signature;
        previewLayer.enableEdit(map);
      } else {
        const positions = state.points.map(point => [point.latitude, point.longitude]);
        if (positions.length) previewLayer = L.polyline(positions, { color: "#e87c16", weight: 5, interactive: false }).addTo(map);
      }
    }
    function edited(event) {
      if (!editing || event.layer !== previewLayer) return;
      const geometry = previewLayer.toGeoJSON(false).geometry;
      drawnGeometry = JSON.stringify([controller.getState().choice, geometry]);
      controller.editGeometry(geometry);
    }
    map.on?.("editable:editing", edited);
    const controller = createController({ geolocation, settings,
      async onConfirm(geometry) {
        await writeQueue;
        function checkContext() {
          const current = context();
          if (destroyed || (runtime && !runtime.isActive()) || disabled || !canStart()) {
            throw new Error("Le formulaire n’autorise plus ce transfert. Le relevé est conservé.");
          }
          if (activeContext && (!current || current.key !== activeContext.key
            || current.contextKey !== activeContext.contextKey
            || current.territoryKey !== activeContext.territoryKey
            || current.type !== activeContext.type
            || current.version !== activeContext.version)) {
            throw new Error("Le contexte ou la version a changé. Annulez ce relevé.");
          }
        }
        checkContext();
        if (storageConflict) throw new Error("Un autre onglet a modifié ce relevé. Il est conservé.");
        if (activeContext && store && !storageWarning) {
          const record = await store.read(activeContext.key);
          if (record?.trackId !== activeContext.trackId || record?.revision !== activeContext.revision) {
            throw new Error("Le relevé GPS a changé dans un autre onglet. Il est conservé.");
          }
        }
        checkContext();
        // These consumers transfer synchronously; only an explicit success
        // permits deletion of the track. A refusal/exception preserves it.
        return onConfirm(geometry);
      },
      onChange(state) {
        if (destroyed) return;
        if (state.phase === "idle") centeredOnFirstPoint = false;
        else if (!centeredOnFirstPoint && state.phase === "recording"
            && state.points.length === 1) {
          const point = state.points[0];
          const maxZoom = map.getMaxZoom();
          const targetZoom = Number.isFinite(maxZoom) ? Math.min(19, maxZoom) : 19;
          map.setView([point.latitude, point.longitude],
            Math.max(map.getZoom(), targetZoom), { animate: false });
          centeredOnFirstPoint = true;
        }
        if (state.phase === "idle") removeActive();
        else persist(state);
        draw(state);
        const phaseChanged = section.dataset.gpsPhase !== state.phase;
        render(state);
        onState(state);
        if (phaseChanged && state.phase === "adjusting" && previewLayer?.getBounds
            && map.fitBounds) {
          map.fitBounds(previewLayer.getBounds(), { animate: false, maxZoom: map.getZoom(),
            paddingTopLeft: [24, 24], paddingBottomRight: [24,
              Math.min(section.offsetHeight + 36, map.getSize().y * 0.65)] });
        }
        if (phaseChanged && state.phase !== "idle") section.focus?.({ preventScroll: true });
      } });
    function loadDraft() {
      const target = context();
      const identity = target && `${target.contextKey}:${target.key}:${target.type}:${target.territoryKey}`;
      if (identity === loadedKey || controller.getState().phase !== "idle") return;
      loadedKey = identity;
      savedDraft = null;
      invalidDraft = null;
      storageWarning = store ? null : "Relevé non sauvegardé localement dans ce contexte.";
      const generation = ++loadGeneration;
      loadingDraft = Boolean(target);
      if (!target) { render(); return; }
      store.read(target.key).then(draft => {
        if (destroyed || generation !== loadGeneration || controller.getState().phase !== "idle") return;
        loadingDraft = false;
        savedDraft = drafts.validDraft(draft, target) ? draft : null;
        invalidDraft = draft && !savedDraft ? draft : null;
        if (invalidDraft) storageWarning = "Relevé local illisible. Abandon explicite requis.";
        render();
      }).catch(error => {
        if (destroyed || generation !== loadGeneration) return;
        loadingDraft = false;
        storageWarning = `Stockage local indisponible : ${error.message || "erreur"}`;
        render();
      });
    }
    function render(state = controller.getState()) {
      if (destroyed) return;
      const available = geolocation.isStandalone()
        && ["LineString", "Polygon"].includes(geometryType());
      const floating = state.phase !== "idle";
      if (floating && section.parentElement !== document.body) document.body.append(section);
      else if (!floating && section.parentElement !== container) container.append(section);
      // A form refresh must not re-enable competing geometry tools or a save
      // while this acquisition owns the map. The GPS bar lives outside it.
      lockForm(floating);
      section.classList.toggle("gps-geometry-tracking--floating", floating);
      section.hidden = !available && state.phase === "idle" && !savedDraft && !invalidDraft;
      button("start").hidden = state.phase !== "idle";
      button("start").disabled = disabled || !canStart() || loadingDraft
        || Boolean(store && !context()) || Boolean(savedDraft || invalidDraft);
      section.dataset.gpsPhase = state.phase;
      section.setAttribute("aria-label", state.phase === "adjusting" ? "Ajustement du relevé GPS" : "Acquisition GPS");
      section.setAttribute("aria-busy", String(state.pending));
      for (const name of ["pause", "finish", "confirm", "cancel", "restore", "abandon", "choice"]) {
        button(name).disabled = disabled || state.pending;
      }
      button("restore").hidden = state.phase !== "idle" || !savedDraft;
      button("restore").disabled = disabled || !canStart() || Boolean(savedDraft && mismatch(savedDraft));
      button("abandon").hidden = state.phase !== "idle" || !(savedDraft || invalidDraft);
      button("pause").hidden = !["recording", "paused"].includes(state.phase);
      button("pause").textContent = state.phase === "paused" ? "Reprendre" : "Pause";
      if (state.phase === "paused" && activeContext && mismatch(activeContext)) button("pause").disabled = true;
      button("finish").hidden = !["recording", "paused"].includes(state.phase);
      button("confirm").hidden = state.phase !== "adjusting";
      button("choice-label").hidden = state.phase !== "adjusting" || !state.correctedGeometry;
      button("choice").value = state.choice;
      button("cancel").hidden = state.phase === "idle";
      button("error").hidden = !state.validationError;
      button("error").textContent = state.validationError || "";
      const labels = { idle: "", recording: "● Relevé en cours", paused: "Relevé en pause",
        adjusting: state.correction
          ? state.choice === "corrected"
            ? "Correction automatique appliquée. Ajustez les sommets ou retrouvez le tracé brut."
            : "Tracé brut sélectionné. Ajustez les sommets, puis validez."
          : "Ajustez les sommets, puis validez.", error: "Relevé interrompu." };
      const accuracy = state.accuracy == null ? "" : ` · précision ±${Math.round(state.accuracy)} m`;
      const contextWarning = activeContext && state.phase !== "idle" ? mismatch(activeContext) : null;
      button("status").textContent = `${savedDraft && state.phase === "idle"
        ? mismatch(savedDraft)
          ? `${mismatch(savedDraft)} Relevé conservé ; changez le contexte ou abandonnez-le.`
          : `Relevé conservé · ${savedDraft.points.length} points. Reprendre ou abandonner.`
        : labels[state.phase]}${state.phase === "idle" ? ""
        : ` · ${state.points.length} points${accuracy}`}${state.correctionWarning ? ` · ${state.correctionWarning}` : ""}${state.warning ? ` · ${state.warning}` : ""}${contextWarning ? ` · ${contextWarning} Annulez ce relevé.` : ""}${storageWarning ? ` · ${storageWarning}` : ""}`;
    }
    button("start").addEventListener("click", () => {
      if (disabled || !canStart() || loadingDraft || savedDraft || invalidDraft) return;
      try { baselineGeometry = Geometry.clone(getDraft?.() || null); }
      catch (error) { storageWarning = error.message; render(); return; }
      const target = context();
      activeContext = target ? { ...target, trackId: root.crypto.randomUUID(), revision: 0 } : null;
      storageConflict = false;
      lastWriteSignature = null;
      controller.start(geometryType());
      if (controller.getState().phase === "idle") activeContext = null;
    });
    button("restore").addEventListener("click", () => {
      const target = context();
      if (!savedDraft || mismatch(savedDraft, target) || !canStart()) return;
      baselineGeometry = Geometry.clone(Object.hasOwn(savedDraft, "baselineGeometry")
        ? savedDraft.baselineGeometry : getDraft?.() ?? null);
      if (savedDraft.schemaVersion === 2 && Object.hasOwn(savedDraft, "baselineGeometry")
          && JSON.stringify(baselineGeometry) !== JSON.stringify(getDraft?.() || null)) {
        try {
          if (restoreDraft(Geometry.clone(baselineGeometry)) !== true) throw new Error("Le brouillon précédent n’a pas pu être restauré.");
        } catch (error) { storageWarning = error.message; render(); return; }
      }
      activeContext = { ...target, trackId: savedDraft.trackId, revision: savedDraft.revision };
      const draft = savedDraft;
      savedDraft = null;
      lastWriteSignature = null;
      centeredOnFirstPoint = draft.points.length > 0;
      if (!controller.restore(draft)) { activeContext = null; savedDraft = draft; render(); }
    });
    button("abandon").addEventListener("click", () => {
      if (!savedDraft && !invalidDraft) return;
      const target = context();
      if (!target) return;
      const draft = savedDraft;
      const invalid = invalidDraft;
      savedDraft = null;
      invalidDraft = null;
      ++loadGeneration;
      enqueue(() => invalid ? store.removeInvalid(target.key, invalid)
        : store.remove(target.key, draft.trackId, draft.revision));
      render();
    });
    button("pause").addEventListener("click", () => {
      if (controller.getState().phase === "paused") controller.resume();
      else controller.pause();
    });
    button("finish").addEventListener("click", () => controller.finish());
    button("confirm").addEventListener("click", () => void controller.confirm());
    button("choice").addEventListener("change", () => controller.select(button("choice").value));
    const cancel = () => {
      if (runtime && !runtime.isActive()) { controller.suspend(); return false; }
      return controller.cancel();
    };
    button("cancel").addEventListener("click", cancel);
    const suspend = () => controller.suspend();
    const territoryChanged = () => {
      if (activeContext && activeContext.territoryKey !== root.DiguesGpsTerritoryKey) controller.suspend();
      loadDraft();
    };
    const resize = () => render();
    const headerCapabilities = () => {
      if (!geolocation.isStandalone()) controller.suspend();
      render();
    };
    root.addEventListener?.("pagehide", suspend);
    root.addEventListener?.("digues-app:context-closed", suspend);
    root.addEventListener?.("digues-app:gps-territory-changed", territoryChanged);
    root.addEventListener?.("resize", resize);
    const editorPanel = container.closest?.("#editor-panel");
    let observer = null;
    if (editorPanel && typeof root.MutationObserver === "function") {
      observer = new root.MutationObserver(() => {
        if (editorPanel.hidden && controller.getState().phase !== "idle") cancel();
      });
      observer.observe(editorPanel, { attributes: true, attributeFilter: ["hidden"] });
    }
    document.addEventListener?.("digues-app:header-capabilities", headerCapabilities);
    render();
    loadDraft();
    return {
      cancel,
      focus() { section.focus?.({ preventScroll: true }); },
      refresh() { render(); loadDraft(); },
      setDisabled(value) { disabled = Boolean(value); render(); },
      isActive: () => controller.getState().phase !== "idle",
      getState: controller.getState,
      destroy() {
        if (destroyed) return;
        controller.suspend();
        destroyed = true;
        ++loadGeneration;
        root.removeEventListener?.("pagehide", suspend);
        root.removeEventListener?.("digues-app:context-closed", suspend);
        root.removeEventListener?.("digues-app:gps-territory-changed", territoryChanged);
        root.removeEventListener?.("resize", resize);
        document.removeEventListener?.("digues-app:header-capabilities", headerCapabilities);
        observer?.disconnect();
        clearLayers();
        lockForm(false);
        map.off?.("editable:editing", edited);
        section.remove();
      },
    };
  }

  return { createController, createWidget, geometryFromPoints };
});
