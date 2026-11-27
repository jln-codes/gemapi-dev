/* Présentation de consultation Base ; aucun contrôle de formulaire métier. */
(function (root, factory) {
  const common = typeof module === "object" && module.exports;
  const api = factory(common ? require("./base-detail-registry.js") : root.BaseDetailRegistry,
    common ? require("./base-detail-repository.js") : root.BaseDetailRepository);
  if (common) module.exports = api;
  root.BaseDetailView = api;
})(globalThis, function (Registry, Repository) {
  "use strict";
  const states = { notLoaded: "Non chargé", unset: "Non renseigné", empty: "Vide", unresolved: "Valeur non résolue", error: "Indisponible" };
  function format(spec, result) {
    if (result.state !== "value") return states[result.state] || "Non renseigné";
    let value = result.value;
    if (spec.format === "boolean") value = value ? "Oui" : "Non";
    else if (spec.format === "date" && /^\d{4}-\d{2}-\d{2}$/.test(String(value))) value = String(value).split("-").reverse().join("/");
    else if (spec.format === "number" && /^[+-]?\d+(\.\d+)?$/.test(String(value))) {
      // Format decimal strings without an IEEE-754 round trip (PR/numeric).
      const [integer, decimal] = String(value).split(".");
      value = integer.replace(/\B(?=(\d{3})+(?!\d))/g, "\u202f") + (decimal === undefined ? "" : `,${decimal}`);
    } else if (Array.isArray(value)) value = value.join(" · ");
    return `${value}${spec.unit ? ` ${spec.unit}` : ""}`;
  }
  function create({ panel, document: doc = panel.ownerDocument, onBack, onClose, onEdit, onMap, onNavigate, onObservations, onCreateObservation }) {
    const node = (tag, className, text) => {
      const element = doc.createElement(tag);
      if (className) element.className = className;
      if (text !== undefined) element.textContent = text;
      return element;
    };
    const button = (label, action, id) => {
      const element = node("button", "base-detail__button", label);
      element.type = "button"; if (id) element.id = id;
      element.addEventListener("click", () => void action()); return element;
    };
    panel.classList.add("base-detail");
    const header = node("header", "base-detail__header");
    const identity = node("div", "base-detail__identity");
    const title = node("h1"); title.id = "data-entity-detail-title";
    const subtitle = node("p", "base-detail__subtitle"); identity.append(title, subtitle);
    const actions = node("nav", "base-detail__actions"); actions.setAttribute("aria-label", "Actions de la fiche");
    const back = button("Retour", onBack, "data-entity-detail-back");
    const map = button("Voir sur Carte", onMap, "data-entity-detail-map");
    const edit = button("Modifier", onEdit, "data-entity-detail-edit");
    edit.dataset.requires = "business.update";
    const close = button("×", onClose, "base-detail-close"); close.setAttribute("aria-label", "Fermer la fiche");
    actions.append(back, map, edit, close); header.append(identity, actions);
    const status = node("p", "base-detail__status"); status.id = "data-entity-detail-status";
    status.setAttribute("role", "status");
    const error = node("p", "base-detail__error"); error.id = "data-entity-detail-error"; error.setAttribute("role", "alert");
    const scroll = node("div", "base-detail__scroll"); scroll.id = "data-entity-detail-read";
    const grid = node("div", "base-detail__grid");
    const primary = node("div", "base-detail__column"), context = node("div", "base-detail__column");
    grid.append(primary, context);
    const extensions = node("div", "base-detail__extensions");
    const followup = node("section", "base-detail__followup"); followup.dataset.section = "followup";
    const followupTitle = node("h2", "base-detail__section-title", "Suivi");
    const followupActions = node("div", "base-detail__followup-actions");
    const observationsToggle = button("Observations", onObservations, "base-detail-observations-toggle");
    observationsToggle.setAttribute("aria-expanded", "false"); observationsToggle.setAttribute("aria-controls", "data-entity-detail-observation-list");
    const createObservation = button("+ Observation", onCreateObservation, "data-entity-detail-create-observation");
    createObservation.dataset.requires = "business.create";
    const observationList = node("div"); observationList.id = "data-entity-detail-observation-list"; observationList.hidden = true;
    followupActions.append(observationsToggle, createObservation); followup.append(followupTitle, followupActions, observationList);
    scroll.append(grid, extensions, followup);
    const editorHost = node("div", "base-detail__editor-host"); editorHost.hidden = true;
    panel.replaceChildren(header, status, error, scroll, editorHost);
    const slots = new Map(), properties = new Map();
    let contextualSubtitle = "Consultation";
    function section(key, titleText, parent = extensions, collapsed = false) {
      const element = node(collapsed ? "details" : "section", "base-detail__section");
      element.dataset.section = key; element.dataset.tronconSection = key === "technical" ? "diagnostics" : key;
      element.append(node(collapsed ? "summary" : "h2", "base-detail__section-title", titleText));
      parent.append(element); return element;
    }
    function renderValue(output, detail, spec) {
      output.replaceChildren();
      const result = Repository.value(detail, spec); output.dataset.valueState = result.state;
      if (spec.format !== "relations") { output.textContent = format(spec, result); return; }
      if (!result.items.length) { output.textContent = result.state === "empty" ? "Aucun rattachement" : states[result.state]; return; }
      const list = node("ul", "base-detail__relations");
      for (const item of result.items) {
        const entry = node("li");
        const supported = Boolean(Registry.definitions[item.canonicalResource || item.resource]);
        const name = supported ? button(item.label, () => onNavigate(item.canonicalResource || item.resource, item.id)) : node("span", null, item.label);
        name.dataset.relationResource = item.resource || ""; name.dataset.relationId = item.id;
        entry.append(name);
        if (item.qualification) entry.append(node("small", "base-detail__qualification", item.qualification));
        entry.append(node("code", "technical-identifier", item.id));
        list.append(entry);
      }
      output.append(list);
    }
    function updateProperties(detail) {
      for (const { output, spec } of properties.values()) renderValue(output, detail, spec);
      title.textContent = `${detail.definition.label} — ${detail.title}`;
      contextualSubtitle = detail.subtitle; subtitle.textContent = contextualSubtitle;
    }
    function render(detail) {
      primary.replaceChildren(); context.replaceChildren(); extensions.replaceChildren();
      slots.clear(); properties.clear();
      const fields = Registry.fields(detail.resource, detail.values);
      for (const group of detail.definition.sections) {
        const entries = fields.filter(spec => spec.section === group.key);
        if (!entries.length) continue;
        const block = section(group.key, group.title, group.column === "primary" ? primary : context, group.collapsed);
        if (group.key === "historical") block.append(node("p", "base-detail__help", "Valeurs renseignées conservées, complémentaires au profil habituel de ce type."));
        const list = node("dl", "base-detail__properties");
        for (const spec of entries) {
          const row = node("div"), term = node("dt", null, spec.label), output = node("dd");
          output.dataset.field = spec.key; row.append(term, output); list.append(row);
          properties.set(spec.key, { spec, output });
        }
        block.append(list); slots.set(group.key, block);
      }
      followupActions.replaceChildren(observationsToggle, createObservation);
      observationList.replaceChildren(); observationList.hidden = true; observationsToggle.setAttribute("aria-expanded", "false");
      followup.hidden = !detail.definition.observations && !["troncons", "desordres"].includes(detail.resource);
      observationsToggle.hidden = !detail.definition.observations;
      updateProperties(detail); error.textContent = detail.errors.length ? "Certaines informations complémentaires n’ont pas pu être chargées. Les données disponibles restent consultables." : "";
      const invalidity = detail.values.invalidity_causes;
      const invalidityLabels = { MISSING_GEOMETRY: "Géométrie absente", INVALID_GEOMETRY: "Géométrie invalide",
        UNEXPECTED_GEOMETRY: "Type de géométrie incompatible", UNDETERMINED_CLASSIFICATION: "Classification à compléter" };
      if (invalidity?.length) primary.prepend(node("p", "base-detail__notice", invalidity.map(code => invalidityLabels[code] || code).join(" · ")));
    }
    function slot(key, titleText, { collapsed = false, parent } = {}) {
      if (!slots.has(key)) slots.set(key, section(key, titleText, parent || extensions, collapsed));
      return slots.get(key);
    }
    function sync({ loading = false, editing = false, pending = false, canEdit = false, canCreate = false, canMap = false } = {}) {
      panel.setAttribute("aria-busy", String(loading || pending));
      panel.dataset.mode = editing ? "edit" : "read";
      header.hidden = editing;
      subtitle.textContent = loading ? "Chargement…" : editing ? "Édition" : contextualSubtitle;
      back.disabled = close.disabled = pending; map.disabled = pending || loading;
      map.hidden = !canMap; edit.hidden = !canEdit || editing; edit.disabled = loading || pending;
      createObservation.hidden = !canCreate;
      scroll.hidden = editing; editorHost.hidden = !editing;
    }
    return { node, button, panel, header, actions, title, subtitle, status, error, back, close, map, edit, scroll, grid, extensions, followup,
      followupActions, observationList, observationsToggle, editorHost, slots, slot, render, updateProperties, sync };
  }
  return { create, format };
});
