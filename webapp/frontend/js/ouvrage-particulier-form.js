/* Champs métier Ouvrage particulier partagés par Carte et Base. */
(function (root, factory) {
  const relationSelector = typeof module === "object" && module.exports
    ? require("./relation-selector.js") : root.RelationSelector;
  const api = factory(relationSelector);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.OuvrageParticulierForm = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (RelationSelector) {
  "use strict";
  const FormView = typeof module === "object" && module.exports
    ? require("./entity-form-view.js") : globalThis.EntityFormView;

  const PROFILES = Object.freeze({
    ouvrages_particuliers: Object.freeze({
      classId: "ouvrages_particuliers", composition: "entity-form", resource: "ouvrages-particuliers",
      observationResource: "ouvrages_particuliers", apiPath: "/api/ouvrages-particuliers",
      singular: "Ouvrage particulier", plural: "Ouvrages particuliers",
      validLabel: "Ouvrage valide",
      readPermission: "business.read", updatePermission: "business.update",
      relatedResources: ["troncons", "digues", "systemes", "desordres"],
    }),
  });

  function profile(classId) {
    const settings = PROFILES[classId];
    if (!settings) throw new Error("Catégorie métier inconnue.");
    return settings;
  }

  function composition(settings = Object.values(PROFILES)[0], { geometryEditable = true } = {}) {
    return FormView.fromFields(settings, readFields(settings), {
      classification: ["type_id"],
      attachments: ["troncon_id", "desordre_ids"],
      overrides: {},
      geometry: { types: ["Point", "LineString", "Polygon"], required: false, editable: geometryEditable, coordinates: geometryEditable, ...(geometryEditable ? { reperageParent: "troncon_id" } : {}), controls: "draft" }, children: [],
    });
  }
  const EDIT_KEYS = Object.freeze([
    "type_id", "designation", "libelle", "commentaire", "date_debut", "date_fin", "troncon_id", "valid",
  ]);

  function readFields() {
    return [
      ["designation", "Désignation", "text", true],
      ["type_id", "Type", "select", true, null, "type_libelle"],
      ["libelle", "Libellé", "text", true],
      ["commentaire", "Commentaire", "textarea", true],
      ["date_debut", "Date de début", "date", true],
      ["date_fin", "Date de fin", "date", true],
      ["troncon_id", "Tronçon", "select", true, null, "troncon_libelle"],
      ["desordre_ids", "Désordres associés", "relations", true],
      ["valid", "Ouvrage valide", "checkbox", true],
      ["localisation", "Localisation", "text", false],
    ];
  }

  async function loadChoices(_settings, fetchJson, _referenceData, feature = null) {
    const selectedIds = feature?.properties?.desordre_ids || [];
    const [response, selectedDesordres, catalog] = await Promise.all([
      fetchJson("/api/troncons/options"),
      RelationSelector.loadSelected(fetchJson, selectedIds),
      fetchJson("/api/referentiels/types-ouvrage-particulier"),
    ]);
    if (!Array.isArray(response?.troncons) || !Array.isArray(selectedDesordres?.items)
        || !Array.isArray(catalog?.references)) {
      throw new Error("Relations de l'Ouvrage particulier indisponibles.");
    }
    const missing = new Set((selectedDesordres.missing_ids || []).map(String));
    const desordres = [...selectedDesordres.items, ...selectedIds
      .filter(id => missing.has(String(id)))
      .map(id => ({ id: String(id), label: `Désordre introuvable — ${id}`, missing: true }))];
    return { troncons: response.troncons, desordres, types: catalog.references, fetchJson };
  }

  function fill(form, feature, choices, _referenceData, relationSelector) {
    const properties = feature?.properties || {};
    for (const key of EDIT_KEYS) {
      if (key === "troncon_id" || key === "type_id") continue;
      if (key === "valid") form.elements.valid.checked = Boolean(properties.valid);
      else form.elements[key].value = properties[key] || "";
    }
    relationSelector.fill(form.elements.troncon_id, choices.troncons,
      properties.troncon_id ? [properties.troncon_id] : [], {
      multiple: false, emptyLabel: "Sans tronçon", include: row => row.valid !== false,
      preserveSelectedOutsideFilter: true,
      label: row => [row.digue_libelle, row.libelle].filter(Boolean).join(" — "),
      outsideLabel: label => `${label} (actuel, inactif)`,
    });
    relationSelector.fill(form.elements.type_id, choices.types,
      properties.type_id ? [properties.type_id] : [], {
        multiple: false, emptyLabel: "Type non renseigné", include: row => row.valid !== false,
        preserveSelectedOutsideFilter: true,
        label: row => row.libelle || row.designation || row.id,
      });
    relationSelector.fill(form.elements.desordre_ids, choices.desordres,
      properties.desordre_ids || [], {
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

  function initialValues(feature) {
    const properties = feature?.properties || {};
    return Object.fromEntries(EDIT_KEYS.map(key =>
      [key, key === "valid" ? Boolean(properties.valid) : (properties[key] || "")]));
  }

  function currentValues(form) {
    return Object.fromEntries(EDIT_KEYS.map(key =>
      [key, key === "valid" ? form.elements.valid.checked : form.elements[key].value]));
  }

  function isDirty(form, feature, relationSelector = RelationSelector) {
    const before = initialValues(feature);
    const after = currentValues(form);
    return EDIT_KEYS.some(key => before[key] !== after[key])
      || (feature?.properties?.desordre_ids || []).map(String).sort().join(",")
        !== relationSelector.values(form.elements.desordre_ids).sort().join(",");
  }

  function payload(form, _settings, relationSelector = RelationSelector, feature = null) {
    const values = currentValues(form);
    const optional = value => value.trim() || null;
    const result = {
      designation: optional(values.designation), libelle: optional(values.libelle),
      type_id: optional(values.type_id),
      commentaire: optional(values.commentaire), date_debut: optional(values.date_debut),
      date_fin: optional(values.date_fin), troncon_id: values.troncon_id || null,
      desordre_ids: relationSelector.values(form.elements.desordre_ids),
      valid: values.valid,
    };
    if (feature?.properties?.id && result.type_id === (feature.properties.type_id ?? null)) delete result.type_id;
    return result;
  }

  function displayValue(field, values, form) {
    if (field[0] !== "desordre_ids") return null;
    const select = form?.elements?.desordre_ids;
    return select ? [...select.selectedOptions].map(option => option.textContent).join(" · ")
      : (values.desordre_ids || []).join(" · ");
  }

  return { PROFILES, profile, composition, readFields, loadChoices, fill,
    initialValues, currentValues, isDirty, payload, displayValue };
});
