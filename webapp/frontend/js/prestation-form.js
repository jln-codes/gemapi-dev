/* Champs et associations Prestation partagés par Carte et Base. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PrestationForm = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const FormView = typeof module === "object" && module.exports
    ? require("./entity-form-view.js") : globalThis.EntityFormView;
  const PROFILES = Object.freeze({ prestations: Object.freeze({
    classId: "prestations", composition: "entity-form", resource: "prestations", observationResource: "prestations",
    apiPath: "/api/prestations", singular: "Prestation",
    readPermission: "business.read", updatePermission: "business.update",
    relatedResources: ["troncons", "digues", "systemes"],
  }) });
  const OBJECT_FAMILIES = Object.freeze([
    ["desordres", "Désordres"], ["ouvrages_hydrauliques", "Ouvrages hydrauliques"],
    ["ouvrages_particuliers", "Ouvrages particuliers"], ["mobilier", "Mobilier"],
    ["equipements_mesure", "Équipements de mesure"], ["reseaux_techniques", "Réseaux techniques"],
    ["cheminements", "Cheminements"], ["vegetation", "Végétation"],
    ["amenagements_hydrauliques", "Aménagements hydrauliques"],
  ]);
  const SCALARS = ["type_prestation_id", "designation", "libelle", "commentaire", "date_debut",
    "date_fin", "cout_global", "cout_metre", "realisation_interne", "valid"];
  function profile(classId) {
    if (!PROFILES[classId]) throw new Error("Catégorie métier inconnue.");
    return PROFILES[classId];
  }
  function composition(settings = Object.values(PROFILES)[0], { geometryEditable = true } = {}) {
    return FormView.fromFields(settings, [...readFields(settings), ...OBJECT_FAMILIES.map(([key, label]) => [`objects_${key}`, label, "relations", true])], {
      classification: ["type_prestation_id"],
      attachments: ["troncon_ids", "intervenant_ids"].concat(OBJECT_FAMILIES.map(([key]) => `objects_${key}`)),
      overrides: {"date_fin": {"required": true}, "cout_global": {"min": "0"}, "cout_metre": {"min": "0"}, "valid": {"options": [["", "Non renseignée"], ["true", "Valide"], ["false", "Invalide"]]}},
      geometry: { types: ["Point", "LineString", "Polygon"], required: false, editable: geometryEditable, coordinates: geometryEditable, ...(geometryEditable ? { reperageParent: "troncon_ids" } : {}), controls: "draft" }, children: [],
    });
  }
  function readFields() {
    return [["type_prestation_id", "Type", "select", true, "typesPrestation", "type_libelle"],
      ["designation", "Désignation", "text", true], ["libelle", "Libellé", "text", true],
      ["commentaire", "Commentaire", "textarea", true],
      ["date_debut", "Date de début", "date", true], ["date_fin", "Date de fin", "date", true],
      ["cout_global", "Coût global", "number", true], ["cout_metre", "Coût par mètre", "number", true],
      ["realisation_interne", "Réalisation interne", "tristate", true],
      ["valid", "Validité", "tristate", true],
      ["troncon_ids", "Tronçons", "relations", true],
      ["intervenant_ids", "Intervenants", "relations", true],
      ["object_ids", "Objets concernés", "objects", false],
      ["localisation", "Localisation", "text", false]];
  }
  async function loadChoices(_settings, fetchJson, referenceData) {
    const [types, relations] = await Promise.all([
      referenceData.load("typesPrestation", fetchJson), fetchJson("/api/prestations/relations/options"),
    ]);
    if (!Array.isArray(relations?.troncons) || !Array.isArray(relations?.contacts)) {
      throw new Error("Relations Prestation indisponibles.");
    }
    return { types, relations };
  }
  function fill(form, feature, choices, referenceData, relationSelector) {
    const p = feature?.properties || {};
    for (const key of SCALARS.filter(key => key !== "type_prestation_id")) {
      form.elements[key].value = ["valid", "realisation_interne"].includes(key) && p[key] != null
        ? String(p[key]) : (p[key] ?? "");
    }
    form.elements.date_fin.required = !p.id;
    form.elements.type_prestation_id.disabled = false;
    referenceData.fillSelect(form.elements.type_prestation_id, choices.types,
      p.type_prestation_id, "Aucun type", p.type_libelle);
    const fillRelation = (name, rows, ids) => relationSelector.fill(form.elements[name], rows || [], ids || [], {
      multiple: true, include: row => row.valid === true, preserveSelectedOutsideFilter: true,
      label: row => row.libelle || row.designation || row.id,
      outsideLabel: label => `${label} (actuel, inactif)`,
    });
    fillRelation("troncon_ids", choices.relations.troncons, p.troncon_ids);
    fillRelation("intervenant_ids", choices.relations.contacts, p.intervenant_ids);
    for (const [family] of OBJECT_FAMILIES) {
      fillRelation(`objects_${family}`, choices.relations[family], p.object_ids?.[family]);
    }
  }
  function isDirty(form, feature, relationSelector) {
    const p = feature?.properties || {};
    const linksDirty = (ids, select) => (ids || []).map(String).sort().join(",")
      !== relationSelector.values(select).sort().join(",");
    return SCALARS.some(key => String(p[key] ?? "") !== form.elements[key].value)
      || linksDirty(p.troncon_ids, form.elements.troncon_ids)
      || linksDirty(p.intervenant_ids, form.elements.intervenant_ids)
      || OBJECT_FAMILIES.some(([family]) =>
        linksDirty(p.object_ids?.[family], form.elements[`objects_${family}`]));
  }
  function payload(form, _settings, relationSelector, feature) {
    const optional = key => form.elements[key].value.trim() || null;
    if (!feature?.properties?.id && !optional("date_fin")) {
      throw new Error("La date de fin de l'action réalisée est obligatoire.");
    }
    const amount = key => optional(key) === null ? null : Number(optional(key));
    const choice = key => form.elements[key].value === "" ? null : form.elements[key].value === "true";
    const result = {
      designation: optional("designation"), libelle: optional("libelle"),
      commentaire: optional("commentaire"), date_debut: optional("date_debut"),
      date_fin: optional("date_fin"), cout_global: amount("cout_global"),
      cout_metre: amount("cout_metre"), realisation_interne: choice("realisation_interne"),
      valid: choice("valid"),
    };
    const current = feature?.properties;
    if (current?.id && !current.date_fin && !result.date_fin) delete result.date_fin;
    const changed = (before, after) => (before || []).map(String).sort().join(",")
      !== after.slice().sort().join(",");
    for (const name of ["troncon_ids", "intervenant_ids"]) {
      const ids = relationSelector.values(form.elements[name]);
      if (!current || changed(current[name], ids)) result[name] = ids;
    }
    const objects = Object.fromEntries(OBJECT_FAMILIES.map(([family]) =>
      [family, relationSelector.values(form.elements[`objects_${family}`])]));
    if (!current || OBJECT_FAMILIES.some(([family]) =>
      changed(current.object_ids?.[family], objects[family]))) result.object_ids = objects;
    result.type_prestation_id = optional("type_prestation_id");
    return result;
  }
  return { PROFILES, OBJECT_FAMILIES, profile, composition, readFields, loadChoices,
    fill, isDirty, payload };
});
