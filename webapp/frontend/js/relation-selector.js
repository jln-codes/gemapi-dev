(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.RelationSelector = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  let selectorSequence = 0;

  function identifiers(values = []) {
    return values.map(String);
  }

  function choices(
    rows,
    selectedValues = [],
    {
      id = (row) => row.id,
      include = () => true,
      preserveSelectedOutsideFilter = false,
    } = {},
  ) {
    const selected = new Set(identifiers(selectedValues));
    return rows.reduce((result, row) => {
      const value = String(id(row));
      const visible = Boolean(include(row));
      if (!visible && !(preserveSelectedOutsideFilter && selected.has(value))) {
        return result;
      }
      result.push({ row, value, selected: selected.has(value), outsideFilter: !visible });
      return result;
    }, []);
  }

  function fill(
    select,
    rows,
    selectedValues = [],
    {
      multiple = true,
      emptyLabel = "Aucune valeur",
      label = (row) => String(row.libelle || row.designation || row.id),
      outsideLabel = (value) => value,
      createOption = null,
      ...choiceOptions
    } = {},
  ) {
    const retainedRows = attached.get(select)?.state.rows() || [];
    const rowsById = new Map(rows.map(row => [String(row.id), row]));
    retainedRows.forEach(row => {
      const value = String(row.id);
      if (!rowsById.has(value)) rowsById.set(value, row);
    });
    rows = [...rowsById.values()];
    const ownerDocument = select.ownerDocument || globalThis.document;
    const optionFactory = createOption
      || (() => ownerDocument.createElement("option"));
    select.replaceChildren();
    select.multiple = Boolean(multiple);
    if (!multiple) {
      const empty = optionFactory();
      empty.value = "";
      empty.textContent = emptyLabel;
      select.append(empty);
    }
    const rendered = new Set();
    choices(rows, selectedValues, choiceOptions).forEach((choice) => {
      const option = optionFactory();
      option.value = choice.value;
      const baseLabel = label(choice.row);
      option.textContent = choice.outsideFilter ? outsideLabel(baseLabel, choice.row) : baseLabel;
      if (option.dataset) option.dataset.missing = String(Boolean(choice.row.missing));
      option.selected = choice.selected;
      select.append(option);
      rendered.add(choice.value);
    });
    identifiers(selectedValues).filter(value => !rendered.has(value)).forEach((value) => {
      const option = optionFactory();
      option.value = value;
      option.textContent = outsideLabel(value, null);
      if (option.dataset) option.dataset.missing = "true";
      option.selected = true;
      select.append(option);
    });
    return select;
  }

  function values(select) {
    const options = select.selectedOptions
      ? Array.from(select.selectedOptions)
      : Array.from(select.children || []).filter((option) => option.selected);
    return options.map((option) => String(option.value)).filter(Boolean);
  }

  function selectionState(initialRows = [], initialValues = []) {
    const initial = identifiers(initialValues);
    const selected = new Map(initial.map(value => [value, {
      id: value, label: value, missing: true,
    }]));
    function merge(rows = [], missingIds = []) {
      rows.forEach(row => {
        const value = String(row.id);
        if (selected.has(value)) selected.set(value, { ...row, id: value, missing: false });
      });
      identifiers(missingIds).forEach(value => {
        if (selected.has(value)) selected.set(value, {
          id: value, label: `Désordre introuvable — ${value}`, missing: true,
        });
      });
      return api();
    }
    function add(row) {
      const value = String(row.id);
      selected.set(value, { ...row, id: value, missing: false });
      return api();
    }
    function remove(value) {
      selected.delete(String(value));
      return api();
    }
    function api() {
      return {
        add, remove, merge,
        has: value => selected.has(String(value)),
        ids: () => [...selected.keys()],
        rows: () => [...selected.values()],
        isDirty: () => initial.join("\u0000") !== [...selected.keys()].join("\u0000"),
      };
    }
    merge(initialRows);
    return api();
  }

  async function loadSelected(
    fetchJson,
    selectedValues,
    { url = "/api/desordres/selection", batchSize = 100 } = {},
  ) {
    const ids = [...new Set(identifiers(selectedValues))];
    if (!ids.length) return { items: [], missing_ids: [] };
    const items = [];
    const missing = [];
    for (let offset = 0; offset < ids.length; offset += batchSize) {
      const parameters = new URLSearchParams();
      ids.slice(offset, offset + batchSize).forEach(id => parameters.append("ids", id));
      const response = await fetchJson(`${url}?${parameters}`);
      if (!Array.isArray(response?.items) || !Array.isArray(response?.missing_ids)) {
        throw new Error("Sélection de Désordres invalide.");
      }
      items.push(...response.items);
      missing.push(...response.missing_ids.map(String));
    }
    const byId = new Map(items.map(row => [String(row.id), row]));
    return {
      items: ids.map(id => byId.get(id)).filter(Boolean),
      missing_ids: ids.filter(id => missing.includes(id) || !byId.has(id)),
    };
  }

  async function loadDisorders(fetchJson, selectedValues = []) {
    const selectedIds = identifiers(selectedValues);
    const selected = await loadSelected(fetchJson, selectedIds);
    const missing = new Set((selected.missing_ids || []).map(String));
    return {
      desordres: [...selected.items, ...selectedIds.filter(id => missing.has(id))
        .map(id => ({ id, label: `Désordre introuvable — ${id}`, missing: true }))],
      fetchJson,
    };
  }

  function sameValues(left = [], right = []) {
    return identifiers(left).sort().join("\u0000") === identifiers(right).sort().join("\u0000");
  }

  function relationChanged(select, initialValues = []) {
    return !sameValues(values(select), initialValues);
  }

  function assignChanged(payload, key, select, initialValues = [], { force = false } = {}) {
    if (force || relationChanged(select, initialValues)) payload[key] = values(select);
    return payload;
  }

  const attached = new WeakMap();

  function detachSearch(select) {
    attached.get(select)?.destroy();
  }

  function selectedRows(select) {
    const controller = attached.get(select);
    if (controller) return controller.state.rows();
    return Array.from(select?.selectedOptions || []).map(option => ({
      id: String(option.value), label: option.textContent || String(option.value),
    }));
  }

  function attachSearch(
    select,
    {
      fetchJson,
      searchUrl = "/api/desordres/search",
      pageSize = 50,
      readOnly = false,
      searchLabel = "Rechercher un Désordre",
      selectedLabel = "Désordres associés",
      isCurrent = () => true,
    } = {},
  ) {
    if (!select || typeof fetchJson !== "function") {
      throw new Error("Sélecteur de relations incomplet.");
    }
    attached.get(select)?.destroy();
    const documentTarget = select.ownerDocument || globalThis.document;
    const initialRows = Array.from(select.children || []).map(option => ({
      id: String(option.value), label: option.textContent || String(option.value),
    }));
    const state = selectionState(initialRows, values(select));
    const container = documentTarget.createElement("section");
    container.className = "relation-search-selector";
    container.setAttribute("aria-busy", "false");
    if (readOnly) container.classList.add("relation-search-selector--readonly");
    const selectorId = `relation-search-${++selectorSequence}`;
    const selectedTitle = documentTarget.createElement("h3");
    selectedTitle.className = "relation-search-heading";
    selectedTitle.id = `${selectorId}-selected-title`;
    selectedTitle.textContent = selectedLabel;
    const selectedList = documentTarget.createElement("ul");
    selectedList.className = "relation-search-selected";
    selectedList.setAttribute("aria-labelledby", selectedTitle.id);
    selectedList.setAttribute("aria-live", "polite");
    const searchControls = documentTarget.createElement("div");
    searchControls.className = "relation-search-controls";
    const searchField = documentTarget.createElement("label");
    searchField.className = "relation-search-query";
    const searchFieldLabel = documentTarget.createElement("span");
    searchFieldLabel.textContent = searchLabel;
    const input = documentTarget.createElement("input");
    input.id = `${selectorId}-query`;
    input.type = "search";
    input.autocomplete = "off";
    input.maxLength = 200;
    input.placeholder = "Libellé, type, lieu ou UUID";
    searchField.append(searchFieldLabel, input);
    const searchButton = documentTarget.createElement("button");
    searchButton.type = "button";
    searchButton.textContent = "Rechercher";
    const resultsTitle = documentTarget.createElement("h3");
    resultsTitle.className = "relation-search-heading relation-search-results-heading";
    resultsTitle.id = `${selectorId}-results-title`;
    resultsTitle.textContent = "Résultats de la recherche";
    const results = documentTarget.createElement("ul");
    results.className = "relation-search-results";
    results.setAttribute("aria-labelledby", resultsTitle.id);
    const moreButton = documentTarget.createElement("button");
    moreButton.type = "button";
    moreButton.className = "secondary relation-search-more";
    moreButton.textContent = "Afficher la suite";
    moreButton.hidden = true;
    const status = documentTarget.createElement("p");
    status.className = "field-help relation-search-status";
    status.setAttribute("role", "status");
    const error = documentTarget.createElement("p");
    error.className = "field-help relation-search-error";
    error.setAttribute("role", "alert");
    error.hidden = true;
    searchControls.append(searchField, searchButton);
    container.append(selectedTitle, selectedList);
    if (readOnly) {
      const readOnlyNote = documentTarget.createElement("p");
      readOnlyNote.className = "field-help relation-search-readonly-note";
      readOnlyNote.textContent = "Lecture seule.";
      container.append(readOnlyNote);
    } else {
      container.append(searchControls, resultsTitle, results, moreButton, status, error);
    }
    select.hidden = true;
    select.setAttribute("aria-hidden", "true");
    select.insertAdjacentElement("afterend", container);

    let requestSequence = 0;
    let currentCursor = null;
    let resultRows = [];
    let requestController = null;
    let destroyed = false;
    let debounce = null;

    function rowLabel(row) {
      return String(row.label || row.libelle || row.designation || row.id);
    }
    function rowContext(row) {
      return [row.type_label, row.category_label, row.lieu_dit]
        .filter((value, index, all) => value && all.indexOf(value) === index)
        .join(" — ");
    }
    function dispatchChange() {
      const EventConstructor = documentTarget.defaultView?.Event || globalThis.Event;
      select.dispatchEvent(new EventConstructor("change", { bubbles: true }));
    }
    function syncSelect() {
      const optionFactory = () => documentTarget.createElement("option");
      fill(select, state.rows(), state.ids(), {
        multiple: true, createOption: optionFactory, label: rowLabel,
      });
      select.hidden = true;
      select.setAttribute("aria-hidden", "true");
    }
    function renderSelected() {
      selectedList.replaceChildren();
      if (!state.rows().length) {
        const empty = documentTarget.createElement("li");
        empty.className = "relation-search-empty";
        empty.textContent = "Aucun Désordre sélectionné.";
        selectedList.append(empty);
        return;
      }
      state.rows().forEach(row => {
        const item = documentTarget.createElement("li");
        const text = documentTarget.createElement("span");
        text.textContent = rowLabel(row);
        const context = rowContext(row);
        if (context) {
          const detail = documentTarget.createElement("small");
          detail.textContent = context;
          text.append(documentTarget.createElement("br"), detail);
        }
        item.append(text);
        if (!readOnly) {
          const remove = documentTarget.createElement("button");
          remove.type = "button";
          remove.className = "secondary";
          remove.textContent = "Retirer";
          remove.setAttribute("aria-label", `Retirer ${rowLabel(row)}`);
          remove.addEventListener("click", () => {
            if (select.disabled) return;
            state.remove(row.id);
            syncSelect();
            renderSelected();
            renderResults();
            dispatchChange();
            input.focus();
          });
          item.append(remove);
        }
        selectedList.append(item);
      });
    }
    function renderResults() {
      results.replaceChildren();
      if (!resultRows.length) {
        const empty = documentTarget.createElement("li");
        empty.className = "relation-search-empty";
        empty.textContent = "Aucun résultat.";
        results.append(empty);
        return;
      }
      resultRows.forEach(row => {
        const item = documentTarget.createElement("li");
        const button = documentTarget.createElement("button");
        button.type = "button";
        button.className = "relation-search-result";
        const context = rowContext(row);
        button.textContent = context ? `${rowLabel(row)} — ${context}` : rowLabel(row);
        button.disabled = select.disabled || state.has(row.id);
        button.setAttribute("aria-pressed", state.has(row.id) ? "true" : "false");
        button.addEventListener("click", () => {
          if (select.disabled || state.has(row.id)) return;
          state.add(row);
          syncSelect();
          renderSelected();
          renderResults();
          dispatchChange();
          input.focus();
        });
        item.append(button);
        results.append(item);
      });
    }
    function active(sequence) {
      return !destroyed && sequence === requestSequence && isCurrent()
        && (select.isConnected !== false);
    }
    async function search({ append = false } = {}) {
      if (readOnly || select.disabled || destroyed) return;
      const sequence = ++requestSequence;
      requestController?.abort();
      requestController = typeof AbortController === "function" ? new AbortController() : null;
      error.hidden = true;
      container.setAttribute("aria-busy", "true");
      status.textContent = append ? "Chargement de la suite…" : "Recherche en cours…";
      searchButton.disabled = true;
      moreButton.disabled = true;
      const parameters = new URLSearchParams({ q: input.value.trim(), limit: String(pageSize) });
      if (append && currentCursor) parameters.set("cursor", currentCursor);
      try {
        const response = await fetchJson(`${searchUrl}?${parameters}`, requestController
          ? { signal: requestController.signal } : {});
        if (!active(sequence)) return;
        if (!Array.isArray(response?.items) || typeof response?.has_more !== "boolean") {
          throw new Error("Résultats de recherche invalides.");
        }
        resultRows = append ? [...resultRows, ...response.items] : response.items;
        currentCursor = response.next_cursor || null;
        moreButton.hidden = !response.has_more;
        status.textContent = `${resultRows.length} résultat(s) affiché(s).`;
        renderResults();
      } catch (failure) {
        if (!active(sequence) || failure?.name === "AbortError") return;
        error.textContent = failure?.message || "Recherche de Désordres indisponible.";
        error.hidden = false;
        status.textContent = "";
      } finally {
        if (active(sequence)) {
          container.setAttribute("aria-busy", "false");
          searchButton.disabled = false;
          moreButton.disabled = false;
        }
      }
    }
    function setDisabled(disabled) {
      input.disabled = disabled;
      searchButton.disabled = disabled;
      moreButton.disabled = disabled;
      container.querySelectorAll("button").forEach(button => { button.disabled = disabled; });
      if (!disabled) renderResults();
    }
    const observer = typeof MutationObserver === "function"
      ? new MutationObserver(() => setDisabled(select.disabled)) : null;
    observer?.observe(select, { attributes: true, attributeFilter: ["disabled"] });
    function destroy() {
      if (destroyed) return;
      destroyed = true;
      requestSequence += 1;
      requestController?.abort();
      clearTimeout(debounce);
      observer?.disconnect();
      globalThis.removeEventListener?.("digues-app:context-closed", destroy);
      container.remove();
      select.hidden = false;
      select.removeAttribute("aria-hidden");
      attached.delete(select);
    }
    searchButton.addEventListener("click", () => search());
    moreButton.addEventListener("click", () => search({ append: true }));
    input.addEventListener("keydown", event => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      search();
    });
    input.addEventListener("input", () => {
      clearTimeout(debounce);
      debounce = setTimeout(() => search(), 300);
    });
    globalThis.addEventListener?.("digues-app:context-closed", destroy, { once: true });
    syncSelect();
    renderSelected();
    setDisabled(select.disabled || readOnly);
    if (!readOnly) void search();
    const controller = { destroy, search, state, element: container, setDisabled };
    attached.set(select, controller);
    return controller;
  }

  function fillDisorders(
    select,
    choices,
    selectedValues = [],
    options = {},
  ) {
    fill(select, choices?.desordres || [], selectedValues, {
      multiple: true,
      preserveSelectedOutsideFilter: true,
      label: row => row.label || row.libelle || row.designation || row.id,
      outsideLabel: label => label,
    });
    if (typeof choices?.fetchJson !== "function") return null;
    return attachSearch(select, {
      fetchJson: choices?.fetchJson,
      searchLabel: "Rechercher un Désordre à associer",
      selectedLabel: "Désordres associés",
      ...options,
    });
  }

  return {
    choices, fill, values, selectionState, loadSelected, loadDisorders,
    sameValues, relationChanged, assignChanged,
    attachSearch, fillDisorders, detachSearch, selectedRows,
  };
});
