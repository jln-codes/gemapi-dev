(function (root, factory) {
  const dependency = typeof module === "object" && module.exports
    ? require("./reference-data.js") : root.ReferenceData;
  const concurrency = typeof module === "object" && module.exports
    ? require("./resource-concurrency.js") : root.ResourceConcurrency;
  const observationForm = typeof module === "object" && module.exports
    ? require("./observation-form.js") : root.ObservationForm;
  const mobilierForm = typeof module === "object" && module.exports
    ? require("./mobilier-form.js") : root.MobilierForm;
  const ouvrageForm = typeof module === "object" && module.exports
    ? require("./ouvrage-particulier-form.js") : root.OuvrageParticulierForm;
  const cheminementForm = typeof module === "object" && module.exports
    ? require("./cheminement-form.js") : root.CheminementForm;
  const vegetationForm = typeof module === "object" && module.exports
    ? require("./vegetation-form.js") : root.VegetationForm;
  const amenagementForm = typeof module === "object" && module.exports
    ? require("./amenagement-hydraulique-form.js") : root.AmenagementHydrauliqueForm;
  const zoneForm = typeof module === "object" && module.exports
    ? require("./zone-stockage-form.js") : root.ZoneStockageForm;
  const prestationForm = typeof module === "object" && module.exports
    ? require("./prestation-form.js") : root.PrestationForm;
  const litForm = typeof module === "object" && module.exports
    ? require("./lit-form.js") : root.LitForm;
  const relationSelector = typeof module === "object" && module.exports
    ? require("./relation-selector.js") : root.RelationSelector;
  const disorderRelatedObjects = typeof module === "object" && module.exports
    ? require("./disorder-related-objects.js") : root.DisorderRelatedObjects;
  const tronconForm = typeof module === "object" && module.exports
    ? require("./troncon-form.js") : root.TronconForm;
  const formSession = typeof module === "object" && module.exports
    ? require("./entity-form-session.js") : root.EntityFormSession;
  const geometryDraft = typeof module === "object" && module.exports
    ? require("./entity-geometry-draft.js") : root.EntityGeometryDraft;
  const dataEntityDetail = factory(dependency, concurrency, observationForm, mobilierForm,
    ouvrageForm, cheminementForm, vegetationForm, amenagementForm, zoneForm, prestationForm,
    relationSelector, disorderRelatedObjects, litForm, formSession, tronconForm, geometryDraft);
  if (typeof module === "object" && module.exports) module.exports = dataEntityDetail;
  root.DataEntityDetail = dataEntityDetail;
})(typeof globalThis !== "undefined" ? globalThis : this, function (referenceData, Concurrency,
    ObservationForm, MobilierForm, OuvrageForm, CheminementForm, VegetationForm,
    AmenagementForm, ZoneForm, PrestationForm, RelationSelector, DisorderRelatedObjects, LitForm, FormSession, TronconForm, GeometryDraft) {
  "use strict";

  function pilotConfig(classId, formAdapter = MobilierForm) {
    const settings = formAdapter.profile(classId);
    return {
      label: settings.singular, endpoint: settings.apiPath,
      mutationResource: settings.resource, pilotProfile: settings, formAdapter,
      fields: formAdapter.readFields(settings),
    };
  }

  const ENTITY_CONFIGS = Object.freeze({
    mobilier: pilotConfig("mobilier"),
    equipements_mesure: pilotConfig("equipements_mesure"),
    reseaux_techniques: pilotConfig("reseaux_techniques"),
    ouvrages_particuliers: pilotConfig("ouvrages_particuliers", OuvrageForm),
    cheminements: pilotConfig("cheminements", CheminementForm),
    vegetation: pilotConfig("vegetation", VegetationForm),
    amenagements_hydrauliques: pilotConfig("amenagements_hydrauliques", AmenagementForm),
    zones_stockage: pilotConfig("zones_stockage", ZoneForm),
    prestations: pilotConfig("prestations", PrestationForm),
    troncons_lit: pilotConfig("troncons_lit", LitForm),
    elements_morphologiques_lit: pilotConfig("elements_morphologiques_lit", LitForm),
    observations: {
      label: "Observation", endpoint: "/api/observations", partialUpdate: true,
      fields: [],
    },
    systemes: {
      label: "Système d’endiguement",
      endpoint: "/api/systemes-endiguement",
      fields: [
        ["libelle", "Libellé", "text", true],
        ["designation", "Désignation", "text", true],
        ["population_protegee", "Population protégée", "number", true],
        ["classement", "Classement", "text", true],
        ["niveau_protection", "Niveau de protection", "text", true],
        ["commentaire", "Commentaire", "textarea", true],
        ["valid", "Objet valide", "checkbox", true],
      ],
    },
    digues: {
      label: "Digue",
      endpoint: "/api/digues",
      fields: [
        ["libelle", "Libellé", "text", true],
        ["designation", "Désignation", "text", true],
        ["commentaire", "Commentaire", "textarea", true],
        ["systeme_endiguement_libelle", "Système d’endiguement", "text", false],
        ["troncons", "Tronçons physiques", "unified_troncons", false],
        ["valid", "Objet valide", "checkbox", true],
      ],
    },
    troncons: pilotConfig("troncons", TronconForm),
    desordres: {
      label: "Désordre",
      endpoint: "/api/desordres",
      partialUpdate: true,
      fields: [
        ["designation", "Désignation", "text", true],
        ["type_desordre_id", "Type", "select", true, "typesDesordre", "type_desordre_libelle"],
        ["categorie_desordre_id", "Catégorie", "select", true, "categoriesDesordre", "categorie_desordre_libelle"],
        ["cote_id", "Côté", "select", true, "cotes", "cote_libelle"],
        ["position_id", "Position", "select", true, "positions", "position_libelle"],
        ["source_id", "Source", "select", true, "sources", "source_libelle"],
        ["lieu_dit", "Lieu-dit", "text", true],
        ["date_debut", "Date de début", "date", true],
        ["date_fin", "Date de fin", "date", true],
        ["commentaire", "Commentaire", "textarea", true],
        ["valid", "Objet valide", "checkbox", true],
        ["troncons", "Tronçon(s)", "text", false],
        ["digues", "Digue(s)", "text", false],
        ["systemes", "Système(s)", "text", false],
        ["zones_stockage_labels", "Zone(s) de stockage", "text", false],
        ["localisation", "Géométrie", "text", false],
      ],
    },
  });

  function text(value, fallback = "—") {
    const normalized = String(value ?? "").trim();
    return normalized || fallback;
  }

  function formatUnifiedTroncons(rows) {
    return (rows || [])
      .map((row) => `${row.type_physique_libelle || row.type_physique_id} : ${row.designation || row.id}`);
  }

  function formatSubobjects(values, key) {
    return (values[key] || []).map(row => row.designation || row.libelle || row.id)
      .join(", ") || "aucun";
  }

  function entityLabels(entities, entityClass, fallback) {
    const labels = (entities || [])
      .filter((entity) => entity?.class === entityClass)
      .map((entity) => text(
        entity.attributes?.designation || entity.attributes?.libelle,
        "",
      ))
      .filter(Boolean);
    return [...new Set(labels)].join(" · ") || fallback;
  }

  function normalizeDetail(entityClass, payload, entities = []) {
    const config = ENTITY_CONFIGS[entityClass];
    if (!config) throw new Error("Classe métier non prise en charge.");
    const raw = payload?.type === "Feature" ? payload.properties : payload;
    if (!raw || typeof raw !== "object") throw new Error("Réponse détaillée invalide.");
    const values = { ...raw };
    if (config.pilotProfile) {
      const geometry = payload?.geometry;
      const point = geometry?.type === "Point" ? geometry.coordinates : null;
      values.localisation = point && point.length === 2
        ? `Point ${Number(point[1]).toFixed(6)}, ${Number(point[0]).toFixed(6)}`
        : geometry?.type || "Non localisé";
    }
    if (config.formAdapter === LitForm) values.geometry_diagnostics = LitForm.geometryDiagnosticText(payload);
    if (config.formAdapter === TronconForm) {
      values.localisation = TronconForm.geometrySummary(payload);
      values.type_physique_libelle = TronconForm.typeLabel(raw);
    }
    if (entityClass === "observations") ObservationForm.parent(values);
    if (entityClass === "desordres") {
      values.localisation = payload.geometry?.type || "Géométrie non renseignée";
      values.zones_stockage_labels = Array.isArray(raw.zones_stockage)
        ? raw.zones_stockage.map(zone => zone.designation || zone.libelle || zone.id).join(" · ") || "Aucune"
        : "—";
      values.troncons = entityLabels(entities, "troncons", "Sans tronçon");
      values.digues = entityLabels(entities, "digues", "Sans digue");
      values.systemes = entityLabels(entities, "systemes", "Sans système d’endiguement");
    }
    return {
      entityClass,
      id: String(raw.id ?? payload.id ?? ""),
      values,
      title: text(raw.designation || raw.libelle, config.label),
      feature: payload,
    };
  }

  async function defaultFetchJson(url, options = {}) {
    const runtime = globalThis.BusinessRuntime;
    const offline = globalThis.OfflineRuntime;
    await globalThis.ResourceEventTransport?.bootstrap;
    const originHeaders = options.method === "PUT"
      ? await globalThis.ResourceMutationController?.requestHeaders() : {};
    if (runtime !== globalThis.BusinessRuntime || runtime?.isActive?.() === false
        || offline !== globalThis.OfflineRuntime) throw new Error("Le contexte de la base a changé. Rechargez la fiche.");
    const response = await (offline?.fetch || runtime?.fetch || fetch)(url, { ...options,
      headers: { ...originHeaders, ...options.headers },
    });
    let payload = null;
    try { payload = await response.json(); } catch (_error) { /* Repli HTTP. */ }
    if (!response.ok) {
      throw Concurrency.httpError(response.status, payload, "Chargement impossible.");
    }
    return payload;
  }

  // Host navigation and inert controller ports. Form composition belongs to View.
  function createHost(document, panel) {
    const node=(tag,name)=>{const el=document.createElement(tag);el.id=`data-entity-detail-${name}`;return el;};
    const navigation=node("nav","navigation");navigation.className="data-entity-detail-actions";
    for(const [name,label] of [["back","Retour"],["edit","Modifier"],["map","Voir sur Carte"]]){
      const button=node("button",name);button.type="button";button.className="entity-form__button";button.textContent=label;button.hidden=name!=="back";navigation.append(button);
    }
    const title=node("span","title");title.hidden=true;panel.append(navigation,title);
    const status=node("p","status"),read=node("div","read"),form=node("form","form"),
      fields=node("div","edit-fields"),error=node("p","error"),save=node("button","save");
    status.className="data-entity-detail-status";status.setAttribute("role","status");
    read.className="data-entity-detail-body";form.className="data-entity-detail-form";
    fields.className="data-entity-detail-edit-fields";error.className="data-entity-detail-error";
    error.setAttribute("role","alert");
    form.hidden=true;save.type="submit";form.append(fields,save);save.hidden=true;
    const observations=node("section","observations");observations.hidden=true;
    const create=node("button","create-observation");create.type="button";create.textContent="+ Observation";
    observations.append(create,node("div","observation-list"));panel.append(status,read,form,error,observations);
    return panel;
  }

  function createController({
    rootElement = document,
    eventTarget = rootElement.querySelector("main"),
    titleElement = null,
    onBack = null,
    contextActions = null,
    onFormClose = null,
    onReleaseContextActions = () => {},
    onManageReperage = null,
    geometryEditable = false,
    fetchJson = defaultFetchJson,
    getSource = () => "statistics",
    onShow = () => {}, onReady = () => {},
    onClose = () => {},
    onSaved = () => {},
    onMutationConfirmed = () => {},
    onStateChange = () => {},
    hasExternalDraft = () => false,
  } = {}) {
    const byId = (identifier) => rootElement.querySelector(`#${identifier}`);
    const panel = byId("data-entity-detail");
    if (!byId("data-entity-detail-back")) createHost(rootElement.ownerDocument || document, panel);
    const back = byId("data-entity-detail-back");
    const title = titleElement || byId("data-entity-detail-title");
    let status = byId("data-entity-detail-status");
    const read = byId("data-entity-detail-read");
    const edit = byId("data-entity-detail-edit");
    let form = byId("data-entity-detail-form");
    let saveButton = byId("data-entity-detail-save");
    const editActions = byId("data-entity-detail-edit-actions") || saveButton;
    const editFields = byId("data-entity-detail-edit-fields");
    let error = byId("data-entity-detail-error");
    let observationsSection = byId("data-entity-detail-observations");
    let observationsList = byId("data-entity-detail-observation-list");
    const createObservation = byId("data-entity-detail-create-observation");
    const mapButton = byId("data-entity-detail-map");
    if ([panel, back, title, status, read, edit, editActions, form, saveButton,
      editFields, error, eventTarget].some((element) => !element)) {
      throw new Error("Structure DOM de la fiche métier incomplète.");
    }

    const documentTarget = rootElement.ownerDocument || document;
    const hostNavigation = back.parentElement;
    const manageReperage = documentTarget.createElement("button");
    manageReperage.type="button";manageReperage.className="entity-form__button";
    manageReperage.textContent="Gérer le repérage";manageReperage.hidden=true;
    const navigationActions = [...(contextActions || [back, edit, mapButton].filter(Boolean)),manageReperage];
    hostNavigation?.remove();
    const original = { form, status, error, saveButton, observationsSection, observationsList };
    // Initial private ports never compose an alternative form in the host.
    original.form.remove(); original.observationsSection?.remove();
    const hostHeader = panel.querySelector(".data-entity-detail-header");
    let formView = null;
    function removeFormView() {
      tronconSummary?.dispose(); tronconSummary = null;
      if (!formView) return;
      if (contextActions) onReleaseContextActions();
      else hostNavigation?.append(...navigationActions);
      RelationSelector.detachSearch?.(form.elements?.desordre_ids);
      panel.append(read);
      formView.element.remove(); formView = null;
      ({ form, status, error, saveButton, observationsSection, observationsList } = original);
      original.status.hidden = false;
      if (hostHeader) hostHeader.hidden = false;
    }
    function mountFormView(detail, config, readOnly = false) {
      removeFormView();
      const factory = globalThis.EntityFormView;
      let descriptor = config.formAdapter ? config.formAdapter.composition(config.pilotProfile, { geometryEditable: detail.entityClass==="troncons" && geometryEditable, feature: detail.feature })
          : detail.entityClass === "observations" ? ObservationForm.composition(detail.values)
          : globalThis.EntityFormView.fromFields({ classId: detail.entityClass, singular: config.label,
            observationResource: config.observationResource }, config.fields, {
            classification: config.fields.filter(field => field[2] === "select" || ["classement", "niveau_protection"].includes(field[0])).map(field => field[0]),
            overrides: Object.fromEntries(config.fields.filter(field => field[3]).map(([key]) => [key,
              key === "libelle" ? { required: true } : key === "population_protegee" ? { min: "0", step: "1" } : {}])),
          });
      if (!config.formAdapter && detail.entityClass !== "observations") {
        for (const [name,label,kind,editable] of config.fields) if(!editable && kind==="text")
          descriptor.fields.attachments.push({name,label,kind:"output",readOnly:true});
      }
      if(readOnly) descriptor={...descriptor,fields:{},geometry:null,children:["details"]};
      formView = factory.create({ document: documentTarget, descriptor,
        canOpenRelation: resource => Boolean(ENTITY_CONFIGS[resource]),
        onOpenRelation: async (entityClass, entityId) => {
          const previous = { entityClass: detail.entityClass, entityId: detail.id, source, entities: [...entities] };
          if (await open(entityClass, entityId, source, [], { preserveReturn: true })) returnDetail = previous;
        },
        id: `data-${detail.entityClass}-editor`, onSubmit: save,
        onCancel: cancelEditing, onClose: onFormClose || (() => back.click()),
        contextActions: navigationActions,
        onTabChange: name => { detailTab = name; },
        canSelectTab:()=>!hasExternalDraft(),
        onObservations: loadParentObservations,
        onCreateObservation: () => createObservation?.click(),
      });
      for (const [port,control,name] of [[original.observationsList,formView.observationList,"observation-list"],
        [createObservation,formView.createObservation,"create-observation"]]) {
        if(port)port.id=`data-entity-detail-${name}-port`;
        if(control)control.id=`data-entity-detail-${name}`;
      }
      form = formView.form; status = formView.status; error = formView.error;
      saveButton = formView.save; observationsSection = formView.observations;
      observationsList = formView.observationList;
      if (!readOnly) for (const [name,control] of formView.controls)
        if(control.tagName === "OUTPUT") control.value=detail.values[name]??"—";
      if (readOnly) { formView.form.hidden=true; formView.actions.hidden=true; formView.extensions.append(read); }
      if (formView.geometry) formView.geometry.status.textContent = detail.values.localisation || "Non localisé.";
      if(config.formAdapter===TronconForm&&formView.geometry)formView.geometry.setRequirement(true,true);
      original.form.hidden = true; original.status.hidden = true;
      if (original.observationsSection) original.observationsSection.hidden = true;
      if (hostHeader) hostHeader.hidden = true;
      panel.append(formView.element);
      form.addEventListener("input", syncDetailNavigation);
      const boundForm=form;
      form.addEventListener("change",event=>{
        if(form!==boundForm)return;
        if(config.formAdapter===LitForm)LitForm.updateConditional(boundForm,config.pilotProfile);
        if(config.formAdapter===VegetationForm)
          VegetationForm.updateContextualFields(boundForm,
            Object.fromEntries([...boundForm.querySelectorAll("[data-context]")].map(node=>[node.dataset.context,node])),
            RelationSelector);
        if(config.formAdapter === TronconForm) TronconForm.updateConditional(boundForm,detail.feature);
        syncDetailNavigation();
      });
      if (detail.entityClass === "troncons" && globalThis.TronconReperage) {
        const host=documentTarget.createElement("p");host.className="entity-form__help";host.dataset.role="troncon-reperage-summary";
        (formView.sections.get("geometry") || read.querySelector('[data-troncon-section="geometry"]') || formView.extensions).append(host);
        const ownerView=formView;
        tronconSummary=globalThis.TronconReperage.createSummary({container:host,feature:detail.feature,fetchJson,
          isCurrent:()=>formView===ownerView && current?.id===detail.id
            && (!ownerView.form.elements.geometry_mode || ownerView.form.elements.geometry_mode.value==="garder")});
        const summary=tronconSummary;void summary.load();
        let previousMode="garder";
        const updateSummary=()=>{
          if(formView!==ownerView)return;
          const mode=form.elements.geometry_mode?.value||"garder";
          if(mode===previousMode)return;previousMode=mode;
          if(mode!=="garder")host.textContent=detail.feature.properties.geometry_reperage_disponible
            ? "Repérage spécifique conservé : la modification du tracé physique ne remplace pas son axe."
            : mode==="effacer" ? "Le repérage automatique sera indisponible après l’enregistrement du retrait."
              : "Le repérage automatique sera actualisé après l’enregistrement du nouveau tracé.";
          else void summary.load();
        };
        form.addEventListener("input",updateSummary);form.addEventListener("change",updateSummary);
      }
    }

    let current = null;
    let source = "statistics";
    let entities = [];
    let controls = new Map();
    let requestSequence = 0;
    let savingOperation = null;
    let stale = false;
    let conflict = false;
    let saveFailure = null;
    let editing = false;
    let initialControlValues = null;
    let detailTab = "general";
    let relatedObjectsController = null;
    let returnDetail = null;
    let litCharacteristics = null;
    let tronconSummary = null;
    let litIntent = null;
    const relatedClasses = new Set(["troncons", "mobilier", "equipements_mesure", "reseaux_techniques",
      "ouvrages_particuliers", "cheminements", "vegetation", "amenagements_hydrauliques", "zones_stockage",
      "prestations", "troncons_lit", "elements_morphologiques_lit"]);

    const canUpdate = () => !globalThis.BusinessRuntime || globalThis.BusinessRuntime.can("business.update");
    const canCreate = () => !globalThis.BusinessRuntime || globalThis.BusinessRuntime.can("business.create");
    const activeRuntime = () => !globalThis.BusinessRuntime || globalThis.BusinessRuntime.isActive?.() !== false;
    function hasDraft() {
      if (hasExternalDraft()) return true;
      if (litCharacteristics?.hasDraft() || litCharacteristics?.isPending()) return true;
      if (!isEditing()) return false;
      const config = ENTITY_CONFIGS[current.entityClass];
      return conflict || (config.pilotProfile
        ? config.formAdapter.isDirty(form, current.feature, RelationSelector)
        : initialControlValues !== JSON.stringify([...controls].map(([key, control]) => [key, controlValue(control)])));
    }
    function mayLeave() {
      if(current?.entityClass==="troncons"&&globalThis.TronconReperage?.isOpenFor(current.id)&&!globalThis.TronconReperage.close())return false;
      return FormSession.mayLeave({ pending: litCharacteristics?.isPending() || savingOperation,
        uncertain: saveFailure?.uncertain, dirty: hasDraft(),
        confirm: () => globalThis.confirm("Abandonner les modifications de cette fiche ?") });
    }

    function isEditing() {
      return Boolean(current) && editing;
    }

    function observationParent(detail = current) {
      const config = detail && ENTITY_CONFIGS[detail.entityClass];
      const resource = config?.pilotProfile?.observationResource || config?.observationResource;
      if (!resource) return null;
      return {
        resource,
        id: detail.id,
        designation: detail.title,
      };
    }

    function syncDetailNavigation() {
      manageReperage.hidden=current?.entityClass!=="troncons";
      manageReperage.disabled=Boolean(savingOperation||saveFailure?.uncertain||hasExternalDraft());
      if (formView) {
        read.hidden = editing; original.form.hidden = true;
        edit.hidden = editing || !canUpdate(); editActions.hidden = true;
        if (mapButton) mapButton.hidden = !current || !ENTITY_CONFIGS[current.entityClass]?.pilotProfile;
        formView.sync({ mode: editing ? "edit" : "read", objectId: current.id, pending: Boolean(savingOperation), conflict,
          dirty: hasDraft(), error: saveFailure, forbidden: saveFailure?.forbidden,
          uncertain: saveFailure?.uncertain, retryUncertain: false });
        formView.setSubject({ objectName: current.title });
        formView.show(detailTab);
        onStateChange(getState());
        return;
      }
      read.hidden = editing;
      original.form.hidden=true; edit.hidden=true; editActions.hidden=true;
      if(mapButton)mapButton.hidden=true;
      onStateChange(getState());
    }

    function parentIsCurrent(parent, generation) {
      const selected = observationParent();
      return Boolean(selected && detailTab === "observations"
        && selected.resource === parent.resource && selected.id === parent.id
        && isCurrent(generation, current.entityClass, current.id));
    }

    function openParentObservation(id, context) {
      return globalThis.ObservationDetail?.open({
        id,
        parent: context.parent,
        fetchJson,
        onReturn: context.onReturn,
        isParentCurrent: context.isParentCurrent,
      }) || false;
    }

    async function loadParentObservations() {
      const detail = current;
      const parent = observationParent(detail);
      if (!detail || !parent || !observationsList || !globalThis.ObservationParentList) return false;
      const generation = requestSequence;
      const config = ENTITY_CONFIGS[detail.entityClass];
      return globalThis.ObservationParentList.load({
        container: observationsList,
        parent,
        path: `${config.endpoint}/${encodeURIComponent(detail.id)}/observations`,
        fetchJson,
        openObservation: openParentObservation,
        isCurrent: () => parentIsCurrent(parent, generation),
      });
    }

    function getState() {
      return {
        open: Boolean(current) && !panel.hidden,
        resource: current?.entityClass || null,
        id: current?.id || null,
        editing: isEditing(),
        pending: Boolean(savingOperation),
        forbidden: Boolean(saveFailure?.forbidden),
        stale,
        conflict,
        baseVersion: current?.values?.version ?? null,
      };
    }

    function staleMessage() {
      return "Cette fiche a été modifiée ailleurs. Votre saisie est conservée.";
    }

    function markStale() {
      if (!current) return false;
      stale = true;
      status.textContent = staleMessage();
      return true;
    }

    function isCurrent(generation, entityClass, entityId) {
      return generation === requestSequence
        && current?.entityClass === entityClass
        && current.id === entityId
        && current.runtime === globalThis.BusinessRuntime && activeRuntime();
    }

    async function authoritativeDisorderEntities(payload) {
      const raw = payload?.type === "Feature" ? payload.properties : payload;
      if (!Array.isArray(raw?.troncon_ids)) {
        throw new Error("Rattachements patrimoniaux du désordre indisponibles.");
      }
      const tronconIds = [...new Set(raw.troncon_ids.map((identifier) => String(identifier)))];
      const troncons = await Promise.all(tronconIds.map(async (identifier) => ({
        identifier,
        item: await fetchJson(`/api/troncons/${encodeURIComponent(identifier)}`),
      })));
      const related = [];
      const seen = new Set();
      function add(entityClass, id, attributes) {
        if (id == null || String(id).trim() === "") return;
        const key = `${entityClass}:${id}`;
        if (seen.has(key)) return;
        seen.add(key);
        related.push({ class: entityClass, id: String(id), attributes });
      }
      troncons.forEach(({ identifier, item }) => {
        const properties = item?.type === "Feature" ? item.properties : item;
        if (!properties || typeof properties !== "object"
            || String(properties.id ?? "") !== identifier) {
          throw new Error("Rattachement patrimonial du désordre invalide.");
        }
        add("troncons", properties.id, {
          designation: properties.designation,
          libelle: properties.libelle,
        });
        add("digues", properties.digue_id, { libelle: properties.digue_libelle });
        add("systemes", properties.systeme_endiguement_id, {
          libelle: properties.systeme_endiguement_libelle,
        });
      });
      return related;
    }

    function resetSavingState() {
      savingOperation = null;
      setSavingControls(false);
    }

    function setSavingControls(disabled) {
      panel.setAttribute?.("aria-busy", String(disabled));
      if (formView) formView.sync({ mode: editing ? "edit" : "read", objectId: current?.id, pending: disabled, conflict,
        dirty: hasDraft(), error: saveFailure, forbidden: saveFailure?.forbidden,
        uncertain: saveFailure?.uncertain, retryUncertain: false });
      else {
        controls.forEach(control => { control.disabled = disabled; });
        saveButton.disabled = disabled;
      }
      edit.disabled = disabled;
      back.disabled = disabled;
      onStateChange({ ...getState(), pending: disabled });
    }

    function fieldDisplay(field, values) {
      const [key, _label, kind, _editable, _catalog, labelKey] = field;
      if (kind === "select") return text(values[labelKey]);
      if (kind === "checkbox") return values[key] ? "Oui" : "Non";
      if (kind === "tristate") {
        if (current?.entityClass === "zones_stockage")
          return values[key] ? "Valide" : "Qualification incomplète";
        return values[key] == null ? "Non renseigné" : values[key] ? "Oui" : "Non";
      }
      if (kind === "relations" || kind === "objects") return text(values[`${key}_labels`]);
      if (kind === "unified_troncons") return formatUnifiedTroncons(values[key]).join(", ") || "aucun";
      if (kind === "subobjects") return formatSubobjects(values, key);
      return text(values[key]);
    }

    function withRelationLabels(detail, choices) {
      const values = detail.values;
      const labels = (ids, rows) => (ids || []).map(id =>
        (rows || []).find(row => String(row.id) === String(id))?.label
          || (rows || []).find(row => String(row.id) === String(id))?.libelle
          || (rows || []).find(row => String(row.id) === String(id))?.designation
          || String(id)).join(" · ");
      if (choices.desordres) {
        values.desordre_ids_labels = labels(values.desordre_ids, choices.desordres);
      }
      if (detail.entityClass === "troncons") {
        for (const [key] of [...TronconForm.REFERENCES, ...TronconForm.PARENTS]) {
          if (values[key] == null || values[key] === "") continue;
          const rows = choices.references?.[key] || (key === "digue_id" ? choices.digues
            : key === "amenagement_hydraulique_id" ? choices.amenagements
            : key === "segment_hydrographique_support_id" ? choices.segments
            : ["troncon_support_id", "berge_support_id"].includes(key) ? choices.troncons : []);
          const row = (rows || []).find(row => String(row.id) === String(values[key]));
          const labelKey = key.replace(/_id$/, "_libelle");
          const provided = values[labelKey];
          values[labelKey] = provided && provided !== String(values[key]) ? provided
            : row?.label || row?.libelle || row?.designation || "Valeur non résolue";
        }
      } else if (detail.entityClass === "troncons_lit") {
        values.digue_ids_labels = labels(values.digue_ids, choices.digues);
      } else if (detail.entityClass === "elements_morphologiques_lit") {
        values.type_morphologie_libelle = LitForm.TYPES.find(([key]) => key === values.type_morphologie)?.[1] || values.type_morphologie;
        values.segment_libelle = labels(values.segment_id ? [values.segment_id] : [], choices.troncons_lit);
        values.type_zone_libelle = labels(values.type_zone_id ? [values.type_zone_id] : [], choices.zones);
      } else if (detail.entityClass === "cheminements") {
        values.troncon_ids_labels = labels(values.troncon_ids, choices.troncons);
      } else if (detail.entityClass === "vegetation") {
        values.troncon_ids_labels = labels(values.troncon_ids, choices.troncons);
        values.amenagement_hydraulique_ids_labels = labels(values.amenagement_hydraulique_ids, choices.amenagements);
        values.problematique_ids_labels = labels(values.problematique_ids, choices.references?.problematique_ids);
      } else if (detail.entityClass === "amenagements_hydrauliques") {
        values.troncon_ids_labels = labels(values.troncon_ids, choices.troncons);
        values.zone_ids_labels = labels(values.zone_ids, choices.zones);
      } else if (detail.entityClass === "zones_stockage") {
        values.amenagement_hydraulique_id_labels = labels(
          values.amenagement_hydraulique_id ? [values.amenagement_hydraulique_id] : [], choices.amenagements);
        values.collectivite_competente_id_labels = labels(
          values.collectivite_competente_id ? [values.collectivite_competente_id] : [], choices.organismes);
        values.reseau_hydraulique_ids_labels = labels(values.reseau_hydraulique_ids, choices.reseaux);
        for (const key of ["desordre_ids", "prestation_ids", "vegetation_ids", "gestionnaire_ids"]) {
          values[`${key}_labels`] = (values[key] || []).join(" · ");
        }
      } else if (detail.entityClass === "prestations") {
        values.troncon_ids_labels = labels(values.troncon_ids, choices.relations.troncons);
        values.intervenant_ids_labels = labels(values.intervenant_ids, choices.relations.contacts);
        values.object_ids_labels = PrestationForm.OBJECT_FAMILIES.map(([family, label]) => {
          const selected = labels(values.object_ids?.[family], choices.relations[family]);
          return selected ? `${label} : ${selected}` : "";
        }).filter(Boolean).join(" · ");
      }
      detail.choices = choices;
      return detail;
    }

    function section(titleText) {
      const container = documentTarget.createElement("section");
      const heading = documentTarget.createElement("h2");
      heading.textContent = titleText;
      container.append(heading);
      return container;
    }

    function renderSpatialRead(detail) {
      if (!Object.hasOwn(detail.feature, "geometry")) return;
      const container = documentTarget.createElement("section"); read.append(container);
      const generation = requestSequence;
      GeometryDraft.renderRead({ container, geometry: detail.feature.geometry,
        values: detail.values, request: fetchJson,
        isCurrent: () => current === detail && isCurrent(generation, detail.entityClass, detail.id) });
    }

    function renderRead() {
      saveFailure = null;
      removeFormView();
      litCharacteristics?.dispose(); litCharacteristics = null;
      relatedObjectsController?.clear();
      relatedObjectsController = null;
      RelationSelector.detachSearch?.(form.elements?.desordre_ids);
      editing = false;
      const config = ENTITY_CONFIGS[current.entityClass];
      title.textContent = `${config.label} : ${current.title}`;
      read.replaceChildren();
      const invalidityLabels = {
        MISSING_GEOMETRY: "Géométrie absente",
        INVALID_GEOMETRY: "Géométrie invalide",
        UNEXPECTED_GEOMETRY: "Type de géométrie incompatible",
        UNDETERMINED_CLASSIFICATION: "Classification à compléter",
      };
      const invalidity = Array.isArray(current.values.invalidity_causes)
        ? [...new Set(current.values.invalidity_causes)].map(code => invalidityLabels[code]).filter(Boolean) : [];
      if (invalidity.length) {
        const note = documentTarget.createElement("p");
        note.dataset.invalidityCauses = "";
        note.textContent = invalidity.join(" · ");
        read.append(note);
      }
      if (current.entityClass === "observations") {
        title.textContent = ObservationForm.title(current.values);
        const context = documentTarget.createElement("p");
        context.textContent = `Rattachée à : ${ObservationForm.parentText(current.values)}`;
        read.append(context);
      }
      if (["desordres", "troncons"].includes(current.entityClass)) {
        const parent = { resource: current.entityClass, id: current.id, designation: current.title };
        const create = documentTarget.createElement("button");
        create.type = "button";
        create.className = "data-observation-create entity-form__button";
        create.dataset.requires = "business.create";
        create.textContent = "+ Observation";
        create.addEventListener("click", () => globalThis.ObservationCreate.open(parent));
        read.append(create);
        const local = documentTarget.createElement("button");
        local.type = "button"; local.className = "entity-form__button"; local.textContent = "Consulter le périmètre local / observations";
        local.onclick = async () => {
          try { await globalThis.OfflineRuntime.showDetail(parent.resource, parent.id); }
          catch (failure) {
            if (current?.id === parent.id && current.entityClass === parent.resource) status.textContent = failure.message;
          }
        };
        read.append(local);
      }
      if (current.entityClass === "desordres" && globalThis.DisorderReports) {
        const identifier = current.id;
        const pdf = documentTarget.createElement("button");
        pdf.type = "button";
        pdf.className = "data-observation-create disorder-report-action";
        pdf.textContent = "Fiche PDF";
        const pdfStatus = documentTarget.createElement("p");
        pdfStatus.className = "field-help report-status";
        pdfStatus.setAttribute("role", "status");
        globalThis.DisorderReports.createDisorderPdfController({
          button: pdf,
          status: pdfStatus,
          getDisorderId: () => identifier,
          documentTarget,
        });
        read.append(pdf, pdfStatus);
      }
      const detailFields = current.entityClass === "observations" ? ObservationForm.fields(current.values)
        : config.formAdapter === TronconForm ? TronconForm.visibleFields(current.feature) : config.fields;
      const groups = config.formAdapter === TronconForm ? TronconForm.readSections(current.feature)
        .map(group => [group.title, group.fields, group.key]) : [
        ["Informations principales", detailFields.filter((field) => field[3])],
        [config.pilotProfile ? "Rattachement et localisation" : "Rattachement patrimonial",
          detailFields.filter((field) => !field[3])],
      ];
      groups.filter(([_heading, fields]) => fields.length).forEach(([heading, group, key]) => {
        const container = key === "diagnostics" ? documentTarget.createElement("details") : section(heading);
        if (key) container.dataset.tronconSection = key;
        if (key === "diagnostics") {
          container.className = "troncon-diagnostics";
          const summary = documentTarget.createElement("summary"); summary.textContent = heading; container.append(summary);
          group = [...group, ...[...TronconForm.REFERENCES, ...TronconForm.PARENTS]
            .filter(([name]) => current.values[name] != null && current.values[name] !== "")
            .map(([name, label]) => [name, `${label} — identifiant technique`, "text", false])];
        }
        if (key === "historical") {
          const note = documentTarget.createElement("p"); note.className = "entity-form__help";
          note.textContent = "Valeurs renseignées conservées, complémentaires au profil habituel de ce sous-type."; container.append(note);
        }
        const fields = documentTarget.createElement("dl");
        fields.className = "data-entity-detail-fields";
        group.forEach((field) => {
          const row = documentTarget.createElement("div");
          const term = documentTarget.createElement("dt");
          const value = documentTarget.createElement("dd");
          term.textContent = field[1];
          value.textContent = fieldDisplay(field, current.values);
          row.append(term, value);
          fields.append(row);
        });
        container.append(fields);
        read.append(container);
      });
      renderSpatialRead(current);
      if (["amenagements_hydrauliques", "zones_stockage"].includes(current.entityClass)) {
        const detail = current;
        const related = current.entityClass === "amenagements_hydrauliques"
          ? [...(current.values.zones_stockage || []).map(row => ["zones_stockage", row])]
          : current.values.amenagement_hydraulique_id
            ? [["amenagements_hydrauliques", {
              id: current.values.amenagement_hydraulique_id,
              designation: current.values.amenagement_hydraulique_libelle,
            }]] : [];
        if (related.length) {
          const container = section("Accès aux fiches liées");
          for (const [entityClass, row] of related) {
            const button = documentTarget.createElement("button");
            button.type = "button";
            button.className = "secondary";
            button.textContent = row.designation || row.libelle || row.id;
            button.addEventListener("click", () => {
              returnDetail = { entityClass: detail.entityClass, entityId: detail.id,
                source, entities: [...entities] };
              void open(entityClass, row.id, source, [], { preserveReturn: true });
            });
            container.append(button);
          }
          read.append(container);
        }
        if (current.entityClass === "amenagements_hydrauliques"
            && (current.values.troncons || []).length) {
          const container = section("Tronçons associés");
          for (const row of current.values.troncons) {
            const fiche = documentTarget.createElement("details");
            const summary = documentTarget.createElement("summary");
            summary.textContent = row.designation || row.id;
            const description = documentTarget.createElement("p");
            description.textContent = `${row.type_physique_libelle || row.type_physique_id}`
              + ` · UUID ${row.id} · Digue ${row.digue_id || "aucune"}`;
            const button=documentTarget.createElement("button"); button.type="button";button.textContent="Ouvrir le Tronçon";
            button.addEventListener("click",()=>void open("troncons",row.id,source));
            fiche.append(summary, description,button);
            container.append(fiche);
          }
          read.append(container);
        }
      }
      if(current.entityClass === "digues") {
        const container=section("Tronçons associés");
        for(const row of current.values.troncons || []) {
          const button=documentTarget.createElement("button");button.type="button";button.textContent=row.designation||row.libelle||row.id;
          button.addEventListener("click",()=>void open("troncons",row.id,source));container.append(button);
        } read.append(container);
      }
      if (current.entityClass === "desordres" && DisorderRelatedObjects) {
        const detail = current;
        const container = documentTarget.createElement("section");
        read.append(container);
        relatedObjectsController = DisorderRelatedObjects.createController({
          container,
          fetchJson,
          documentTarget,
          isRepresented: item => formView?.representsRelation(item) || false,
          isCurrent: (identifier) => current === detail && current.id === identifier
            && !panel.hidden && detailTab === "general",
          onNavigate: async (item) => {
            returnDetail = {
              entityClass: detail.entityClass,
              entityId: detail.id,
              source,
              entities: [...entities],
            };
            return open(item.resource, item.id, source, [], { preserveReturn: true });
          },
        });
        void relatedObjectsController.open(detail.id);
      }
      if (current.entityClass === "troncons_lit" && globalThis.LitCharacteristics) {
        const container = documentTarget.createElement("section");
        read.append(container);
        litCharacteristics = globalThis.LitCharacteristics.create({ container, fetchJson,
          getParent: () => current?.feature,
          isParentDirty: () => isEditing() || Boolean(savingOperation),
          setStatus: (message, failed = false) => { status.textContent = message; error.textContent = failed ? message : ""; },
          onChanged(feature) {
            const detail = current;
            current = normalizeDetail("troncons_lit", feature);
            current.runtime = detail.runtime;
            withRelationLabels(current, detail.choices);
            stale = false; conflict = false;
            // Le composant confirme et remet son sous-formulaire à zéro ; conserver
            // son instance durant son callback et actualiser ensuite les champs parent.
            title.textContent = `${config.label} : ${current.title}`;
          },
        });
        litCharacteristics.render(current.feature);
        const morphology = documentTarget.createElement("section");
        read.append(morphology);
        LitForm.renderMorphologies(morphology, current.feature, id =>
          open("elements_morphologiques_lit", id, source));
      }
      edit.textContent = "Modifier";
      status.textContent = stale ? staleMessage() : "";
      error.textContent = "";
      mountFormView(current,config,true);
      syncDetailNavigation();
    }

    async function startEditing() {
      if (!canUpdate() || !activeRuntime()) return;
      if (current?.entityClass === "desordres" && globalThis.OfflineRuntime?.isOffline()) {
        await globalThis.OfflineRuntime.showDetail("desordres", current.id);
        return;
      }
      if (current?.entityClass === "observations" && globalThis.OfflineRuntime?.isOffline()) {
        await globalThis.OfflineRuntime.showDetail("observations", current.id);
        return;
      }
      if (!current || !mayLeave()) return;
      litCharacteristics?.reset({ force: true });
      detailTab = "general";
      const detail = current;
      const generation = ++requestSequence;
        error.textContent = "";
      editFields.replaceChildren();
      try {
        const config = ENTITY_CONFIGS[detail.entityClass];
        const pilot = config.pilotProfile;
        if (pilot) {
          const adapter = config.formAdapter;
          const choices = detail.choices || await adapter.loadChoices(
            pilot, fetchJson, referenceData, detail.feature,
          );
          if (!isCurrent(generation, detail.entityClass, detail.id)) return;
          mountFormView(detail, config);
          form.dataset.litClass = config.formAdapter === LitForm ? detail.entityClass : "";
          adapter.fill(form, detail.feature, choices, referenceData, RelationSelector,
            detail.feature?.geometry?.type || "Point",
            Object.fromEntries([...form.querySelectorAll("[data-context]")].map(node=>[node.dataset.context,node])));
          controls = new Map(adapter.readFields(pilot).filter(field => field[3])
            .map(field => field[0])
            .map(key => [key, form.elements[key]]));
          if (detail.entityClass === "prestations") {
            PrestationForm.OBJECT_FAMILIES.forEach(([family]) =>
              controls.set(`objects_${family}`, form.elements[`objects_${family}`]));
          }
        } else {
          const fields = detail.entityClass === "observations" ? ObservationForm.fields(detail.values) : config.fields;
          const choices = await Promise.all(fields.filter(field => field[3] && field[2] === "select")
            .map(async field => [field[0], await referenceData.load(field[4], fetchJson)]));
          if (!isCurrent(generation, detail.entityClass, detail.id)) return;
          mountFormView(detail, config);
          controls = new Map(fields.filter(field => field[3]).map(([key]) => [key, form.elements[key]]));
          for (const [key, label, kind, editable, catalog, labelKey] of fields) {
            if (!editable) continue;
            const control = controls.get(key);
            if (kind === "select") referenceData.fillSelect(control, new Map(choices).get(key),
              detail.values[key], `Sans ${label.toLowerCase()}`, detail.values[labelKey]);
            else if (kind === "checkbox") control.checked = Boolean(detail.values[key]);
            else control.value = detail.values[key] ?? "";
          }
          if (detail.entityClass === "observations") form.elements.parent_label.value = ObservationForm.parentText(detail.values);
        }
      } catch (loadError) {
        if (!isCurrent(generation, detail.entityClass, detail.id)) return;
        error.textContent = `${loadError.message} Modification indisponible.`;
        return;
      }
      editing = true;
      initialControlValues = JSON.stringify([...controls].map(([key, control]) => [key, controlValue(control)]));
      edit.textContent = "Annuler";
      syncDetailNavigation();
      controls.values().next().value?.focus();
    }

    function cancelEditing() {
      if (!current || savingOperation) return;
      if (!mayLeave()) return;
      requestSequence += 1;
      resetSavingState();
      litIntent = null;
      renderRead();
      if (stale || conflict) reloadCurrent();
      edit.focus();
    }

    async function open(
      entityClass, entityId, origin = getSource(), rowEntities = [],
      { preserveReturn = false } = {},
    ) {
      const config = ENTITY_CONFIGS[entityClass];
      const identifier = String(entityId ?? "").trim();
      if (!config || !identifier || !mayLeave() || !activeRuntime()
          || (globalThis.BusinessRuntime && !globalThis.BusinessRuntime.can("business.read"))) return false;
      const runtime = globalThis.BusinessRuntime;
      removeFormView();
      litCharacteristics?.dispose(); litCharacteristics = null; litIntent = null;
      if (!preserveReturn) returnDetail = null;
      relatedObjectsController?.clear();
      relatedObjectsController = null;
      const requestId = ++requestSequence;
      resetSavingState();
      current = null;
      editing = false;
      detailTab = "general";
      stale = false;
      conflict = false;
      source = ["statistics", "views", "sql"].includes(origin)
        ? origin : "statistics";
      entities = Array.isArray(rowEntities) ? rowEntities : [];
      panel.hidden = false;
      read.hidden = true;
      form.hidden = true;
      edit.hidden = true;
      editActions.hidden = true;
      if (observationsSection) observationsSection.hidden = true;
      if (mapButton) mapButton.hidden = true;
      status.textContent = `Chargement de la fiche ${config.label.toLowerCase()}…`;
      onShow({ source, entityClass, entityId: identifier });
      try {
        const payload = await fetchJson(`${config.endpoint}/${encodeURIComponent(identifier)}`);
        if (requestId !== requestSequence || runtime !== globalThis.BusinessRuntime || !activeRuntime()) return false;
        current = normalizeDetail(entityClass, payload, entities);
        if (current.id !== identifier) throw new Error("Identifiant de fiche incohérent.");
        current.runtime = runtime;
        if (relatedClasses.has(entityClass)) {
          const choices = await config.formAdapter.loadChoices(
            config.pilotProfile, fetchJson, referenceData, current.feature,
          );
          if (requestId !== requestSequence || runtime !== globalThis.BusinessRuntime || !activeRuntime()) return false;
          withRelationLabels(current, choices);
        }
        stale = false;
        renderRead();
        onReady({ entityClass, entityId: identifier, entity: current, payload });
        back.focus();
      } catch (loadError) {
        if (requestId !== requestSequence || runtime !== globalThis.BusinessRuntime || !activeRuntime()) return false;
        current = null;
        status.textContent = loadError.message;
        back.focus();
      }
      return true;
    }

    function close() {
      if (!mayLeave()) return false;
      removeFormView();
      litCharacteristics?.dispose(); litCharacteristics = null; litIntent = null;
      requestSequence += 1;
      relatedObjectsController?.clear();
      relatedObjectsController = null;
      returnDetail = null;
      resetSavingState();
      panel.hidden = true;
      form.hidden = true;
      editActions.hidden = true;
      read.hidden = false;
      const previousSource = source;
      current = null;
      editing = false;
      detailTab = "general";
      stale = false;
      conflict = false;
      onClose({ source: previousSource });
      return true;
    }

    function isAffectedBy(mutation) {
      if (!current) return false;
      let candidateIds = [];
      if (observationParent()
          && ["observations", "photos"].includes(mutation?.resource)) return true;
      if ((current.entityClass === "amenagements_hydrauliques" && mutation?.resource === "zones-stockage")
          || (current.entityClass === "zones_stockage" && mutation?.resource === "amenagements-hydrauliques")) {
        return true;
      }
      if (mutation?.resource === (ENTITY_CONFIGS[current.entityClass].mutationResource || current.entityClass)) {
        candidateIds = [current.id];
      } else if ((current.entityClass === "troncons_lit" && mutation?.resource === "elements-morphologiques-lit")
          || (current.entityClass === "digues" && mutation?.resource === "troncons-lit")) {
        // Le reçu ne contient pas les anciens/nouveaux parents : relire la fiche
        // même lors du premier rattachement, absent de son état local.
        return true;
      } else if (current.entityClass === "troncons_lit" && mutation?.resource === "digues") {
        candidateIds = current.values.digue_ids || [];
      } else if (current.entityClass === "elements_morphologiques_lit" && mutation?.resource === "troncons-lit") {
        candidateIds = [current.values.segment_id];
      } else if (current.entityClass === "digues" && mutation?.resource === "systemes") {
        candidateIds = [current.values.systeme_endiguement_id];
      } else if (current.entityClass === "troncons" && mutation?.resource === "digues") {
        candidateIds = [current.values.digue_id];
      } else if (current.entityClass === "troncons" && mutation?.resource === "amenagements-hydrauliques") {
        candidateIds = [current.values.amenagement_hydraulique_id];
      } else if (current.entityClass === "troncons" && mutation?.resource === "systemes") {
        candidateIds = [current.values.systeme_endiguement_id];
      } else if (current.entityClass === "desordres"
          && ["systemes", "digues", "troncons"].includes(mutation?.resource)) {
        candidateIds = entities.filter((entity) => entity?.class === mutation.resource)
          .map((entity) => entity?.id);
      } else if (current.entityClass === "observations" && mutation?.resource === current.values.parent?.resource) {
        candidateIds = [current.values.parent.id];
      } else if (ENTITY_CONFIGS[current.entityClass]?.pilotProfile
          && mutation?.resource === "troncons") {
        candidateIds = [current.values.troncon_id, ...(current.values.troncon_ids || [])];
      } else if (ENTITY_CONFIGS[current.entityClass]?.pilotProfile
          && mutation?.resource === "desordres") {
        candidateIds = current.values.desordre_ids || [];
      } else if (ENTITY_CONFIGS[current.entityClass]?.pilotProfile
          && mutation?.resource === "amenagements-hydrauliques"
          && current.entityClass === "vegetation") {
        candidateIds = current.values.amenagement_hydraulique_ids || [];
      } else {
        return false;
      }
      if (mutation.ids === null) return true;
      if (!Array.isArray(mutation.ids)) return false;
      const identifiers = new Set(candidateIds.filter((id) => id != null).map(String));
      return mutation.ids.some((id) => identifiers.has(String(id)));
    }

    async function reloadCurrent({ propagateError = false } = {}) {
      if (!current) return false;
      if (isEditing() || hasDraft() || savingOperation || litCharacteristics?.hasDraft() || litCharacteristics?.isPending()) {
        markStale();
        return false;
      }
      const detail = current;
      const detailEntities = entities;
      const generation = ++requestSequence;
      const config = ENTITY_CONFIGS[detail.entityClass];
      status.textContent = `Actualisation de la fiche ${config.label.toLowerCase()}…`;
      error.textContent = "";
      try {
        const payload = await fetchJson(
          `${config.endpoint}/${encodeURIComponent(detail.id)}`,
        );
        if (!isCurrent(generation, detail.entityClass, detail.id)) return false;
        const refreshedEntities = detail.entityClass === "desordres"
          ? await authoritativeDisorderEntities(payload) : detailEntities;
        if (!isCurrent(generation, detail.entityClass, detail.id)) return false;
        const refreshed = normalizeDetail(detail.entityClass, payload, refreshedEntities);
        refreshed.runtime = detail.runtime;
        if (relatedClasses.has(detail.entityClass)) {
          const choices = await config.formAdapter.loadChoices(
            config.pilotProfile, fetchJson, referenceData, refreshed.feature,
          );
          if (!isCurrent(generation, detail.entityClass, detail.id)) return false;
          withRelationLabels(refreshed, choices);
        }
        // L'ouverture d'un sous-formulaire C ne change pas requestSequence.
        // Recontrôler après toutes les lectures avant de remplacer son parent.
        if (isEditing() || hasDraft() || savingOperation || litCharacteristics?.hasDraft() || litCharacteristics?.isPending()) {
          markStale();
          return false;
        }
        entities = refreshedEntities;
        current = refreshed;
        stale = false;
        conflict = false;
        renderRead();
        return true;
      } catch (loadError) {
        if (!isCurrent(generation, detail.entityClass, detail.id)) return false;
        stale = true;
        status.textContent = staleMessage();
        error.textContent = loadError.message;
        if (propagateError) throw loadError;
        return false;
      }
    }

    async function invalidate(mutation) {
      if (current?.entityClass === "desordres"
          && DisorderRelatedObjects?.MUTATION_RESOURCES?.has(mutation?.resource)) {
        const inverseRefresh = relatedObjectsController?.invalidate(mutation) || false;
        if (mutation.resource !== "troncons") return inverseRefresh;
      }
      if (!isAffectedBy(mutation)) return false;
      if (observationParent()
          && ["observations", "photos"].includes(mutation.resource)) {
        if (globalThis.ObservationDetail?.isOpenFor(observationParent())) return false;
        return detailTab === "observations" ? loadParentObservations() : false;
      }
      if (current.entityClass === "desordres" && mutation.resource !== "desordres") {
        markStale();
        return false;
      }
      if (isEditing() || savingOperation) {
        markStale();
        return false;
      }
      return reloadCurrent({ propagateError: true });
    }

    function controlValue(control) {
      if (control.type === "checkbox") return control.checked;
      if (control.type === "number") {
        return control.value === "" ? null : Number(control.value);
      }
      return control.value.trim() || null;
    }

    async function save(event) {
      event.preventDefault();
      if (!current || !canUpdate() || !activeRuntime() || hasExternalDraft()) return;
      if (saveFailure?.forbidden || (saveFailure?.uncertain && !ENTITY_CONFIGS[current.entityClass].pilotProfile?.idempotent)) return;
      const detail = current;
      const generation = requestSequence;
      const operation = { generation, entityClass: detail.entityClass, entityId: detail.id };
      if (savingOperation
          && isCurrent(
            savingOperation.generation,
            savingOperation.entityClass,
            savingOperation.entityId,
          )) return;
      const config = ENTITY_CONFIGS[detail.entityClass];
      if (config.pilotProfile && !form.reportValidity()) return;
      const operationSource = source;
      const operationEntities = entities;
      const values = Object.fromEntries(
        [...controls].map(([key, control]) => [key, controlValue(control)]),
      );
      let payload;
      try {
        payload = config.pilotProfile ? config.formAdapter.payload(form, config.pilotProfile,
          RelationSelector, detail.feature)
        : detail.entityClass === "observations" ? ObservationForm.changes(controls, detail.values)
        : config.partialUpdate
        ? Object.fromEntries(Object.entries(values).filter(
          ([key, value]) => value !== (detail.values[key] ?? null),
        )) : values;
      } catch (validationError) {
        error.textContent = validationError.message;
        return;
      }
      if ((config.partialUpdate && !Object.keys(payload).length)
          || (config.pilotProfile && !config.formAdapter.isDirty(form, detail.feature, RelationSelector))) {
        renderRead();
        return;
      }
      savingOperation = operation;
      setSavingControls(true);
      status.textContent = "Enregistrement…";
      error.textContent = "";
      let response;
      try {
        const queue = globalThis.OfflineOperationsRuntime;
        if (queue?.supported(detail.entityClass, payload)
            && (await globalThis.OfflineRuntime?.inspect())?.records.some(row => row.resource === detail.entityClass && row.id === detail.id)) {
          const result = await queue.submit({ resource: detail.entityClass, resourceId: detail.id,
            baseVersion: detail.values.version, changes: payload });
          if (!isCurrent(generation, detail.entityClass, detail.id)) return;
          resetSavingState();
          if (result.status === "synced") {
            renderRead(); await reloadCurrent();
          } else {
            conflict = result.status === "conflict"; stale = true;
            status.textContent = `Enregistré localement — ${globalThis.OfflineOperations.statusLabel(result.status)}. Intention conservée dans Outils → Consultation hors ligne.`;
          }
          return;
        }
        const headers = { "Content-Type": "application/json", ...Concurrency.headers(detail.values.version) };
        if (config.pilotProfile?.idempotent) {
          const signature = JSON.stringify([detail.id, detail.values.version, payload]);
          if (litIntent?.uncertain && litIntent.signature !== signature) throw new Error("Réessayez d’abord l’enregistrement non confirmé avec les mêmes valeurs.");
          if (!litIntent || litIntent.signature !== signature) litIntent = { signature, operationId: globalThis.crypto.randomUUID(), uncertain: false };
          headers["Operation-Id"] = litIntent.operationId;
        }
        response = await fetchJson(`${config.endpoint}/${encodeURIComponent(detail.id)}`, {
          method: "PUT",
          headers,
          body: JSON.stringify(payload),
        });
        if (config.pilotProfile) FormSession.validateReceipt(response, { id: detail.id, version: detail.values.version });
      } catch (saveError) {
        const failure = FormSession.failureState(saveError);
        if (config.pilotProfile?.idempotent && litIntent) litIntent.uncertain = failure.uncertain;
        if (!isCurrent(generation, detail.entityClass, detail.id)) return;
        if (Concurrency.isVersionConflict(saveError)) {
          conflict = true;
          stale = true;
        }
        if (config.pilotProfile?.composition === "entity-form"
            || ["ouvrages_particuliers", "troncons_lit", "elements_morphologiques_lit"].includes(detail.entityClass)) saveFailure = failure;
        status.textContent = saveFailure?.uncertain ? "Résultat non confirmé. Votre saisie est conservée."
          : conflict ? Concurrency.CONFLICT_MESSAGE : "";
        error.textContent = saveError.message;
        resetSavingState();
        return;
      }
      try {
        const savedId = response?.properties?.id ?? response?.id ?? detail.id;
        await onMutationConfirmed({
          resource: detail.entityClass,
          action: "update",
          ids: [String(savedId)],
          ...response.mutation,
        });
      } catch (publicationError) {
        // L'écriture est déjà confirmée : une instrumentation inter-pages
        // défaillante ne doit jamais être présentée comme un échec de sauvegarde.
        console.error("Publication de mutation impossible", publicationError);
      }
      if (!isCurrent(generation, detail.entityClass, detail.id)) return;
      let savedEntity = null;
      try {
        savedEntity = normalizeDetail(detail.entityClass, response, operationEntities);
        if (config.pilotProfile && savedEntity.id !== detail.id) throw new Error("Réponse de sauvegarde incohérente.");
        savedEntity.runtime = detail.runtime;
        if (relatedClasses.has(detail.entityClass)) {
          const savedChoices = form.elements?.desordre_ids
            ? { ...detail.choices,
              desordres: RelationSelector.selectedRows(form.elements.desordre_ids) }
            : detail.choices;
          withRelationLabels(savedEntity, savedChoices);
        }
        current = savedEntity;
        saveFailure = null;
        litIntent = null;
        stale = false;
        conflict = false;
        await onSaved({ entity: savedEntity, source: operationSource, payload: response });
        if (generation !== requestSequence || current !== savedEntity) return;
        renderRead();
        edit.focus();
      } catch (refreshError) {
        if (generation !== requestSequence || current !== savedEntity) return;
        renderRead();
        status.textContent = "Enregistré. L’actualisation locale est incomplète.";
        error.textContent = refreshError.message;
      } finally {
        if (savingOperation === operation) {
          savingOperation = null;
          if (generation === requestSequence
              && (savedEntity ? current === savedEntity
                : isCurrent(generation, detail.entityClass, detail.id))) {
            setSavingControls(false);
          }
        }
      }
    }

    eventTarget.addEventListener("digues-app:open-entity", (event) => {
      open(
        event.detail?.entityClass,
        event.detail?.entityId,
        event.detail?.source || getSource(),
        event.detail?.entities,
      );
    });

    manageReperage.addEventListener("click",()=>void (async()=>{
      if(current?.entityClass!=="troncons"||savingOperation||hasExternalDraft())return;
      if(hasDraft()){formView?.setStatus("Enregistrez ou annulez les modifications du Tronçon avant de gérer son repérage.");return;}
      if(onManageReperage){await onManageReperage();return;}
      const id=current.id,runtime=current.runtime;
      if(editing)cancelEditing();
      if(!await reloadCurrent()||current?.id!==id||current.runtime!==runtime||editing)return;
      globalThis.TronconReperage.open({id,designation:current.title,feature:current.feature,fetchJson,
        ownerLabels:Object.fromEntries([...(current.choices?.troncons||[]).map(row=>[row.id,row.libelle||row.designation||"Tronçon lié"]),[id,current.title]]),
        onMutation:async response=>{
          try{await onMutationConfirmed(response.mutation);}
          finally{if(current?.id===id&&current.runtime===runtime&&!editing&&current.values.version<response.version)await reloadCurrent();}
        },onReturn:()=>{if(current?.id===id)manageReperage.focus({preventScroll:true});}});
    })().catch(error=>formView?.setStatus(error.message,true)));

    back.addEventListener("click", () => {
      if (onBack) { onBack(); return; }
      if (!form.hidden) {
        cancelEditing();
        return;
      }
      if (returnDetail) {
        const target = returnDetail;
        returnDetail = null;
        void open(
          target.entityClass, target.entityId, target.source, target.entities,
          { preserveReturn: true },
        );
        return;
      }
      close();
    });
    edit.addEventListener("click", () => {
      if (form.hidden) {
        startEditing();
      } else {
        cancelEditing();
      }
    });
    form.addEventListener("submit", save);
    createObservation?.addEventListener("click", () => {
      const parent = observationParent();
      if (!parent || !canCreate() || detailTab !== "observations") return;
      globalThis.ObservationCreate?.open(parent, () => {
        if (current && detailTab === "observations") return loadParentObservations();
        return false;
      });
    });
    mapButton?.addEventListener("click", () => {
      if (!current || !ENTITY_CONFIGS[current.entityClass]?.pilotProfile || !activeRuntime() || !mayLeave()) return;
      globalThis.location.assign(globalThis.EntityNavigation.mapUrl(current.entityClass, current.id));
    });
    globalThis.addEventListener?.("beforeunload", (event) => {
      if (!hasDraft() && !savingOperation) return;
      event.preventDefault();
      event.returnValue = "";
    });
    globalThis.addEventListener?.("digues-app:context-closed", () => {
      const wasOpen = !panel.hidden;
      removeFormView();
      litCharacteristics?.dispose(); litCharacteristics = null; litIntent = null;
      requestSequence += 1;
      relatedObjectsController?.clear();
      relatedObjectsController = null;
      returnDetail = null;
      current = null;
      editing = false;
      detailTab = "general";
      savingOperation = null;
      saveFailure = null;
      panel.hidden = true;
      form.hidden = true;
      if (observationsSection) observationsSection.hidden = true;
      if (mapButton) mapButton.hidden = true;
      if (wasOpen) onClose({ source });
    });

    return { getFormView: () => formView,
      cancelEditing,
      close,
      getState,
      hasDraft,
      getSource: () => source,
      invalidate,
      isAffectedBy,
      markStale,
      open,
      reloadCurrent,
      startEditing,
    };
  }

  return { ENTITY_CONFIGS, createHost, createController, normalizeDetail,
    formatUnifiedTroncons, formatSubobjects };
});
