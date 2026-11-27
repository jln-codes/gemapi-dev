/* Deux éditeurs hydrographiques ; dessin et cycle de vie restent dans le socle commun. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LitEditor = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  function create({ classId, root = globalThis, map, L, fetchJson,
    businessForm = root.LitForm, entityEditor = root.EntityEditorShell,
    formSessionFactory = root.EntityFormSession, geometryDraftFactory = root.EntityGeometryDraft,
    relationSelector = root.RelationSelector, referenceData = root.ReferenceData,
    mutationController = root.ResourceMutationController, concurrency = root.ResourceConcurrency,
    characteristicsFactory = root.LitCharacteristics, layerControls = root.MapLayerControls,
    featureStore = null, panel }) {
    const profile = businessForm.profile(classId);
    const document = root.document;
    let formView;
    const shell = entityEditor.create({ mode: "read", objectType: profile.resource });
    const categoryStyle = layerControls.categoryStyle(profile.resource);
    const layer = L.geoJSON({ type: "FeatureCollection", features: [] }).addTo(map);
    const layersById = new Map();
    let features = [], choices = { digues: [], troncons_lit: [], zones: [] };
    let formSession, panelSession, geometryDraft, characteristics, unregisterResource;
    let createId = null, refreshGeneration = 0;
    const current = () => shell.state.snapshot;
    const idOf = feature => String(feature?.properties?.id || feature?.id || "");
    const labelOf = feature => feature?.properties?.designation || feature?.properties?.libelle || idOf(feature);
    formView = root.EntityFormView.create({ root, document, descriptor: businessForm.composition(profile),
      id: `${profile.resource}-editor-panel`, onClose: () => formSession.close() });
    const panelRoot = formView.element;
    formView.setFormId(`${profile.resource}-editor-form`);
    const form = panelRoot.querySelector("form");
    form.dataset.litClass = classId;
    const fieldset = panelRoot.querySelector("fieldset");
    const control = role => panelRoot.querySelector(`[data-role="${role}"]`);
    function setStatus(message, error = false) {
      control("status").textContent = message;
      control("status").classList.toggle("error", Boolean(error));
    }
    function parentDirty() {
      return formView.geometry.coordinateEntry?.hasDraft?.() || geometryDraft.isChanged() || geometryDraft.isDrawing() || businessForm.isDirty(form,
        shell.state.mode === "create" ? { properties: { valid: true } } : current(), relationSelector);
    }
    function formIsDirty() { return parentDirty() || Boolean(characteristics?.hasDraft()); }
    function setPending(pending) {
      fieldset.disabled = pending;
      for (const name of ["save", "cancel", "draw", "confirm"]) control(name).disabled = pending;
      control("save").textContent = pending ? "Enregistrement…" : "Enregistrer";
      geometryDraft?.updateStatus();
      panelSession?.sync();
    }
    function upsertFeature(feature) {
      const id = idOf(feature);
      const known = features.find(item => idOf(item) === id);
      if (known?.properties?.version > feature.properties?.version) return;
      features = [...features.filter(item => idOf(item) !== id), feature];
      const previous = layersById.get(id);
      if (previous) layer.removeLayer(previous);
      layersById.delete(id);
      if (feature.geometry) {
        try {
          const child = L.geoJSON(feature, { style: categoryStyle.path,
            pointToLayer: (_item, latlng) => L.circleMarker(latlng, categoryStyle.point),
            onEachFeature: (item, itemLayer) => itemLayer.on("click", () => void openEdit(idOf(item))) });
          layer.addLayer(child); layersById.set(id, child);
        } catch (_error) { /* Géométrie historique non dessinable : fiche et identité conservées. */ }
      }

      const complete = classId !== "troncons_lit" || Array.isArray(feature.properties?.largeurs_lit);
      if (complete && panelSession?.isActive() && shell.state.mode === "edit"
          && id === String(shell.state.objectId) && feature.properties.version > current()?.properties?.version
          && !formIsDirty() && !shell.state.pending && !shell.state.conflict) {
        shell.setSnapshot(feature); renderFeature(feature); shell.markDirty(false);
      }
    }
    function renderFeature(feature) {
      businessForm.fill(form, feature, choices, referenceData, relationSelector);
      geometryDraft.renderOriginal();
      control("geometry-diagnostics").textContent = businessForm.geometryDiagnosticText(feature);
      characteristics?.render(feature);
      if (classId === "troncons_lit") businessForm.renderMorphologies(control("lit-morphologies"), feature,
        async id => {
          try { await root.MapEntityNavigation.open("elements_morphologiques_lit", id); }
          catch (error) { setStatus(error.message, true); }
        });
      panelSession.sync();
    }
    async function request(url, options = {}) {
      const token = formSession.currentToken();
      const runtime = root.BusinessRuntime;
      const headers = options.method ? await mutationController.requestHeaders() : {};
      if (!formSession.isCurrent(token)) throw new Error("Le contexte de la fiche a changé.");
      const response = await (runtime?.fetch || root.fetch)(url,
        { ...options, headers: { ...headers, ...options.headers } });
      let body = null;
      try { body = await response.json(); } catch (_error) { /* HTTP error handled below. */ }
      if (!response.ok) throw concurrency.httpError(response.status, body, "Enregistrement du lit refusé.");
      if (!body) throw new Error("Réponse Lit invalide.");
      return body;
    }
    function discard() {
      refreshGeneration += 1;
      geometryDraft.reset(); characteristics?.reset({ force: true }); createId = null;
    }
    async function open(mode, id = null) {
      if (characteristics?.isPending()) return null;
      const token = formSession.begin(mode, id, discard);
      if (token === null) return null;
      geometryDraft.clearContract(); form.reset();
      if (mode === "create") createId = root.crypto.randomUUID();
      setStatus("Chargement des données et du contrat géométrique…");
      const loaded = await formSession.load(token, async () => {
        const [feature, loadedChoices, contract] = await Promise.all([
          id ? request(`${profile.apiPath}/${encodeURIComponent(id)}`) : { type: "Feature", geometry: null, properties: { valid: true } },
          businessForm.loadChoices(profile, fetchJson, referenceData),
          fetchJson(`/api/geometry-capabilities/${profile.resource}`),
        ]);
        return { feature, loadedChoices, contract };
      }, ({ feature, loadedChoices, contract }) => {
        if (id && idOf(feature) !== String(id)) throw new Error("Objet Lit incohérent.");
        choices = loadedChoices; geometryDraft.setContract(contract);
        if (id) shell.beginEdit({ objectType: profile.resource, objectId: id,
          data: feature, snapshot: feature, geometryType: feature.geometry?.type || null });
        renderFeature(feature); shell.markDirty(false);
        setStatus(id ? "État relu depuis le serveur." : "");
        form.elements.designation.focus();
      }, error => setStatus(error.message, true));
      return loaded?.feature || null;
    }
    const beginCreate = () => open("create");
    const openEdit = id => open("edit", id);
    function payload() {
      if (characteristics?.hasDraft() || characteristics?.isPending()) throw new Error("Terminez la caractéristique en cours avant d'enregistrer le tronçon.");
      const body = businessForm.payload(form, profile, relationSelector);
      const geometry = geometryDraft.validate({ mode: shell.state.mode, valid: body.valid, label: profile.singular.toLowerCase() });
      if (geometryDraft.isChanged()) body.geometry = geometry;
      if (shell.state.mode === "create") body.id = createId;
      return body;
    }
    async function submit(event) {
      return formSession.save(event, { form, payload, retryUncertain: true,
        async send(body, { editing, id, snapshot, operationId }) {
          const headers = { "Content-Type": "application/json", "Operation-Id": operationId,
            "Geometry-Contract-Version": geometryDraft.contractVersion() };
          if (editing) Object.assign(headers, concurrency.headers(snapshot?.properties?.version));
          return request(editing ? `${profile.apiPath}/${encodeURIComponent(id)}` : profile.apiPath,
            { method: editing ? "PUT" : "POST", headers, body: JSON.stringify(body) });
        },
        validate(result, { editing, id }) {
          if (idOf(result) !== String(editing ? id : createId)) throw new Error("Réponse Lit incohérente.");
        },
        async onSaved(result) {
          geometryDraft.reset(); upsertFeature(result); renderFeature(result);
          setStatus("Objet enregistré et relu depuis le serveur.");
          try { await mutationController.publishMutation(result.mutation, { applyLocally: true }); }
          catch (_error) { setStatus("Objet enregistré. Actualisation des autres vues incomplète.", true); }
        },
      });
    }
    geometryDraft = geometryDraftFactory.create({ map, L, panel, pathStyle: categoryStyle.path,
      allowedGeometryTypes: [], contractResource: profile.resource, getMode: () => shell.state.mode,
      getOriginal: () => current()?.geometry, getOwnerState: () => shell.state,
      controls: { type: form.elements.geometry_type, status: control("geometry-status"), draw: control("draw"),
        confirm: control("confirm"), cancel: formView.geometry.cancel, setDrawingActive: formView.geometry.setDrawingActive, resolveGeometryError: formView.geometry.resolveGeometryError, setTypes: formView.geometry.setTypes, setRequirement: formView.geometry.setRequirement,
        contractStatus: control("geometry-contract-status") },
      canStart: () => Boolean(formSession?.canEdit()) && !characteristics?.hasDraft() && !characteristics?.isPending(),
      onDirty: () => shell.markDirty(), setStatus, getGpsTracking: () => null });
    const layerToggle = layerControls.createBusinessLayerToggle({ rootElement: document, map, layer,
      label: profile.plural, geometryTypes: geometryDraftFactory.supportedTypes, symbolClass: "", category: profile.resource });
    formSession = formSessionFactory.create({ root, shell, panel, element: panelRoot, formView, objectType: profile.resource,
      label: profile.singular, isDirty: formIsDirty,
      hasActiveDraft: () => geometryDraft.isDrawing() || characteristics?.hasDraft() || characteristics?.isPending(),
      onDiscard: discard, setPending, setStatus,
      onDispose: () => { geometryDraft.dispose(); characteristics?.dispose(); unregisterResource?.();
        layerToggle?.label.remove(); map.removeLayer(layer); layersById.clear(); } });
    panelSession = formSession.panelSession;
    if (classId === "troncons_lit") characteristics = characteristicsFactory.create({ root,
      container: control("lit-characteristics"), fetchJson: request, getParent: current,
      isParentDirty: () => formSession.isSaving() || shell.state.pending || shell.state.uncertain
        || shell.state.forbidden || parentDirty(), setStatus,
      onPending(pending) { shell.state.pending = pending; setPending(pending); },
      onChanged(feature) {
        shell.beginEdit({ objectType: profile.resource, objectId: idOf(feature),
          data: feature, snapshot: feature, geometryType: feature.geometry?.type || null });
        upsertFeature(feature); renderFeature(feature); shell.markDirty(false);
      } });
    form.addEventListener("submit", submit);
    form.addEventListener("change", () => { businessForm.updateConditional(form, profile); });
    control("cancel").addEventListener("click", () => { if (!characteristics?.isPending()) formSession.close(); });
    panel.bindCoordinates?.(formView, {
      canApply: () => formSession.canEdit() && !geometryDraft.isDrawing() && geometryDraft.canAcquire()
        && !characteristics?.hasDraft() && !characteristics?.isPending(),
      getGeometry: () => geometryDraft.draftGeometry() || current()?.geometry,
      applyGeometry: geometryDraft.acceptGeometry,
    });
    control("draw").addEventListener("click", geometryDraft.start);
    control("confirm").addEventListener("click", geometryDraft.confirm);
    form.elements.geometry_type.addEventListener("change", () => { if (geometryDraft.changeType()) shell.markDirty(formIsDirty()); });
    geometryDraft.updateStatus();
    async function load() {
      const collection = await fetchJson(profile.apiPath);
      if (collection?.type !== "FeatureCollection") throw new Error("Collection Lit invalide.");
      features = []; layer.clearLayers(); layersById.clear();
      (collection.features || []).forEach(upsertFeature);
      return collection;
    }
    unregisterResource = featureStore?.registerResource(profile.resource, {
      fetchOne: id => fetchJson(`${profile.apiPath}/${encodeURIComponent(id)}`),
      fetchCollection: () => fetchJson(profile.apiPath), getId: idOf, listIds: () => features.map(idOf),
      upsert: upsertFeature, remove: () => {},
      shouldDefer: ({ id }) => shell.state.mode === "edit" && String(shell.state.objectId) === String(id)
        && (shell.hasDraft({ active: geometryDraft.isDrawing() }) || characteristics?.hasDraft() || characteristics?.isPending()),
      markStale: () => { shell.setConflict(); setStatus("Cet objet a changé sur le serveur. Votre brouillon est conservé.", true); },
      clearStale: () => shell.setConflict(false),
    });
    async function refreshCurrent() {
      if (!panelSession.isActive() || shell.state.mode !== "edit") return false;
      if (formIsDirty() || shell.state.pending || shell.state.conflict) {
        shell.setConflict(); setStatus("Cet objet a changé sur le serveur. Votre brouillon est conservé.", true);
        return false;
      }
      const token = formSession.currentToken(), id = shell.state.objectId, refresh = ++refreshGeneration;
      const [feature, loadedChoices] = await Promise.all([
        request(`${profile.apiPath}/${encodeURIComponent(id)}`), businessForm.loadChoices(profile, fetchJson, referenceData),
      ]);
      if (!formSession.isCurrent(token) || refresh !== refreshGeneration) return false;
      if (feature.properties?.version < current()?.properties?.version) return false;
      if (formIsDirty() || shell.state.pending || shell.state.conflict) {
        shell.setConflict(); setStatus("Cet objet a changé sur le serveur. Votre brouillon est conservé.", true);
        return false;
      }
      if (idOf(feature) !== String(id)) throw new Error("Objet Lit incohérent.");
      choices = loadedChoices; shell.setSnapshot(feature); renderFeature(feature); upsertFeature(feature);
      return true;
    }
    return { layer, load, beginCreate, openEdit, refreshCurrent, shell, upsertFeature, dispose: () => formSession.dispose() };
  }
  return { create };
});
