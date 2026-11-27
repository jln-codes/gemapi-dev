/* Consultation/navigation Base. L'édition et le suivi gardent leurs propriétaires. */
(function (root, factory) {
  const common = typeof module === "object" && module.exports;
  const api = factory(root, common ? require("./base-detail-registry.js") : root.BaseDetailRegistry,
    common ? require("./base-detail-repository.js") : root.BaseDetailRepository,
    common ? require("./base-detail-view.js") : root.BaseDetailView);
  if (common) module.exports = api;
  root.BaseDetailController = api;
})(globalThis, function (root, Registry, Repository, View) {
  "use strict";
  function createController({ rootElement = root.document, eventTarget = rootElement.querySelector("main"),
    panel = rootElement.querySelector("#data-entity-detail"), fetchJson = Repository.fetchJson,
    getSource = () => "statistics", onShow = () => {}, onReady = () => {}, onClose = () => {},
    onMutationConfirmed = () => {}, createView = View.create, createEditor = options => root.BaseDetailEditor.create(options),
    repository = Repository.create({ request: fetchJson }) } = {}) {
    const document = panel.ownerDocument;
    let current = null, target = null, source = "statistics", generation = 0, capturedRuntime;
    let loading = false, editing = false, stale = false, originFocus = null, readScroll = 0;
    let currentRead = null;
    const stack = [];
    let reperageSummary = null, characteristics = null, inverse = null;
    const can = permission => !root.BusinessRuntime || root.BusinessRuntime.can(permission);
    const active = () => root.BusinessRuntime?.isActive?.() !== false;
    const live = (token = generation) => token === generation && capturedRuntime === root.BusinessRuntime && active() && !panel.hidden;
    const view = createView({ panel, document, onBack: back, onClose: close, onEdit: startEditing,
      onMap: showMap, onNavigate: navigate, onObservations: toggleObservations, onCreateObservation: createObservation });
    const editor = createEditor({ host: view.editorHost, request: fetchJson, onMutationConfirmed,
      contextActions: [view.back, view.map], onClose: close, onManageReperage: manageReperage,
      onReturn: returnFromEditor, onNavigate: (resource, id) => {
        editing = false; return navigate(resource, id);
      }, onStateChange: sync });
    function hasDraft() { return editor.hasDraft() || Boolean(characteristics?.hasDraft()); }
    function extensionPending() { return Boolean(characteristics?.isPending()); }
    function getState() {
      const editState = editing ? editor.getState() : {};
      return { open: !panel.hidden && Boolean(target), resource: target?.resource || null, id: target?.id || null,
        editing, loading, pending: Boolean(editState.pending || extensionPending()), forbidden: Boolean(editState.forbidden),
        conflict: Boolean(editState.conflict), stale: stale || Boolean(editState.stale), baseVersion: editState.baseVersion ?? current?.values.version ?? null };
    }
    function sync() {
      const state = getState();
      view.sync({ loading, editing, pending: state.pending || editing && editor.isBlocked(),
        canEdit: Boolean(current) && can("business.update"), canCreate: can("business.create"),
        canMap: canMap() });
    }
    function canMap() {
      if (!current?.definition.map) return false;
      try { root.EntityNavigation.target(current.resource, current.id); return true; }
      catch (_error) { return false; }
    }
    function allowLeave() {
      if(current?.resource==='troncons'&&root.TronconReperage?.isOpenFor(current.id)&&!root.TronconReperage.close())return false;
      if (!active() || extensionPending() || editing && editor.isBlocked()) return false;
      if (characteristics?.hasDraft() && !root.confirm("Abandonner les modifications de cette fiche ?")) return false;
      if (!editor.close()) return false;
      editing = false; return true;
    }
    function disposeExtensions() {
      reperageSummary?.dispose(); characteristics?.dispose(); inverse?.clear();
      reperageSummary = characteristics = inverse = null;
    }
    function snapshot() { return target && { ...target, scroll: view.scroll.hidden ? readScroll : view.scroll.scrollTop, followupOpen: !view.observationList.hidden }; }
    async function load(options = {}) {
      const operation = performLoad(options);
      currentRead = operation;
      try { return await operation; }
      finally { if (currentRead === operation) currentRead = null; }
    }
    async function performLoad({ scroll = 0, focus = true, propagateError = false, followupOpen = false } = {}) {
      const token = ++generation, selected = { ...target };
      const previous = current?.id === selected.id && current.resource === selected.resource ? current : null;
      loading = true; stale = false;
      disposeExtensions(); current = null;
      view.scroll.replaceChildren(view.grid, view.extensions, view.followup);
      view.grid.hidden = true; view.extensions.replaceChildren(); view.followup.hidden = true;
      view.title.textContent = Registry.get(selected.resource).label;
      view.status.textContent = `Chargement de la fiche ${Registry.get(selected.resource).label.toLocaleLowerCase("fr")}…`;
      view.error.textContent = ""; sync();
      try {
        const detail = await repository.read(selected.resource, selected.id);
        if (!live(token)) return false;
        current = detail; view.grid.hidden = false;
        view.render(detail); mountExtensions(detail, token); view.scroll.scrollTop = scroll;
        if (followupOpen && detail.definition.observations) {
          view.observationList.hidden = false; view.observationsToggle.setAttribute("aria-expanded", "true");
          void loadObservations();
        }
        view.status.textContent = "";
        onReady({ entityClass: detail.resource, entityId: detail.id, entity: detail, payload: detail.payload });
        if (focus) view.back.focus();
        return true;
      } catch (error) {
        if (!live(token)) return false;
        if (previous) {
          current = previous; stale = true; view.grid.hidden = false;
          view.render(previous); mountExtensions(previous, token); view.scroll.scrollTop = scroll;
        }
        view.error.textContent = error.message;
        view.status.textContent = previous ? "Actualisation impossible. Les dernières données consultées sont conservées." : "Fiche indisponible.";
        if (propagateError) throw error;
        return false;
      } finally { if (live(token)) { loading = false; sync(); } }
    }
    async function open(resource, id, origin = getSource(), _entities = [], { internal = false, restore = null, historyBack = false } = {}) {
      const identifier = String(id ?? "").trim();
      if (!Registry.definitions[resource] || !identifier || !can("business.read") || !allowLeave()) return false;
      if (panel.hidden) { originFocus = document.activeElement; source = ["statistics", "views", "sql"].includes(origin) ? origin : "statistics"; }
      else if (internal && target) stack.push(snapshot());
      if (historyBack) stack.pop();
      if (!internal && !restore) stack.length = 0;
      capturedRuntime = root.BusinessRuntime;
      target = { resource, id: identifier }; panel.hidden = false;
      onShow({ source, entityClass: resource, entityId: identifier });
      return load({ scroll: restore?.scroll || 0, followupOpen: restore?.followupOpen || false });
    }
    function navigate(resource, id) { return open(resource, id, source, [], { internal: true }); }
    function back() {
      if (!stack.length) return close();
      const previous = stack.at(-1);
      return open(previous.resource, previous.id, source, [], { restore: previous, historyBack: true });
    }
    function close({ force = false } = {}) {
      if (!force && !allowLeave()) return false;
      generation++; disposeExtensions(); stack.length = 0;
      current = target = null; loading = editing = stale = false; panel.hidden = true;
      onClose({ source });
      const previousFocus = originFocus; originFocus = null;
      queueMicrotask(() => { if (previousFocus?.isConnected && !previousFocus.closest?.("[hidden]")) previousFocus.focus({ preventScroll: true }); });
      return true;
    }
    async function startEditing() {
      if (!current || loading || !can("business.update") || !allowLeave()) return false;
      const detail = current, token = generation;
      readScroll = view.scroll.scrollTop; editing = true;
      // Editors must not run alongside mutable children in the read view.
      disposeExtensions(); sync();
      try {
        const opened = await editor.open(detail.resource, detail.id);
        if (!live(token)) { editor.close(); return false; }
        if (!opened) {
          view.error.textContent = "La modification n’a pas pu être ouverte.";
          // Keep a failed editor visible if it owns an active draft/uncertain result.
          if (!editor.hasDraft() && editor.close()) { editing = false; mountExtensions(detail, token); }
        }
        return opened;
      } catch (error) {
        if (live(token)) {
          view.error.textContent = error.message;
          if (!editor.hasDraft() && !editor.isBlocked() && editor.close()) { editing = false; mountExtensions(detail, token); }
        }
        return false;
      } finally { if (live(token)) sync(); }
    }
    async function returnFromEditor() {
      if (!target || !active()) return false;
      editing = false;
      const result = await load({ scroll: readScroll, focus: false });
      if (result) view.edit.focus(); return result;
    }
    function showMap() {
      if (!canMap() || !allowLeave()) return false;
      root.location.assign(root.EntityNavigation.mapUrl(current.resource, current.id)); return true;
    }
    async function manageReperage() {
      if(current?.resource!=='troncons'||loading||extensionPending())return false;
      if(hasDraft()||editing&&editor.isBlocked()){
        view.error.textContent='Enregistrez ou annulez les modifications du Tronçon avant de gérer son repérage.';return false;
      }
      const id=current.id,runtime=capturedRuntime;
      if(editing&&!allowLeave())return false;
      if(!await reloadCurrent()||!live()||current?.id!==id||capturedRuntime!==runtime||editing)return false;
      return root.TronconReperage.open({id,designation:current.title,feature:current.payload,fetchJson,ownerLabels:current.ownerLabels,
        onMutation:async response=>{
          try{await onMutationConfirmed(response.mutation);}
          finally{if(live()&&current?.id===id&&!editing&&current.values.version<response.version)await reloadCurrent();}
        },onReturn:()=>{if(live()&&current?.id===id)view.slots.get('geometry')?.querySelector('[data-role=manage-reperage]')?.focus({preventScroll:true});}});
    }
    function observationParent() {
      return current && { resource: current.definition.observations || current.resource, id: current.id, designation: current.title };
    }
    async function loadObservations() {
      if (!current?.definition.observations || !root.ObservationParentList || view.observationList.hidden) return false;
      const detail = current, token = generation, parent = observationParent();
      return root.ObservationParentList.load({ container: view.observationList, parent,
        path: `${detail.definition.endpoint}/${encodeURIComponent(detail.id)}/observations`, fetchJson,
        isCurrent: () => live(token) && current === detail && !editing && !view.observationList.hidden,
        openObservation: (id, context) => root.ObservationDetail?.open({ id, ...context, fetchJson }) || false });
    }
    function toggleObservations() {
      view.observationList.hidden = !view.observationList.hidden;
      view.observationsToggle.setAttribute("aria-expanded", String(!view.observationList.hidden));
      return loadObservations();
    }
    function createObservation() {
      if (!current || !can("business.create") || !live()) return false;
      if (!current.definition.observations && !["troncons", "desordres"].includes(current.resource)) return false;
      const token = generation;
      return root.ObservationCreate?.open(observationParent(), () => live(token) ? loadObservations() : false);
    }
    function mountExtensions(detail, token) {
      const isCurrent = () => live(token) && current === detail && !editing;
      if (Object.hasOwn(detail.payload, "geometry") && root.EntityGeometryDraft) {
        const geometry = view.slot("geometry-details", "Coordonnées et repérage détaillés", { collapsed: true, parent: view.slots.get("geometry") });
        let rendered = false;
        geometry.addEventListener("toggle", () => {
          if (!geometry.open || rendered || !isCurrent()) return;
          rendered = true; const content = view.node("div"); geometry.append(content);
          root.EntityGeometryDraft.renderRead({ container: content, geometry: detail.payload.geometry, values: detail.values, request: fetchJson, isCurrent });
        });
      }
      if (["troncons", "desordres"].includes(detail.resource)) {
        const local = view.button("Consulter le périmètre local / observations", async () => {
          try { await root.OfflineRuntime.showDetail(detail.resource, detail.id); }
          catch (error) { if (isCurrent()) view.error.textContent = error.message; }
        });
        view.followupActions.append(local);
      }
      if (detail.resource === "desordres" && root.DisorderReports) {
        const block = view.slot("reports", "Documents générés");
        const button = view.button("Fiche PDF", () => {}), status = view.node("p"); status.setAttribute("role", "status");
        root.DisorderReports.createDisorderPdfController({ button, status, getDisorderId: () => detail.id, documentTarget: document });
        block.append(button, status);
      }
      if (detail.resource === "desordres" && root.DisorderRelatedObjects) {
        const block = view.slot("associated-objects", "Autres objets associés");
        // This existing component owns its title as well as its list.
        block.replaceChildren();
        inverse = root.DisorderRelatedObjects.createController({ container: block, fetchJson, documentTarget: document,
          isCurrent, isRepresented: item => Object.values(detail.relations).some(relation => relation.items.some(row => row.resource === item.resource && row.id === item.id)),
          onNavigate: item => navigate(item.resource, item.id) });
        void inverse.open(detail.id);
      }
      if (detail.resource === "troncons" && root.TronconReperage) {
        const block=view.slots.get('geometry')||view.slot('geometry','Géométrie physique');
        const summary=view.node('p','base-detail__help');summary.dataset.role='troncon-reperage-summary';
        const manage=view.button('Gérer le repérage',manageReperage);manage.dataset.role='manage-reperage';
        block.append(summary,manage);
        reperageSummary=root.TronconReperage.createSummary({container:summary,feature:detail.payload,fetchJson,isCurrent});
        void reperageSummary.load();
      }
      if (detail.resource === "troncons_lit" && root.LitCharacteristics) {
        const block = view.slot("lit-characteristics", "Caractéristiques descriptives du lit");
        characteristics = root.LitCharacteristics.create({ container: block, fetchJson, getParent: () => current?.payload,
          canEdit: () => isCurrent() && !stale, isParentDirty: () => editing, onPending: sync,
          setStatus: (message, failed) => { view.status.textContent = message; if (failed) view.error.textContent = message; sync(); },
          onChanged(payload) { if (!isCurrent()) return; detail.payload = payload; detail.values = { ...detail.values, ...payload.properties }; view.updateProperties(detail); } });
        characteristics.render(detail.payload);
      }
    }
    function markStale() { stale = true; view.status.textContent = "Cette fiche a changé ailleurs. Votre saisie est conservée."; return true; }
    function isAffectedBy(mutation) {
      if (!mutation?.resource) return false;
      const resource = Object.keys(Registry.definitions).find(key => Registry.get(key).mutationResource === mutation.resource) || mutation.resource;
      if (!current) return Boolean(target && resource === target.resource
        && (mutation.ids === null || mutation.ids?.includes(target.id)));
      if (current.definition.observations && ["observations", "photos"].includes(resource)) return true;
      if (current.resource === "amenagements_hydrauliques" && resource === "zones_stockage"
          || current.resource === "zones_stockage" && resource === "amenagements_hydrauliques"
          || ["troncons_lit", "digues"].includes(current.resource) && resource === "elements_morphologiques_lit"
          || current.resource === "digues" && ["troncons", "troncons_lit"].includes(resource)) return true;
      const ids = [current.resource === resource ? current.id : null,
        ...Object.values(current.relations).flatMap(relation => relation.items.filter(item => item.resource === resource || item.canonicalResource === resource).map(item => item.id))].filter(Boolean);
      return ids.length > 0 && (mutation.ids === null || mutation.ids?.some(id => ids.includes(String(id))));
    }
    async function reloadCurrent({ propagateError = false } = {}) {
      if (!target) return false;
      if (editing || hasDraft() || extensionPending()) return markStale() && false;
      return load({ scroll: view.scroll.scrollTop, focus: false, propagateError, followupOpen: !view.observationList.hidden });
    }
    async function invalidate(mutation) {
      if (loading && currentRead) {
        const token = generation;
        const integrated = await currentRead;
        if (!live(token)) return false;
        if (!integrated) throw new Error("Actualisation de la fiche impossible.");
      }
      if (editing) { await editor.invalidate(mutation); if (isAffectedBy(mutation)) markStale(); return false; }
      if (current?.resource === "desordres" && root.DisorderRelatedObjects?.MUTATION_RESOURCES.has(mutation.resource)) await inverse?.invalidate(mutation);
      if (!isAffectedBy(mutation)) return false;
      if (!current) return reloadCurrent({ propagateError: true });
      if (hasDraft() || extensionPending()) return markStale() && false;
      if (current.definition.observations && ["observations", "photos"].includes(mutation.resource)) {
        if (root.ObservationDetail?.isOpenFor(observationParent()) || view.observationList.hidden) return false;
        const loaded = await loadObservations();
        if (!loaded && current) throw new Error("Actualisation des observations impossible.");
        return loaded;
      }
      return reloadCurrent({ propagateError: true });
    }
    eventTarget.addEventListener("digues-app:open-entity", event => {
      void open(event.detail?.entityClass, event.detail?.entityId, event.detail?.source || getSource(), event.detail?.entities);
    });
    root.addEventListener?.("beforeunload", event => { if (hasDraft() || extensionPending()) { event.preventDefault(); event.returnValue = ""; } });
    root.addEventListener?.("digues-app:context-closed", () => { if (!panel.hidden) close({ force: true }); });
    return { open, close, back, navigate, startEditing, reloadCurrent, invalidate, isAffectedBy, markStale, hasDraft, getState,
      getSource: () => source, getView: () => view };
  }
  return { createController };
});
