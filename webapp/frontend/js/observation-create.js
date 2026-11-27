/* Formulaire commun ; les parents préparés utilisent l'intention durable. */
(function (root) {
  "use strict";
  const labels = Object.freeze({
    desordres: "Désordre", troncons: "Tronçon", ouvrages_hydrauliques: "Ouvrage hydraulique",
    equipements_mesure: "Équipement de mesure", cheminements: "Cheminement", mobilier: "Mobilier",
    reseaux_techniques: "Réseau technique", amenagements_hydrauliques: "AH réglementaire",
    zones_stockage: "Zone de stockage",
    vegetation: "Végétation", ouvrages_particuliers: "Ouvrage particulier",
    prestations: "Prestation",
  });
  let dialog, form, fieldset, status, context, result, save, close, reloadReferences, state, formView;
  const byName = name => form.elements.namedItem(name);

  async function request(url, options = {}) {
    await root.ResourceEventTransport?.bootstrap;
    const headers = options.method ? await root.ResourceMutationController.requestHeaders() : {};
    const response = await (globalThis.BusinessRuntime?.fetch || fetch)(url, { ...options, headers: { ...headers, ...options.headers },
      signal: AbortSignal.timeout(20000) });
    let payload;
    try { payload = await response.json(); } catch (_error) { /* Résultat potentiellement inconnu. */ }
    if (!response.ok || !payload) {
      const detail = payload?.detail;
      const message = typeof detail === "string" ? detail : Array.isArray(detail)
        ? detail.map(item => `${item.loc?.slice(1).join(".") || "Champ"} : ${item.msg}`).join(" · ")
        : detail?.message || "Le serveur n’a pas confirmé la création.";
      const error = new Error(message);
      error.status = response.ok ? 502 : response.status;
      error.code = detail?.code;
      throw error;
    }
    return payload;
  }

  function showStatus(message, error = false) {
    status.textContent = message;
    status.classList.toggle("error", error);
  }

  function lock() {
    formView.sync({mode:state.created ? "read" : "create",objectId:state.created?.id,
      pending:state.busy,uncertain:state.unknown,retryUncertain:true,dirty:state.dirty});
    fieldset.disabled = state.busy || state.unknown || state.attempted || !state.referencesReady;
    save.disabled = state.busy || !state.referencesReady || state.blocked;
    close.disabled = state.busy || state.unknown;
    reloadReferences.disabled = state.busy;
    save.textContent = state.unknown ? "Vérifier / réessayer la création" : "Enregistrer l’observation";
  }

  function closeDialog() {
    if (state.busy || state.unknown) return;
    if (!state.created && state.dirty && !root.confirm("Abandonner la saisie de cette observation ?")) return;
    const current = state;
    const parent = state.parent;
    const local = state.created?.version === null;
    state = null;
    dialog.close();
    if (current.created) notifySaved(current);
    if (local) void root.OfflineRuntime.showParent(parent);
  }

  function renderResult(observation) {
    context.textContent = `${labels[observation.parent.resource]} — ${observation.parent.designation}`;
    result.replaceChildren();
    const fields = [
      ["Identifiant", observation.id], ["Désignation", observation.designation],
      ["Date", observation.date], ["Évolution", observation.evolution],
      ["Suite à apporter", observation.suite_apporter_libelle], ["Complément", observation.suite],
      ["Urgence", observation.urgence_libelle], ["Nombre de désordres", observation.nombre_desordres],
      ["Validité", observation.valid ? "Valide" : "Invalide"],
    ];
    for (const [label, value] of fields) {
      const row = document.createElement("div"), term = document.createElement("dt"), detail = document.createElement("dd");
      term.textContent = label; detail.textContent = value == null || value === "" ? "—" : String(value);
      row.append(term, detail); result.append(row);
    }
    root.PhotoMedia?.renderGallery(dialog.querySelector("#observation-created-photos"), observation);
    dialog.querySelector("#observation-created-photos").hidden = false;
    formView.setSubject({ objectName: observation.designation });
    result.hidden = false;
    fieldset.hidden = true;
    save.hidden = true;
    reloadReferences.hidden = true;
  }

  function notifySaved(current) {
    try { Promise.resolve(current.onSaved?.(current.created)).catch(error => console.error("Actualisation du parent", error)); }
    catch (error) { console.error("Actualisation du parent", error); }
  }

  function assertResult(payload, current) {
    if (payload?.id !== current.id || payload.parent?.resource !== current.parent.resource
        || payload.parent?.id !== current.parent.id || !Number.isSafeInteger(payload.version) || payload.version < 1) {
      throw new Error("Réponse incohérente. Vérifiez la même création avant de poursuivre.");
    }
  }

  async function submit(event) {
    event.preventDefault();
    const current = state;
    if (!current || current.busy || current.created || current.blocked || !current.referencesReady) return;
    if (!current.unknown && !current.attempted) {
      if (!form.reportValidity()) return;
      const optional = name => byName(name).value.trim() || null;
      current.payload = {
        id: current.id, parent: { resource: current.parent.resource, id: current.parent.id },
        designation: optional("designation"), date: optional("date"), evolution: optional("evolution"),
        suite_apporter_id: optional("suite_apporter_id"), suite: optional("suite"),
        urgence_id: current.parent.resource === "desordres" ? optional("urgence_id") : null,
        nombre_desordres: byName("nombre_desordres").value === "" ? null : Number(byName("nombre_desordres").value),
        valid: true,
      };
    }
    current.busy = true;
    lock(); showStatus("Création en cours…");
    try {
      if (current.editOperation || current.durableId || await root.OfflineRuntime?.preparedParent(current.parent)) {
        current.localPath = true;
        if (!navigator.locks) throw new Error("Écriture durable indisponible sans Web Locks ; brouillon conservé.");
        let outcome;
        if (current.editOperation) {
          const operation = await root.OfflineRuntime.store.editCreate(current.editOperation, current.payload);
          current.operationId = operation.operationId;
          current.durableId = operation.operationId; current.durableOperation = operation;
          outcome = { operation };
        } else {
          if (!current.durableId) {
            const queued = await root.OfflineRuntime.store.enqueueCreate({ operationId: current.operationId, changes: current.payload });
            current.durableId = queued.operationId; current.durableOperation = queued;
          }
          // Once durable, a secondary refresh/lock failure must not masquerade
          // as a failed creation or allow a second enqueue.
          try { await root.OfflineOperationsRuntime.synchronize(true); } catch (_) { /* retained below */ }
          outcome = { operation: (await root.OfflineRuntime.store.listOperations()).find(o => o.operationId === current.durableId) };
        }
        const operation = outcome.operation;
        const row = operation ? root.LocalProjection.localCreation(operation)
          : await root.OfflineRuntime.store.read("observations", current.id);
        current.created = row.payload; current.unknown = false; current.dirty = false;
        renderResult(row.payload);
        notifySaved(current);
        showStatus(operation ? `Observation enregistrée localement — ${root.OfflineOperations.statusLabel(operation.status)}.`
          : "Observation créée et enregistrée. Synchronisation confirmée.");
        return;
      }
      current.attempted = true;
      const payload = await request("/api/observations", { method: "POST",
        headers: { "Content-Type": "application/json", "Operation-Id": current.operationId },
        body: JSON.stringify(current.payload) });
      assertResult(payload, current);
      if (!root.ResourceInvalidation.normalizeMutationPayload(payload.mutation)
          || payload.mutation?.resource !== "observations" || payload.mutation?.action !== "create"
          || payload.mutation?.ids?.length !== 1
          || payload.mutation?.ids?.[0] !== current.id
          || payload.mutation?.versions?.[current.id] !== payload.version) {
        throw new Error("Reçu incomplet. Vérifiez la même création avant de poursuivre.");
      }
      current.created = payload;
      current.unknown = false;
      current.dirty = false;
      // L'état autoritaire est visible avant les effets secondaires de publication.
      renderResult(payload);
      notifySaved(current);
      showStatus("Observation créée et enregistrée.");
      try {
        await root.ResourceMutationController.publishMutation(payload.mutation, { applyLocally: true });
      } catch (error) {
        console.error("Publication Observation créée", error);
        showStatus("Observation créée et enregistrée. L’actualisation des autres vues est incomplète.", true);
      }
    } catch (error) {
      if (current.localPath) {
        if (current.durableId) {
          current.created = root.LocalProjection.localCreation(current.durableOperation).payload;
          current.unknown = false; current.dirty = false; renderResult(current.created);
          notifySaved(current);
          showStatus("Observation enregistrée localement. Actualisation incomplète : " + error.message, true);
        } else {
          current.unknown = false;
          showStatus("Enregistrement local refusé ; saisie conservée. " + error.message, true);
        }
        return;
      }
      // Un résultat réseau/5xx est inconnu, jamais une autorisation de changer
      // l'UUID ou l'intention. Un retry restitue le reçu initial si COMMIT a eu lieu.
      current.unknown = !!current.durableId || !error.status || error.status >= 500 || [408, 429].includes(error.status);
      current.blocked = !current.unknown;
      const prefix = current.unknown ? "Résultat non confirmé. Réessayez la même création. "
        : error.code === "observation-id-collision" ? "Collision d’UUID. "
          : error.code === "operation-id-collision" ? "Collision d’Operation-Id. "
            : error.status === 422 ? "Validation refusée. " : "Création refusée. ";
      showStatus(prefix + error.message + (current.blocked ? " Cette intention reste figée. Fermez puis ouvrez une nouvelle création pour la corriger." : ""), true);
    } finally {
      current.busy = false;
      lock();
    }
  }

  async function loadReferences(current) {
    showStatus("Chargement des choix de référence…");
    try {
      const [suites, urgences] = await Promise.all([
        root.ReferenceData.load("suitesApporter", request),
        current.parent.resource === "desordres" ? root.ReferenceData.load("urgences", request) : [],
      ]);
      if (state !== current) return;
      root.ReferenceData.fillSelect(byName("suite_apporter_id"), suites, byName("suite_apporter_id").value, "Aucune suite à apporter");
      root.ReferenceData.fillSelect(byName("urgence_id"), urgences, byName("urgence_id").value, "Aucune urgence");
      byName("suite_apporter_id").disabled = false; byName("urgence_id").disabled = false;
      current.referencesReady = true;
      reloadReferences.hidden = true;
      showStatus(""); lock(); byName("designation").focus();
    } catch (error) {
      if (state !== current) return;
      showStatus("Chargement des références impossible. " + error.message, true);
      reloadReferences.hidden = false;
      if (root.OfflineRuntime?.networkError(error) && await root.OfflineRuntime.preparedParent(current.parent)) {
        if (state !== current) return;
        for (const name of ["suite_apporter_id", "urgence_id"]) {
          root.ReferenceData.fillSelect(byName(name), [], byName(name).value, "Choix indisponibles hors connexion");
          byName(name).disabled = true;
        }
        current.referencesReady = true; lock();
        showStatus("Création locale disponible. Référentiels non préparés : choix facultatifs indisponibles.");
      }
    }
  }

  function initialize() {
    dialog = document.createElement("dialog");
    dialog.id = "observation-create-dialog";
    dialog.className = "observation-create-dialog";
    dialog.setAttribute("aria-labelledby", "observation-create-title");
    const descriptor = root.ObservationForm.composition({parent:{resource:"desordres",id:"pending"}});
    descriptor.actions = [{name:"references",label:"Recharger les choix",hidden:true}];
    descriptor.children = ["result", "photos"];
    formView = root.EntityFormView.create({root, document, descriptor, id:"observation-create",
      onSubmit:submit, onCancel:closeDialog, onClose:closeDialog});
    dialog.replaceChildren(formView.element); document.body.append(dialog);
    form=formView.form; fieldset=formView.fieldset; status=formView.status;
    context=formView.controls.get("parent_label"); context.id="observation-create-parent";
    result=formView.children.get("result"); result.id="observation-create-result"; result.hidden=true;
    formView.children.get("photos").id="observation-created-photos";
    save=formView.save; close=formView.cancel; reloadReferences=formView.extraActions.get("references");
    save.id="observation-create-submit"; close.id="observation-create-close";
    reloadReferences.id="observation-create-references"; status.id="observation-create-status";
    formView.fieldGroups.get("urgence_id").id="observation-create-urgency";
        form.addEventListener("input", () => { if (state && !state.busy && !state.unknown) state.dirty = true; });
    dialog.addEventListener("cancel", event => { event.preventDefault(); closeDialog(); });
    reloadReferences.addEventListener("click", () => loadReferences(state));
    root.addEventListener("beforeunload", event => {
      if (state && (state.dirty || state.busy || state.unknown)) { event.preventDefault(); event.returnValue = ""; }
    });
    root.ResourceMutationController.registerInvalidator("open-detail", async mutation => {
      const current = state;
      if (!current?.created || current.created.version === null || !["observations", "photos"].includes(mutation.resource)) return;
      if (mutation.resource === "observations" && ((mutation.ids !== null && !mutation.ids.includes(current.id)) || mutation.action === "create")) return;
      const sequence = current.readSequence = (current.readSequence || 0) + 1;
      try {
        const payload = await request(`/api/observations/${encodeURIComponent(current.id)}`);
        if (state !== current || current.readSequence !== sequence) return;
        assertResult(payload, current);
        if (payload.version < current.created.version) return;
        current.created = payload; renderResult(payload); showStatus("Observation relue depuis PostgreSQL.");
      } catch (error) {
        if (state === current && current.readSequence === sequence) showStatus("Observation enregistrée. Actualisation impossible : " + error.message, true);
        throw error;
      }
    });
  }

  function open(parent, onSaved = null) {
    if (!parent?.id || !Object.hasOwn(labels, parent.resource)) throw new Error("Parent Observation invalide.");
    if (root.BusinessRuntime && !root.BusinessRuntime.can("business.create")) return false;
    if (!dialog) initialize();
    if (dialog.open) return false;
    const now = new Date();
    state = { id: root.ResourceEvents.createId(), operationId: root.ResourceEvents.createId(),
      parent: { resource: parent.resource, id: String(parent.id), designation: parent.designation || parent.id },
      onSaved, referencesReady: false, busy: false, unknown: false, dirty: false, blocked: false };
    dialog.dataset.observationId = state.id;
    dialog.querySelector("#observation-created-photos").hidden = true;
    form.reset(); form.hidden = false; fieldset.hidden = false; save.hidden = false; result.hidden = true; result.replaceChildren();
    for (const name of ["suite_apporter_id", "urgence_id"]) {
      byName(name).disabled = false;
      root.ReferenceData.fillSelect(byName(name), [], null);
    }
    reloadReferences.hidden = true;
    context.textContent = `${labels[parent.resource]} — ${state.parent.designation}`;
    byName("date").value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    for(const key of ["urgence_id","nombre_desordres"])
      formView.fieldGroups.get(key).hidden = parent.resource !== "desordres";
    lock(); dialog.showModal(); void loadReferences(state);
    return true;
  }
  function edit(operation) {
    if (!open(operation.parentContext)) return false;
    state.id = operation.resourceId; state.operationId = operation.operationId;
    state.editOperation = operation.operationId;
    dialog.dataset.observationId = state.id;
    for (const [key, value] of Object.entries(operation.changes)) {
      const control = byName(key);
      if (!control) continue;
      if (control.tagName === "SELECT") root.ReferenceData.fillSelect(control, [], value);
      else control.value = value ?? "";
    }
    return true;
  }
  async function refreshConfirmed() {
    const current = state;
    if (!current?.created || current.created.version !== null || current.busy) return;
    if ((await root.OfflineRuntime.store.listOperations()).some(o => o.resourceId === current.id)) return;
    const row = await root.OfflineRuntime.store.read("observations", current.id);
    if (state !== current || !row) return;
    assertResult(row.payload, current);
    current.created = row.payload; renderResult(row.payload); showStatus("Observation synchronisée — état confirmé par le serveur.");
  }
  root.ObservationCreate = { open, edit, refreshConfirmed };
})(globalThis);
