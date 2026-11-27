/* Champs métier du Tronçon de lit et de sa morphologie, partagés Carte/Base. */
(function (root, factory) {
  const selectors = typeof module === "object" && module.exports
    ? require("./relation-selector.js") : root.RelationSelector;
  const api = factory(selectors);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LitForm = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (RelationSelector) {
  "use strict";
  const FormView = typeof module === "object" && module.exports
    ? require("./entity-form-view.js") : globalThis.EntityFormView;
  const PROFILES = Object.freeze({
    troncons_lit: Object.freeze({ classId: "troncons_lit", composition: "entity-form", resource: "troncons-lit",
      apiPath: "/api/troncons-lit", singular: "Tronçon de lit", plural: "Tronçons de lit",
      relatedResources: ["digues"],
      readPermission: "business.read", updatePermission: "business.update", idempotent: true }),
    elements_morphologiques_lit: Object.freeze({ classId: "elements_morphologiques_lit", composition: "entity-form",
      resource: "elements-morphologiques-lit", apiPath: "/api/elements-morphologiques-lit",
      singular: "Élément morphologique du lit", plural: "Éléments morphologiques du lit",
      relatedResources: ["troncons-lit"],
      readPermission: "business.read", updatePermission: "business.update", idempotent: true }),
  });
  const TYPES = Object.freeze([["ILE", "Île"], ["BANC", "Banc"],
    ["ATTERISSEMENT", "Atterrissement"], ["INDETERMINE", "Indéterminé"]]);
  const COMMON = ["designation", "libelle", "commentaire", "date_debut", "date_fin", "valid"];
  const MORPHOLOGY = ["type_morphologie", "segment_id", "longueur", "largeur",
    "rattache_berge", "vegetalise", "type_zone_id"];
  const morphology = settings => settings.classId === "elements_morphologiques_lit";
  const keys = settings => [...COMMON, ...(morphology(settings) ? MORPHOLOGY : [])];
  function profile(classId) {
    if (!PROFILES[classId]) throw new Error("Famille Lit inconnue.");
    return PROFILES[classId];
  }
  function composition(settings = Object.values(PROFILES)[0], { geometryEditable = true } = {}) {
    return FormView.fromFields(settings, readFields(settings), {
      classification: ["type_morphologie", "rattache_berge", "vegetalise", "type_zone_id"],
      attachments: ["segment_id", "digue_ids"],
      overrides: { type_morphologie: { required: true, options: [["", "Choisir"], ...TYPES] } },
      geometry: { types: morphology(settings) ? ["Point", "LineString", "Polygon"] : ["LineString", "Polygon"], required: false, editable: geometryEditable, coordinates: geometryEditable, controls: "draft" }, children: settings.classId === "troncons_lit" ? ["lit-characteristics", "lit-morphologies"] : [],
    });
  }
  function readFields(settings) {
    return [
      ...(morphology(settings) ? [["type_morphologie", "Sous-type", "select", true, null, "type_morphologie_libelle"]] : []),
      ["designation", "Désignation", "text", true], ["libelle", "Libellé", "text", true],
      ["commentaire", "Commentaire", "textarea", true], ["date_debut", "Date de début", "date", true],
      ["date_fin", "Date de fin", "date", true],
      ...(morphology(settings) ? [
        ["segment_id", "Tronçon de lit", "select", true, null, "segment_libelle"],
        ["longueur", "Longueur (m)", "number", true], ["largeur", "Largeur (m)", "number", true],
        ["rattache_berge", "Rattaché à une berge", "tristate", true],
        ["vegetalise", "Végétalisé", "tristate", true],
        ["type_zone_id", "Type d'atterrissement", "select", true, null, "type_zone_libelle"],
      ] : [["digue_ids", "Digues associées", "relations", true]]),
      ["valid", "Objet valide", "checkbox", true], ["localisation", "Localisation", "text", false],
      ["geometry_diagnostics", "Diagnostic géométrique", "text", false],
      ...(morphology(settings) ? [["pr_debut", "PR de début conservé", "text", false],
        ["pr_fin", "PR de fin conservé", "text", false]] : []),
    ];
  }
  async function loadChoices(settings, fetchJson, referenceData) {
    const [options, zones] = await Promise.all([
      fetchJson("/api/troncons-lit/options"),
      morphology(settings) ? referenceData.load("typesZoneAtterrissementLit", fetchJson) : [],
    ]);
    if (!Array.isArray(options?.digues) || !Array.isArray(options?.troncons_lit)) {
      throw new Error("Relations du lit indisponibles.");
    }
    return { ...options, zones };
  }
  function updateConditional(form, settings) {
    if (!morphology(settings)) return;
    const zone = form.elements.type_zone_id?.closest(".entity-form__field");
    if (zone) zone.hidden = form.elements.type_morphologie.value !== "ATTERISSEMENT"
      && !form.elements.type_zone_id.value;
  }
  function fill(form, feature, choices, referenceData, selectors = RelationSelector) {
    const settings = profile(form.dataset.litClass || (form.elements.type_morphologie
      ? "elements_morphologiques_lit" : "troncons_lit"));
    const properties = feature?.properties || {};
    for (const key of keys(settings)) {
      const control = form.elements[key];
      if (!control) continue;
      if (key === "valid") control.checked = properties.valid !== false;
      else control.value = properties[key] == null ? "" : String(properties[key]);
    }
    const fillRelations = (name, rows, selected, multiple, emptyLabel) => selectors.fill(
      form.elements[name], rows, selected, { multiple, emptyLabel,
        preserveSelectedOutsideFilter: true, include: row => row.valid !== false,
        label: row => row.designation || row.libelle || row.id,
        outsideLabel: label => `${label} (actuel, inactif)` });
    if (morphology(settings)) {
      fillRelations("segment_id", choices.troncons_lit, properties.segment_id ? [properties.segment_id] : [], false, "Sans tronçon de lit");
      referenceData.fillSelect(form.elements.type_zone_id, choices.zones,
        properties.type_zone_id, "Type non renseigné", properties.type_zone_libelle);
    } else fillRelations("digue_ids", choices.digues, properties.digue_ids || [], true, "");
    updateConditional(form, settings);
  }
  function payload(form, settings, selectors = RelationSelector) {
    const result = {};
    for (const key of keys(settings)) {
      const control = form.elements[key];
      if (key === "valid") result[key] = control.checked;
      else if (["rattache_berge", "vegetalise"].includes(key)) {
        result[key] = control.value === "" ? null : control.value === "true";
      } else result[key] = control.value.trim() || null;
    }
    if (!morphology(settings)) result.digue_ids = selectors.values(form.elements.digue_ids);
    if (morphology(settings) && result.type_zone_id && result.type_morphologie !== "ATTERISSEMENT") {
      throw new Error("Le type d'atterrissement doit être retiré explicitement avant de changer de sous-type.");
    }
    return result;
  }
  function isDirty(form, feature, selectors = RelationSelector) {
    const settings = profile(form.dataset.litClass || (form.elements.type_morphologie
      ? "elements_morphologiques_lit" : "troncons_lit"));
    const properties = feature?.properties || {};
    for (const key of keys(settings)) {
      const before = key === "valid" ? properties.valid !== false : String(properties[key] ?? "");
      const after = key === "valid" ? form.elements[key].checked : form.elements[key].value;
      if (before !== after) return true;
    }
    return !morphology(settings) && (properties.digue_ids || []).map(String).sort().join("|")
      !== selectors.values(form.elements.digue_ids).sort().join("|");
  }
  function renderMorphologies(container, feature, onOpen) {
    const document = container.ownerDocument;
    container.replaceChildren();
    if (!feature?.properties?.id) { container.hidden = true; return; }
    container.hidden = false;
    const title = document.createElement("h3"); title.textContent = "Éléments morphologiques associés";
    const list = document.createElement("ul");
    for (const row of feature.properties.elements_morphologiques_lit || []) {
      const item = document.createElement("li"), button = document.createElement("button");
      const label = TYPES.find(([type]) => type === row.type_morphologie)?.[1] || row.type_morphologie;
      button.type = "button";
      button.textContent = [label, row.designation || row.libelle || row.id, row.valid === false ? "Non valide" : ""].filter(Boolean).join(" · ");
      button.addEventListener("click", () => { void onOpen(row.id); });
      item.append(button); list.append(item);
    }
    if (!list.children.length) { const empty = document.createElement("li"); empty.textContent = "Aucun élément associé."; list.append(empty); }
    container.append(title, list);
  }
  function geometryDiagnosticText(feature) {
    const labels = { GEOMETRIE_ABSENTE: "Géométrie absente", FORME_HORS_CONTRAT: "Forme hors du contrat courant",
      SRID_HORS_CONTRAT: "Référentiel spatial différent de celui de la base", TOPOLOGIE_INVALIDE: "Topologie invalide",
      GEOMETRIE_INEXPLOITABLE: "Géométrie non affichable ; données conservées" };
    return (feature?.properties?.geometry_diagnostics || []).map(code => labels[code] || code).join(" · ");
  }
  return { PROFILES, TYPES, profile, geometryDiagnosticText, renderMorphologies, composition, readFields, loadChoices,
    fill, payload, isDirty, updateConditional };
});
