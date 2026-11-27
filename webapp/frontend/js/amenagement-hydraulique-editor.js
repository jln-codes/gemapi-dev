/* Éditeur non spatial de l'AH réglementaire, dans le panneau partagé Carte. */
(function (root, factory) {
  const formAdapter = typeof module === "object" && module.exports
    ? require("./amenagement-hydraulique-form.js") : root.AmenagementHydrauliqueForm;
  const api = factory(formAdapter);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.AmenagementHydrauliqueEditor = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (formAdapter) {
  "use strict";

  function create({ root = globalThis, fetchJson,
    referenceData = root.ReferenceData, entityEditor = root.EntityEditorShell,
    relationSelector = root.RelationSelector,
    mutationController = root.ResourceMutationController,
    concurrency = root.ResourceConcurrency,
    observationCreate = root.ObservationCreate,
    openObservation = null, featureStore = null, panel = null }) {
    if (!fetchJson || !referenceData || !entityEditor || !relationSelector
        || !mutationController || !concurrency || !observationCreate || !panel) {
      throw new Error("Dépendances de l'éditeur AH réglementaire incomplètes.");
    }
    const document = root.document;
    let formView;
    const shell = entityEditor.create({ mode: "read", objectType: "amenagements-hydrauliques" });
    formView = root.EntityFormView.create({ root, document, descriptor: formAdapter.composition(formAdapter.profile("amenagements_hydrauliques")),
      id: "amenagement-hydraulique-editor-panel", onObservations: () => void loadObservations(shell.state.objectId), onClose: () => panelSession.close() });
    const panelRoot = formView.element;
    formView.setFormId(`amenagement-hydraulique-editor-form`);
    const form = panelRoot.querySelector("form");
    const fieldset = panelRoot.querySelector("fieldset");
    const status = panelRoot.querySelector('[data-role="status"]');
    const saveButton = panelRoot.querySelector('[data-role="save"]');
    const cancelButton = panelRoot.querySelector('[data-role="cancel"]');
    const observationsList = panelRoot.querySelector('[data-role="observations"]');
    const childSection = panelRoot.querySelector('[data-role="sous-objets-section"]');
    const childList = panelRoot.querySelector('[data-role="sous-objets"]');
    const current = () => shell.state.snapshot;
    const idOf = feature => String(feature?.properties?.id || feature?.id || "");
    const labelOf = feature => feature?.properties?.designation || feature?.properties?.libelle || idOf(feature);
    const can = permission => !root.BusinessRuntime || root.BusinessRuntime.can(permission);
    let features = [], choices = { troncons: [], zones: [], desordres: [] }, readSequence = 0;
    let panelSession;

    function setStatus(message, error = false) {
      status.textContent = message;
      status.classList.toggle("error", Boolean(error));
    }
    function setPending(pending) {
      fieldset.disabled = pending;
      saveButton.disabled = pending;
      cancelButton.disabled = pending;
      saveButton.textContent = pending ? "Enregistrement…" : "Enregistrer";
      panelSession.sync();
    }
    function upsertFeature(feature) {
      const id = idOf(feature);
      features = [...features.filter(item => idOf(item) !== id), feature];

    }
    function compositionLink(resource, row) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary";
      button.textContent = row.designation || row.libelle || String(row.id);
      button.addEventListener("click", () => void root.MapEntityNavigation?.open(resource, row.id));
      return button;
    }
    function renderFeature(feature) {
      const p = feature?.properties || {};
      formAdapter.fill(form, feature, choices, referenceData, relationSelector);
      childSection.hidden = shell.state.mode !== "edit";
      childList.replaceChildren();
      if (shell.state.mode === "edit") {
        for (const [label, resource, rows] of [
          ["Zones de stockage", "zones_stockage", p.zones_stockage || []],
          ["Tronçons associés", "troncons", p.troncons || []],
        ]) {
          const group = document.createElement("div");
          const heading = document.createElement("strong");
          heading.textContent = `${label} : `;
          group.append(heading);
          if (!rows.length) {
            const empty = document.createElement("span"); empty.textContent = "aucun"; group.append(empty);
          } else rows.forEach(row => group.append(compositionLink(resource, row)));
          childList.append(group);
        }
      }
      panelSession.sync();
    }
    async function request(url, options = {}) {
      const headers = options.method ? await mutationController.requestHeaders() : {};
      const response = await (root.BusinessRuntime?.fetch || root.fetch)(url,
        { ...options, headers: { ...headers, ...options.headers } });
      let body = null;
      try { body = await response.json(); } catch (_error) { /* HTTP sans corps. */ }
      if (!response.ok) throw concurrency.httpError(response.status, body, "Opération AH refusée.");
      if (!body) throw new Error("Réponse AH invalide.");
      return body;
    }
    async function loadRelations(feature = null) {
      const sequence = readSequence;
      const loaded = await formAdapter.loadChoices(null, fetchJson, referenceData, feature);
      if (sequence !== readSequence || !panelSession.isActive()) return;
      choices = loaded;
      formAdapter.fill(form, feature, choices, referenceData, relationSelector);
    }
    async function loadObservations(id) {
      const sequence = readSequence;
      return root.ObservationParentList.load({
        container: observationsList, parent: { resource: "amenagements_hydrauliques", id },
        path: `/api/amenagements-hydrauliques/${encodeURIComponent(id)}/observations`,
        fetchJson, openObservation,
        isCurrent: () => panelSession.isActive() && sequence === readSequence
          && shell.state.mode === "edit" && String(shell.state.objectId) === String(id),
      });
    }
    async function beginCreate() {
      if (!can("business.create") || !panelSession.prepare()) return;
      const sequence = ++readSequence;
      shell.beginCreate({ objectType: "amenagements-hydrauliques" });
      form.reset(); observationsList.replaceChildren();
      panelSession.showTab("general");
      panelSession.update();
      setStatus("Chargement des relations…"); panelSession.setLoading(true);
      try {
        await loadRelations();
        if (sequence !== readSequence || !panelSession.isActive()) return;
        renderFeature({ properties: { valid: null } });
        shell.markDirty(false);
        setStatus("");
        form.elements.designation.focus();
      } catch (error) {
        if (sequence === readSequence && panelSession.isActive()) setStatus(error.message, true);
      } finally { if (sequence === readSequence) panelSession.setLoading(false); }
    }
    async function openEdit(id) {
      if (!can("business.read") || !panelSession.prepare(id)) return;
      const sequence = ++readSequence;
      shell.beginRead({ objectType: "amenagements-hydrauliques" });
      form.reset(); observationsList.replaceChildren(); panelSession.showTab("general");
      panelSession.update();
      setStatus("Chargement de l'AH…"); panelSession.setLoading(true);
      try {
        const feature = await request(`/api/amenagements-hydrauliques/${encodeURIComponent(id)}`);
        if (sequence !== readSequence || !panelSession.isActive()) return;
        if (idOf(feature) !== String(id)) throw new Error("Objet incohérent.");
        await loadRelations(feature);
        if (sequence !== readSequence || !panelSession.isActive()) return;
        shell.beginEdit({ objectType: "amenagements-hydrauliques", objectId: id,
          data: feature, snapshot: feature, geometryType: null });
        renderFeature(feature); shell.markDirty(false);
        await loadObservations(id);
        if (sequence !== readSequence || !panelSession.isActive()) return;
        panelSession.update();
        setStatus("État relu depuis PostgreSQL.");
        return feature;
      } catch (error) {
        if (sequence === readSequence && panelSession.isActive()) setStatus(error.message, true);
      } finally { if (sequence === readSequence) panelSession.setLoading(false); }
    }
    async function submit(event) {
      event.preventDefault();
      if (shell.state.pending || !panelSession.isActive()
          || !can(shell.state.mode === "create" ? "business.create" : "business.update")
          || !form.reportValidity()) return;
      const editing = shell.state.mode === "edit";
      const body = formAdapter.payload(form, null, relationSelector, editing ? current() : null);
      shell.beginSave(); setPending(true); setStatus("Enregistrement dans PostgreSQL…");
      try {
        const headers = { "Content-Type": "application/json" };
        if (editing) {
          Object.assign(headers, concurrency.headers(current()?.properties?.version));
          headers["Operation-Id"] = root.crypto.randomUUID();
        }
        const result = await request(editing
          ? `/api/amenagements-hydrauliques/${encodeURIComponent(shell.state.objectId)}`
          : "/api/amenagements-hydrauliques",
        { method: editing ? "PUT" : "POST", headers, body: JSON.stringify(body) });
        if (!result?.properties?.id || !Number.isInteger(result.properties.version))
          throw new Error("Réponse AH sans identifiant ou version.");
        if (editing && idOf(result) !== String(shell.state.objectId))
          throw new Error("Réponse AH incohérente.");
        shell.confirm(result);
        if (!editing) shell.beginEdit({ objectType: "amenagements-hydrauliques",
          objectId: result.properties.id, data: result, snapshot: result, geometryType: null });
        upsertFeature(result); renderFeature(result);
        panelSession.update();
        await loadObservations(result.properties.id);
        setStatus("AH enregistré et relu depuis PostgreSQL.");
        try { await mutationController.publishMutation(result.mutation, { applyLocally: true }); }
        catch (error) {
          console.error("Publication AH incomplète", error);
          setStatus("AH enregistré. Actualisation des autres vues incomplète.", true);
        }
      } catch (error) {
        shell.fail(error, { conflict: concurrency.isVersionConflict(error) });
        setStatus(error.message, true);
      } finally { setPending(false); }
    }
    panelSession = panel.connect({
      element: panelRoot, formView, shell, label: "AH réglementaire",
      onObservations: () => void loadObservations(shell.state.objectId),
      isDirty: () => formAdapter.isDirty(form, current(), relationSelector),
      hasActiveDraft: () => false,
      onDiscard: () => { ++readSequence; observationsList.replaceChildren(); },
    });
    form.addEventListener("submit", submit);
    form.addEventListener("input", () => shell.markDirty(formAdapter.isDirty(form, current(), relationSelector)));
    form.addEventListener("change", () => shell.markDirty(formAdapter.isDirty(form, current(), relationSelector)));
    cancelButton.addEventListener("click", () => panelSession.close());
    const observationButton = panelRoot.querySelector('[data-role="create-observation"]');
    observationButton.dataset.requires = "business.create";
    observationButton.addEventListener("click", () => {
      const p = current()?.properties;
      if (p?.id) observationCreate.open({ resource: "amenagements_hydrauliques", id: p.id,
        designation: p.designation || p.libelle || p.id }, () => loadObservations(p.id));
    });
    root.addEventListener("beforeunload", event => {
      if (panelSession.isActive() && shell.hasDraft()) {
        event.preventDefault(); event.returnValue = "";
      }
    });

    async function load() {
      const collection = await fetchJson("/api/amenagements-hydrauliques");
      if (collection?.type !== "FeatureCollection") throw new Error("Collection AH invalide.");
      features = collection.features || [];

      return collection;
    }
    featureStore?.registerResource("amenagements-hydrauliques", {
      fetchOne: id => fetchJson(`/api/amenagements-hydrauliques/${encodeURIComponent(id)}`),
      fetchCollection: () => fetchJson("/api/amenagements-hydrauliques"),
      getId: idOf, listIds: () => features.map(idOf), upsert: upsertFeature,
      remove: () => {},
      shouldDefer({ id }) {
        return shell.state.mode === "edit" && String(shell.state.objectId) === String(id)
          && shell.hasDraft();
      },
      markStale() { shell.setConflict(); setStatus("Cet AH a changé sur le serveur. Votre brouillon est conservé.", true); },
      clearStale() { shell.setConflict(false); },
    });
    return { load, openEdit, beginCreate, shell, upsertFeature };
  }
  return { create };
});

if (typeof module === "undefined" && typeof map !== "undefined") {
  const amenagementHydrauliqueEditor = AmenagementHydrauliqueEditor.create({
    fetchJson, referenceData: ReferenceData,
    entityEditor: EntityEditorShell, relationSelector: RelationSelector,
    mutationController: ResourceMutationController, concurrency: ResourceConcurrency,
    observationCreate: ObservationCreate, openObservation,
    featureStore: mapFeatureStore, panel: businessEditorPanel,
  });
  globalThis.MapEntityNavigation?.register("amenagements_hydrauliques", amenagementHydrauliqueEditor);
  globalThis.MapCreateActions?.register("amenagement-hydraulique",
    () => amenagementHydrauliqueEditor.beginCreate());
  mapDataReadyPromise.then(() => amenagementHydrauliqueEditor.load()).catch(error => {
    console.error("Chargement des AH impossible", error);
  });
}
