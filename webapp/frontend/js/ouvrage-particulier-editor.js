(function (root, factory) {
  const session = typeof module === "object" && module.exports
    ? require("./entity-form-session.js") : root.EntityFormSession;
  const geometryDraft = typeof module === "object" && module.exports
    ? require("./entity-geometry-draft.js") : root.EntityGeometryDraft;
  const api = factory(session, geometryDraft);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.OuvrageParticulierEditor = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (EntityFormSession, EntityGeometryDraft) {
  "use strict";

  function create({ root = globalThis, map, L, fetchJson,
    entityEditor = root.EntityEditorShell,
    businessForm = root.OuvrageParticulierForm,
    formSessionFactory = EntityFormSession,
    geometryDraftFactory = EntityGeometryDraft,
    relationSelector = root.RelationSelector,
    mutationController = root.ResourceMutationController,
    concurrency = root.ResourceConcurrency,
    observationCreate = root.ObservationCreate,
    layerControls = root.MapLayerControls,
    openObservation = null, featureStore = null, panel = null }) {
    if (!map || !L || !fetchJson || !entityEditor || !businessForm || !formSessionFactory || !geometryDraftFactory || !relationSelector
        || !mutationController || !concurrency || !observationCreate || !layerControls || !panel) {
      throw new Error("Dépendances de l'éditeur Ouvrage particulier incomplètes.");
    }
    const document = root.document;
    let formView;
    const shell = entityEditor.create({ mode: "read", objectType: "ouvrages-particuliers" });
    const profile = businessForm.profile("ouvrages_particuliers");
    const categoryStyle = layerControls.categoryStyle("ouvrages-particuliers");
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
    let choices = { troncons: [] };
    let panelRoot, form, fieldset, status, geometryType;
    let geometryStatus, saveButton, cancelButton, drawButton;
    let confirmButton, observationButton, observationsList;
    let geometryDraft;
    let formSession, panelSession, layerToggle, unregisterResource;
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
      geometryDraft?.updateStatus();
      panelSession.sync();
    }


    function upsertFeature(feature) {
      const id = idOf(feature);
      features = [...features.filter((item) => idOf(item) !== id), feature];
      const previous = layersById.get(id);
      if (previous) layer.removeLayer(previous);
      layersById.delete(id);
      if (feature.geometry) {
        try {
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
        } catch (error) {
          // L'objet et sa géométrie restent dans la couche et la fiche :
          // une source historique vide/non dessinable ne bloque pas sa correction.
          console.warn("Géométrie d'ouvrage non affichable", error);
        }
      }

    }

    function renderFeature(feature) {
      businessForm.fill(form, feature, choices, null, relationSelector);
      geometryDraft.renderOriginal();
      panelSession.sync();
    }

    function formIsDirty() {
      return formView.geometry.coordinateEntry?.hasDraft?.() || geometryDraft.isChanged() || businessForm.isDirty(form,
        shell.state.mode === "create" ? { properties: { valid: true } } : current());
    }

    function payload() {
      if (gpsTracking?.isActive()) throw new Error("Terminez ou annulez le tracé GPS.");
      if (geometryDraft.isDrawing()) throw new Error("Confirmez ou annulez le dessin en cours.");
      const result = businessForm.payload(form, profile, relationSelector, shell.state.mode === "edit" ? current() : null);
      const geometryChanged = geometryDraft.isChanged();
      const geometry = geometryDraft.validate({
        mode: shell.state.mode, valid: result.valid,
        label: "cet ouvrage particulier",
      });
      if (geometryChanged) result.geometry = geometry;
      return result;
    }

    async function request(url, options = {}) {
      const token = formSession.currentToken();
      const runtime = root.BusinessRuntime;
      const headers = options.method ? await mutationController.requestHeaders() : {};
      if (!formSession.isCurrent(token)) throw new Error("Le contexte de la fiche a changé.");
      const response = await (runtime?.fetch || root.fetch)(url, {
        ...options, headers: { ...headers, ...options.headers },
      });
      let body = null;
      try { body = await response.json(); } catch (_error) { /* Réponse HTTP. */ }
      if (!response.ok) throw concurrency.httpError(response.status, body, "Opération Ouvrage refusée.");
      if (!body) throw new Error("Réponse Ouvrage invalide.");
      return body;
    }

    async function loadObservations(id) {
      const sequence = formSession.currentToken();
      return root.ObservationParentList.load({
        container: observationsList, parent: { resource: "ouvrages_particuliers", id },
        path: `/api/ouvrages-particuliers/${encodeURIComponent(id)}/observations`,
        fetchJson, openObservation,
        isCurrent: () => formSession.isCurrent(sequence) && ["edit", "read"].includes(shell.state.mode) && String(shell.state.objectId) === String(id),
      });
    }

    async function beginCreate() {
      const sequence = formSession.begin("create", null, geometryDraft.reset);
      if (sequence === null) return;
      geometryDraft.clearContract();
      form.reset();

      observationsList.replaceChildren();
      panelSession.update();
      setStatus("Chargement des tronçons…");
      return formSession.load(sequence, () => Promise.all([
        businessForm.loadChoices(profile, fetchJson, null),
        fetchJson("/api/geometry-capabilities/ouvrages-particuliers"),
      ]), ([loadedChoices, contract]) => {
        choices = loadedChoices; geometryDraft.setContract(contract);
        renderFeature({ properties: { valid: true } });
        shell.markDirty(false);
        setStatus("");
        form.elements.designation.focus();
      }, error => setStatus(error.message, true));
    }

    async function openEdit(id) {
      const sequence = formSession.begin("edit", id, geometryDraft.reset);
      if (sequence === null) return;
      geometryDraft.clearContract();
      form.reset();
      observationsList.replaceChildren();
      panelSession.update();
      setStatus("Chargement de l'ouvrage…");
      return formSession.load(sequence, async () => {
        const feature = await request(`/api/ouvrages-particuliers/${encodeURIComponent(id)}`);
        if (!formSession.isCurrent(sequence)) return null;
        if (String(feature?.properties?.id || "") !== String(id)) throw new Error("Objet incohérent.");
        const [loadedChoices, contract] = await Promise.all([
          businessForm.loadChoices(profile, fetchJson, null, feature),
          fetchJson("/api/geometry-capabilities/ouvrages-particuliers"),
        ]);
        return { feature, loadedChoices, contract };
      }, async ({ feature, loadedChoices, contract }) => {
        choices = loadedChoices; geometryDraft.setContract(contract);
        shell.beginEdit({ objectType: "ouvrages-particuliers", objectId: id,
          data: feature, snapshot: feature, geometryType: feature.geometry?.type || null });
        renderFeature(feature);

        shell.markDirty(false);
        await loadObservations(id);
        if (!formSession.isCurrent(sequence)) return;
        panelSession.update();
        setStatus("État relu depuis PostgreSQL.");
      }, error => setStatus(error.message, true));
    }

    async function submit(event) {
      return formSession.save(event, { form, payload, retryUncertain: shell.state.mode === "edit",
        async send(body, { editing, id, snapshot, operationId }) {
          const headers = { "Content-Type": "application/json",
            "Geometry-Contract-Version": geometryDraft.contractVersion() };
          if (editing) {
            Object.assign(headers, concurrency.headers(snapshot?.properties?.version));
            headers["Operation-Id"] = operationId;
          }
          return request(editing
            ? `/api/ouvrages-particuliers/${encodeURIComponent(id)}`
            : "/api/ouvrages-particuliers",
          { method: editing ? "PUT" : "POST", headers, body: JSON.stringify(body) });
        },
        async onSaved(result, { token }) {
          geometryDraft.reset();
          upsertFeature(result);
          choices = { ...choices,
            desordres: relationSelector.selectedRows(form.elements.desordre_ids) };
          renderFeature(result);
          panelSession.update();
          await loadObservations(result.properties.id);
          if (!formSession.isCurrent(token)) return;
          setStatus("Ouvrage enregistré et relu depuis PostgreSQL.");
          try {
            await mutationController.publishMutation(result.mutation, { applyLocally: true });
          } catch (error) {
            console.error("Publication Ouvrage incomplète", error);
            setStatus("Ouvrage enregistré. Actualisation des autres vues incomplète.", true);
          }
        },
      });
    }

    function cancel() { return formSession.close(); }

    function initializeDom() {
      layerToggle = layerControls.createBusinessLayerToggle({
        rootElement: document, map, layer, label: "Ouvrages particuliers",
        // Symboles du moteur de rendu, indépendants des autorisations de saisie.
        geometryTypes: geometryDraftFactory.supportedTypes,
        symbolClass: "legend-ouvrage-particulier",
        category: "ouvrages-particuliers",
      });
      formView = root.EntityFormView.create({ root, document, descriptor: businessForm.composition(businessForm.profile("ouvrages_particuliers")),
        id: "ouvrage-particulier-editor-panel", onObservations: () => void loadObservations(shell.state.objectId), onClose: () => formSession.close() });
      panelRoot = formView.element;
      formView.setFormId(`ouvrage-particulier-editor-form`);
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
        map, L, panel, pathStyle: categoryStyle.path,
        allowedGeometryTypes: [], contractResource: "ouvrages-particuliers",
        getMode: () => shell.state.mode,
        controls: { type: geometryType, status: geometryStatus, draw: drawButton,
          confirm: confirmButton, cancel: formView.geometry.cancel, setDrawingActive: formView.geometry.setDrawingActive, resolveGeometryError: formView.geometry.resolveGeometryError,
          setTypes: formView.geometry.setTypes, setRequirement: formView.geometry.setRequirement,
          contractStatus: panelRoot.querySelector("[data-role='geometry-contract-status']") },
        getOriginal: () => current()?.geometry, getOwnerState: () => shell.state,
        canStart: () => Boolean(formSession?.canEdit()),
        onDirty: () => shell.markDirty(), setStatus,
        getGpsTracking: () => gpsTracking,
      });
      formSession = formSessionFactory.create({
        root, shell, panel, element: panelRoot, formView, objectType: "ouvrages-particuliers",
        label: "Ouvrage particulier",
        onObservations: () => void loadObservations(shell.state.objectId),
        isDirty: formIsDirty, hasActiveDraft: () => geometryDraft.isDrawing() || gpsTracking?.isActive(),
        onDiscard: () => { geometryDraft.reset(); observationsList.replaceChildren(); },
        setPending, setStatus,
        onDispose: () => {
          gpsTracking?.destroy?.();
          gpsTracking = null;
          geometryDraft.dispose();
          relationSelector.detachSearch?.(form.elements.desordre_ids);
          unregisterResource?.();
          layerToggle?.label.remove();
          map.removeLayer(layer);
          layersById.clear();
        },
      });
      panelSession = formSession.panelSession;
      gpsTracking = root.PwaGeometryTracking?.createWidget({
        root, map, L, container: formView.geometry.methods, ownerId: "ouvrage-particulier-gps",
        geometryType: () => geometryType.value,
        getOriginal: () => current()?.geometry,
        getDraft: () => geometryDraft.draftGeometry() || current()?.geometry,
        draftContext: () => ({ resource: "ouvrages-particuliers", mode: shell.state.mode,
          id: shell.state.objectId, version: current()?.properties?.version ?? null }),
        canStart: () => formSession.canEdit() && !shell.state.conflict && !geometryDraft.isDrawing() && geometryDraft.canAcquire(),
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
          drawButton.disabled = active || shell.state.pending || !permitted || !geometryDraft.canAcquire();
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
      form.addEventListener("submit", submit);
      cancelButton.addEventListener("click", cancel);
      panel.bindCoordinates?.(formView, {
        canApply: () => formSession.canEdit() && !geometryDraft.isDrawing()
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
      observationButton.addEventListener("click", () => {
        const properties = current()?.properties;
        if (properties?.id) observationCreate.open({
          resource: "ouvrages_particuliers", id: properties.id,
          designation: properties.designation || properties.libelle || properties.id,
        }, () => loadObservations(properties.id));
      });

      geometryDraft.updateStatus();
    }

    async function load() {
      const collection = await fetchJson("/api/ouvrages-particuliers");
      if (collection?.type !== "FeatureCollection") throw new Error("Collection Ouvrage invalide.");
      features = collection.features || [];
      layer.clearLayers();
      layersById.clear();
      features.forEach(upsertFeature);

      return collection;
    }

    initializeDom();
    unregisterResource = featureStore?.registerResource("ouvrages-particuliers", {
      fetchOne: (id) => fetchJson(`/api/ouvrages-particuliers/${encodeURIComponent(id)}`),
      fetchCollection: () => fetchJson("/api/ouvrages-particuliers"),
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
        setStatus("Cet ouvrage a changé sur le serveur. Votre brouillon est conservé.", true);
      },
      clearStale() { shell.setConflict(false); },
    });
    return { layer, load, openEdit, beginCreate, shell, upsertFeature,
      dispose: () => formSession.dispose() };
  }

  return { create, geometryFromVertices: EntityGeometryDraft.geometryFromVertices };
});
