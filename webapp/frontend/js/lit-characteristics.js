/* Occurrences descriptives éditées dans la fiche de leur Tronçon de lit. */
(function (root, factory) {
  const common = typeof module === "object" && module.exports;
  const api = factory(common ? require("./reference-data.js") : root.ReferenceData,
    common ? require("./resource-concurrency.js") : root.ResourceConcurrency);
  if (common) module.exports = api;
  else root.LitCharacteristics = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (ReferenceData, Concurrency) {
  "use strict";

  const FAMILIES = Object.freeze({
    largeurs: Object.freeze({ label: "Largeur moyenne", collection: "largeurs_lit",
      fields: [{ name: "largeur_moyenne", label: "Largeur moyenne (m)", kind: "decimal" }] }),
    pentes: Object.freeze({ label: "Pente moyenne", collection: "pentes_lit",
      fields: [{ name: "pente_moyenne", label: "Pente moyenne (%)", kind: "decimal" }] }),
    domanialites: Object.freeze({ label: "Domanialité", collection: "domanialites_lit",
      fields: [{ name: "type_id", label: "Statut domanial", catalog: "domanialitesLit" }] }),
    "regimes-ecoulement": Object.freeze({ label: "Régime d'écoulement", collection: "regimes_ecoulement_lit",
      fields: [{ name: "regime_id", label: "Régime", catalog: "regimesEcoulementLit" }] }),
    "occupations-riveraines": Object.freeze({ label: "Occupation riveraine", collection: "occupations_riveraines_lit",
      fields: [{ name: "type_occupation_id", label: "Occupation", catalog: "occupationsRiverainesLit" },
        { name: "rive_id", label: "Rive", catalog: "rives" }] }),
  });
  const COMMON_FIELDS = Object.freeze([
    { name: "designation", label: "Désignation" },
    { name: "commentaire", label: "Commentaire", kind: "textarea" },
    { name: "date_debut", label: "Date de début", kind: "date" },
    { name: "date_fin", label: "Date de fin", kind: "date" },
    { name: "pr_debut", label: "PR de début", kind: "decimal" },
    { name: "pr_fin", label: "PR de fin", kind: "decimal" },
    { name: "valid", label: "Caractéristique valide", kind: "boolean" },
  ]);
  const RETAINED_FIELDS = Object.freeze([
    ["date_maj", "Date de mise à jour"], ["created_by", "Créateur"], ["edited_by", "Dernier éditeur"],
    ["systeme_rep_id", "Système de repérage"], ["borne_debut_id", "Borne de début"],
    ["borne_fin_id", "Borne de fin"], ["borne_debut_distance", "Distance à la borne de début"],
    ["borne_fin_distance", "Distance à la borne de fin"], ["borne_debut_aval", "Début en aval"],
    ["borne_fin_aval", "Fin en aval"], ["position_debut_ewkt", "Position de début"],
    ["position_fin_ewkt", "Position de fin"],
  ]);

  function family(name) {
    const spec = FAMILIES[name];
    if (!spec) throw new Error("Famille descriptive inconnue.");
    return spec;
  }
  function fields(name) { return [...family(name).fields, ...COMMON_FIELDS]; }
  function decimal(value, label) {
    if (value === null || value === undefined || value === "") return null;
    if (typeof value !== "string") throw new Error(`${label} doit être saisi comme un nombre décimal.`);
    const text = value.trim();
    if (!text) return null;
    if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(text)) {
      throw new Error(`${label} : utilisez un nombre décimal, avec un point si nécessaire.`);
    }
    // Aucun passage par Number : numeric PostgreSQL peut dépasser la précision
    // IEEE-754 du navigateur. Le serveur contrôle lui aussi la valeur finie.
    return text;
  }
  function normalizedValue(field, value) {
    if (field.kind === "boolean") return value === true;
    if (field.kind === "decimal") return decimal(value, field.label);
    const text = value == null ? "" : String(value).trim();
    return text || null;
  }
  function buildPayload(name, values, { original = null, id = null } = {}) {
    const result = {};
    for (const field of fields(name)) {
      const value = normalizedValue(field, values[field.name]);
      const previous = original == null ? undefined : normalizedValue(field,
        field.kind === "decimal" && original[field.name] != null
          ? String(original[field.name]) : original[field.name]);
      if (original == null || value !== previous) result[field.name] = value;
    }
    const start = result.date_debut === undefined ? original?.date_debut : result.date_debut;
    const end = result.date_fin === undefined ? original?.date_fin : result.date_fin;
    if (start && end && start > end) throw new Error("La date de fin précède la date de début.");
    if (original == null) {
      if (!id) throw new Error("Identité de la caractéristique indisponible.");
      result.id = id;
    }
    return result;
  }

  function create({ root = globalThis, container, fetchJson, getParent,
    onChanged = () => {}, onPending = () => {}, setStatus = () => {}, isParentDirty = () => false,
    canEdit = () => true, referenceData = ReferenceData,
    mutationController = root.ResourceMutationController }) {
    if (!container?.ownerDocument || !fetchJson || !getParent || !referenceData || !mutationController) {
      throw new Error("Section des caractéristiques du lit incomplète.");
    }
    const document = container.ownerDocument;
    let parent = null, editing = null, pending = false, generation = 0, disposed = false;
    let notifiedPending = false;
    let controls = {}, inputForm = null, fieldset = null, saveButton = null, formView = null;
    const lists = new Map(), addButtons = new Map();
    const can = permission => canEdit() && (!root.BusinessRuntime || root.BusinessRuntime.can(permission));
    const contextKey = () => root.BusinessRuntime?.key("lit-characteristics") || null;
    const current = token => !disposed && token === generation && editing
      && editing.context === contextKey() && (!root.BusinessRuntime || root.BusinessRuntime.isActive?.() !== false);
    const element = (tag, text, className) => {
      const node = document.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (className) node.className = className;
      return node;
    };
    const header = element("h3", "Caractéristiques descriptives");
    const notice = element("p", "Enregistrez le Tronçon de lit pour ajouter ses caractéristiques.", "field-help");
    const status = element("p", "", "field-help");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    const editor = element("div");
    editor.hidden = true;
    container.classList.add("lit-characteristics");
    container.replaceChildren(header, notice);
    for (const [name, spec] of Object.entries(FAMILIES)) {
      const section = element("section"), heading = element("h4", spec.label);
      section.dataset.family = name;
      const add = element("button", `Ajouter — ${spec.label}`);
      add.type = "button";
      add.dataset.action = "add-characteristic";
      add.addEventListener("click", () => void open(name));
      const list = element("ul");
      lists.set(name, list);
      addButtons.set(name, add);
      section.append(heading, add, list);
      container.append(section);
    }
    container.append(editor, status);

    function message(text, error = false) {
      status.textContent = text;
      status.dataset.error = String(error);
      setStatus(text, error);
    }
    function syncPending() {
      if (notifiedPending !== pending) { notifiedPending = pending; onPending(pending); }
      formView?.sync({ mode: editing?.original ? "edit" : "create", objectId: editing?.original?.id,
        pending, uncertain: Boolean(editing?.attempt?.uncertain), retryUncertain: true,
        conflict: Boolean(editing?.conflict), error: editing?.loadFailed ? new Error("Chargement impossible") : null });

      for (const button of addButtons.values()) button.disabled = pending || Boolean(editing) || !parent?.properties?.id;
    }
    function properties() { return parent?.properties || {}; }
    function rows(name) { return Array.isArray(properties()[family(name).collection]) ? properties()[family(name).collection] : []; }
    function rowLabel(name, row) {
      const parts = [row.designation];
      for (const field of family(name).fields) {
        if (row[field.name] != null) parts.push(`${field.label} : ${row[`${field.name}_libelle`] ?? row[field.name]}`);
      }
      if (row.pr_debut != null || row.pr_fin != null) parts.push(`PR ${row.pr_debut ?? "—"} → ${row.pr_fin ?? "—"}`);
      if (row.valid === false) parts.push("Non valide");
      return parts.filter(Boolean).join(" · ") || "Occurrence sans valeur renseignée";
    }
    function paintRows() {
      notice.hidden = Boolean(properties().id);
      for (const [name, list] of lists) {
        list.replaceChildren();
        for (const row of rows(name)) {
          const item = element("li"), label = element("span", rowLabel(name, row));
          item.dataset.id = String(row.id);
          const identity = element("small", `UUID : ${row.id}`, "field-help");
          item.append(label, identity);
          const details = element("details"), values = element("dl");
          details.append(element("summary", "Détails de la caractéristique"));
          for (const field of fields(name)) {
            const value = row[field.name];
            if (value == null) continue;
            values.append(element("dt", field.label), element("dd", typeof value === "boolean"
              ? (value ? "Oui" : "Non") : String(value)));
          }
          details.append(values);
          const retained = retainedDetails(row);
          if (retained) details.append(retained);
          item.append(details);
          if (can("business.update")) {
            const edit = element("button", "Modifier");
            edit.type = "button";
            edit.disabled = pending || Boolean(editing);
            edit.addEventListener("click", () => void open(name, row.id));
            item.append(edit);
          }
          list.append(item);
        }
        if (!list.children.length) list.append(element("li", "Aucune occurrence."));
        addButtons.get(name).hidden = !can("business.create");
      }
      syncPending();
    }
    function render(feature) {
      if (disposed) return false;
      if (editing) {
        if (String(feature?.properties?.id) !== editing.parentId
            || feature?.properties?.version !== editing.parentVersion) {
          message("Le Tronçon de lit a changé. Votre brouillon est conservé ; annulez puis relisez la fiche avant de réessayer.", true);
        }
        return false;
      }
      parent = feature;
      paintRows();
      return true;
    }
    function retainedDetails(row) {
      const details = element("details"), values = element("dl");
      details.append(element("summary", "Repérage et attribution conservés"));
      for (const [name, label] of RETAINED_FIELDS) {
        if (row?.[name] == null) continue;
        values.append(element("dt", label), element("dd", typeof row[name] === "boolean"
          ? (row[name] ? "Oui" : "Non") : String(row[name])));
      }
      if (!values.children.length) return null;
      details.append(values);
      return details;
    }
    async function open(name, id = null) {
      family(name);
      if (disposed || pending || editing || !can(id ? "business.update" : "business.create")) return false;
      if (isParentDirty()) { message("Enregistrez ou annulez les modifications du Tronçon avant de modifier ses caractéristiques.", true); return false; }
      const selected = getParent();
      if (!selected?.properties?.id || !Number.isSafeInteger(selected.properties.version)) return false;
      parent = selected;
      const original = id ? rows(name).find(row => String(row.id) === String(id)) : null;
      if (id && !original) { message("Caractéristique introuvable dans ce Tronçon de lit.", true); return false; }
      const token = ++generation;
      editing = { family: name, original, id: original?.id || root.crypto.randomUUID(),
        parentId: String(properties().id), parentVersion: properties().version, context: contextKey(), attempt: null };
      controls = {};
      const descriptor = { family: name,
        fields: { identification: [], classification: [], attachments: [
          { name: "parent_label", label: "Tronçon de lit", kind: "output", readOnly: true,
            value: properties().designation || properties().libelle || editing.parentId }], } };
      for (const field of fields(name)) {
        descriptor.fields[field.catalog ? "classification" : "identification"].push({ name: field.name,
          label: field.label, kind: field.catalog ? "select" : field.kind === "boolean" ? "checkbox"
            : field.kind === "textarea" ? "textarea" : field.kind === "date" ? "date" : "text",
          ...(field.kind === "decimal" ? { inputMode: "decimal" } : {}) });
      }
      formView = (root.EntityFormView || (typeof require === "function" && require("./entity-form-view.js")))
        .create({ root, document, descriptor, id: `lit-${name}-characteristic-${editing.id}`,
          onSubmit: save, onCancel: () => reset(), onClose: () => reset() });
      inputForm = formView.form; fieldset = formView.fieldset; saveButton = formView.save;
      inputForm.dataset.role = "lit-characteristic-form";
      const catalogs = [];
      for (const field of fields(name)) {
        const input = formView.controls.get(field.name);
        if (field.kind === "boolean") input.checked = original ? original[field.name] === true : true;
        else input.value = original?.[field.name] == null ? "" : String(original[field.name]);
        if (field.catalog) catalogs.push({ field, input });
        controls[field.name] = input;
      }
      const kept = retainedDetails(original);
      if (kept) formView.sections.get("attachments").append(kept);
      formView.setSectionNotice("identification", "Les PR saisis ne calculent pas de position.");
      editor.replaceChildren(formView.element);
      editor.hidden = false;
      pending = true;
      paintRows();
      message("Chargement de la caractéristique…");
      try {
        const loaded = await Promise.all(catalogs.map(({ field }) => referenceData.load(field.catalog, fetchJson)));
        if (!current(token)) return false;
        catalogs.forEach(({ field, input }, index) => referenceData.fillSelect(input, loaded[index], original?.[field.name], "Non renseigné", original?.[`${field.name}_libelle`]));
        message("");
        controls[fields(name)[0].name]?.focus?.();
        return true;
      } catch (error) {
        if (current(token)) { editing.loadFailed = true; message(error.message, true); }
        return false;
      } finally {
        if (current(token)) { pending = false; syncPending(); }
      }
    }
    async function save(event = { preventDefault() {} }) {
      event.preventDefault();
      if (!editing || pending || editing.loadFailed || disposed || !inputForm.reportValidity()
          || !can(editing.original ? "business.update" : "business.create")) return null;
      if (isParentDirty()) { message("Enregistrez ou annulez les modifications du Tronçon avant de modifier ses caractéristiques.", true); return null; }
      const token = generation, state = editing;
      let body;
      try {
        const values = Object.fromEntries(fields(state.family).map(field => [field.name,
          field.kind === "boolean" ? controls[field.name].checked : controls[field.name].value]));
        body = buildPayload(state.family, values, { original: state.original, id: state.id });
        if (!Object.keys(body).length) { message("Aucune modification à enregistrer."); return null; }
      } catch (error) { message(error.message, true); return null; }
      const serialized = JSON.stringify(body);
      if (!state.attempt || state.attempt.payload !== serialized) {
        if (state.attempt?.uncertain) { message("Réessayez d'abord l'enregistrement non confirmé avec les mêmes valeurs.", true); return null; }
        state.attempt = { payload: serialized, operationId: root.crypto.randomUUID(), uncertain: false };
      }
      pending = true;
      paintRows();
      message("Enregistrement de la caractéristique…");
      try {
        const origin = await mutationController.requestHeaders();
        if (!current(token)) return null;
        const path = `/api/troncons-lit/${encodeURIComponent(state.parentId)}/caracteristiques/${state.family}`
          + (state.original ? `/${encodeURIComponent(state.id)}` : "");
        const response = await (root.BusinessRuntime?.fetch || root.fetch)(path, {
          method: state.original ? "PUT" : "POST", body: state.attempt.payload,
          headers: { ...origin, "Content-Type": "application/json", ...Concurrency.headers(state.parentVersion),
            "Operation-Id": state.attempt.operationId },
        });
        let result;
        try { result = await response.json(); } catch (_error) { /* erreur de transport ou réponse illisible */ }
        if (!response.ok) throw Concurrency.httpError(response.status, result, "Caractéristique refusée.");
        if (String(result?.properties?.id) !== state.parentId || !Number.isSafeInteger(result.properties.version)
            || !Array.isArray(result.properties[family(state.family).collection])
            || !result.properties[family(state.family).collection].some(row => String(row.id) === String(state.id))) {
          throw new Error("Réponse de caractéristique incohérente. Relisez le Tronçon de lit.");
        }
        if (!current(token)) return null;
        pending = false;
        reset({ force: true });
        render(result);
        message("Caractéristique enregistrée.");
        let refreshFailed = false;
        try { await onChanged(result); } catch (_error) { refreshFailed = true; }
        try {
          if (!disposed && contextKey() === state.context) await mutationController.publishMutation(result.mutation, { applyLocally: true });
        } catch (_error) { refreshFailed = true; }
        if (refreshFailed && !disposed) message("Caractéristique enregistrée ; actualisation de la fiche incomplète.", true);
        return result;
      } catch (error) {
        if (current(token)) {
          state.attempt.uncertain = !Number.isInteger(error.status) || error.status >= 500;
          message(error.message, true);
        }
        return null;
      } finally {
        if (current(token)) { pending = false; paintRows(); }
      }
    }
    function reset({ force = false } = {}) {
      if (pending && editing?.attempt && !force) return false;
      generation += 1;
      editing = null;
      pending = false;
      controls = {};
      inputForm = null;
      fieldset = null;
      saveButton = null;
      editor.replaceChildren();
      editor.hidden = true;
      message("");
      paintRows();
      return true;
    }
    function dispose() {
      if (disposed) return;
      reset({ force: true });
      disposed = true;
      container.replaceChildren();
    }
    render(getParent());
    return { render, open, save, reset, dispose, hasDraft: () => Boolean(editing), isPending: () => pending };
  }
  return { create, FAMILIES, fields, decimal, buildPayload };
});
