(function (root, factory) {
  const formAdapter = typeof module === "object" && module.exports
    ? require("./vegetation-form.js") : root.VegetationForm;
  const geometryDraft = typeof module === "object" && module.exports
    ? require("./entity-geometry-draft.js") : root.EntityGeometryDraft;
  const api = factory(formAdapter, geometryDraft);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.VegetationEditor = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (formAdapter, EntityGeometryDraft) {
  "use strict";

  function create({ root = globalThis, map, L, fetchJson,
    referenceData = root.ReferenceData,
    geometryDraftFactory = EntityGeometryDraft,
    entityEditor = root.EntityEditorShell,
    relationSelector = root.RelationSelector,
    mutationController = root.ResourceMutationController,
    concurrency = root.ResourceConcurrency,
    observationCreate = root.ObservationCreate,
    layerControls = root.MapLayerControls,
    openObservation = null, featureStore = null, panel = null }) {
    if (!map || !L || !fetchJson || !referenceData || !geometryDraftFactory || !entityEditor || !relationSelector
        || !mutationController || !concurrency || !observationCreate || !layerControls || !panel) {
      throw new Error("Dépendances de l'éditeur Végétation incomplètes.");
    }
    const document = root.document;
    let formView;
    const shell = entityEditor.create({ mode: "read", objectType: "vegetation" });
    const categoryStyle = layerControls.categoryStyle("vegetation");
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
    let troncons = [];
    let amenagements = [];
    let desordres = [];
    let choicesFetchJson = null;
    let references = {};
    let panelRoot, form, fieldset, status, geometryType;
    let contextualContainers = {};
    let geometryStatus, saveButton, cancelButton, drawButton;
    let confirmButton, observationButton, observationsList;
    let geometryDraft;
    const allowedGeometryTypes = ["Point", "LineString", "Polygon"];
    let readSequence = 0;
    let panelSession;
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

    function updateContextualFields() {
      formAdapter.updateContextualFields(form, contextualContainers, relationSelector);
    }

    function renderFeature(feature) {
      geometryDraft.renderOriginal();
      formAdapter.fill(form, feature, { references, troncons, amenagements,
        desordres, fetchJson: choicesFetchJson },
        referenceData, relationSelector, geometryType.value, contextualContainers);
      panelSession.sync();
    }

    function formIsDirty() {
      return formView.geometry.coordinateEntry?.hasDraft?.() || geometryDraft.isChanged() || formAdapter.isDirty(form,
        shell.state.mode === "create" ? { properties: { valid: true } } : current(), relationSelector);
    }

    function payload() {
      if (gpsTracking?.isActive()) throw new Error("Terminez ou annulez le tracé GPS.");
      if (geometryDraft.isDrawing()) throw new Error("Confirmez ou annulez le dessin en cours.");
      const feature = shell.state.mode === "edit" ? current() : null;
      const result = formAdapter.payload(form, null, relationSelector, feature);
      const mustValidateGeometry = shell.state.mode === 'create' || geometryDraft.isChanged()
        || (result.valid ?? feature?.properties?.valid ?? true);
      const geometry = mustValidateGeometry ? entityEditor.requireGeometry({
        mode: shell.state.mode,
        currentGeometry: current()?.geometry,
        geometryChanged: geometryDraft.isChanged(),
        draftGeometry: geometryDraft.draftGeometry(),
        allowedTypes: allowedGeometryTypes,
        label: "cette végétation",
      }) : feature?.geometry;
      if (geometryDraft.isChanged()) result.geometry = geometry;
      return result;
    }

    async function request(url, options = {}) {
      const headers = options.method ? await mutationController.requestHeaders() : {};
      const response = await (root.BusinessRuntime?.fetch || root.fetch)(url, {
        ...options, headers: { ...headers, ...options.headers },
      });
      let body = null;
      try { body = await response.json(); } catch (_error) { /* Réponse HTTP. */ }
      if (!response.ok) throw concurrency.httpError(response.status, body, "Opération Végétation refusée.");
      if (!body) throw new Error("Réponse Végétation invalide.");
      return body;
    }

    async function loadRelations(feature = null) {
      const sequence = readSequence;
      const choices = await formAdapter.loadChoices(null, fetchJson, referenceData, feature);
      if (sequence !== readSequence || !panelSession.isActive()) return;
      ({ references, troncons, amenagements, desordres } = choices);
      choicesFetchJson = choices.fetchJson;
      formAdapter.fill(form, feature, choices, referenceData, relationSelector,
        geometryType.value, contextualContainers);
    }

    async function loadObservations(id) {
      const sequence = readSequence;
      return root.ObservationParentList.load({
        container: observationsList, parent: { resource: "vegetation", id },
        path: `/api/vegetation/${encodeURIComponent(id)}/observations`,
        fetchJson, openObservation,
        isCurrent: () => panelSession.isActive() && sequence === readSequence && shell.state.mode === "edit" && String(shell.state.objectId) === String(id),
      });
    }

    async function beginCreate() {
      if (!can("business.create") || !panelSession.prepare()) return;
      const sequence = ++readSequence;
      geometryDraft.reset();
      shell.beginCreate({ objectType: "vegetation" });
      form.reset();

      observationsList.replaceChildren();
      panelSession.showTab("general");
      panelSession.update();
      setStatus("Chargement des relations et référentiels…");
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
      geometryDraft.reset();
      const sequence = ++readSequence;
      shell.beginRead({ objectType: "vegetation" });
      form.reset();
      observationsList.replaceChildren();
      panelSession.showTab("general");
      panelSession.update();
      setStatus("Chargement de la Végétation…");
      panelSession.setLoading(true);
      try {
        const feature = await request(`/api/vegetation/${encodeURIComponent(id)}`);
        if (sequence !== readSequence || !panelSession.isActive()) return;
        if (String(feature?.properties?.id || "") !== String(id)) throw new Error("Objet incohérent.");
        await loadRelations(feature);
        if (sequence !== readSequence || !panelSession.isActive()) return;
        shell.beginEdit({ objectType: "vegetation", objectId: id,
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
      try { body = payload(); } catch (error) { setStatus(error.message, true); return; }
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
          ? `/api/vegetation/${encodeURIComponent(shell.state.objectId)}`
          : "/api/vegetation",
        { method: editing ? "PUT" : "POST", headers, body: JSON.stringify(body) });
        if (!result?.properties?.id || !Number.isInteger(result.properties.version)) {
          throw new Error("Réponse Végétation sans identifiant ou version.");
        }
        if (editing && String(result.properties.id) !== String(shell.state.objectId)) {
          throw new Error("Réponse Végétation incohérente.");
        }
        shell.confirm(result);
        if (!editing) shell.beginEdit({ objectType: "vegetation",
          objectId: result.properties.id, data: result, snapshot: result,
          geometryType: result.geometry?.type || null });
        geometryDraft.reset();
        upsertFeature(result);
        renderFeature(result);
        panelSession.update();
        setStatus("Végétation enregistrée et relue depuis PostgreSQL.");
        try {
          await mutationController.publishMutation(result.mutation, { applyLocally: true });
        } catch (error) {
          console.error("Publication Végétation incomplète", error);
          setStatus("Végétation enregistrée. Actualisation des autres vues incomplète.", true);
        }
        try {
          await loadObservations(result.properties.id);
        } catch (error) {
          console.error("Relecture des observations Végétation impossible", error);
          setStatus("Végétation enregistrée. Liste des observations à actualiser.", true);
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
        rootElement: document, map, layer, label: "Végétation",
        geometryTypes: allowedGeometryTypes,
        symbolClass: "legend-vegetation",
        category: "vegetation",
      });
      formView = root.EntityFormView.create({ root, document, descriptor: formAdapter.composition(formAdapter.profile("vegetation")),
        id: "vegetation-editor-panel", onObservations: () => void loadObservations(shell.state.objectId), onClose: () => panelSession.close() });
      panelRoot = formView.element;
      formView.setFormId(`vegetation-editor-form`);
      form = panelRoot.querySelector("form");
      fieldset = panelRoot.querySelector("fieldset");
      status = panelRoot.querySelector("[data-role='status']");
      geometryType = form.elements.geometry_type;
      geometryStatus = panelRoot.querySelector("[data-role='geometry-status']");
      saveButton = panelRoot.querySelector("[data-role='save']");
      cancelButton = panelRoot.querySelector("[data-role='cancel']");
      drawButton = panelRoot.querySelector("[data-role='draw']");
      confirmButton = panelRoot.querySelector("[data-role='confirm']");
      observationButton = panelRoot.querySelector("[data-role='create-observation']");
      observationButton.dataset.requires = "business.create";
      observationsList = panelRoot.querySelector("[data-role='observations']");
      geometryDraft = geometryDraftFactory.create({
        map, L, panel, pathStyle: categoryStyle.path, allowedGeometryTypes,
        controls: { type: geometryType, status: geometryStatus, draw: drawButton,
          confirm: confirmButton, cancel: formView.geometry.cancel, setDrawingActive: formView.geometry.setDrawingActive, resolveGeometryError: formView.geometry.resolveGeometryError },
        getOriginal: () => current()?.geometry, getOwnerState: () => shell.state,
        canStart: () => !shell.state.pending
          && can(shell.state.mode === "create" ? "business.create" : "business.update"),
        onDirty: () => shell.markDirty(), setStatus,
        getGpsTracking: () => gpsTracking,
      });
      panelSession = panel.connect({
        element: panelRoot, formView, shell, label: "Végétation",
        onObservations: () => void loadObservations(shell.state.objectId),
        isDirty: formIsDirty, hasActiveDraft: () => geometryDraft.isDrawing() || gpsTracking?.isActive(),
        onDiscard: () => { ++readSequence; geometryDraft.reset(); observationsList.replaceChildren(); },
      });
      gpsTracking = root.PwaGeometryTracking?.createWidget({
        root, map, L, container: formView.geometry.methods, ownerId: "vegetation-gps",
        geometryType: () => geometryType.value,
        getOriginal: () => current()?.geometry,
        getDraft: () => geometryDraft.draftGeometry() || current()?.geometry,
        draftContext: () => ({ resource: "vegetation", mode: shell.state.mode,
          id: shell.state.objectId, version: current()?.properties?.version ?? null }),
        canStart: () => !shell.state.pending && !shell.state.conflict && !shell.state.forbidden && !geometryDraft.isDrawing()
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
          if (!geometryDraft.acceptGeometry(geometry)) return false;
          shell.markDirty(formIsDirty());
          formView.geometry.status.textContent = "Tracé GPS confirmé dans le brouillon. Enregistrez pour sauvegarder.";
          return true;
        },
      });
      contextualContainers = Object.fromEntries(
        ["sous_type"]
        .map((key) => [key, panelRoot.querySelector(`[data-context='${key}']`)]));
      form.addEventListener("submit", submit);
      form.addEventListener("input", () => shell.markDirty(formIsDirty()));
      form.addEventListener("change", () => shell.markDirty(formIsDirty()));
      cancelButton.addEventListener("click", cancel);
      panel.bindCoordinates?.(formView, {
        canApply: () => panelSession.isActive() && !shell.state.pending && !geometryDraft.isDrawing()
          && !gpsTracking?.isActive() && geometryDraft.canAcquire(),
        getGeometry: () => geometryDraft.draftGeometry() || current()?.geometry,
        applyGeometry: geometryDraft.acceptGeometry,
      });
      drawButton.addEventListener("click", geometryDraft.start);
      confirmButton.addEventListener("click", geometryDraft.confirm);
      geometryType.addEventListener("change", () => {
        if (!geometryDraft.changeType()) return;
        shell.markDirty(formIsDirty());
      });
      form.elements.famille_vegetation.addEventListener("change", () => {
        updateContextualFields();
      });
      form.elements.valid.addEventListener('change', updateContextualFields);
      observationButton.addEventListener("click", () => {
        const properties = current()?.properties;
        if (properties?.id) observationCreate.open({
          resource: "vegetation", id: properties.id,
          designation: properties.designation || properties.libelle || properties.id,
        }, () => loadObservations(properties.id));
      });
      root.addEventListener("beforeunload", (event) => {
        if (panelSession.isActive() && (shell.state.mode === "create"
          ? formIsDirty() || geometryDraft.isDrawing() || gpsTracking?.isActive()
          : shell.hasDraft({ active: geometryDraft.isDrawing() || gpsTracking?.isActive() }))) {
          event.preventDefault();
          event.returnValue = "";
        }
      });

      geometryDraft.updateStatus();
    }

    async function load() {
      const collection = await fetchJson("/api/vegetation");
      if (collection?.type !== "FeatureCollection") throw new Error("Collection Végétation invalide.");
      features = collection.features || [];
      layer.clearLayers();
      layersById.clear();
      features.forEach(upsertFeature);

      return collection;
    }

    initializeDom();
    featureStore?.registerResource("vegetation", {
      fetchOne: (id) => fetchJson(`/api/vegetation/${encodeURIComponent(id)}`),
      fetchCollection: () => fetchJson("/api/vegetation"),
      getId: idOf,
      listIds: () => features.map(idOf),
      upsert: upsertFeature,
      remove: () => {},
      shouldDefer({ id }) {
        return shell.state.mode === "edit"
          && String(shell.state.objectId) === String(id)
          && shell.hasDraft({ active: geometryDraft.isDrawing() || gpsTracking?.isActive() });
      },
      markStale() {
        shell.setConflict();
        setStatus("Cette Végétation a changé sur le serveur. Votre brouillon est conservé.", true);
      },
      clearStale() { shell.setConflict(false); },
    });
    return { layer, load, openEdit, beginCreate, shell, upsertFeature };
  }

  return { create, geometryFromVertices: EntityGeometryDraft.geometryFromVertices };
});

// Chargé après map.js : le module métier reste autonome et ne change pas le contrôleur Désordre.
if (typeof module === "undefined" && typeof map !== "undefined") {
  const vegetationEditor = VegetationEditor.create({
    map, L, fetchJson,
    referenceData: ReferenceData,
    entityEditor: EntityEditorShell,
    relationSelector: RelationSelector,
    mutationController: ResourceMutationController,
    concurrency: ResourceConcurrency,
    observationCreate: ObservationCreate,
    openObservation,
    featureStore: mapFeatureStore,
    panel: businessEditorPanel,
  });
  globalThis.MapEntityNavigation?.register("vegetation", vegetationEditor);
  globalThis.MapCreateActions?.register("vegetation", () => vegetationEditor.beginCreate());
  mapDataReadyPromise.then(() => vegetationEditor.load()).catch((error) => {
    console.error("Chargement de la Végétation impossible", error);
  });
}
