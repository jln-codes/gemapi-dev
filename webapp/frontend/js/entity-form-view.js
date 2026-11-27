/* Composition des formulaires. Les sessions et mutations restent chez leurs propriétaires. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.EntityFormView = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const SECTIONS = Object.freeze(["identification", "classification", "characteristics", "attachments", "geometry", "historical", "actions"]);
  const LABELS = Object.freeze({ identification: "Identification", classification: "Classification",
    characteristics: "Caractéristiques", attachments: "Rattachements", geometry: "Géométrie",
    historical: "Informations historiques complémentaires", actions: "Actions" });
  const FAMILIES = Object.freeze({
    systemes: ["Système d’endiguement", "Nouveau"], digues: ["Digue", "Nouvelle"],
    troncons: ["Tronçon", "Nouveau"], desordres: ["Désordre", "Nouveau"],
    mobilier: ["Mobilier", "Nouveau"], equipements_mesure: ["Équipement de mesure", "Nouvel"],
    reseaux_techniques: ["Réseau technique", "Nouveau"], ouvrages_particuliers: ["Ouvrage particulier", "Nouvel"],
    cheminements: ["Cheminement", "Nouveau"], vegetation: ["Végétation", "Nouvelle"],
    amenagements_hydrauliques: ["Aménagement hydraulique", "Nouvel"], zones_stockage: ["Zone de stockage", "Nouvelle"],
    prestations: ["Prestation", "Nouvelle"], troncons_lit: ["Tronçon de lit", "Nouveau"],
    elements_morphologiques_lit: ["Élément morphologique du lit", "Nouvel"],
    observations: ["Observation", "Nouvelle"], photos: ["Photo", "Nouvelle"],
    largeurs: ["Largeur", "Nouvelle"], pentes: ["Pente", "Nouvelle"], domanialites: ["Domanialité", "Nouvelle"],
    "regimes-ecoulement": ["Régime d’écoulement", "Nouveau"], "occupations-riveraines": ["Occupation riveraine", "Nouvelle"],
  });
  const CONTEXTS = Object.freeze({ "photo-localisation": "Localisation du photographe", "troncon-reperage": "Gestion du repérage" });
  const CONTEXT_FAMILIES = Object.freeze({ "photo-localisation": "photos", "troncon-reperage": "troncons" });
  const MODES = Object.freeze({ map: "Carto", xy: "X/Y", lonlat: "Lat/Lon", bornage: "Bornage",
    point: "Carto / coordonnées", pr: "PR", effacer: "Effacer la localisation" });
  const KINDS = new Set(["text", "textarea", "date", "number", "checkbox", "select", "relation", "file", "output", "image", "button", "hidden", "datetime-local"]);
  const RELATION_RESOURCES = Object.freeze({
    troncon_id: "troncons", troncon_ids: "troncons", segment_id: "troncons_lit",
    troncon_support_id: "troncons", berge_support_id: "troncons", segment_hydrographique_support_id: "troncons_lit",
    digue_id: "digues", digue_ids: "digues", systeme_endiguement_id: "systemes",
    amenagement_hydraulique_id: "amenagements_hydrauliques", zone_ids: "zones_stockage",
    desordre_ids: "desordres", gestionnaire_ids: "contacts",
  });
  function geometryStatus({ drawing = false, geometry = null, count = 0, minimum = 1 } = {}) {
    if (drawing) return `${count} sommet(s) — ${minimum} minimum.`;
    return "";
  }
  function updateMetreStepBase(input) {
    const distance = input.valueAsNumber;
    // A one-metre step must also accept fractional distances from GPS and SQL.
    // The fractional part is the lowest nonnegative value on that step grid.
    input.min = distance > 0 && !Number.isInteger(distance)
      ? (distance < 1 ? String(distance) : `0.${String(distance).split(".")[1]}`)
      : "0";
  }
  function setMetreDistance(input, value) {
    input.value = value;
    updateMetreStepBase(input);
  }
  function keys(value, allowed, label) {
    if (!value || typeof value !== "object" || Array.isArray(value)
        || Object.keys(value).some(key => !allowed.includes(key))) {
      throw new TypeError(`Composition de formulaire invalide : ${label}.`);
    }
  }
  function validate(descriptor) {
    keys(descriptor, ["family", "context", "fields", "geometry", "observations", "children", "actions"], "descripteur");
    if (!Object.hasOwn(FAMILIES, descriptor.family)) {
      throw new TypeError("Identité du formulaire manquante.");
    }
    if (descriptor.context && CONTEXT_FAMILIES[descriptor.context] !== descriptor.family) {
      throw new TypeError("Contexte de formulaire inconnu.");
    }
    keys(descriptor.fields, SECTIONS.filter(key => key !== "actions"), "sections");
    if (descriptor.geometry && descriptor.fields.geometry?.length) throw new TypeError("Deux compositions géométriques concurrentes.");
    const names = new Set();
    for (const fields of [...Object.values(descriptor.fields), descriptor.geometry?.fields || []]) {
      if (!Array.isArray(fields)) throw new TypeError("Champs de section invalides.");
      for (const field of fields) {
        keys(field, ["name", "label", "kind", "required", "multiple", "size", "rows", "help", "options", "step", "min", "max", "readOnly", "inputMode", "id", "context", "hidden", "placeholder", "accept", "capture", "value", "group", "maxLength", "pattern", "autocomplete", "contexts", "technical", "numberControl"], "champ");
        if (!/^[a-z][a-z0-9_]*$/.test(field.name || "") || names.has(field.name)
            || typeof field.label !== "string" || !field.label || !KINDS.has(field.kind)) {
          throw new TypeError("Champ inconnu ou dupliqué.");
        }
        if (field.numberControl && (field.kind !== "number" || !["coordinate", "metres"].includes(field.numberControl))) {
          throw new TypeError("Contrôle numérique inconnu.");
        }
        names.add(field.name);
      }
    }
    if (descriptor.geometry) {
      keys(descriptor.geometry, ["types", "typeName", "required", "editable", "controls", "fields", "modes", "modeName", "operations", "groupLayouts", "coordinates", "reperageParent"], "géométrie");
      if (descriptor.geometry.typeName && (!/^[a-z][a-z0-9_]*$/.test(descriptor.geometry.typeName)
          || names.has(descriptor.geometry.typeName))) throw new TypeError("Nom du contrôle géométrique invalide ou dupliqué.");
      if (!Array.isArray(descriptor.geometry.types) || !descriptor.geometry.types.length
          || descriptor.geometry.types.some(type => !["Point", "LineString", "Polygon"].includes(type))
          || typeof descriptor.geometry.required !== "boolean"
          || typeof descriptor.geometry.editable !== "boolean") throw new TypeError("Capacité géométrique invalide.");
      if (descriptor.geometry.modes && (!Array.isArray(descriptor.geometry.modes)
          || descriptor.geometry.modes.some(mode => !Object.hasOwn(MODES, mode))
          || !/^[a-z][a-z-]*$/.test(descriptor.geometry.modeName))) throw new TypeError("Modes de localisation invalides.");
      if (descriptor.geometry.operations?.some(name => !["position", "vertices", "mini-map"].includes(name))) {
        throw new TypeError("Opération géométrique inconnue.");
      }
      if (descriptor.geometry.groupLayouts && Object.entries(descriptor.geometry.groupLayouts)
        .some(([path, layout]) => !/^[a-z][a-z0-9-]*(\/[a-z][a-z0-9-]*)*$/.test(path)
          || !["coordinate-pair", "endpoints"].includes(layout))) {
        throw new TypeError("Disposition de coordonnées inconnue.");
      }
      if (descriptor.geometry.coordinates && (descriptor.geometry.modes || !descriptor.geometry.editable)) {
        throw new TypeError("Contrat de saisie de coordonnées invalide.");
      }
      if (descriptor.geometry.reperageParent && (!descriptor.geometry.coordinates
          || !descriptor.fields.attachments?.some(field => field.name === descriptor.geometry.reperageParent
            && field.kind === "relation"))) throw new TypeError("Rattachement de bornage invalide.");
    }
    if (descriptor.observations) {
      keys(descriptor.observations, ["resource", "read", "create"], "observations");
      if (!descriptor.observations.resource || typeof descriptor.observations.read !== "boolean"
          || typeof descriptor.observations.create !== "boolean") throw new TypeError("Contrat Observation invalide.");
    }
    if (descriptor.children && (!Array.isArray(descriptor.children) || descriptor.children.some(name =>
      !["lit-characteristics", "lit-morphologies", "sous-objets", "photos", "media-preview", "details", "result"].includes(name)))) {
      throw new TypeError("Composant enfant inconnu.");
    }
    if (new Set(descriptor.children || []).size !== (descriptor.children || []).length) throw new TypeError("Composant enfant dupliqué.");
    const actionNames=new Set(["save","cancel","close","general-tab","observations-tab","create-observation"]);
    for(const action of descriptor.actions || []){
      keys(action,["name","label","hidden","requires"],"action");
      if(!/^[a-z][a-z0-9-]*$/.test(action.name || "") || !action.label || actionNames.has(action.name)) throw new TypeError("Action invalide ou dupliquée.");
      actionNames.add(action.name);
    }
    if(descriptor.geometry?.controls && !["point","draft","specialized"].includes(descriptor.geometry.controls)) throw new TypeError("Commandes géométriques inconnues.");
    return descriptor;
  }
  // Adapt existing explicitly declared business fields; never infer from SQL or HTML.
  function fromFields(profile, entries, { classification = [], attachments = [], overrides = {},
    geometry = null, observations = profile.observationResource, children = [] } = {}) {
    const fields = { identification: [], classification: [], attachments: [] };
    for (const [name, label, type, editable] of entries) {
      if (!editable) continue;
      const kind = type === "relations" || (type === "select" && attachments.includes(name)) ? "relation"
        : type === "tristate" ? "select" : type;
      const field = { name, label, kind,
        ...(kind === "textarea" ? { rows: 3 } : {}),
        ...(kind === "number" ? { step: "any" } : {}),
        ...(type === "relations" || kind === "relation" && name.endsWith("_ids") ? { multiple: true, size: 5 } : {}),
        ...(type === "tristate" ? { options: [["", "Non renseigné"], ["true", "Oui"], ["false", "Non"]] } : {}),
        ...overrides[name] };
      fields[attachments.includes(name) ? "attachments" : classification.includes(name) ? "classification" : "identification"].push(field);
    }
    return validate({ family: profile.classId, fields, geometry, children,
      ...(observations ? { observations: { resource: observations, read: true, create: true } } : {}) });
  }
  function create({ root = globalThis, document = root.document, descriptor, id, titleId = `${id}-title`,
    contextActions = [], submitAction = true,
    onSubmit = () => {}, onCancel = () => {}, onClose = onCancel,
    canOpenRelation = (resource, identifier) => root.MapEntityNavigation?.canOpen(resource, identifier),
    onOpenRelation = (resource, identifier) => root.MapEntityNavigation.open(resource, identifier),
    onObservations = () => {}, onCreateObservation = () => {}, onTabChange = () => {}, canSelectTab = () => true }) {
    validate(descriptor);
    if (!document || !id) throw new TypeError("Hôte de formulaire manquant.");
    const node = (tag, className, text) => {
      const result = document.createElement(tag);
      if (className) result.className = `entity-form__${className}`;
      if (text !== undefined) result.textContent = text;
      return result;
    };
    const button = (role, text, handler) => {
      const result = node("button", "button", text);
      result.type = "button"; result.dataset.role = role;
      if (handler) result.addEventListener("click", handler);
      return result;
    };
    const element = node("div"); element.className = "entity-form"; element.id = id;
    const header = node("header", "header");
    const identity = node("div", "identity");
    const title = node("strong", "title"); title.id = titleId;
    const subtitle = node("span", "subtitle");
    identity.append(title, subtitle);
    const close = button("close", "×", onClose); close.setAttribute("aria-label", "Fermer");
    header.append(identity, close); element.append(header);
    element.setAttribute("aria-labelledby", title.id);
    const tabs = node("nav", "tabs"); tabs.setAttribute("role", "tablist");
    const generalTab = button("general-tab", "Général", () => show("general"));
    const observationsTab = descriptor.observations?.read
      ? button("observations-tab", "Observations", () => show("observations")) : null;
    const general = node("section", "body"); general.dataset.role = "general-section";
    const observations = observationsTab ? node("section", "body") : null;
    for (const [name, tab, panel] of [["general", generalTab, general], ["observations", observationsTab, observations]]) {
      if (!tab) continue;
      tab.id = `${id}-${name}-tab`; panel.id = `${id}-${name}`;
      tab.setAttribute("role", "tab"); tab.setAttribute("aria-controls", panel.id);
      panel.setAttribute("role", "tabpanel"); panel.setAttribute("aria-labelledby", tab.id);
      tabs.append(tab);
    }
    const messages = node("div", "messages");
    const context = node("nav", "context-actions");
    context.setAttribute("aria-label", "Actions contextuelles");
    function setContextActions(controls) { context.replaceChildren(...controls); }
    setContextActions(contextActions);
    const status = node("p", "message"); status.dataset.role = "status";
    status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
    const error = node("p", "message"); error.classList.add("error"); error.setAttribute("role", "alert");
    messages.append(status, error);
    const form = node("form", "form"); form.id = `${id}-form`;
    const fieldset = node("fieldset", "fields");
    const controls = new Map(); const sections = new Map(); const groups = new Map();
    function section(key) {
      const block = node("section", "section"); block.dataset.section = key;
      const heading = node("h2", "section-title", LABELS[key]); heading.id = `${id}-${key}-title`;
      block.setAttribute("aria-labelledby", heading.id); block.append(heading);
      sections.set(key, block); return block;
    }
    const fieldGroups = new Map();
    const relationLinks = [];
    function relationResource(name) {
      if (name === "parent") return { digues: "systemes", troncons: "digues" }[family];
      return RELATION_RESOURCES[name];
    }
    function relationTarget(field, input) {
      const resource = relationResource(field.name);
      const identifier = input.value;
      if (field.multiple || !resource || input.selectedOptions[0]?.dataset.missing === "true"
          || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(identifier)
          || !can("business.read") || !canOpenRelation(resource, identifier)) return null;
      return { resource, identifier };
    }
    function refreshRelations() {
      for (const { field, input, open } of relationLinks) {
        const target = relationTarget(field, input);
        open.hidden = !target;
        if (target) open.href = root.EntityNavigation.mapUrl(target.resource, target.identifier);
        else open.removeAttribute("href");
        const blocked = state.pending || state.loading || state.uncertain || state.closed;
        open.setAttribute("aria-disabled", String(Boolean(blocked)));
        open.tabIndex = blocked ? -1 : 0;
      }
    }
    function representsRelation(item) {
      return Object.values(descriptor.fields).flat().some(field => field.kind === "relation"
        && relationResource(field.name) === item.resource
        && [...controls.get(field.name).selectedOptions].some(option => option.value === String(item.id)));
    }
    function fieldNode(field) {
        const label = node(field.kind === "relation" ? "div" : "label", "field");
        if (field.kind === "checkbox") label.classList.add("entity-form__field--checkbox");
        if (field.kind === "relation") label.classList.add("entity-form__relations");
        const labelText = node("span", "label", field.label);
        labelText.id = `${id}-${field.name}-label`;
        const input = node(["select", "relation"].includes(field.kind) ? "select"
          : field.kind === "textarea" ? "textarea" : field.kind === "output" ? "output"
          : field.kind === "image" ? "img" : field.kind === "button" ? "button" : "input", "control");
        if (input.tagName === "INPUT") input.type = field.kind;
        if (field.kind === "output") input.setAttribute("role", "note");
        if (field.kind === "button") { input.type = "button"; input.textContent = field.label; labelText.hidden = true; }
        if (field.kind === "image") { input.alt = field.label; labelText.hidden = true; }
        for (const attribute of ["step", "min", "max", "inputMode", "placeholder", "accept", "capture", "maxLength", "pattern", "autocomplete"]) {
          if (field[attribute] !== undefined) input.setAttribute(attribute === "inputMode" ? "inputmode" : attribute, field[attribute]);
        }
        if (field.readOnly) input.readOnly = true;
        if (field.hidden || field.kind === "hidden") label.hidden = true;
        if (field.technical) label.classList.add("technical-identifier");
        if (field.context) label.dataset.context = field.context;
        if (field.contexts) label.dataset.createTypes = field.contexts.join(" ");
        for (const [value, text] of field.options || []) {
          const option = node("option", null, text); option.value = value; input.append(option);
        }
        if (field.value !== undefined && field.kind !== "checkbox") input.value = field.value;
        if (field.numberControl === "coordinate") input.classList.add("entity-form__coordinate");
        if (field.numberControl === "metres") {
          input.step = "1";
          updateMetreStepBase(input);
          input.addEventListener("input", () => updateMetreStepBase(input));
          form.addEventListener("reset", () => queueMicrotask(() => updateMetreStepBase(input)));
        }
        input.name = field.name; input.id = field.id || `${id}-${field.name}`;
        input.setAttribute("aria-labelledby", labelText.id);
        if (field.kind === "checkbox") input.checked = field.value !== false;
        if (field.required) input.required = true;
        if (field.multiple) input.multiple = true;
        if (field.size) input.size = field.size;
        if (field.rows) input.rows = field.rows;
        label.append(labelText);
        if (field.kind === "relation" && !field.multiple) {
          const row = node("div", "relation-control");
          // A navigation link remains usable when the parent itself is locked
          // or the form is read-only. Saving/uncertain operations still block it.
          const open = node("a", "button", "↗");
          open.dataset.role = "open-relation";
          open.addEventListener("click", async event => {
            event.preventDefault();
            const target = relationTarget(field, input);
            if (!target || open.disabled || open.getAttribute("aria-disabled") === "true") return;
            open.disabled = true;
            try { await onOpenRelation(target.resource, target.identifier); }
            catch (failure) { if (element.isConnected) status.textContent = failure.message; }
            finally { open.disabled = false; }
          });
          open.setAttribute("aria-label", `Ouvrir la fiche — ${field.label}`);
          open.title = `Ouvrir la fiche — ${field.label}`;
          open.hidden = true;
          relationLinks.push({ field, input, open });
          row.append(input, open);
          label.append(row);
        } else label.append(input);
        if (field.help) {
          const help = node("span", "help", field.help); help.id = `${input.id}-help`;
          input.setAttribute("aria-describedby", help.id); label.append(help);
        }
        controls.set(field.name, input); fieldGroups.set(field.name, label); return label;
    }
    function appendFields(block, fields, layouts = {}) {
      for (let index = 0; index < fields.length; index++) {
        const field = fields[index];
        let target = block;
        if (field.group) {
          const path = field.group.split("/");
          if (path.some(name => !/^[a-z][a-z0-9-]*$/.test(name))) throw new TypeError("Groupe de champs invalide.");
          for (const [index] of path.entries()) {
            const name = path.slice(0, index + 1).join("/");
            if (!groups.has(name)) {
              const layout = Object.hasOwn(layouts, name) ? layouts[name] : null;
              const parent = path.slice(0, index).join("/");
              const endpoint = layouts[parent] === "endpoints"
                && (path[index] === "start" ? "Début" : path[index] === "end" ? "Fin" : null);
              const group = node(endpoint ? "fieldset" : "div", "group");
              group.dataset.group = name;
              if (layout) group.classList.add(`entity-form__${layout}`);
              if (endpoint) {
                group.classList.add("entity-form__endpoint");
                group.append(node("legend", "label", endpoint));
              }
              groups.set(name, group);
              target.append(group);
            }
            target = groups.get(name);
          }
        }
        const end = fields[index + 1];
        if (field.name === "date_debut" && field.kind === "date" && end?.name === "date_fin"
            && end.kind === "date" && end.group === field.group) {
          const range = node("div", "date-range");
          range.append(fieldNode(field), fieldNode(end));
          target.append(range);
          index++;
        } else {
          target.append(fieldNode(field));
        }
      }
    }
    for (const key of SECTIONS.slice(0, 4)) {
      const fields = descriptor.fields[key]; if (!fields || (!fields.length && key !== "characteristics")) continue;
      const block = section(key);
      appendFields(block, fields);
      fieldset.append(block);
    }
    let drawingActive = false;
    let geometry = null;
    if (descriptor.fields.geometry?.length) {
      const block = section("geometry"); appendFields(block, descriptor.fields.geometry); fieldset.append(block);
    }
    if (descriptor.geometry) {
      const spec = descriptor.geometry;
      const block = section("geometry");
      block.classList.add("entity-form__geometry");

      const label = node("div", "field");
      const typeLabel = node("label", "label", "Type de dessin");
      typeLabel.id = `${id}-geometry-type-label`;
      const type = node("select", "control");
      type.name = spec.typeName || "geometry_type";
      type.id = `${id}-${type.name}`; typeLabel.htmlFor = type.id;
      type.setAttribute("aria-labelledby", typeLabel.id);
      const fixedType = node("output", "geometry-fixed-type");
      fixedType.setAttribute("aria-labelledby", typeLabel.id);
      label.append(typeLabel, type, fixedType);
      block.append(label);
      const requirement = node("p", "help");
      requirement.dataset.role = "geometry-requirement";
      block.append(requirement);

      const modeSelector = node("fieldset", "geometry-modes");
      modeSelector.append(node("legend", "label", "Mode de localisation"));
      const modeChoices = new Map();
      const modeOptions = node("div", "mode-options");
      const modes = spec.modes || (spec.coordinates
        ? ["map", "xy", "lonlat", ...(spec.reperageParent ? ["bornage"] : [])] : []);
      for (const [index, mode] of modes.entries()) {
        const choice = node("label", "mode-choice");
        const input = node("input");
        input.type = "radio";
        input.name = spec.modeName || `${id}-location-mode`;
        input.value = mode;
        input.defaultChecked = index === 0;
        choice.append(input, node("span", null, MODES[mode]));
        modeChoices.set(mode, { choice, input });
        modeOptions.append(choice);
      }
      modeSelector.append(modeOptions);
      if (modeChoices.size > 1) block.append(modeSelector);
      const availableModes = new Set(modeChoices.keys());
      const modeStates = new Map([...modeChoices.keys()].map(mode => [mode, { visible: true, enabled: true }]));
      function setModeAvailability(availability, { locked = false } = {}) {
        const active = [...modeChoices].find(([, { input }]) => input.checked)?.[0];
        for (const [mode, available] of Object.entries(availability)) {
          if (!modeChoices.has(mode)) throw new TypeError("Mode de localisation inconnu.");
          const state = typeof available === "boolean" ? { visible: available, enabled: available } : available;
          if (!state || typeof state.visible !== "boolean" || typeof state.enabled !== "boolean") {
            throw new TypeError("Disponibilité de localisation invalide.");
          }
          modeStates.set(mode, state);
          if (state.visible && state.enabled) availableModes.add(mode);
          else availableModes.delete(mode);
        }
        for (const [mode, { choice, input }] of modeChoices) {
          choice.hidden = !modeStates.get(mode).visible;
          input.disabled = !availableModes.has(mode) || locked;
        }
        modeSelector.hidden = [...modeStates.values()].filter(state => state.visible).length <= 1;
        if (!active || availableModes.has(active)) return false;
        const next = availableModes.values().next().value;
        if (next) modeChoices.get(next).input.checked = true;
        return true;
      }

      const entry = node("div", "geometry-entry");
      const component = node("div", "geometry-component");
      appendFields(component, spec.fields || [], spec.groupLayouts);
      let coordinateEntry = null;
      if (spec.coordinates) {
        const panel = node("div", "geometry-commands");
        panel.hidden = true;
        const endpoints = ["start", "end"].map(endpoint =>
          [1, 2].map(axis => ({ name: `coordinate_${endpoint}_${axis}`, label: axis === 1 ? "X" : "Y",
            kind: "number", numberControl: "coordinate", step: "any", inputMode: "decimal", group: `coordinates/${endpoint}` })));
        appendFields(panel, endpoints.flat(), { coordinates: "endpoints" });
        const actions = node("div", "geometry-actions");
        const accept = button("confirm-coordinates", "Confirmer les coordonnées");
        const cancel = button("cancel-coordinates", "Annuler la saisie");
        const status = node("p", "help");
        status.setAttribute("role", "status");
        actions.append(accept, cancel);
        panel.append(actions);
        component.append(panel);
        let bornage = null;
        if (spec.reperageParent) {
          const reference = fieldNode({ name: "bornage_reference", kind: "select", label: "Tronçon de référence du bornage" });
          reference.hidden = true;
          const panel = node("div", "geometry-commands");
          panel.hidden = true;
          const senses = [["AVANT_BORNE", "Amont"], ["SUR_BORNE", "Sur borne"], ["APRES_BORNE", "Aval"]];
          for (const endpoint of ["start", "end"]) appendFields(panel, [
            { name: `bornage_${endpoint}_borne`, label: "Borne", kind: "select", group: `bornage/${endpoint}` },
            { name: `bornage_${endpoint}_distance`, label: "Distance (m)", kind: "number", numberControl: "metres",
              inputMode: "decimal", group: `bornage/${endpoint}` },
            { name: `bornage_${endpoint}_sense`, label: "Sens", kind: "select", options: senses, group: `bornage/${endpoint}` },
          ], { bornage: "endpoints" });
          const actions = node("div", "geometry-actions");
          const confirm = button("confirm-bornage", "Confirmer le bornage");
          const cancel = button("cancel-bornage", "Annuler la saisie");
          const notice = node("p", "help", "La confirmation remplace le dessin par la géométrie calculée depuis les bornes.");
          actions.append(confirm, cancel);
          panel.append(notice, actions);
          component.append(reference, panel);
          bornage = { panel, confirm, cancel, reference, referenceInput: controls.get("bornage_reference"),
            parent: controls.get(spec.reperageParent),
            start: groups.get("bornage/start"), end: groups.get("bornage/end"),
            endpoints: ["start", "end"].map(endpoint => ({
              borne: controls.get(`bornage_${endpoint}_borne`), distance: controls.get(`bornage_${endpoint}_distance`),
              sense: controls.get(`bornage_${endpoint}_sense`),
            })) };
        }
        // The same state message serves all methods of the local draft.
        component.append(status);
        coordinateEntry = { panel, actions, accept, cancel, status,
          bornage,
          endpoints: endpoints.map(fields => fields.map(({ name }) => ({
            input: controls.get(name), label: fieldGroups.get(name).firstElementChild,
          }))),
          start: groups.get("coordinates/start"), end: groups.get("coordinates/end") };
      }
      const canvas = node("div", "geometry-canvas");
      const commands = node("div", "geometry-commands");
      commands.classList.add("entity-form__geometry-toolbar");
      const methods = node("div", "geometry-methods");
      const pointOnly = spec.controls === "point";
      const draw = button(pointOnly ? "place-point" : "draw", "Dessiner");
      draw.classList.add("geometry-method-button");
      methods.append(draw);
      const operationActions = node("div", "geometry-actions");
      const confirm = button(pointOnly ? "confirm-geometry" : "confirm", "Terminer");
      const cancel = button("cancel-drawing", "Annuler le dessin");
      confirm.hidden = cancel.hidden = true;
      operationActions.append(cancel, confirm);
      commands.append(methods, operationActions);
      canvas.append(commands);
      const operations = new Map();
      for (const name of spec.operations || []) {
        const miniMap = name === "mini-map";
        const panel = node("div", "geometry-commands");
        panel.hidden = true;
        const start = button(name, miniMap ? "Dessiner" : "Modifier");
        start.classList.add("geometry-method-button");
        const tools = node("div", "geometry-methods");
        const actions = node("div", "geometry-actions");
        const cancel = button("cancel-drawing", miniMap ? "Annuler" : "Annuler le dessin");
        const confirm = button("confirm-drawing", miniMap ? "Confirmer" : "Terminer");
        const status = node("p", "help");
        status.setAttribute("role", "status");
        const surface = miniMap ? node("div", "geometry-map") : null;
        if (surface) panel.append(surface);
        actions.append(cancel, confirm);
        tools.append(start);
        if (!miniMap) methods.append(tools);
        panel.append(actions, status);
        if (!miniMap) canvas.append(panel);
        operations.set(name, { panel, tools, start, actions, cancel, confirm, status, surface });
      }
      entry.append(canvas, component);
      block.append(entry);

      const geometryStatus = node("p", "help");
      geometryStatus.dataset.role = spec.controls === "draft" ? "geometry-status" : "point-status";
      geometryStatus.setAttribute("role", "status");
      const contractStatus = node("p", "help");
      contractStatus.dataset.role = "geometry-contract-status";
      contractStatus.setAttribute("role", "status");
      const diagnostics = node("p", "help");
      diagnostics.dataset.role = "geometry-diagnostics";
      const details = node("div", "geometry-details");
      block.append(geometryStatus, contractStatus, diagnostics, details);

      function setTypes(types) {
        type.replaceChildren(...types.map(value => {
          const option = node("option", null, value);
          option.value = value;
          return option;
        }));
        type.hidden = types.length < 2;
        fixedType.hidden = types.length !== 1;
        fixedType.defaultValue = types.length === 1 ? types[0] : "";
        label.hidden = types.length === 0;
      }
      function setRequirement(required, allowMissingWhenInvalid = false) {
        requirement.textContent = !required ? "Géométrie facultative"
          : allowMissingWhenInvalid ? "Géométrie obligatoire pour un objet valide" : "Géométrie obligatoire";
      }
      setTypes(spec.types);
      setRequirement(spec.required);
      geometry = { block, entry, component, canvas, methods, operationActions, modeSelector, operations, coordinateEntry,
        modeChoices, setModeAvailability, hasMode: mode => availableModes.has(mode),
        details, cancel, diagnostics, status: geometryStatus, draw, confirm, commands, type, requirement,
        resolveGeometryError(value, ownerState) {
          const failure = ownerState?.error;
          if (!value || failure?.geometryCause !== "required" || status.textContent !== failure.message) return;
          ownerState.error = null;
          setStatus("");
          sync(ownerState);
        },
        setDrawingActive(active) {
          drawingActive = active;
          save.disabled = active || state.pending || state.loading || state.uncertain || state.forbidden || state.conflict;
        },
        contractStatus, setTypes, setRequirement };
      fieldset.append(block);
    }
    if (descriptor.fields.historical) {
      const block = section("historical");
      block.append(node("p", "help", "Ces valeurs renseignées sont conservées ; elles complètent les champs habituellement proposés pour ce sous-type."));
      appendFields(block, descriptor.fields.historical); fieldset.append(block);
    }
    const actions = section("actions"); actions.classList.add("entity-form__actions");
    const cancel = button("cancel", "Fermer", onCancel);
    const save = button("save", "Enregistrer"); save.type = "submit";
    const extraActions = new Map();
    for (const action of descriptor.actions || []) {
      keys(action, ["name", "label", "hidden", "requires"], "action");
      if (!/^[a-z][a-z0-9-]*$/.test(action.name)) throw new TypeError("Action invalide.");
      const control = button(action.name, action.label);
      control.hidden = Boolean(action.hidden); control.setAttribute(`data-${action.name}`, "");
      if (action.requires) control.dataset.requires = action.requires;
      extraActions.set(action.name, control); actions.append(control);
    }
    actions.append(cancel, save); form.append(fieldset); general.append(form);
    // The footer submits only this form; business children keep their own forms.
    function setFormId(value) { form.id = value; save.setAttribute("form", value); }
    setFormId(form.id);
    const extensions = node("div", "extensions");
    general.append(extensions);
    form.addEventListener("submit", onSubmit);
    let observationList = null, createObservation = null;
    if (observations) {
      observations.dataset.role = "observations-section";
      createObservation = button("create-observation", "+ Observation", onCreateObservation);
      observationList = node("div"); observationList.dataset.role = "observations";
      observations.append(createObservation, observationList);
    }
    const children = new Map();
    for (const name of descriptor.children || []) {
      const block = node("section", "children"); block.dataset.role = `${name}-section`;
      const content = node("div"); content.dataset.role = name; block.append(content);
      extensions.append(block); children.set(name, content);
    }
    general.append(actions);
    element.append(tabs, context, messages, general);
    if (observations) element.append(observations);
    let state = { mode: "read", objectId: null, pending: false, loading: false };
    let selected = "general";
    const can = permission => !root.BusinessRuntime || root.BusinessRuntime.can(permission);
    function show(name, notify = true) {
      if (!["general", "observations"].includes(name)) throw new TypeError("Onglet de formulaire inconnu.");
      if (name === "observations" && (!observationsTab || observationsTab.disabled)) return false;
      const changed = selected !== name;
      if(changed && notify && !canSelectTab(name))return false;
      selected = name;
      general.hidden = name !== "general"; if (observations) observations.hidden = name !== "observations";
      for (const [tab, active] of [[generalTab, name === "general"], [observationsTab, name === "observations"]]) {
        if (!tab) continue;
        tab.setAttribute("aria-selected", String(active)); tab.tabIndex = active ? 0 : -1;
      }
      if (changed && notify) onTabChange(name);
      if (changed && notify && name === "observations") onObservations();
      return true;
    }
    tabs.addEventListener("keydown", event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      const available = [generalTab, observationsTab].filter(tab => tab && !tab.disabled);
      const next = event.key === "Home" ? available[0] : event.key === "End" ? available.at(-1)
        : available[(available.indexOf(document.activeElement) + (event.key === "ArrowLeft" ? -1 : 1) + available.length) % available.length];
      event.preventDefault(); next.click(); next.focus();
    });
    let family = descriptor.family;
    let objectName = "";
    function setSubject(next = {}) {
      if (next.family) {
        if (!Object.hasOwn(FAMILIES, next.family)) throw new TypeError("Famille de formulaire inconnue.");
        family = next.family;
      }
      if (Object.hasOwn(next, "objectName")) objectName = String(next.objectName || "").trim();
      const [label, article] = FAMILIES[family];
      const subject = CONTEXTS[descriptor.context] || label;
      title.textContent = state.mode === "create" ? `${article} ${label.toLocaleLowerCase("fr")}`
        : objectName && objectName !== label ? `${subject} — ${objectName}` : subject;
      tabs.setAttribute("aria-label", `Rubriques ${subject}`);
    }
    function sync(next = {}) {
      if (state.mode !== (next.mode ?? state.mode)
          || Object.hasOwn(next, "objectId") && next.objectId !== state.objectId || next.closed) {
        geometry?.coordinateEntry?.reset?.();
      }
      if (Object.hasOwn(next, "objectId") && next.objectId !== state.objectId) objectName = "";
      state = { ...state, ...next };
      setSubject();
      const busy = state.pending || state.loading;
      const writable = !state.closed && state.mode !== "read" && !state.forbidden && can(state.mode === "create" ? "business.create" : "business.update");
      const blocked = state.uncertain && !state.retryUncertain;
      const phase = state.closed ? "closed" : state.pending ? "saving" : state.loading ? "loading"
        : state.uncertain ? "uncertain" : state.forbidden ? "forbidden" : state.conflict ? "conflict"
        : state.error ? "error" : !writable ? "readonly" : state.dirty ? "dirty" : "ready";
      element.dataset.state = phase;
      element.setAttribute("aria-busy", String(busy)); fieldset.disabled = busy || state.uncertain || !writable;
      save.hidden = !writable || !submitAction; save.disabled = !submitAction || drawingActive || busy || blocked || !writable;
      save.dataset.requires = state.mode === "create" ? "business.create" : "business.update";
      save.textContent = state.pending ? "Enregistrement…" : state.uncertain
        ? (state.retryUncertain ? "Réessayer l'enregistrement" : "Résultat non confirmé") : "Enregistrer";
      cancel.textContent = state.mode !== "read" && (writable || state.dirty || state.error) ? "Annuler" : "Fermer";
      close.disabled = state.pending || state.uncertain;
      cancel.disabled = state.closed || close.disabled;
      if (geometry) {
        geometry.commands.hidden = !descriptor.geometry.editable || !writable || descriptor.geometry.controls === "specialized";
        geometry.draw.disabled = busy || state.uncertain || !writable;
        geometry.draw.textContent = state.mode === "create" ? "Dessiner" : "Modifier";
        geometry.draw.dataset.requires = save.dataset.requires;
        geometry.coordinateEntry?.sync?.({ writable: descriptor.geometry.editable && writable, busy: busy || blocked });
      }
      if (observationsTab) observationsTab.disabled = state.mode === "create" || !state.objectId || busy || !can("business.read");
      if (createObservation) createObservation.hidden = state.mode === "create" || !state.objectId
        || !descriptor.observations.create || !can("business.create");
      if (createObservation) createObservation.dataset.requires = "business.create";
      if (!state.objectId || observationsTab?.disabled) show("general");
      refreshRelations();
      subtitle.textContent = state.pending ? "Enregistrement…" : state.loading ? "Chargement…"
        : state.closed ? "Fermé" : state.uncertain ? "Résultat non confirmé — saisie conservée"
        : state.forbidden ? "Accès refusé — saisie conservée"
        : state.conflict ? "Conflit — saisie conservée"
        : state.error ? "Erreur — saisie conservée"
        : state.mode === "create" ? "Brouillon local" : writable ? (state.dirty ? "Édition — modifications en cours" : "Édition") : "Consultation";
    }
    const sectionNotices = new Map();
    function setSectionNotice(section, text, { warning = false } = {}) {
      const target = sections.get(section);
      if (!target || section === "actions") throw new TypeError("Section de message inconnue.");
      let notice = sectionNotices.get(section);
      if (!notice) {
        notice = node("p", "help");
        notice.setAttribute("role", "status");
        target.append(notice);
        sectionNotices.set(section, notice);
      }
      notice.textContent = text;
      notice.classList.toggle("entity-form__warning", warning);
    }
    function setFieldNotice(name, text) {
      const target = fieldGroups.get(name), input = controls.get(name);
      if (!target || !input) throw new TypeError("Champ de message inconnu.");
      let notice = target.querySelector(".entity-form__help");
      if (!notice) {
        notice = node("span", "help");
        notice.id = `${input.id}-help`;
        input.setAttribute("aria-describedby", notice.id);
        target.append(notice);
      }
      notice.textContent = text;
    }
    form.addEventListener("submit", event => {
      if (!drawingActive) return;
      event.preventDefault(); event.stopImmediatePropagation();
    }, true);
    function setStatus(message, failed = false) { status.textContent = message; status.classList.toggle("error", failed); }
    // Options are also filled programmatically by the adapters. Observe their
    // replacement without changing selections or emitting synthetic mutations.
    if (relationLinks.length && root.MutationObserver) {
      new root.MutationObserver(refreshRelations).observe(form, { childList: true, subtree: true });
      form.addEventListener("change", refreshRelations);
      form.addEventListener("reset", () => queueMicrotask(refreshRelations));
    }
    sync(); show("general");
    return { element, form, fieldset, controls, groups, fieldGroups, children, extraActions, general, tabs, context, extensions, actions, sections, geometry, show, sync, setSubject, setStatus, setSectionNotice, setFieldNotice, setContextActions, setFormId,
      status, error, save, cancel, close, observations, observationList, createObservation,
      generalTab, observationsTab, representsRelation };
  }
  return { create, validate, fromFields, geometryStatus, setMetreDistance, SECTIONS, familyLabel: family => FAMILIES[family]?.[0] };
});
