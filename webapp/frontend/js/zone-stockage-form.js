/* Formulaire commun Carte/Base de la Zone de stockage autonome. */
(function (root, factory) {
  const relations = typeof module === "object" && module.exports
    ? require("./relation-selector.js") : root.RelationSelector;
  const api = factory(relations);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ZoneStockageForm = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (RelationSelector) {
  "use strict";
  const FormView = typeof module === "object" && module.exports
    ? require("./entity-form-view.js") : globalThis.EntityFormView;
  const PROFILES = Object.freeze({ zones_stockage: Object.freeze({
    classId: "zones_stockage", composition: "entity-form", resource: "zones-stockage",
    observationResource: "zones_stockage", apiPath: "/api/zones-stockage",
    singular: "Zone de stockage", readPermission: "business.read",
    updatePermission: "business.update", relatedResources: ["amenagements-hydrauliques", "desordres"],
  }) });
  const TEXT = ["designation", "libelle", "commentaire", "date_debut", "date_fin"];
  const NUMBERS = ["capacite_stockage", "superficie", "profondeur_moyenne"];
  function profile(classId) {
    if (!PROFILES[classId]) throw new Error("Catégorie métier inconnue.");
    return PROFILES[classId];
  }
  function composition(settings = Object.values(PROFILES)[0], { geometryEditable = true } = {}) {
    return FormView.fromFields(settings, readFields(settings), {
      classification: ["type_id", "fonctionnement_id"],
      attachments: ["collectivite_competente_id", "amenagement_hydraulique_id", "reseau_hydraulique_ids"],
      overrides: {"valid": {"options": [["true", "Valide"], ["false", "Qualification incomplète"]]}},
      geometry: { types: ["Polygon"], required: true, editable: geometryEditable, controls: "draft" }, children: ["sous-objets"],
    });
  }
  function readFields() {
    return [["type_id", "Type", "select", true, "typesAmenagementHydraulique", "type_libelle"],
      ["designation", "Désignation", "text", true], ["libelle", "Libellé", "text", true],
      ["commentaire", "Commentaire", "textarea", true],
      ["date_debut", "Date de début", "date", true], ["date_fin", "Date de fin", "date", true],
      ["fonctionnement_id", "Fonctionnement", "select", true, "fonctionnementsAmenagementHydraulique", "fonctionnement_libelle"],
      ["capacite_stockage", "Capacité", "number", true],
      ["superficie", "Superficie", "number", true],
      ["profondeur_moyenne", "Profondeur moyenne", "number", true],
      ["collectivite_competente_id", "Collectivité compétente", "relations", true],
      ["amenagement_hydraulique_id", "AH réglementaire", "relations", true],
      ["reseau_hydraulique_ids", "Réseaux hydrauliques", "relations", true],
      ["desordre_ids", "Désordres", "relations", false],
      ["prestation_ids", "Prestations", "relations", false],
      ["vegetation_ids", "Végétations", "relations", false],
      ["gestionnaire_ids", "Gestionnaires", "relations", false],
      ["organes", "Organes de protection collective", "subobjects", false],
      ["ouvrages_hydrauliques", "Ouvrages hydrauliques", "subobjects", false],
      ["structures", "Structures", "subobjects", false],
      ["geometry_status", "Qualification géométrique", "text", false],
      ["valid", "Validité", "tristate", true]];
  }
  async function loadChoices(_settings, fetchJson, referenceData) {
    const [types, fonctionnements, links] = await Promise.all([
      referenceData.load("typesAmenagementHydraulique", fetchJson),
      referenceData.load("fonctionnementsAmenagementHydraulique", fetchJson),
      fetchJson("/api/zones-stockage/relations/options"),
    ]);
    if (!Array.isArray(links?.amenagements) || !Array.isArray(links?.reseaux)
        || !Array.isArray(links?.organismes)) {
      throw new Error("Relations de Zone invalides.");
    }
    return { types, fonctionnements, amenagements: links.amenagements,
      reseaux: links.reseaux, organismes: links.organismes };
  }
  function fill(form, feature, choices, referenceData, relationSelector = RelationSelector) {
    const p = feature?.properties || {};
    for (const key of TEXT) form.elements[key].value = p[key] ?? "";
    for (const key of NUMBERS) form.elements[key].value = p[key] ?? "";
    form.elements.valid.value = String(p.valid ?? true);
    referenceData.fillSelect(form.elements.type_id, choices.types, p.type_id, "Aucun type", p.type_libelle);
    referenceData.fillSelect(form.elements.fonctionnement_id, choices.fonctionnements,
      p.fonctionnement_id, "Aucun fonctionnement", p.fonctionnement_libelle);
    referenceData.fillSelect(form.elements.amenagement_hydraulique_id, choices.amenagements,
      p.amenagement_hydraulique_id, "Aucun AH", p.amenagement_hydraulique_libelle);
    referenceData.fillSelect(form.elements.collectivite_competente_id, choices.organismes,
      p.collectivite_competente_id, "Aucune collectivité", p.collectivite_competente_libelle);
    relationSelector.fill(form.elements.reseau_hydraulique_ids, choices.reseaux,
      p.reseau_hydraulique_ids || [], { multiple: true, preserveSelectedOutsideFilter: true,
        label: row => row.libelle });
  }
  function isDirty(form, feature, relationSelector = RelationSelector) {
    const p = feature?.properties || {};
    return [...TEXT, ...NUMBERS, "type_id", "fonctionnement_id", "amenagement_hydraulique_id",
      "collectivite_competente_id"]
      .some(key => String(p[key] ?? "") !== form.elements[key].value)
      || String(p.valid ?? true) !== form.elements.valid.value
      || (p.reseau_hydraulique_ids || []).map(String).sort().join(",")
        !== relationSelector.values(form.elements.reseau_hydraulique_ids).sort().join(",");
  }
  function payload(form, _settings, relationSelector = RelationSelector, feature = null) {
    const optional = key => form.elements[key].value.trim() || null;
    const result = {
      ...Object.fromEntries(TEXT.map(key => [key, optional(key)])),
      ...Object.fromEntries(NUMBERS.map(key => [key,
        form.elements[key].value === "" ? null : Number(form.elements[key].value)])),
      type_id: optional("type_id"), fonctionnement_id: optional("fonctionnement_id"),
      collectivite_competente_id: optional("collectivite_competente_id"),
      amenagement_hydraulique_id: optional("amenagement_hydraulique_id"),
      reseau_hydraulique_ids: relationSelector.values(form.elements.reseau_hydraulique_ids),
      valid: form.elements.valid.value === "true",
    };
    if (feature?.properties?.id) for (const key of ['type_id', 'fonctionnement_id']) {
      if (result[key] === (feature.properties[key] ?? null)) delete result[key];
    }
    return result;
  }
  return { PROFILES, profile, composition, readFields, loadChoices, fill, isDirty, payload };
});
