(function (root, factory) {
  const GeometryDraft = typeof module === "object" && module.exports
    ? require("./entity-geometry-draft.js") : root.EntityGeometryDraft;
  const api = factory(GeometryDraft);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ReseauTechniqueEditor = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (GeometryDraft) {
  "use strict";

  function geometryFromLatLngs(type, latlngs) {
    const positions = latlngs.map((latlng) => [Number(latlng.lng), Number(latlng.lat)]);
    return type === "Point"
      ? { type, coordinates: positions[0] }
      : { type, coordinates: positions };
  }

  function create({
    root = globalThis,
    map,
    L,
    fetchJson,
    referenceData = root.ReferenceData,
    businessForm = root.MobilierForm,
    entityEditor = root.EntityEditorShell,
    relationSelector = root.RelationSelector,
    mutationController = root.ResourceMutationController,
    concurrency = root.ResourceConcurrency,
    observationCreate = root.ObservationCreate,
    layerControls = root.MapLayerControls,
    openObservation = null,
    featureStore = null, panel = null,
  }) {
    if (!map || !L || !fetchJson || !referenceData || !businessForm || !entityEditor
        || !relationSelector || !mutationController || !concurrency || !layerControls || !panel) {
      throw new Error("Dépendances de l'éditeur Réseau technique incomplètes.");
    }

    const shell = entityEditor.create({ mode: "read", objectType: "reseaux-techniques" });
    const profile = businessForm.profile("reseaux_techniques");
    const document = root.document;
    let formView;
    const categoryStyle = layerControls.categoryStyle("reseaux-techniques");
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
    let choices = { types: [], troncons: [] };
    let panelRoot;
    let form;
    let fieldset;
    let status;
    let geometryType;
    let geometryStatus;
    let saveButton;
    let cancelButton;
    let drawButton;
    let confirmGeometryButton;
    let observationButton;
    let observationsList;
    let draftLayer = null;
    let draftPoints = [];
    let geometryChanged = false;
    let drawingActive = false;
    let readSequence = 0;
    let panelSession;
    let acquisition = null;
    let gpsTracking = null;
    let gpsInteractionActive = false;

    function featureId(feature) {
      return String(feature?.properties?.id || feature?.id || "");
    }

    function featureLabel(feature) {
      const properties = feature?.properties || {};
      return properties.designation || properties.type_libelle || featureId(feature);
    }

    function currentFeature() {
      return shell.state.snapshot;
    }

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
      confirmGeometryButton.disabled = pending;
      observationButton.disabled = pending;
      saveButton.textContent = pending ? "Enregistrement…" : "Enregistrer";
      panelSession.sync();
    }

    function draftGeometry() {
      if (!draftPoints.length) return null;
      if (geometryType.value === "Point") return geometryFromLatLngs("Point", draftPoints);
      if (draftPoints.length < 2) return null;
      return geometryFromLatLngs("LineString", draftPoints);
    }

    function updateGeometryStatus() {
      geometryStatus.textContent = root.EntityFormView.geometryStatus({
        geometry: draftGeometry() || currentFeature()?.geometry, drawing: drawingActive,
        count: draftPoints.length, minimum: geometryType.value === "Point" ? 1 : 2,
      });
      GeometryDraft.syncDrawingControls({ draw: drawButton, confirm: confirmGeometryButton, cancel: formView?.geometry.cancel, setDrawingActive: formView?.geometry.setDrawingActive, resolveGeometryError: formView?.geometry.resolveGeometryError, ownerState: shell.state },
        { drawing: drawingActive, type: geometryType.value, count: draftPoints.length, geometry: draftGeometry() || currentFeature()?.geometry });
      if (drawingActive) panel.updateGeometryDrawing(
        `${draftPoints.length} sommet(s) — ${geometryType.value === "Point" ? 1 : 2} minimum.`,
        geometryType.value !== "Point" && draftPoints.length >= 2,
      );
    }

    function stopDrawing() {
      if (!drawingActive) return;
      drawingActive = false;
      gpsTracking?.refresh();
      { acquisition?.stop(); acquisition = null; }
      panel.finishGeometryDrawing();

      updateGeometryStatus();
    }

    function removeDraftLayer() {
      if (draftLayer) map.removeLayer(draftLayer);
      draftLayer = null;
    }

    function redrawDraft() {
      removeDraftLayer();
      if (!draftPoints.length) return;
      if (geometryType.value === "Point") {
        draftLayer = L.marker(draftPoints[0], { draggable: true }).addTo(map);
        draftLayer.on("dragend", () => {
          draftPoints = [draftLayer.getLatLng()];
          geometryChanged = true;
          shell.markDirty();
          updateGeometryStatus();
        });
      } else {
        draftLayer = L.polyline(draftPoints, categoryStyle.path).addTo(map);
      }
    }

    function applyGeometry(geometry) {
      stopDrawing();
      const coordinates = geometry.type === "Point" ? [geometry.coordinates]
        : geometry.type === "Polygon" ? geometry.coordinates[0].slice(0, -1) : geometry.coordinates;
      draftPoints = coordinates.map(([lng, lat]) => ({ lng, lat }));
      redrawDraft();
      geometryChanged = true;
      shell.markDirty(formIsDirty());
      updateGeometryStatus();
    }

    function captureVertex(event) {
      if (geometryType.value === "Point") {
        draftPoints = [event.latlng];
        redrawDraft();
        geometryChanged = true;
        shell.markDirty();
        stopDrawing();
        return;
      }
      draftPoints.push(event.latlng);
      redrawDraft();
      shell.markDirty();
      updateGeometryStatus();
    }

    function startDrawing() {
      if (shell.state.pending || !can(shell.state.mode === "create" ? "business.create" : "business.update")) return;
      stopDrawing();
      const previous = { points: draftPoints.slice(), changed: geometryChanged };
      removeDraftLayer();
      draftPoints = [];
      geometryChanged = false;
      drawingActive = true;
      gpsTracking?.refresh();

      acquisition = GeometryDraft.beginAcquisition({ map, getType: () => geometryType.value,
        getVertices: () => draftPoints, onVertex: captureVertex, finish: confirmGeometry,
        readDraft: () => previous, restoreDraft(before) {
          draftPoints = before.points; geometryChanged = before.changed; drawingActive = false;
          redrawDraft(); panel.finishGeometryDrawing(); shell.markDirty(formIsDirty()); updateGeometryStatus();
        } });
      panel.beginGeometryDrawing({
        instruction: geometryType.value === "Point" ? "Choisissez le Point sur la carte."
          : "Ajoutez au moins deux sommets puis confirmez la LineString.",
        cancel: cancelDrawing, finish: confirmGeometry, returnFocusTarget: drawButton,
      });
      updateGeometryStatus();
    }

    function confirmGeometry() {
      if (geometryType.value !== "LineString" || draftPoints.length < 2) {
        setStatus("Une LineString exige au moins deux sommets.", true);
        return;
      }
      geometryChanged = true;
      shell.markDirty();
      stopDrawing();

    }




    function upsertFeature(feature) {
      const id = featureId(feature);
      features = [...features.filter((item) => featureId(item) !== id), feature];
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
        temporary.eachLayer((featureLayer) => {
          layer.addLayer(featureLayer);
          layersById.set(id, featureLayer);
        });
      }

    }

    function renderFeature(feature) {
      businessForm.fill(form, feature, choices, referenceData, relationSelector);
      geometryType.value = feature.geometry?.type || "Point";
      panelSession.sync();
      updateGeometryStatus();
      gpsTracking?.refresh();
    }

    function formIsDirty() {
      return formView.geometry.coordinateEntry?.hasDraft?.() || geometryChanged || businessForm.isDirty(form,
        shell.state.mode === "create" ? { properties: { valid: true } } : currentFeature(),
        relationSelector);
    }

    function payload() {
      if (gpsTracking?.isActive()) throw new Error("Terminez ou annulez le tracé GPS.");
      if (drawingActive) throw new Error("Confirmez ou annulez le dessin en cours.");
      const feature = shell.state.mode === "edit" ? currentFeature() : null;
      const result = businessForm.payload(form, profile, relationSelector, feature);
      if (geometryChanged) result.geometry = draftGeometry();
      return result;
    }

    async function request(url, options = {}) {
      const originHeaders = options.method ? await mutationController.requestHeaders() : {};
      const response = await (root.BusinessRuntime?.fetch || root.fetch)(url, {
        ...options, headers: { ...originHeaders, ...options.headers },
      });
      let body = null;
      try { body = await response.json(); } catch (_error) { /* HTTP fallback. */ }
      if (!response.ok) {
        throw concurrency.httpError(response.status, body, "Opération Réseau refusée.");
      }
      if (!body) throw new Error("Réponse Réseau invalide.");
      return body;
    }

    async function loadReferences(feature = null) {
      const sequence = readSequence;
      const loaded = await businessForm.loadChoices(profile, fetchJson, referenceData, feature);
      if (sequence !== readSequence || !panelSession.isActive()) return;
      choices = loaded;
      businessForm.fill(form, feature || { properties: { valid: true } },
        choices, referenceData, relationSelector);
    }

    async function loadObservations(id) {
      const sequence = readSequence;
      return root.ObservationParentList.load({
        container: observationsList, parent: { resource: "reseaux_techniques", id },
        path: `/api/reseaux-techniques/${encodeURIComponent(id)}/observations`,
        fetchJson, openObservation,
        isCurrent: () => panelSession.isActive() && sequence === readSequence && shell.state.mode === "edit" && String(shell.state.objectId) === String(id),
      });
    }

    function cancelDrawing() {
      acquisition?.cancel(); acquisition = null;
      updateGeometryStatus();
    }

    function resetDraft() {
      gpsTracking?.cancel();
      stopDrawing();
      removeDraftLayer();
      draftPoints = [];
      geometryChanged = false;
    }

    async function beginCreate() {
      if (!can("business.create") || !panelSession.prepare()) return;
      const sequence = ++readSequence;
      resetDraft();
      shell.beginCreate({ objectType: "reseaux-techniques" });
      form.reset();
      geometryType.value = "Point";

      observationsList.replaceChildren();
      panelSession.showTab("general");
      panelSession.update();
      setStatus("Chargement des choix…");
      panelSession.setLoading(true);
      try {
        await loadReferences();
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
      shell.beginRead({ objectType: "reseaux-techniques" });
      form.reset();
      observationsList.replaceChildren();
      panelSession.showTab("general");
      panelSession.update();
      setStatus("Chargement du Réseau technique…");
      panelSession.setLoading(true);
      try {
        const feature = await request(`/api/reseaux-techniques/${encodeURIComponent(id)}`);
        if (sequence !== readSequence || !panelSession.isActive()) return;
        if (String(feature?.properties?.id || "") !== String(id)) throw new Error("Objet incohérent.");
        await loadReferences(feature);
        if (sequence !== readSequence || !panelSession.isActive()) return;
        shell.beginEdit({ objectType: "reseaux-techniques", objectId: id,
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
          Object.assign(headers, concurrency.headers(currentFeature()?.properties?.version));
          headers["Operation-Id"] = root.crypto.randomUUID();
        }
        const result = await request(
          editing
            ? `/api/reseaux-techniques/${encodeURIComponent(shell.state.objectId)}`
            : "/api/reseaux-techniques",
          { method: editing ? "PUT" : "POST", headers, body: JSON.stringify(body) },
        );
        if (!result?.properties?.id || !Number.isInteger(result.properties.version)) {
          throw new Error("Réponse Réseau technique sans identifiant ou version.");
        }
        if (editing && String(result.properties.id) !== String(shell.state.objectId)) {
          throw new Error("Réponse Réseau technique incohérente.");
        }
        shell.confirm(result);
        if (!editing) shell.beginEdit({ objectType: "reseaux-techniques",
          objectId: result.properties.id, data: result, snapshot: result,
          geometryType: result.geometry?.type || null });
        resetDraft();
        upsertFeature(result);
        renderFeature(result);
        panelSession.update();
        await loadObservations(result.properties.id);
        setStatus("Réseau technique enregistré et relu depuis PostgreSQL.");
        try {
          await mutationController.publishMutation(result.mutation, { applyLocally: true });
        } catch (error) {
          console.error("Publication Réseau incomplète", error);
          setStatus("Réseau enregistré. L'actualisation des autres vues est incomplète.", true);
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
        rootElement: document, map, layer, label: "Réseaux techniques",
        geometryTypes: ["Point", "LineString"], symbolClass: "legend-reseau-technique",
        category: "reseaux-techniques",
      });

      formView = root.EntityFormView.create({ root, document, descriptor: businessForm.composition(businessForm.profile("reseaux_techniques")),
        id: "reseau-technique-editor-panel", onObservations: () => void loadObservations(shell.state.objectId), onClose: () => panelSession.close() });
      panelRoot = formView.element;
      formView.setFormId(`reseau-technique-editor-form`);
      form = panelRoot.querySelector("form");
      fieldset = panelRoot.querySelector("fieldset");
      status = panelRoot.querySelector("[data-role='status']");
      geometryType = form.elements.geometry_type;
      geometryStatus = panelRoot.querySelector("[data-role='geometry-status']");
      saveButton = panelRoot.querySelector("[data-role='save']");
      cancelButton = panelRoot.querySelector("[data-role='cancel']");
      drawButton = panelRoot.querySelector("[data-role='draw']");
      confirmGeometryButton = panelRoot.querySelector("[data-role='confirm']");
      observationButton = panelRoot.querySelector("[data-role='create-observation']");
      observationButton.dataset.requires = "business.create";
      observationsList = panelRoot.querySelector("[data-role='observations']");
      panelSession = panel.connect({
        element: panelRoot, formView, shell, label: "Réseau technique",
        onObservations: () => void loadObservations(shell.state.objectId),
        isDirty: formIsDirty, hasActiveDraft: () => drawingActive || gpsTracking?.isActive(),
        onDiscard: () => { ++readSequence; resetDraft(); observationsList.replaceChildren(); },
      });
      gpsTracking = root.PwaGeometryTracking?.createWidget({
        root, map, L, container: formView.geometry.methods, ownerId: "reseau-technique-gps",
        geometryType: () => geometryType.value,
        getOriginal: () => currentFeature()?.geometry,
        getDraft: () => draftGeometry() || currentFeature()?.geometry,
        draftContext: () => ({ resource: "reseaux-techniques", mode: shell.state.mode,
          id: shell.state.objectId, version: currentFeature()?.properties?.version ?? null }),
        canStart: () => !shell.state.pending && !shell.state.conflict && !shell.state.forbidden && !drawingActive
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
        canApply: () => panelSession.isActive() && !shell.state.pending && !drawingActive && !gpsTracking?.isActive()
          && can(shell.state.mode === "create" ? "business.create" : "business.update"),
        getGeometry: () => draftGeometry() || currentFeature()?.geometry,
        applyGeometry,
      });
      form.addEventListener("submit", submit);
      form.addEventListener("input", () => shell.markDirty(formIsDirty()));
      form.addEventListener("change", () => shell.markDirty(formIsDirty()));
      cancelButton.addEventListener("click", cancel);
      drawButton.addEventListener("click", startDrawing);
      formView.geometry.cancel.addEventListener("click", cancelDrawing);
      confirmGeometryButton.addEventListener("click", confirmGeometry);
      geometryType.addEventListener("change", () => {
        if (drawingActive || geometryChanged) restoreGeometry();
        gpsTracking?.refresh();
      });
      observationButton.addEventListener("click", () => {
        const properties = currentFeature()?.properties;
        if (properties?.id) observationCreate.open({
          resource: "reseaux_techniques", id: properties.id,
          designation: properties.designation || properties.type_libelle || properties.id,
        }, () => loadObservations(properties.id));
      });
      root.addEventListener("beforeunload", (event) => {
        if (panelSession.isActive() && (shell.state.mode === "create"
          ? formIsDirty() || drawingActive || gpsTracking?.isActive()
          : shell.hasDraft({ active: drawingActive || gpsTracking?.isActive() }))) {
          event.preventDefault();
          event.returnValue = "";
        }
      });

      updateGeometryStatus();
    }

    async function load() {
      const collection = await fetchJson("/api/reseaux-techniques");
      if (collection?.type !== "FeatureCollection") {
        throw new Error("Collection Réseau technique invalide.");
      }
      features = collection.features || [];
      layer.clearLayers();
      layersById.clear();
      features.forEach(upsertFeature);

      return collection;
    }

    initializeDom();
    featureStore?.registerResource("reseaux-techniques", {
      fetchOne: (id) => fetchJson(`/api/reseaux-techniques/${encodeURIComponent(id)}`),
      fetchCollection: () => fetchJson("/api/reseaux-techniques"),
      getId: featureId,
      listIds: () => features.map(featureId),
      upsert: upsertFeature,
      remove: () => {},
      shouldDefer({ id }) {
        return shell.state.mode === "edit"
          && String(shell.state.objectId) === String(id)
          && shell.hasDraft({ active: drawingActive || gpsTracking?.isActive() });
      },
      markStale() {
        shell.setConflict();
        setStatus("Ce Réseau a changé sur le serveur. Votre brouillon est conservé.", true);
      },
      clearStale() { shell.setConflict(false); },
    });
    return { layer, load, openEdit, beginCreate, shell, upsertFeature };
  }

  return { create, geometryFromLatLngs };
});
