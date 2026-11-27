(function (root, factory) {
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
  const dataViewBuilder = factory(mobilierForm, ouvrageForm, cheminementForm,
    vegetationForm, amenagementForm, zoneForm, prestationForm);
  if (typeof module === "object" && module.exports) module.exports = dataViewBuilder;
  root.DataViewBuilder = dataViewBuilder;
})(typeof globalThis !== "undefined" ? globalThis : this, function (MobilierForm, OuvrageForm,
    CheminementForm, VegetationForm, AmenagementForm, ZoneForm, PrestationForm) {
  "use strict";

  const MIN_BUILDER_HEIGHT = 88;
  const MIN_RESULT_HEIGHT = 120;
  const RESIZER_HEIGHT = 8;
  const PAGE_SIZE = 500;
  const SCROLL_LOAD_THRESHOLD = 160;
  const GEOGRAPHIC_CLASSES = new Set(["systemes", "troncons", "desordres", "photos"]);

  function clampBuilderHeight(
    proposedHeight,
    workspaceHeight,
    minBuilderHeight = MIN_BUILDER_HEIGHT,
    minResultHeight = MIN_RESULT_HEIGHT,
    resizerHeight = RESIZER_HEIGHT,
  ) {
    const available = Math.max(0, workspaceHeight - resizerHeight);
    const effectiveBuilderMinimum = Math.min(minBuilderHeight, available / 2);
    const effectiveResultMinimum = Math.min(
      minResultHeight,
      Math.max(0, available - effectiveBuilderMinimum),
    );
    const maximum = Math.max(
      effectiveBuilderMinimum,
      available - effectiveResultMinimum,
    );
    return Math.min(maximum, Math.max(effectiveBuilderMinimum, proposedHeight));
  }

  function normalizeCatalog(payload) {
    if (!Array.isArray(payload?.roots) || !payload.roots.length
      || !Array.isArray(payload?.tables)) {
      throw new Error("Catalogue de vues invalide.");
    }
    const roots = payload.roots.map((item) => ({
      id: String(item.id), label: String(item.label),
    }));
    const rootIds = new Set(roots.map((item) => item.id));
    const defaultRoot = String(payload.default_root || "");
    if (!rootIds.has(defaultRoot)) throw new Error("Catalogue de vues invalide.");
    const catalog = {
      defaultRoot,
      localOnly: payload.local_only === true,
      roots,
      relations: Array.isArray(payload.relations) ? payload.relations : [],
      tables: payload.tables.map((table) => ({
        id: String(table.id),
        label: String(table.label),
        compatibleRoots: Array.isArray(table.compatible_roots)
          ? table.compatible_roots.map(String) : [],
        fields: Array.isArray(table.fields) ? table.fields.map((field) => ({
          id: String(field.id),
          label: String(field.label),
          type: String(field.type || "text"),
          sourceClass: String(field.source_class || table.id),
          sourceField: String(field.source_field || field.id),
          navigable: Boolean(field.navigable),
        })) : [],
      })),
    };
    if (payload.default_view) {
      const preset = payload.default_view;
      if (!rootIds.has(preset.root) || !Array.isArray(preset.fields) || !preset.fields.length
          || new Set(preset.fields.map(item => `${item.table}.${item.field}`)).size !== preset.fields.length
          || !preset.fields.every(selection => {
            const table = catalog.tables.find(item => item.id === selection.table);
            return table && (!table.compatibleRoots.length || table.compatibleRoots.includes(preset.root))
              && table.fields.some(field => field.id === selection.field);
          })) throw new Error("Configuration initiale absente du catalogue de vues.");
      catalog.defaultView = { root: preset.root, fields: preset.fields.map(item => ({ ...item })) };
    }
    return catalog;
  }

  function tablesForRoot(catalog, root) {
    return (catalog.tables || []).filter((table) => (
      !table.compatibleRoots.length || table.compatibleRoots.includes(root)
    ));
  }

  function inferRoot(catalog, selectedFields) {
    const selectedTables = [
      ...new Set(selectedFields.map((selection) => selection.table)),
    ];

    if (!selectedTables.length) return "";

    const selectedTableIds = new Set(selectedTables);

    const roots = [...(catalog.roots || [])];
    // Prefer the catalogue's initial root only while it remains represented
    // and compatible; removing its fields restores the normal inference.
    roots.sort((a, b) => Number(b.id === catalog.defaultView?.root)
      - Number(a.id === catalog.defaultView?.root));
    const root = roots.find((rootDefinition) => (
      selectedTableIds.has(rootDefinition.id)
      && selectedTables.every((tableId) => {
        const table = (catalog.tables || []).find((item) => item.id === tableId);
        return table && (
          !table.compatibleRoots.length
          || table.compatibleRoots.includes(rootDefinition.id)
        );
      })
    ));

    return root?.id || "";
  }

  function addSelectedField(selectedFields, table, field) {
    const key = `${table}.${field}`;
    if (selectedFields.some((selection) => `${selection.table}.${selection.field}` === key)) {
      return [...selectedFields];
    }
    return [...selectedFields, { table, field }];
  }

  function removeSelectedField(selectedFields, index) {
    return selectedFields.filter((_selection, selectionIndex) => selectionIndex !== index);
  }

  function availableFields(catalog, table, selectedFields) {
    const selectedKeys = new Set(
      selectedFields.map((selection) => `${selection.table}.${selection.field}`),
    );
    return ((catalog.tables || []).find((item) => item.id === table)?.fields || []).filter((field) => (
      !selectedKeys.has(`${table}.${field.id}`)
    ));
  }

  function relationPath(relations, start, target) {
    if (!start || !target) return [];
    if (start === target) return [start];
    const neighbors = new Map();
    for (const relation of relations || []) {
      const parent = String(relation?.parent || "");
      const child = String(relation?.child || "");
      if (!parent || !child) continue;
      if (!neighbors.has(parent)) neighbors.set(parent, []);
      if (!neighbors.has(child)) neighbors.set(child, []);
      neighbors.get(parent).push(child);
      neighbors.get(child).push(parent);
    }
    const queue = [[start]];
    const visited = new Set([start]);
    while (queue.length) {
      const path = queue.shift();
      for (const neighbor of neighbors.get(path[path.length - 1]) || []) {
        if (visited.has(neighbor)) continue;
        const nextPath = [...path, neighbor];
        if (neighbor === target) return nextPath;
        visited.add(neighbor);
        queue.push(nextPath);
      }
    }
    return [];
  }

  function dependenciesForDefinition(definition, relations) {
    if (!definition?.root || !Array.isArray(definition.fields)
        || !definition.fields.length) return [];
    const dependencies = new Set([definition.root]);
    const pilot = [MobilierForm, OuvrageForm, CheminementForm, VegetationForm,
      AmenagementForm, ZoneForm, PrestationForm].map(form => form.PROFILES[definition.root]).find(Boolean);
    if (pilot) {
      // Le filtre Patrimoine et les libellés de rattachement dépendent aussi de ces parents.
      return [pilot.resource, ...(pilot.relatedResources || ["troncons", "digues", "systemes"])];
    }
    const targets = new Set([
      ...definition.fields.map((field) => field?.table).filter(Boolean),
      "troncons",
    ]);
    targets.forEach((target) => {
      const path = relationPath(relations, definition.root, target);
      if (path.length) path.forEach((resource) => dependencies.add(resource));
      else if (definition.fields.some((field) => field?.table === target)) {
        dependencies.add(target);
      }
    });
    return [...dependencies];
  }

  async function defaultFetchJson(url, options = {}) {
    await globalThis.ResourceEventTransport?.bootstrap;
    const response = await (globalThis.OfflineRuntime?.fetch || globalThis.BusinessRuntime?.fetch || fetch)(url, options);
    let payload = null;
    try { payload = await response.json(); } catch (_error) { /* HTTP fallback. */ }
    if (!response.ok) {
      const detail = payload?.detail;
      throw new Error(typeof detail === "string" ? detail : "Chargement impossible.");
    }
    return payload;
  }

  function createController({
    rootElement = document,
    fetchJson = defaultFetchJson,
    hiddenTronconIds = new Set(),
    onOpenEntity = () => {},
    onDatasetChange = () => {},
  } = {}) {
    const byId = (identifier) => rootElement.querySelector(`#${identifier}`);
    const workspace = byId("data-view-builder-workspace");
    const builder = rootElement.querySelector(".data-view-builder");
    const rootSelect = byId("data-view-root");
    const tableSelect = byId("data-view-table");
    const fieldSelect = byId("data-view-field");
    const addButton = byId("data-view-add-field");
    const fieldList = byId("data-view-field-list");
    const resizer = byId("data-view-resizer");
    const table = byId("data-view-result-table");
    const tableHead = byId("data-view-result-head");
    const tableBody = byId("data-view-result-body");
    const empty = byId("data-view-result-empty");
    const status = byId("data-view-result-status");
    const exportBar = byId("data-view-export-bar");
    const scrollContainer = rootElement.querySelector(".data-view-result-scroll");
    if ([workspace, builder, rootSelect, tableSelect, fieldSelect, addButton, fieldList,
      resizer, table, tableHead, tableBody, empty, status, exportBar,
      scrollContainer].some((item) => !item)) {
      throw new Error("Structure DOM du constructeur de vues incomplète.");
    }

    const documentTarget = rootElement.ownerDocument || document;
    hiddenTronconIds = new Set(hiddenTronconIds);
    let catalog = { defaultRoot: "", roots: [], relations: [], tables: [] };
    let catalogPromise = null;
    let selectedFields = [];
    let result = { columns: [], rows: [] };
    let requestSequence = 0;
    let loadingRequest = null;
    let nextOffset = 0;
    let loaded = false;
    let activeDefinition = null;
    let open = false;
    let initialized = false;
    let needsRefresh = false;
    let catalogStale = false;
    function currentRoot() {
      return inferRoot(catalog, selectedFields);
    }

    function fillFieldSelect() {
      fieldSelect.replaceChildren();
      availableFields(catalog, tableSelect.value, selectedFields).forEach((field) => {
        const option = documentTarget.createElement("option");
        option.value = field.id;
        option.textContent = field.label;
        fieldSelect.append(option);
      });
      addButton.disabled = fieldSelect.children.length === 0;
    }

    function fillTableSelect(preferredTable = tableSelect.value) {
      const root = currentRoot();
      rootSelect.value = root;

      const tables = root
        ? tablesForRoot(catalog, root)
        : (catalog.tables || []);

      tableSelect.replaceChildren();
      tables.forEach((tableDefinition) => {
        const option = documentTarget.createElement("option");
        option.value = tableDefinition.id;
        option.textContent = tableDefinition.label;
        tableSelect.append(option);
      });

      if (tables.some((table) => table.id === preferredTable)) {
        tableSelect.value = preferredTable;
      }

      tableSelect.disabled = tables.length === 0;
      fieldSelect.disabled = tables.length === 0;
      fillFieldSelect();
    }

    function renderSelectedFields() {
      fieldList.replaceChildren();
      selectedFields.forEach((selection, index) => {
        const tableDefinition = catalog.tables.find((item) => item.id === selection.table);
        const field = tableDefinition?.fields.find((item) => item.id === selection.field);
        const chip = documentTarget.createElement("span");
        chip.className = "data-view-field-chip";
        chip.textContent = `${tableDefinition?.label || selection.table} — ${field?.label || selection.field}`;
        const remove = documentTarget.createElement("button");
        remove.type = "button";
        remove.textContent = "×";
        remove.setAttribute("aria-label", `Retirer ${chip.textContent}`);
        remove.addEventListener("click", () => {
          selectedFields = removeSelectedField(selectedFields, index);
          renderSelectedFields();
          fillTableSelect();
          query();
        });
        chip.append(remove);
        fieldList.append(chip);
      });
    }

    function renderResult() {
      const rows = result.rows || [];
      tableHead.replaceChildren();
      (result.columns || []).forEach((column) => {
        const heading = documentTarget.createElement("th");
        heading.scope = "col";
        heading.textContent = column.label;
        if (column.navigable) heading.className = "data-entity-column";
        tableHead.append(heading);
      });
      tableBody.replaceChildren();
      rows.forEach((row) => {
        const tableRow = documentTarget.createElement("tr");
        (row.values || []).forEach((value, index) => {
          const cell = documentTarget.createElement("td");
          const displayValue = value == null || value === "" ? "—" : String(value);
          const column = result.columns?.[index];
          const matchingEntities = (row.entities || []).filter((entity) => (
            column?.navigable && entity?.class === column.source_class
          ));
          if (matchingEntities.length) {
            matchingEntities.forEach((entity, entityIndex) => {
              if (entityIndex) {
                const separator = documentTarget.createElement("span");
                separator.textContent = " · ";
                cell.append(separator);
              }
              const openButton = documentTarget.createElement("button");
              openButton.type = "button";
              openButton.className = "data-entity-link";
              openButton.dataset.entityClass = column.source_class;
              openButton.dataset.entityId = String(entity.id);
              const entityValue = entity.attributes?.[column.source_field];
              openButton.textContent = entityValue == null || entityValue === ""
                ? displayValue : String(entityValue);
              openButton.addEventListener("click", () => onOpenEntity(
                column.source_class, String(entity.id), row.entities,
              ));
              cell.append(openButton);
            });
          } else {
            cell.textContent = displayValue;
          }
          tableRow.append(cell);
        });
        tableBody.append(tableRow);
      });
      const hasFields = selectedFields.length > 0;
      const hasRows = rows.length > 0;
      table.hidden = !hasFields || !hasRows;
      empty.hidden = hasFields && hasRows;
      empty.textContent = hasFields
        ? "Aucune ligne dans la sélection Patrimoine actuelle."
        : "Ajoutez un champ pour construire la vue.";
      exportBar.hidden = !hasFields;
      const total = Number(result.total || 0);
      status.textContent = hasFields
        ? `${rows.length} ligne(s) chargée(s) sur ${total}` : "";
      const entityClasses = new Set(
        (result.columns || []).map((column) => column.source_class),
      );
      if (GEOGRAPHIC_CLASSES.has(result.granularity)) {
        entityClasses.add(result.granularity);
      }
      onDatasetChange({
        root: currentRoot(),
        fields: selectedFields,
        hiddenTronconIds: [...hiddenTronconIds],
        columns: result.columns || [],
        entityClasses: [...entityClasses],
      });
      return rows;
    }

    async function loadNextBatch(generation = requestSequence, propagateError = false) {
      if (!open) return null;
      if (generation !== requestSequence || !activeDefinition) return null;
      if (loadingRequest?.generation === generation) return loadingRequest.promise;
      if (loaded && !result.has_next) return null;
      const requestedOffset = nextOffset;
      status.textContent = result.rows.length
        ? `${result.rows.length} ligne(s) chargée(s) — chargement en cours…`
        : "Construction de la vue…";
      const promise = fetchJson("/api/data/view-query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          root: activeDefinition.root,
          fields: activeDefinition.fields,
          hidden_troncon_ids: [...activeDefinition.hiddenTronconIds],
          offset: requestedOffset,
          page_size: PAGE_SIZE,
        }),
      })
        .then((payload) => {
          if (generation !== requestSequence) return null;
          if (!Array.isArray(payload?.columns) || !Array.isArray(payload?.rows)) {
            throw new Error("Résultat de vue invalide.");
          }
          const accumulatedRows = [...result.rows, ...payload.rows];
          result = {
            ...payload,
            rows: accumulatedRows,
            has_next: Boolean(payload.has_next) && payload.rows.length > 0,
          };
          nextOffset = Number(payload.offset || requestedOffset) + payload.rows.length;
          loaded = true;
          return renderResult();
        })
        .catch((error) => {
          if (generation !== requestSequence) return null;
          needsRefresh = true;
          if (result.rows.length) {
            status.textContent = `${result.rows.length} ligne(s) chargée(s) — ${error.message}`;
          } else {
            result = { columns: [], rows: [] };
            table.hidden = true;
            empty.hidden = false;
            empty.textContent = error.message;
            status.textContent = "Vue indisponible";
            onDatasetChange({ fields: [], entityClasses: [] });
          }
          if (propagateError) throw error;
          return null;
        });
      loadingRequest = { generation, promise };
      await promise;
      if (loadingRequest?.generation === generation
          && loadingRequest.promise === promise) {
        loadingRequest = null;
      }
      return promise;
    }

    async function query(propagateError = false) {
      needsRefresh = true;
      if (!open || !initialized) return;
      needsRefresh = false;
      requestSequence += 1;
      const generation = requestSequence;
      result = { columns: [], rows: [] };
      nextOffset = 0;
      loaded = false;
      activeDefinition = null;
      loadingRequest = null;
      tableBody.replaceChildren();
      scrollContainer.scrollTop = 0;
      if (!selectedFields.length) {
        renderResult();
        return;
      }
      const root = currentRoot();
      if (!root) {
        result = { columns: [], rows: [] };
        table.hidden = true;
        empty.hidden = false;
        empty.textContent = "La sélection de tables est incompatible.";
        status.textContent = "Vue indisponible";
        onDatasetChange({ fields: [], entityClasses: [] });
        return;
      }
      activeDefinition = {
        root,
        fields: selectedFields.map((field) => ({ ...field })),
        hiddenTronconIds: [...hiddenTronconIds],
      };
      return loadNextBatch(generation, propagateError === true);
    }

    async function refresh() {
      if (!open || !initialized) { needsRefresh = true; return; }
      const rowCount = result.rows.length;
      const scrollTop = scrollContainer.scrollTop;
      await query(true);
      while (open && result.has_next && result.rows.length < rowCount) await loadNextBatch(requestSequence, true);
      scrollContainer.scrollTop = scrollTop;
    }

    function dependsOn(resource) {
      return dependenciesForDefinition({ root: currentRoot(), fields: selectedFields }, catalog.relations).includes(resource);
    }

    async function invalidate(mutation) {
      if (!dependsOn(mutation?.resource)) return false;
      await refresh();
      return true;
    }

    async function loadCatalog(force = false) {
      if (force) catalogPromise = null;
      if (!catalogPromise) {
        tableSelect.disabled = true;
        rootSelect.disabled = true;
        fieldSelect.disabled = true;
        addButton.disabled = true;
        catalogPromise = fetchJson("/api/data/view-catalog")
          .then((payload) => {
            catalog = normalizeCatalog(payload);
            catalogStale = false;
            rootSelect.replaceChildren();
            catalog.roots.forEach((rootDefinition) => {
              const option = documentTarget.createElement("option");
              option.value = rootDefinition.id;
              option.textContent = rootDefinition.label;
              rootSelect.append(option);
            });
            rootSelect.disabled = true;
            if (!initialized) {
              // Apply once, never restore an intentionally emptied selection.
              if (!selectedFields.length && catalog.defaultView) {
                selectedFields = catalog.defaultView.fields.map(field => ({ ...field }));
              }
              initialized = true;
              needsRefresh = true;
              renderSelectedFields();
            }
            fillTableSelect();
            renderSelectedFields();
            if (force) needsRefresh = true;
            if (open && needsRefresh) void query();
            return catalog;
          })
          .catch((error) => {
            catalogPromise = null;
            empty.textContent = error.message;
            status.textContent = "Catalogue indisponible";
            return { defaultRoot: "", roots: [], relations: [], tables: [] };
          });
      }
      return catalogPromise;
    }

    function setOpen(nextOpen) {
      if (open === nextOpen) return;
      open = nextOpen;
      workspace.hidden = !open;
      if (!open) {
        if (loadingRequest) needsRefresh = true;
        return;
      }
      if (!initialized || catalogStale) return loadCatalog(catalogStale);
      if (needsRefresh) return refresh();
    }

    function setHiddenTronconIds(nextHiddenTronconIds) {
      const next = new Set(nextHiddenTronconIds);
      if (next.size === hiddenTronconIds.size && [...next].every(id => hiddenTronconIds.has(id))) return;
      hiddenTronconIds = next;
      return query();
    }

    function setBuilderHeight(height) {
      const value = clampBuilderHeight(height, workspace.clientHeight);
      workspace.style.setProperty("--data-view-builder-height", `${value}px`);
      resizer.setAttribute("aria-valuenow", String(Math.round(value)));
      return value;
    }

    tableSelect.addEventListener("change", fillFieldSelect);
    addButton.addEventListener("click", () => {
      if (!tableSelect.value || !fieldSelect.value) return;
      selectedFields = addSelectedField(
        selectedFields, tableSelect.value, fieldSelect.value,
      );
      renderSelectedFields();
      fillTableSelect(tableSelect.value);
      query();
    });
    scrollContainer.addEventListener("scroll", () => {
      const remaining = scrollContainer.scrollHeight
        - scrollContainer.scrollTop - scrollContainer.clientHeight;
      if (remaining <= SCROLL_LOAD_THRESHOLD) loadNextBatch();
    });

    let drag = null;
    resizer.addEventListener("pointerdown", (event) => {
      drag = { startY: event.clientY, startHeight: builder.getBoundingClientRect().height };
      resizer.setPointerCapture?.(event.pointerId);
      event.preventDefault();
    });
    resizer.addEventListener("pointermove", (event) => {
      if (!drag) return;
      setBuilderHeight(drag.startHeight + event.clientY - drag.startY);
    });
    function stopResize(event) {
      if (!drag) return;
      drag = null;
      resizer.releasePointerCapture?.(event.pointerId);
    }
    resizer.addEventListener("pointerup", stopResize);
    resizer.addEventListener("pointercancel", stopResize);
    resizer.addEventListener("keydown", (event) => {
      if (!["ArrowUp", "ArrowDown"].includes(event.key)) return;
      const direction = event.key === "ArrowUp" ? -1 : 1;
      setBuilderHeight(builder.getBoundingClientRect().height + direction * 16);
      event.preventDefault();
    });

    globalThis.addEventListener?.("digues-app:backend-reconnected", () => {
      if (!catalog.localOnly) return;
      catalogStale = true;
      if (open) void loadCatalog(true);
    });

    renderResult();
    return {
      dependsOn,
      invalidate,
      loadCatalog,
      loadNextBatch,
      query,
      refresh,
      setBuilderHeight,
      setHiddenTronconIds,
      setOpen,
    };
  }

  return {
    addSelectedField,
    availableFields,
    clampBuilderHeight,
    createController,
    dependenciesForDefinition,
    inferRoot,
    normalizeCatalog,
    removeSelectedField,
    relationPath,
    tablesForRoot,
  };
});
