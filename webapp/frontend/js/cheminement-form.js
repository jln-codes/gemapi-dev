/* Champs et relations Cheminement partagés par Carte et Base. */
(function (root, factory) {
  const relationSelector = typeof module === "object" && module.exports
    ? require("./relation-selector.js") : root.RelationSelector;
  const api = factory(relationSelector);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.CheminementForm = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (RelationSelector) {
  "use strict";
  const FormView = typeof module === "object" && module.exports
    ? require("./entity-form-view.js") : globalThis.EntityFormView;

  const PROFILES = Object.freeze({ cheminements: Object.freeze({
    classId: "cheminements", composition: "entity-form", resource: "cheminements", observationResource: "cheminements",
    apiPath: "/api/cheminements", singular: "Cheminement",
    readPermission: "business.read", updatePermission: "business.update",
    relatedResources: ["troncons", "digues", "systemes", "desordres"],
  }) });
  const REFERENCES = [
    ["materiau_id", "Matériau", "materiauxCheminement"],
    ["revetement_id", "Revêtement", "revetementsCheminement"],
    ["usage_id", "Usage", "usagesCheminement"],
    ["type_voie_id", "Type de voie", "typesVoie"],
    ["position_id", "Position", "positions"],
    ["cote_id", "Côté", "cotes"],
    ["securite_id", "Sécurité", "securites"],
    ["orientation_id", "Orientation", "orientationsOuvrage"],
    ["position_haut_id", "Position haute", "positions"],
    ["position_bas_id", "Position basse", "positions"],
    ["revetement_haut_id", "Revêtement haut", "revetementsCheminement"],
    ["revetement_bas_id", "Revêtement bas", "revetementsCheminement"],
    ["source_id", "Source d’information", "sources"],
  ];
  const SCALARS = ["designation", "libelle", "commentaire", "date_debut", "date_fin",
    "largeur", "dimension_horizontale", "dimension_verticale", "numero_secteur",
    "statut_acces", "type_cheminement_id", ...REFERENCES.map(([name]) => name)];
  const LISTS = ["troncon_ids", "desordre_ids"];

  function profile(classId) {
    if (!PROFILES[classId]) throw new Error("Catégorie métier inconnue.");
    return PROFILES[classId];
  }
  function composition(settings = Object.values(PROFILES)[0], { geometryEditable = true } = {}) {
    return FormView.fromFields(settings, readFields(settings), {
      classification: ["type_cheminement_id", "materiau_id", "revetement_id", "usage_id", "type_voie_id", "position_id", "cote_id", "securite_id", "orientation_id", "position_haut_id", "position_bas_id", "revetement_haut_id", "revetement_bas_id", "source_id", "statut_acces"],
      attachments: ["troncon_ids", "desordre_ids"],
      overrides: {"type_cheminement_id": {"required": true}, "numero_secteur": {"step": "1"}, "statut_acces": {"kind": "select", "options": [["", "Non renseigné"], ["PUBLIC", "Public"], ["PRIVE", "Privé"]]}},
      geometry: { types: ["LineString", "Polygon"], required: true, editable: geometryEditable, coordinates: geometryEditable, ...(geometryEditable ? { reperageParent: "troncon_ids" } : {}), controls: "draft" }, children: [],
    });
  }
  function readFields() {
    return [
      ["type_cheminement_id", "Type", "select", true, "typesCheminement", "type_libelle"],
      ["designation", "Désignation", "text", true], ["libelle", "Libellé", "text", true],
      ["commentaire", "Commentaire", "textarea", true],
      ["date_debut", "Date de début", "date", true], ["date_fin", "Date de fin", "date", true],
      ["largeur", "Largeur (m)", "number", true],
      ["dimension_horizontale", "Dimension horizontale (m)", "number", true],
      ["dimension_verticale", "Dimension verticale (m)", "number", true],
      ["numero_secteur", "Numéro de secteur", "number", true],
      ["statut_acces", "Statut d’accès", "text", true],
      ...REFERENCES.map(([name, label, catalog]) => [name, label, "select", true, catalog]),
      ["troncon_ids", "Tronçons liés", "relations", true],
      ["desordre_ids", "Désordres associés", "relations", true],
      ["valid", "Cheminement valide", "checkbox", true],
      ["localisation", "Localisation", "text", false],
    ];
  }
  async function loadChoices(_settings, fetchJson, referenceData, feature = null) {
    const selectedIds = feature?.properties?.desordre_ids || [];
    const [types, troncons, selectedDesordres, referenceChoices] = await Promise.all([
      referenceData.load("typesCheminement", fetchJson), fetchJson("/api/troncons/options"),
      RelationSelector.loadSelected(fetchJson, selectedIds),
      Promise.all(REFERENCES.map(async ([name, , catalog]) =>
        [name, await referenceData.load(catalog, fetchJson)])),
    ]);
    if (!Array.isArray(troncons?.troncons) || !Array.isArray(selectedDesordres?.items)) {
      throw new Error("Relations Cheminement indisponibles.");
    }
    const missing = new Set((selectedDesordres.missing_ids || []).map(String));
    const desordres = [...selectedDesordres.items, ...selectedIds.filter(id => missing.has(String(id)))
      .map(id => ({ id: String(id), label: `Désordre introuvable — ${id}`, missing: true }))];
    return { types, troncons: troncons.troncons, desordres,
      referenceChoices: Object.fromEntries(referenceChoices), fetchJson };
  }
  function fill(form, feature, choices, referenceData, relationSelector) {
    const p = feature?.properties || {};
    for (const key of SCALARS.filter(key => key !== "type_cheminement_id"
      && !REFERENCES.some(([name]) => name === key))) {
      form.elements[key].value = p[key] ?? "";
    }
    form.elements.valid.checked = Boolean(p.valid);
    referenceData.fillSelect(form.elements.type_cheminement_id, choices.types,
      p.type_cheminement_id, "Choisir un type", p.type_libelle);
    for (const [name] of REFERENCES) {
      referenceData.fillSelect(form.elements[name], choices.referenceChoices[name],
        p[name], "Non renseigné");
    }
    relationSelector.fill(form.elements.troncon_ids, choices.troncons, p.troncon_ids || [], {
      multiple: true, include: row => row.valid !== false, preserveSelectedOutsideFilter: true,
      label: row => [row.digue_libelle, row.libelle].filter(Boolean).join(" — "),
      outsideLabel: label => `${label} (actuel, inactif)`,
    });
    relationSelector.fill(form.elements.desordre_ids, choices.desordres, p.desordre_ids || [], {
      multiple: true, preserveSelectedOutsideFilter: true,
      label: row => row.label || row.libelle || row.id,
      outsideLabel: label => label,
    });
    relationSelector.attachSearch(form.elements.desordre_ids, {
      fetchJson: choices.fetchJson,
      searchLabel: "Rechercher un Désordre à associer",
      selectedLabel: "Désordres associés",
    });
  }
  function isDirty(form, feature, relationSelector) {
    const p = feature?.properties || {};
    return SCALARS.some(key => String(p[key] ?? "") !== form.elements[key].value)
      || Boolean(p.valid) !== form.elements.valid.checked
      || LISTS.some(key => (p[key] || []).map(String).sort().join(",")
        !== relationSelector.values(form.elements[key]).sort().join(","));
  }
  function payload(form, _settings, relationSelector, feature = null) {
    const optional = key => form.elements[key].value.trim() || null;
    if (!form.elements.type_cheminement_id.value) throw new Error("Le type de Cheminement est obligatoire.");
    const number = key => optional(key) === null ? null : Number(optional(key));
    const result = {
      designation: optional("designation"), libelle: optional("libelle"),
      commentaire: optional("commentaire"), date_debut: optional("date_debut"),
      date_fin: optional("date_fin"), type_cheminement_id: form.elements.type_cheminement_id.value,
      largeur: number("largeur"), dimension_horizontale: number("dimension_horizontale"),
      dimension_verticale: number("dimension_verticale"), numero_secteur: number("numero_secteur"),
      statut_acces: optional("statut_acces"),
      ...Object.fromEntries(REFERENCES.map(([name]) => [name, optional(name)])),
      troncon_ids: relationSelector.values(form.elements.troncon_ids),
      desordre_ids: relationSelector.values(form.elements.desordre_ids), valid: form.elements.valid.checked,
    };
    if (feature?.properties?.id) for (const key of ['type_cheminement_id', ...REFERENCES.map(([name]) => name), 'statut_acces']) {
      if (result[key] === (feature.properties[key] ?? null)) delete result[key];
    }
    return result;
  }
  function displayValue(field, values, form) {
    if (field[2] !== "relations") return null;
    const key = field[0];
    const choices = form?.querySelector(`[name="${key}"]`);
    return choices ? [...choices.selectedOptions].map(option => option.textContent).join(" · ") :
      (values[key] || []).join(" · ");
  }
  return { PROFILES, profile, composition, readFields, loadChoices, fill, isDirty, payload, displayValue };
});
