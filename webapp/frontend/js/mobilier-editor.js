(function (root, factory) {
  const form = typeof module === "object" && module.exports
    ? require("./mobilier-form.js") : root.MobilierForm;
  const GeometryDraft = typeof module === "object" && module.exports
    ? require("./entity-geometry-draft.js") : root.EntityGeometryDraft;
  const api = factory(form, GeometryDraft);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.MobilierEditor = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (MobilierForm, GeometryDraft) {
  "use strict";

  function geometryFromLatLng(latlng) {
    return {
      type: "Point",
      coordinates: [Number(latlng.lng), Number(latlng.lat)],
    };
  }

  function create({
    root = globalThis,
    map,
    L,
    fetchJson,
    referenceData = root.ReferenceData,
    entityEditor = root.EntityEditorShell,
    formViewFactory = root.EntityFormView,
    formSessionFactory = root.EntityFormSession,
    relationSelector = root.RelationSelector,
    mutationController = root.ResourceMutationController,
    concurrency = root.ResourceConcurrency,
    observationCreate = root.ObservationCreate,
    layerControls = root.MapLayerControls,
    openObservation = null,
    featureStore = null,
    panel = null,
    profile = {},
  }) {
    const settings = { ...MobilierForm.profile(profile.classId || "mobilier"), ...profile };
    if (!map || !L || !fetchJson || !referenceData || !entityEditor
        || !relationSelector || !mutationController || !concurrency || !layerControls || !panel
        || !formViewFactory || !formSessionFactory) {
      throw new Error(`Dépendances de l'éditeur ${settings.singular} incomplètes.`);
    }

    const shell = entityEditor.create({ mode: "read", objectType: settings.resource });
    const document = root.document;
    const categoryStyle = layerControls.categoryStyle(settings.resource);
    const layer = L.geoJSON({ type: "FeatureCollection", features: [] }, {
      pointToLayer(_feature, latlng) {
        return L.circleMarker(latlng, categoryStyle.point);
      },
      onEachFeature(feature, featureLayer) {
        featureLayer.on("click", () => void openEdit(feature.properties.id));
      },
    }).addTo(map);
    const layersById = new Map();
    let features = [];
    let choices = { types: [], troncons: [], desordres: [], fetchJson: null };
    let panelRoot;
    let form;
    let pointStatus;
    let placeButton;
    let acquisition = null;
    let observationsList;
    let view, session, panelSession;
    let draftMarker = null;
    let pointChanged = false;
    let placementActive = false;
    const can = permission => !root.BusinessRuntime || root.BusinessRuntime.can(permission);
    const isActive = () => panelSession.isActive();
    function syncPermissions() {
      session?.sync();
      if (draftMarker?.dragging) draftMarker.dragging[session?.canEdit() ? "enable" : "disable"]();
    }

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

    function setStatus(message, error = false) { view.setStatus(message, error); session?.sync(); }
    function setPending() { syncPermissions(); }

    function updatePointStatus() {
      const geometry = draftMarker
        ? geometryFromLatLng(draftMarker.getLatLng())
        : currentFeature()?.geometry;
      pointStatus.textContent = "";
      GeometryDraft.syncDrawingControls({ ...view.geometry, cancel: view.geometry.cancel, ownerState: shell.state },
        { drawing: placementActive, type: "Point", geometry });
      session?.sync();
    }

    function stopPlacement() {
      if (!placementActive) return;
      placementActive = false;
      acquisition?.stop(); acquisition = null;
      panel.finishPointPlacement();
      updatePointStatus();

    }

    function removeDraftMarker() {
      stopPlacement();
      if (draftMarker) map.removeLayer(draftMarker);
      draftMarker = null;
      pointChanged = false;
    }

    function capturePoint(event) {
      stopPlacement();
      if (draftMarker) map.removeLayer(draftMarker);
      draftMarker = L.marker(event.latlng, { draggable: true }).addTo(map);
      draftMarker.on("dragend", () => {
        pointChanged = true;
        shell.markDirty();
        updatePointStatus();
      });
      pointChanged = true;
      shell.markDirty();
      updatePointStatus();
    }

    function startPlacement() {
      if (!session.canEdit() || view.geometry.coordinateEntry?.hasDraft?.()) return;
      const carto = view.geometry.modeChoices.get("map")?.input;
      if (carto && !carto.checked) carto.click();
      stopPlacement();
      placementActive = true;

      acquisition = GeometryDraft.beginAcquisition({ map, getType: () => "Point",
        getVertices: () => [], onVertex: capturePoint, finish: stopPlacement });
      panel.beginPointPlacement(stopPlacement, placeButton);
      updatePointStatus();
    }




    function upsertFeature(feature) {
      const id = featureId(feature);
      features = [...features.filter((item) => featureId(item) !== id), feature];
      const previous = layersById.get(id);
      if (previous) layer.removeLayer(previous);
      layersById.delete(id);
      if (feature.geometry?.type === "Point") {
        const temporary = L.geoJSON(feature, {
          pointToLayer(_item, latlng) {
            return L.circleMarker(latlng, categoryStyle.point);
          },
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
      MobilierForm.fill(form, feature, choices, referenceData, relationSelector);
      syncPermissions();
      updatePointStatus();
    }

    function formIsDirty() {
      const feature = shell.state.mode === "create"
        ? { properties: { valid: true } } : currentFeature();
      return view.geometry.coordinateEntry?.hasDraft?.() || pointChanged || MobilierForm.isDirty(form, feature, relationSelector);
    }

    function payload() {
      const feature = shell.state.mode === "edit" ? currentFeature() : null;
      const result = MobilierForm.payload(form, settings, relationSelector, feature);
      const draftGeometry = draftMarker
        ? geometryFromLatLng(draftMarker.getLatLng()) : null;
      entityEditor.requireGeometry({
        mode: shell.state.mode,
        currentGeometry: currentFeature()?.geometry,
        geometryChanged: pointChanged,
        draftGeometry,
        allowedTypes: ["Point"],
        label: "cet objet",
      });
      if (pointChanged) result.geometry = draftGeometry;
      return result;
    }

    async function request(url, options = {}) {
      const token = session.currentToken();
      const runtime = root.BusinessRuntime;
      const originHeaders = options.method
        ? await mutationController.requestHeaders() : {};
      if (!session.isCurrent(token) || runtime !== root.BusinessRuntime || runtime?.isActive?.() === false) {
        throw new Error("Le contexte de la base a changé. Rechargez la fiche.");
      }
      const response = await (runtime?.fetch || root.fetch)(url, {
        ...options,
        headers: { ...originHeaders, ...options.headers },
      });
      let body = null;
      try { body = await response.json(); } catch (_error) { /* HTTP fallback. */ }
      if (!response.ok) {
        throw concurrency.httpError(response.status, body, `Opération ${settings.singular} refusée.`);
      }
      if (!body) throw new Error(`Réponse ${settings.singular} invalide.`);
      return body;
    }

    async function loadObservations(id) {
      const sequence = session.currentToken();
      return root.ObservationParentList.load({
        container: observationsList,
        parent: { resource: settings.observationResource || settings.resource, id },
        path: `${settings.apiPath}/${encodeURIComponent(id)}/observations`,
        fetchJson, openObservation,
        isCurrent: () => session.isCurrent(sequence) && ["edit", "read"].includes(shell.state.mode) && String(shell.state.objectId) === String(id),
      });
    }

    function resetForm() {
      removeDraftMarker(); form.reset(); form.elements.valid.checked = true;
      observationsList.replaceChildren();
    }

    async function beginCreate() {
      const token = session.begin("create", null, resetForm);
      if (token === null) return;
      panelSession.update();
      setStatus("Chargement des choix…");
      await session.load(token,
        () => MobilierForm.loadChoices(settings, fetchJson, referenceData),
        loadedChoices => {
          choices = loadedChoices;
          renderFeature({ properties: { valid: true } }); shell.markDirty(false);
          setStatus("");
        }, error => setStatus(error.message, true));
      if (session.isCurrent(token)) form.elements.designation.focus();
    }

    async function openEdit(id) {
      const token = session.begin("edit", id, resetForm);
      if (token === null) return;
      panelSession.update();
      const result = await session.load(token, async () => {
        const feature = await request(`${settings.apiPath}/${encodeURIComponent(id)}`);
        if (String(feature?.properties?.id || "") !== String(id)) throw new Error("Objet incohérent.");
        const loadedChoices = await MobilierForm.loadChoices(settings, fetchJson, referenceData, feature);
        return { feature, loadedChoices };
      }, ({ feature, loadedChoices }) => {
        choices = loadedChoices;
        shell.beginEdit({ objectType: settings.resource, objectId: id, data: feature,
          snapshot: feature, geometryType: feature.geometry?.type || null });
        renderFeature(feature); shell.markDirty(false);
        panelSession.update({ title: `${settings.singular} : ${featureLabel(feature)}`,
          subtitle: "État relu depuis PostgreSQL" });
        setStatus("État relu depuis PostgreSQL.");
      }, error => setStatus(error.message, true));
      if (!result || !session.isCurrent(token)) return;
      await loadObservations(id);
      return session.isCurrent(token) ? result.feature : undefined;
    }

    async function submit(event) {
      return session.save(event, {
        form, payload, retryUncertain: shell.state.mode === "edit",
        send(body, { editing, id, snapshot, operationId }) {
          const headers = { "Content-Type": "application/json" };
          if (editing) {
            Object.assign(headers, concurrency.headers(snapshot?.properties?.version));
            headers["Operation-Id"] = operationId;
          }
          return request(editing ? `${settings.apiPath}/${encodeURIComponent(id)}` : settings.apiPath,
            { method: editing ? "PUT" : "POST", headers, body: JSON.stringify(body) });
        },
        async onSaved(result, { editing, token }) {
          removeDraftMarker(); upsertFeature(result); renderFeature(result);
          if (!editing) view.show("general");
          panelSession.update({ title: `${settings.singular} : ${featureLabel(result)}`,
            subtitle: "État relu depuis PostgreSQL" });
          setStatus(`${settings.singular} enregistré et relu depuis PostgreSQL.`);
          if (!session.isCurrent(token)) return;
          try {
            await mutationController.publishMutation(result.mutation, { applyLocally: true });
          } catch (error) {
            console.error(`Publication ${settings.singular} incomplète`, error);
            if (session.isCurrent(token)) setStatus(`${settings.singular} enregistré. L'actualisation des autres vues est incomplète.`, true);
          }
          if (session.isCurrent(token)) await loadObservations(result.properties.id);
        },
      });
    }

    function initializeDom() {
      layerControls.createBusinessLayerToggle({
        rootElement: document, map, layer, label: settings.plural,
        geometryTypes: ["Point"], symbolClass: `legend-${settings.domPrefix}`,
        category: settings.resource,
      });

      view = formViewFactory.create({ root, descriptor: MobilierForm.composition(settings),
        id: `${settings.domPrefix}-editor-panel`, onSubmit: submit,
        onCancel: () => session.close(), onClose: () => session.close(),
        onObservations: () => loadObservations(shell.state.objectId),
        onCreateObservation: () => {
          if (!session.isCurrent(session.currentToken())) return;
          const properties = currentFeature()?.properties;
          if (properties?.id && can("business.create")) observationCreate.open({
            resource: settings.observationResource, id: properties.id,
            designation: properties.designation || properties.type_libelle || properties.id,
          }, () => loadObservations(properties.id));
        },
      });
      panelRoot = view.element; form = view.form; pointStatus = view.geometry.status;
      placeButton = view.geometry.draw;
      observationsList = view.observationList;
      session = formSessionFactory.create({ root, shell, panel, element: panelRoot,
        objectType: settings.resource, label: settings.singular,
        formView: view, isDirty: formIsDirty, hasActiveDraft: () => placementActive,
        onObservations: () => loadObservations(shell.state.objectId),
        onDiscard() {
          removeDraftMarker(); observationsList.replaceChildren();
          relationSelector.detachSearch?.(form.elements.desordre_ids);
        },
        setPending, setStatus,
      });
      panelSession = session.panelSession;
      panel.bindCoordinates?.(view, {
        canApply: () => session.canEdit() && !placementActive,
        getGeometry: () => draftMarker ? geometryFromLatLng(draftMarker.getLatLng()) : currentFeature()?.geometry,
        applyGeometry: ({ coordinates: [lng, lat] }) => capturePoint({ latlng: L.latLng(lat, lng) }),
      });
      placeButton.addEventListener("click", startPlacement);
      view.geometry.cancel.addEventListener("click", stopPlacement);
      root.addEventListener("keydown", event => {
        if (event.key !== "F2" || !isActive()) return;
        event.preventDefault(); startPlacement();
      });

      updatePointStatus();
    }

    async function load() {
      const collection = await fetchJson(settings.apiPath);
      if (collection?.type !== "FeatureCollection") {
        throw new Error(`Collection ${settings.singular} invalide.`);
      }
      features = collection.features || [];
      layer.clearLayers();
      layersById.clear();
      features.forEach(upsertFeature);

      return collection;
    }

    initializeDom();
    featureStore?.registerResource(settings.resource, {
      fetchOne: (id) => fetchJson(`${settings.apiPath}/${encodeURIComponent(id)}`),
      fetchCollection: () => fetchJson(settings.apiPath),
      getId: featureId,
      listIds: () => features.map(featureId),
      upsert: upsertFeature,
      remove: () => {},
      shouldDefer({ id }) {
        return shell.state.mode === "edit"
          && String(shell.state.objectId) === String(id)
          && shell.hasDraft({ active: placementActive });
      },
      markStale() {
        shell.setConflict();
        setStatus(
          `Ce ${settings.singular} a changé sur le serveur. Votre brouillon est conservé.`,
          true,
        );
      },
      clearStale() { shell.setConflict(false); },
    });
    return { layer, load, openEdit, beginCreate, shell, upsertFeature };
  }

  return { create, geometryFromLatLng };
});
