(function (root, factory) {
  const formAdapter = typeof module === "object" && module.exports
    ? require("./cheminement-form.js") : root.CheminementForm;
  const GeometryDraft = typeof module === "object" && module.exports
    ? require("./entity-geometry-draft.js") : root.EntityGeometryDraft;
  const api = factory(formAdapter, GeometryDraft);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.CheminementEditor = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (formAdapter, GeometryDraft) {
  "use strict";

  const POLYGON_TYPE_IDS = new Set(["CAC"]);

  function allowedGeometryTypes(typeId) {
    return POLYGON_TYPE_IDS.has(String(typeId || ""))
      ? ["LineString", "Polygon"] : ["LineString"];
  }

  function geometryFromVertices(type, vertices) {
    const coordinates = vertices.map(({ lng, lat }) => [Number(lng), Number(lat)]);
    if (type === "Point") return { type, coordinates: coordinates[0] };
    if (type === "LineString") return { type, coordinates };
    return { type: "Polygon", coordinates: [[...coordinates, coordinates[0]]] };
  }

  function create({ root = globalThis, map, L, fetchJson,
    referenceData = root.ReferenceData,
    entityEditor = root.EntityEditorShell,
    relationSelector = root.RelationSelector,
    mutationController = root.ResourceMutationController,
    concurrency = root.ResourceConcurrency,
    observationCreate = root.ObservationCreate,
    layerControls = root.MapLayerControls,
    openObservation = null, featureStore = null, panel = null }) {
    if (!map || !L || !fetchJson || !referenceData || !entityEditor || !relationSelector
        || !mutationController || !concurrency || !observationCreate || !layerControls || !panel) {
      throw new Error("Dépendances de l'éditeur Cheminement incomplètes.");
    }
    const document = root.document;
    let formView;
    const shell = entityEditor.create({ mode: "read", objectType: "cheminements" });
    const categoryStyle = layerControls.categoryStyle("cheminements");
    const layer = L.geoJSON({ type: "FeatureCollection", features: [] }, {
      pointToLayer(_feature, latlng) {
        return L.circleMarker(latlng, categoryStyle.point);
      },
      style: categoryStyle.path,
      onEachFeature(feature, featureLayer) {
        featureLayer.on("click", () => void openEdit(feature.properties.id));
      },
    }).addTo(map);
    const layersById = new Map();
    let features = [];
    let troncons = [], desordres = [], types = [], relationChoices = null;
    let panelRoot, form, fieldset, status, tronconSelect, desordreSelect, typeSelect, geometryType;
    let geometryStatus, saveButton, cancelButton, drawButton;
    let confirmButton, observationButton, observationsList;
    let drawing = false;
    let vertices = [];
    let draftLayer = null;
    let geometryChanged = false;
    let readSequence = 0;
    let panelSession;
    let acquisition = null;
    let gpsTracking = null;
    let gpsInteractionActive = false;

    const current = () => shell.state.snapshot;
    const idOf = (feature) => String(feature?.properties?.id || feature?.id || "");
    const labelOf = (feature) => feature?.properties?.designation
      || feature?.properties?.libelle || idOf(feature);

    const can = (permission) => !root.BusinessRuntime || root.BusinessRuntime.can(permission);

    function setStatus(message, error = false) {
      status.textContent = message;
      status.classList.toggle("error", Boolean(error));
    }

    function setPending(pending) {
      gpsTracking?.setDisabled(pending);
      fieldset.disabled = pending;
      saveButton.disabled = pending;
      cancelButton.disabled = pending;
      drawButton.disabled = pending;
      confirmButton.disabled = pending;
      observationButton.disabled = pending;
      saveButton.textContent = pending ? "Enregistrement…" : "Enregistrer";
      panelSession.sync();
    }

    function requiredVertices() {
      return { Point: 1, LineString: 2, Polygon: 3 }[geometryType.value];
    }

    function draftGeometry() {
      if (vertices.length < requiredVertices()) return null;
      return geometryFromVertices(geometryType.value, vertices);
    }

    function syncGeometryTypeOptions(preferredType = null) {
      const allowed = allowedGeometryTypes(typeSelect?.value);
      const hasCandidate = Boolean(drawing || geometryChanged || current()?.geometry);
      let desired = preferredType || (geometryChanged || drawing
        ? geometryType.value : current()?.geometry?.type) || geometryType.value || "LineString";
      if (!allowed.includes(desired) && !hasCandidate) desired = "LineString";
      const retained = ["LineString", "Polygon"].includes(desired) ? desired : "LineString";
      formView.geometry.setTypes(allowed.includes(retained) ? allowed : [...allowed, retained]);
      const incompatible = [...geometryType.options].find(option => !allowed.includes(option.value));
      if (incompatible) incompatible.disabled = true;
      geometryType.value = retained;
      return allowed;
    }

    function updateGeometryStatus() {
      const geometry = geometryChanged ? draftGeometry() : current()?.geometry;
      const candidateType = geometry?.type || (drawing ? geometryType.value : null);
      const incompatible = candidateType
        && !allowedGeometryTypes(typeSelect?.value).includes(candidateType);
      geometryStatus.classList.toggle("error", Boolean(incompatible));
      geometryStatus.textContent = incompatible
        ? `${candidateType} incompatible avec ce type de Cheminement. Corrigez le type métier ou la géométrie.`
        : root.EntityFormView.geometryStatus({ geometry, drawing, count: vertices.length, minimum: requiredVertices() });
      GeometryDraft.syncDrawingControls({ draw: drawButton, confirm: confirmButton, cancel: formView?.geometry.cancel, setDrawingActive: formView?.geometry.setDrawingActive, resolveGeometryError: formView?.geometry.resolveGeometryError, ownerState: shell.state },
        { drawing: drawing, type: geometryType.value, count: vertices.length, geometry: draftGeometry() || current()?.geometry });
      if (drawing) panel.updateGeometryDrawing(
        `${vertices.length} sommet(s) — ${requiredVertices()} minimum.`,
        geometryType.value !== "Point" && vertices.length >= requiredVertices(),
      );
    }

    function stopDrawing() {
      if (drawing) { acquisition?.stop(); acquisition = null; }
      if (drawing) panel.finishGeometryDrawing();
      drawing = false;
      gpsTracking?.refresh();

      updateGeometryStatus();
    }

    function redrawDraft() {
      if (draftLayer) map.removeLayer(draftLayer);
      draftLayer = null;
      if (!vertices.length) return;
      if (geometryType.value === "Point") {
        draftLayer = L.marker(vertices[0], { draggable: true }).addTo(map);
        draftLayer.on("dragend", () => {
          vertices = [draftLayer.getLatLng()];
          geometryChanged = true;
          shell.markDirty();
          updateGeometryStatus();
        });
      } else if (geometryType.value === "Polygon" && vertices.length >= 3) {
        draftLayer = L.polygon(vertices, categoryStyle.path).addTo(map);
      } else {
        draftLayer = L.polyline(vertices, categoryStyle.path).addTo(map);
      }
    }

    function applyGeometry(geometry) {
      stopDrawing();
      const coordinates = geometry.type === "Point" ? [geometry.coordinates]
        : geometry.type === "Polygon" ? geometry.coordinates[0].slice(0, -1) : geometry.coordinates;
      vertices = coordinates.map(([lng, lat]) => ({ lng, lat }));
      redrawDraft();
      geometryChanged = true;
      shell.markDirty(formIsDirty());
      updateGeometryStatus();
    }

    function captureVertex(event) {
      if (geometryType.value === "Point") vertices = [event.latlng];
      else vertices.push(event.latlng);
      redrawDraft();
      shell.markDirty();
      if (geometryType.value === "Point") {
        geometryChanged = true;
        stopDrawing();
      }
      updateGeometryStatus();
    }

    function startDrawing() {
      if (shell.state.pending || !can(shell.state.mode === "create" ? "business.create" : "business.update")) return;
      stopDrawing();
      const previous = { points: vertices.slice(), changed: geometryChanged };
      if (draftLayer) map.removeLayer(draftLayer);
      draftLayer = null;
      vertices = [];
      geometryChanged = false;
      drawing = true;
      gpsTracking?.refresh();
      acquisition = GeometryDraft.beginAcquisition({ map, getType: () => geometryType.value,
        getVertices: () => vertices, onVertex: captureVertex, finish: confirmGeometry,
        readDraft: () => previous, restoreDraft(before) {
          vertices = before.points; geometryChanged = before.changed; drawing = false;
          redrawDraft(); panel.finishGeometryDrawing(); shell.markDirty(formIsDirty()); updateGeometryStatus();
        } });
      panel.beginGeometryDrawing({ instruction: "Dessinez sur la carte.",
        cancel: cancelDrawing, finish: confirmGeometry, returnFocusTarget: drawButton });
      updateGeometryStatus();
    }

    function confirmGeometry() {
      if (vertices.length < requiredVertices()) {
        setStatus(`Ce dessin exige ${requiredVertices()} sommet(s) minimum.`, true);
        return;
      }
      geometryChanged = true;
      shell.markDirty();
      stopDrawing();

    }

    function cancelDrawing() {
      acquisition?.cancel(); acquisition = null;
      updateGeometryStatus();
    }

    function resetDraft() {
      gpsTracking?.cancel();
      stopDrawing();
      if (draftLayer) map.removeLayer(draftLayer);
      draftLayer = null;
      vertices = [];
      geometryChanged = false;
      updateGeometryStatus();
    }




    function upsertFeature(feature) {
      const id = idOf(feature);
      features = [...features.filter((item) => idOf(item) !== id), feature];
      const previous = layersById.get(id);
      if (previous) layer.removeLayer(previous);
      layersById.delete(id);
      if (feature.geometry) {
        const temporary = L.geoJSON(feature, {
          pointToLayer(_item, latlng) {
            return L.circleMarker(latlng, categoryStyle.point);
          },
          style: categoryStyle.path,
          onEachFeature(item, featureLayer) {
            featureLayer.on("click", () => void openEdit(item.properties.id));
          },
        });
        temporary.eachLayer((item) => {
          layer.addLayer(item);
          layersById.set(id, item);
        });
      }

    }

    function renderFeature(feature) {
      formAdapter.fill(form, feature, relationChoices || { types, troncons, desordres },
        referenceData, relationSelector);
      syncGeometryTypeOptions(feature?.geometry?.type || "LineString");
      panelSession.sync();
      updateGeometryStatus();
      gpsTracking?.refresh();
    }

    function formIsDirty() {
      return formView.geometry.coordinateEntry?.hasDraft?.() || geometryChanged || formAdapter.isDirty(form,
        shell.state.mode === "create" ? { properties: { valid: true } } : current(), relationSelector);
    }

    function payload() {
      if (gpsTracking?.isActive()) throw new Error("Terminez ou annulez le tracé GPS.");
      if (drawing) throw new Error("Confirmez ou annulez le dessin en cours.");
      const result = formAdapter.payload(form, null, relationSelector, shell.state.mode === "edit" ? current() : null);
      const geometry = entityEditor.requireGeometry({
        mode: shell.state.mode,
        currentGeometry: current()?.geometry,
        geometryChanged,
        draftGeometry: draftGeometry(),
        allowedTypes: allowedGeometryTypes(typeSelect.value),
        label: "ce cheminement",
      });
      if (geometryChanged) result.geometry = geometry;
      return result;
    }

    async function request(url, options = {}) {
      const headers = options.method ? await mutationController.requestHeaders() : {};
      const response = await (root.BusinessRuntime?.fetch || root.fetch)(url, {
        ...options, headers: { ...headers, ...options.headers },
      });
      let body = null;
      try { body = await response.json(); } catch (_error) { /* Réponse HTTP. */ }
      if (!response.ok) throw concurrency.httpError(response.status, body, "Opération Cheminement refusée.");
      if (!body) throw new Error("Réponse Cheminement invalide.");
      return body;
    }

    async function loadRelations(feature = null) {
      const sequence = readSequence;
      const choices = await formAdapter.loadChoices(null, fetchJson, referenceData, feature);
      if (sequence !== readSequence || !panelSession.isActive()) return;
      ({ types, troncons, desordres } = choices);
      relationChoices = choices;
    }

    async function loadObservations(id) {
      const sequence = readSequence;
      return root.ObservationParentList.load({
        container: observationsList, parent: { resource: "cheminements", id },
        path: `/api/cheminements/${encodeURIComponent(id)}/observations`,
        fetchJson, openObservation,
        isCurrent: () => panelSession.isActive() && sequence === readSequence && shell.state.mode === "edit" && String(shell.state.objectId) === String(id),
      });
    }

    async function beginCreate() {
      if (!can("business.create") || !panelSession.prepare()) return;
      const sequence = ++readSequence;
      resetDraft();
      shell.beginCreate({ objectType: "cheminements" });
      form.reset();

      observationsList.replaceChildren();
      panelSession.showTab("general");
      panelSession.update();
      setStatus("Chargement des référentiels et relations…");
      panelSession.setLoading(true);
      try {
        await loadRelations();
        if (sequence !== readSequence || !panelSession.isActive()) return;
        renderFeature({ properties: { valid: true } });
        shell.markDirty(false);
        setStatus("");
        form.elements.designation.focus();
      } catch (error) {
        if (sequence === readSequence && panelSession.isActive()) setStatus(error.message, true);
      } finally {
        if (sequence === readSequence) panelSession.setLoading(false);
      }
    }

    async function openEdit(id) {
      if (!can("business.read") || !panelSession.prepare(id)) return;
      resetDraft();
      const sequence = ++readSequence;
      shell.beginRead({ objectType: "cheminements" });
      form.reset();
      observationsList.replaceChildren();
      panelSession.showTab("general");
      panelSession.update();
      setStatus("Chargement du Cheminement…");
      panelSession.setLoading(true);
      try {
        const feature = await request(`/api/cheminements/${encodeURIComponent(id)}`);
        if (sequence !== readSequence || !panelSession.isActive()) return;
        if (String(feature?.properties?.id || "") !== String(id)) throw new Error("Objet incohérent.");
        await loadRelations(feature);
        if (sequence !== readSequence || !panelSession.isActive()) return;
        shell.beginEdit({ objectType: "cheminements", objectId: id,
          data: feature, snapshot: feature, geometryType: feature.geometry?.type || null });
        renderFeature(feature);

        shell.markDirty(false);
        await loadObservations(id);
        if (sequence !== readSequence || !panelSession.isActive()) return;
        panelSession.update();
        setStatus("État relu depuis PostgreSQL.");
        return feature;
      } catch (error) {
        if (sequence === readSequence && panelSession.isActive()) setStatus(error.message, true);
      } finally {
        if (sequence === readSequence) panelSession.setLoading(false);
      }
    }

    async function submit(event) {
      event.preventDefault();
      if (shell.state.pending || !panelSession.isActive()
          || !can(shell.state.mode === "create" ? "business.create" : "business.update")
          || !form.reportValidity()) return;
      let body;
      try { body = payload(); } catch (error) {
        if(error.geometryCause) shell.fail(error, { conflict: shell.state.conflict });
        setStatus(error.message, true); return;
      }
      const editing = shell.state.mode === "edit";
      shell.beginSave();
      setPending(true);
      setStatus("Enregistrement dans PostgreSQL…");
      try {
        const headers = { "Content-Type": "application/json" };
        if (editing) {
          Object.assign(headers, concurrency.headers(current()?.properties?.version));
          headers["Operation-Id"] = root.crypto.randomUUID();
        }
        const result = await request(editing
          ? `/api/cheminements/${encodeURIComponent(shell.state.objectId)}`
          : "/api/cheminements",
        { method: editing ? "PUT" : "POST", headers, body: JSON.stringify(body) });
        if (!result?.properties?.id || !Number.isInteger(result.properties.version)) {
          throw new Error("Réponse Cheminement sans identifiant ou version.");
        }
        if (editing && String(result.properties.id) !== String(shell.state.objectId)) {
          throw new Error("Réponse Cheminement incohérente.");
        }
        shell.confirm(result);
        if (!editing) shell.beginEdit({ objectType: "cheminements",
          objectId: result.properties.id, data: result, snapshot: result,
          geometryType: result.geometry?.type || null });
        resetDraft();
        upsertFeature(result);
        renderFeature(result);
        panelSession.update();
        await loadObservations(result.properties.id);
        setStatus("Cheminement enregistré et relu depuis PostgreSQL.");
        try {
          await mutationController.publishMutation(result.mutation, { applyLocally: true });
        } catch (error) {
          console.error("Publication Cheminement incomplète", error);
          setStatus("Cheminement enregistré. Actualisation des autres vues incomplète.", true);
        }
      } catch (error) {
        shell.fail(error, { conflict: error.status === 409 });
        setStatus(error.message, true);
      } finally {
        setPending(false);
      }
    }

    function cancel() { return panelSession.close(); }

    function initializeDom() {
      layerControls.createBusinessLayerToggle({
        rootElement: document, map, layer, label: "Cheminements",
        geometryTypes: ["LineString", "Polygon"],
        symbolClass: "legend-cheminement",
        category: "cheminements",
      });
      formView = root.EntityFormView.create({ root, document, descriptor: formAdapter.composition(formAdapter.profile("cheminements")),
        id: "cheminement-editor-panel", onObservations: () => void loadObservations(shell.state.objectId), onClose: () => panelSession.close() });
      panelRoot = formView.element;
      formView.setFormId(`cheminement-editor-form`);
      form = panelRoot.querySelector("form");
      fieldset = panelRoot.querySelector("fieldset");
      status = panelRoot.querySelector("[data-role='status']");
      typeSelect = form.elements.type_cheminement_id;
      tronconSelect = form.elements.troncon_ids;
      desordreSelect = form.elements.desordre_ids;
      geometryType = form.elements.geometry_type;
      geometryStatus = panelRoot.querySelector("[data-role='geometry-status']");
      saveButton = panelRoot.querySelector("[data-role='save']");
      cancelButton = panelRoot.querySelector("[data-role='cancel']");
      drawButton = panelRoot.querySelector("[data-role='draw']");
      confirmButton = panelRoot.querySelector("[data-role='confirm']");
      observationButton = panelRoot.querySelector("[data-role='create-observation']");
      observationButton.dataset.requires = "business.create";
      observationsList = panelRoot.querySelector("[data-role='observations']");
      panelSession = panel.connect({
        element: panelRoot, formView, shell, label: "Cheminement",
        onObservations: () => void loadObservations(shell.state.objectId),
        isDirty: formIsDirty, hasActiveDraft: () => drawing || gpsTracking?.isActive(),
        onDiscard: () => { ++readSequence; resetDraft(); observationsList.replaceChildren(); },
      });
      gpsTracking = root.PwaGeometryTracking?.createWidget({
        root, map, L, container: formView.geometry.methods, ownerId: "cheminement-gps",
        geometryType: () => geometryType.value,
        getOriginal: () => current()?.geometry,
        getDraft: () => draftGeometry() || current()?.geometry,
        draftContext: () => ({ resource: "cheminements", mode: shell.state.mode,
          id: shell.state.objectId, version: current()?.properties?.version ?? null }),
        canStart: () => !shell.state.pending && !shell.state.conflict && !shell.state.forbidden && !drawing
          && allowedGeometryTypes(typeSelect.value).includes(geometryType.value)
          && can(shell.state.mode === "create" ? "business.create" : "business.update"),
        onState: (state) => {
          const active = state.phase !== "idle";
          if (active && !gpsInteractionActive) {
            gpsInteractionActive = true;
            panel.beginGpsTracking?.(() => gpsTracking?.cancel());
          } else if (!active && gpsInteractionActive) {
            gpsInteractionActive = false;
            panel.finishGpsTracking?.();
          }
          const permitted = can(shell.state.mode === "create" ? "business.create" : "business.update");
          drawButton.disabled = active || shell.state.pending || !permitted;
          geometryType.disabled = active || shell.state.pending;

          saveButton.disabled = active || shell.state.pending || !permitted;
        },
        onConfirm: (geometry) => {
          applyGeometry(geometry);
          formView.geometry.status.textContent = "Tracé GPS confirmé dans le brouillon. Enregistrez pour sauvegarder.";
          return true;
        },
      });
      panel.bindCoordinates?.(formView, {
        canApply: () => panelSession.isActive() && !shell.state.pending && !drawing && !gpsTracking?.isActive()
          && can(shell.state.mode === "create" ? "business.create" : "business.update"),
        getGeometry: () => draftGeometry() || current()?.geometry,
        applyGeometry,
      });
      form.addEventListener("submit", submit);
      form.addEventListener("input", () => shell.markDirty(formIsDirty()));
      form.addEventListener("change", () => shell.markDirty(formIsDirty()));
      typeSelect.addEventListener("change", () => {
        syncGeometryTypeOptions();
        updateGeometryStatus();
        gpsTracking?.refresh();
      });
      cancelButton.addEventListener("click", cancel);
      drawButton.addEventListener("click", startDrawing);
      formView.geometry.cancel.addEventListener("click", cancelDrawing);
      confirmButton.addEventListener("click", confirmGeometry);
      geometryType.addEventListener("change", () => {
        const requestedType = geometryType.value;
        if (drawing || geometryChanged) resetDraft();
        geometryType.value = requestedType;
        updateGeometryStatus();
        shell.markDirty(formIsDirty());
        gpsTracking?.refresh();
      });
      observationButton.addEventListener("click", () => {
        const properties = current()?.properties;
        if (properties?.id) observationCreate.open({
          resource: "cheminements", id: properties.id,
          designation: properties.designation || properties.libelle || properties.id,
        }, () => loadObservations(properties.id));
      });
      root.addEventListener("beforeunload", (event) => {
        if (panelSession.isActive() && (shell.state.mode === "create"
          ? formIsDirty() || drawing || gpsTracking?.isActive()
          : shell.hasDraft({ active: drawing || gpsTracking?.isActive() }))) {
          event.preventDefault();
          event.returnValue = "";
        }
      });

      updateGeometryStatus();
    }

    async function load() {
      const collection = await fetchJson("/api/cheminements");
      if (collection?.type !== "FeatureCollection") throw new Error("Collection Cheminement invalide.");
      features = collection.features || [];
      layer.clearLayers();
      layersById.clear();
      features.forEach(upsertFeature);

      return collection;
    }

    initializeDom();
    featureStore?.registerResource("cheminements", {
      fetchOne: (id) => fetchJson(`/api/cheminements/${encodeURIComponent(id)}`),
      fetchCollection: () => fetchJson("/api/cheminements"),
      getId: idOf,
      listIds: () => features.map(idOf),
      upsert: upsertFeature,
      remove: () => {},
      shouldDefer({ id }) {
        return shell.state.mode === "edit"
          && String(shell.state.objectId) === String(id)
          && shell.hasDraft({ active: drawing || gpsTracking?.isActive() });
      },
      markStale() {
        shell.setConflict();
        setStatus("Ce Cheminement a changé sur le serveur. Votre brouillon est conservé.", true);
      },
      clearStale() { shell.setConflict(false); },
    });
    return { layer, load, openEdit, beginCreate, shell, upsertFeature };
  }

  return { create, geometryFromVertices, allowedGeometryTypes };
});
