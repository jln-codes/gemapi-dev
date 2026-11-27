(function (root, factory) {
  const heritagePanel = factory();
  if (typeof module === "object" && module.exports) module.exports = heritagePanel;
  root.HeritagePanel = heritagePanel;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function descendantTronconIds(kind, item) {
    if (kind === "Tronçon") return item?.id == null ? [] : [String(item.id)];
    if (kind === "Digue") return (item?.troncons || []).map((value) => String(value.id));
    if (kind === "Système d'endiguement") {
      return (item?.digues || []).flatMap((digue) => (
        (digue.troncons || []).map((value) => String(value.id))
      ));
    }
    return [];
  }

  function normalizeIdArray(values) {
    if (!Array.isArray(values)) return null;
    const normalized = [];
    const seen = new Set();
    for (const value of values) {
      if (typeof value !== "string" || !value.trim()) return null;
      const identifier = value.trim();
      if (!seen.has(identifier)) {
        seen.add(identifier);
        normalized.push(identifier);
      }
    }
    return normalized;
  }

  function normalizeSelection(selection) {
    if (selection === null || typeof selection !== "object" || Array.isArray(selection)) {
      return null;
    }
    const systemeIds = normalizeIdArray(selection.systemeIds);
    const digueIds = normalizeIdArray(selection.digueIds);
    const tronconIds = normalizeIdArray(selection.tronconIds);
    if (!systemeIds || !digueIds || !tronconIds) return null;
    return { systemeIds, digueIds, tronconIds };
  }

  function selectionFromVisibility(data, hiddenTronconIds) {
    const systemeIds = [];
    const digueIds = [];
    const tronconIds = [];
    const isFullyVisible = (ids) => (
      ids.length > 0 && ids.every((id) => !hiddenTronconIds.has(id))
    );
    for (const systeme of data?.systemes || []) {
      const systemeTronconIds = [];
      for (const digue of systeme.digues || []) {
        const ids = descendantTronconIds("Digue", digue);
        systemeTronconIds.push(...ids);
        if (isFullyVisible(ids)) digueIds.push(String(digue.id));
        tronconIds.push(...ids.filter((id) => !hiddenTronconIds.has(id)));
      }
      if (isFullyVisible(systemeTronconIds)) systemeIds.push(String(systeme.id));
    }
    for(const row of [...(data?.amenagements_hydrauliques||[]).flatMap(a=>a.troncons||[]),...(data?.troncons_sans_parent||[]),...(data?.troncons_hors_se||[])])
      if(!hiddenTronconIds.has(String(row.id)))tronconIds.push(String(row.id));
    return { systemeIds, digueIds, tronconIds:[...new Set(tronconIds)] };
  }

  async function defaultFetchJson(url, options = {}) {
    await globalThis.ResourceEventTransport?.bootstrap;
    const response = await (globalThis.OfflineRuntime?.fetch || globalThis.BusinessRuntime?.fetch || fetch)(url, options);
    let payload = null;
    try { payload = await response.json(); } catch (_error) { /* HTTP fallback. */ }
    if (!response.ok) {
      const detail = payload?.detail;
      throw new Error(typeof detail === "string" ? detail : detail?.message || "Requête impossible.");
    }
    return payload;
  }

  function createController({
    rootElement = document,
    fetchJson = defaultFetchJson,
    getShowUuid = () => false,
    hiddenTronconIds = new Set(),
    canZoom = () => false,
    onZoom = () => {},
    onSelection = () => {},
    onVisibilityChange = () => {},
    onDataChange = () => {},
  } = {}) {
    const byId = (id) => rootElement.querySelector(`#${id}`);
    const panel = byId("heritage-panel");
    const toggleButton = byId("toggle-heritage");
    const closeButton = byId("close-heritage");
    const tree = byId("heritage-tree");
    const loading = byId("heritage-loading");
    const selectionSummary = byId("heritage-selection-summary");
    if ([panel, toggleButton, closeButton, tree, loading].some((element) => !element)) {
      throw new Error("Structure DOM du module Patrimoine incomplète.");
    }

    let data = { systemes: [] };
    let selected = null;
    let selectedButton = null;
    let selectSequence = 0;
    let nodeSequence = 0;
    const selectionHandlers = new WeakMap();
    const selectionViews = new WeakMap();
    let pendingSelection = null;
    let controls = [];
    let loaded = false;
    let loadingPromise = null;
    let loadSequence = 0;
    let selectionGeneration = 0;
    let closeTimer = null;
    const appRoot = panel.closest?.("main");
    function finishClose() {
      if (toggleButton.getAttribute("aria-expanded") === "false") panel.hidden = true;
      clearTimeout(closeTimer);
      closeTimer = null;
    }
    appRoot?.addEventListener("transitionend", (event) => {
      if (event.target === appRoot && event.propertyName === "--heritage-shift") finishClose();
    });
    const label = (item, fallback = "Sans libellé") => (
      item?.libelle || item?.designation || (getShowUuid() ? item?.id : null) || fallback
    );
    function updateSelectionSummary() {
      if (!selectionSummary) return;
      const troncons=allTroncons();
      const visible = troncons.filter((troncon) => !hiddenTronconIds.has(String(troncon.id)));
      const scope = selected ? `${selected.kind} consulté : ${label(selected.item)}. ` : "";
      const visibleLabels = visible.slice(0, 2).map((troncon) => label(troncon)).join(", ");
      const remainder = visible.length > 2 ? ` et ${visible.length - 2} autre(s)` : "";
      selectionSummary.textContent = `${scope}${visible.length} tronçon(s) visible(s) sur ${troncons.length}`
        + (visibleLabels ? ` : ${visibleLabels}${remainder}.` : ".");
    }
    function updateControls() {
      controls.forEach(({ input, zoomButton, tronconIds }) => {
        const active = tronconIds.filter((id) => !hiddenTronconIds.has(id)).length;
        input.disabled = tronconIds.length === 0;
        input.checked = tronconIds.length > 0 && active === tronconIds.length;
        input.indeterminate = active > 0 && active < tronconIds.length;
        input.setAttribute("aria-checked", input.indeterminate ? "mixed" : String(input.checked));
        zoomButton.disabled = !canZoom(tronconIds);
      });
      updateSelectionSummary();
    }
    function toggleVisibility(tronconIds) {
      if (!tronconIds.length) return;
      const allActive = tronconIds.every((id) => !hiddenTronconIds.has(id));
      tronconIds.forEach((id) => hiddenTronconIds[allActive ? "add" : "delete"](id));
      selectionGeneration += 1;
      updateControls();
      onVisibilityChange(hiddenTronconIds, getSelection(), { origin: "user" });
    }
    function zoomIcon() {
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "0 0 16 16");
      svg.setAttribute("aria-hidden", "true");
      ["M6 2H2v4", "M10 2h4v4", "M14 10v4h-4", "M6 14H2v-4"].forEach((pathData) => {
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", pathData);
        svg.append(path);
      });
      return svg;
    }
    function findSelectionButton(kind, identifier, parentId = null) {
      return Array.from(tree.querySelectorAll(".tree-name")).find(button =>
        button.dataset.objectKind === kind && button.dataset.objectId === String(identifier)
        && (!parentId || button.closest(".tree-node").dataset.pathKey.endsWith(`:${parentId}`)));
    }
    async function select(kind, item, parent, button, resource, consultation, notify = true) {
      const generation = ++selectSequence;
      let selection = { kind, item, parent, resource };
      if (notify) pendingSelection = generation;
      try {
        if (await onSelection(selection, { origin: notify ? "user" : "restore" }) === false) return false;
        if (generation !== selectSequence) return false;
        const currentButton = findSelectionButton(kind, item.id, parent?.id);
        if (!currentButton) return false;
        if (currentButton !== button) {
          button = currentButton;
          selection = selectionViews.get(button);
          consultation = button.closest(".tree-node").querySelector(":scope > .tree-consultation");
          if (await onSelection(selection, { origin: "restore" }) === false || generation !== selectSequence) return false;
        }
      } catch (error) {
        if (generation === selectSequence) loading.textContent = error.message;
        return false;
      } finally {
        if (pendingSelection === generation) pendingSelection = null;
      }
      selectedButton?.classList.remove("selected");
      selectedButton?.removeAttribute("aria-current");
      tree.querySelectorAll(".tree-consultation").forEach(detail => { detail.hidden = true; });
      selectedButton = button;
      selectedButton.classList.add("selected");
      selectedButton.setAttribute("aria-current", "true");
      selected = selection;
      if (consultation) consultation.hidden = false;
      selectionGeneration += 1;
      updateSelectionSummary();
      return true;
    }
    function createNode(kind, item, parent, children, level, { navigationOnly = false, resource = null, typeLabel = null } = {}) {
      resource ||= { "Système d'endiguement": "systemes", Digue: "digues", "Tronçon": "troncons" }[kind];
      const node = document.createElement("div");
      node.className = "tree-node";
      node.dataset.pathKey = `${kind}:${item.id}:${parent?.id || ""}`;
      const row = document.createElement("div");
      row.className = "tree-row";
      row.setAttribute("role", "treeitem");
      row.setAttribute("aria-level", String(level));
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "tree-toggle";
      toggle.setAttribute("aria-label", `Déplier ${label(item)}`);
      toggle.textContent = "▸";
      const name = document.createElement("button");
      name.type = "button";
      name.className = "tree-name";
      name.dataset.objectKind = kind;
      name.dataset.objectId = String(item.id);
      name.dataset.objectResource = resource;
      name.textContent = label(item);
      name.title = name.textContent;
      let consultation = null;
      if (navigationOnly) {
        const icon = document.createElement("span");
        icon.className = "tree-kind-icon";
        icon.setAttribute("aria-hidden", "true");
        icon.textContent = kind === "Aménagement hydraulique" ? "◈" : kind === "Zone de stockage" ? "▱" : "〰";
        name.prepend(icon);
        name.setAttribute("aria-label", `${kind} : ${label(item)}`);
        if (typeLabel) {
          const subtype = document.createElement("small");
          subtype.className = "tree-kind-label"; subtype.textContent = typeLabel;
          name.append(subtype);
        }
      }
      if(typeLabel && !navigationOnly) {const subtype=document.createElement("small");subtype.className="tree-kind-label";subtype.textContent=typeLabel;name.append(subtype);}
      selectionViews.set(name, { kind, item, parent, resource });
      selectionHandlers.set(name, notify => select(kind, item, parent, name, resource, consultation, notify));
      name.addEventListener("click", () => void selectionHandlers.get(name)(true));
      const tronconIds = descendantTronconIds(kind, item);
      const actions = document.createElement("div");
      actions.className = "tree-row-actions";
      const visibility = document.createElement("input");
      visibility.type = "checkbox";
      visibility.className = "tree-visibility";
      visibility.title = `Afficher ou masquer ${label(item)}`;
      visibility.setAttribute("aria-label", visibility.title);
      visibility.addEventListener("click", (event) => {
        event.stopPropagation();
        toggleVisibility(tronconIds);
      });
      const zoom = document.createElement("button");
      zoom.type = "button";
      zoom.className = "tree-zoom";
      zoom.title = `Cadrer sur ${label(item)}`;
      zoom.setAttribute("aria-label", zoom.title);
      zoom.append(zoomIcon());
      zoom.addEventListener("click", (event) => {
        event.stopPropagation();
        onZoom(kind, item, tronconIds);
      });
      actions.append(visibility, zoom);
      if (!navigationOnly) controls.push({ input: visibility, zoomButton: zoom, tronconIds });
      row.append(toggle, name);
      if (!navigationOnly) row.append(actions);
      node.append(row);
      if (consultation) node.append(consultation);
      if (!children.length) {
        toggle.classList.add("empty");
        toggle.disabled = true;
        return node;
      }
      const childContainer = document.createElement("div");
      childContainer.className = "tree-node-children";
      childContainer.setAttribute("role", "group");
      childContainer.hidden = true;
      children.forEach((child) => childContainer.append(child));
      node.append(childContainer);
      toggle.setAttribute("aria-expanded", "false");
      toggle.addEventListener("click", () => {
        const expanded = toggle.getAttribute("aria-expanded") === "true";
        toggle.setAttribute("aria-expanded", String(!expanded));
        toggle.textContent = expanded ? "▸" : "▾";
        toggle.setAttribute("aria-label", `${expanded ? "Déplier" : "Replier"} ${label(item)}`);
        childContainer.hidden = expanded;
      });
      return node;
    }
    function render(nextData) {
      const expanded = new Set([...tree.querySelectorAll('.tree-toggle[aria-expanded="true"]')]
        .map(toggle => toggle.closest(".tree-node").dataset.pathKey));
      data = nextData;
      loaded = true;
      controls = []; nodeSequence = 0;
      tree.replaceChildren();
      const branch = (title) => {
        const section = document.createElement("section");
        section.className = "heritage-branch"; section.setAttribute("role", "group");
        section.setAttribute("aria-label", title);
        const heading = document.createElement("h3"); heading.textContent = title;
        section.append(heading); tree.append(section); return section;
      };
      const systemsBranch = branch("Systèmes d'endiguement");
      data.systemes.forEach((systeme) => {
        const digues = systeme.digues.map((digue) => {
          const troncons = digue.troncons.map((troncon) => createNode("Tronçon", troncon, digue, [], 3,
            { typeLabel: troncon.type_physique_libelle || troncon.type_physique_id }));
          return createNode("Digue", digue, systeme, troncons, 2);
        });
        systemsBranch.append(createNode("Système d'endiguement", systeme, null, digues, 1));
      });
      const ahBranch = branch("Aménagements hydrauliques");
      for (const ah of data.amenagements_hydrauliques || []) {
        const children = [
          ...(ah.zones_stockage || []).map(zone => createNode("Zone de stockage", zone, ah, [], 2,
            { navigationOnly: true, resource: "zones_stockage", typeLabel: "Zone" })),
          ...(ah.troncons || []).map(troncon => createNode("Tronçon", troncon, ah, [], 2,
            { resource: "troncons", typeLabel: troncon.type_physique_libelle || troncon.type_physique_id })),
        ];
        ahBranch.append(createNode("Aménagement hydraulique", ah, null, children, 1,
          { navigationOnly: true, resource: "amenagements_hydrauliques" }));
      }
      const unbound=branch("Tronçons sans parent");
      for(const row of data.troncons_sans_parent || []) unbound.append(createNode("Tronçon",row,null,[],1,{typeLabel:row.type_physique_libelle}));
      if (data.troncons_hors_se?.length) {
        const prepared = branch("Autres tronçons préparés");
        for (const row of data.troncons_hors_se) prepared.append(createNode("Tronçon", row, null, [], 1,
          { typeLabel: row.type_physique_libelle || row.type_physique_id }));
      }
      if (!Array.isArray(data.amenagements_hydrauliques)) {
        const unavailable = document.createElement("p"); unavailable.className = "field-help";
        unavailable.textContent = "Navigation AH indisponible dans cet état local."; ahBranch.append(unavailable);
      }
      tree.querySelectorAll(".tree-node").forEach(node => {
        if (expanded.has(node.dataset.pathKey)) node.querySelector(':scope > .tree-row > .tree-toggle[aria-expanded="false"]')?.click();
      });
      if (selected) {
        selectedButton = findSelectionButton(selected.kind, selected.item.id, selected.parent?.id);
        selectedButton?.classList.add("selected"); selectedButton?.setAttribute("aria-current", "true");
      }
      loading.textContent = data.systemes.length
        ? `${data.systemes.length} système(s) chargé(s).`
        : "Aucun système d’endiguement disponible.";
      updateControls();
      onDataChange(data);
    }
    function allTroncons() {
      const rows=[...(data.systemes||[]).flatMap(s=>(s.digues||[]).flatMap(d=>d.troncons||[])),
        ...(data.amenagements_hydrauliques||[]).flatMap(a=>a.troncons||[]),...(data.troncons_sans_parent||[]),...(data.troncons_hors_se||[])];
      return [...new Map(rows.map(row=>[String(row.id),row])).values()];
    }
    function availableTronconIds() {return allTroncons().map(row=>String(row.id));}
    function requestHierarchy({ preserveSelection = false, propagateError = false } = {}) {
      const selectedTronconIds = preserveSelection
        ? new Set(getSelection().tronconIds) : null;
      const selectedIdentity = preserveSelection && selected
        ? { kind: selected.kind, id: String(selected.item.id), parentId: selected.parent?.id } : null;
      const selectionGenerationStart = selectionGeneration;
      const generation = ++loadSequence;
      loading.textContent = preserveSelection
        ? "Actualisation du patrimoine…" : "Chargement du patrimoine…";
      const request = fetchJson("/api/systemes-endiguement")
        .then((payload) => {
          if (!Array.isArray(payload.systemes)) throw new Error("Réponse hiérarchique invalide.");
          if (generation !== loadSequence) return false;
          const selectionChangedDuringReload = preserveSelection
            && selectionGeneration !== selectionGenerationStart;
          const selectionToRestore = selectionChangedDuringReload
            ? new Set(getSelection().tronconIds) : selectedTronconIds;
          const identityToRestore = selectionChangedDuringReload && selected
            ? { kind: selected.kind, id: String(selected.item.id), parentId: selected.parent?.id }
            : selectedIdentity;
          render(payload);
          if (selectionToRestore) {
            hiddenTronconIds.clear();
            availableTronconIds().forEach((id) => {
              if (!selectionToRestore.has(id)) hiddenTronconIds.add(id);
            });
            updateControls();
          }
          if (pendingSelection === null) {
            selected = null; selectedButton = null;
            if (identityToRestore) selectById(identityToRestore.kind, identityToRestore.id, identityToRestore.parentId, { notify: false });
          }
          updateSelectionSummary();
          return true;
        })
        .catch((error) => {
          if (generation !== loadSequence) return false;
          console.error("Chargement du patrimoine impossible", error);
          loading.textContent = error.message;
          if (propagateError) throw error;
          return false;
        })
        .finally(() => {
          if (generation === loadSequence && loadingPromise === request) {
            loadingPromise = null;
          }
        });
      loadingPromise = request;
      return request;
    }
    async function load() {
      if (loaded) return true;
      if (!loadingPromise) loadingPromise = requestHierarchy();
      return loadingPromise;
    }
    async function reload({ propagateError = false } = {}) {
      if (!loaded && !loadingPromise && !propagateError) return load();
      return requestHierarchy({ preserveSelection: loaded, propagateError });
    }
    function getSelection() {
      return selectionFromVisibility(data, hiddenTronconIds);
    }
    async function applySelection(selection, { origin = "remote" } = {}) {
      const normalized = normalizeSelection(selection);
      if (!normalized) return false;
      await load();
      if (!loaded) return false;

      const selectedTronconIds = new Set(normalized.tronconIds);
      const currentTronconIds = availableTronconIds();
      hiddenTronconIds.clear();
      currentTronconIds.forEach((id) => {
        if (!selectedTronconIds.has(id)) hiddenTronconIds.add(id);
      });
      selectionGeneration += 1;
      updateControls();
      onVisibilityChange(hiddenTronconIds, getSelection(), { origin });
      return true;
    }
    function setOpen(open) {
      clearTimeout(closeTimer);
      closeTimer = null;
      if (open) {
        panel.hidden = false;
        panel.inert = false;
        // Establish the offscreen position before TopNavigation changes the shared shift.
        panel.getBoundingClientRect();
      } else {
        panel.inert = true;
        if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) panel.hidden = true;
        else closeTimer = setTimeout(finishClose, 240);
      }
      toggleButton.setAttribute("aria-expanded", String(open));
      toggleButton.setAttribute("aria-label", `${open ? "Fermer" : "Ouvrir"} le patrimoine`);
      toggleButton.title = `${open ? "Fermer" : "Ouvrir"} le patrimoine`;
      if (open) load();
    }
    function selectById(kind, identifier, parentId = null, { notify = true } = {}) {
      const button = findSelectionButton(kind, identifier, parentId);
      return button ? selectionHandlers.get(button)(notify) : false;
    }
    tree.addEventListener("keydown", (event) => {
      const button = event.target.closest(".tree-name, .tree-toggle");
      if (!button) return;
      const node = button.closest(".tree-node");
      const name = node.querySelector(":scope > .tree-row > .tree-name");
      const toggle = node.querySelector(":scope > .tree-row > .tree-toggle");
      const names = [...tree.querySelectorAll(".tree-name")].filter(item => item.getClientRects().length);
      const index = names.indexOf(name);
      let target = null;
      if (event.key === "ArrowDown") target = names[Math.min(index + 1, names.length - 1)];
      else if (event.key === "ArrowUp") target = names[Math.max(index - 1, 0)];
      else if (event.key === "Home") target = names[0];
      else if (event.key === "End") target = names.at(-1);
      else if (event.key === "ArrowRight") {
        if (toggle.getAttribute("aria-expanded") === "false") toggle.click();
        else target = node.querySelector(".tree-node-children .tree-name");
      } else if (event.key === "ArrowLeft") {
        if (toggle.getAttribute("aria-expanded") === "true") toggle.click();
        else target = node.parentElement.closest(".tree-node")?.querySelector(":scope > .tree-row > .tree-name");
      } else return;
      event.preventDefault(); target?.focus();
    });
    return {
      closeButton,
      applySelection,
      get data() { return data; },
      get selected() { return selected; },
      getSelection,
      load,
      reload,
      panel,
      render,
      selectById,
      setOpen,
      toggleButton,
      updateControls,
    };
  }

  return {
    createController,
    descendantTronconIds,
    normalizeSelection,
    selectionFromVisibility,
  };
});
