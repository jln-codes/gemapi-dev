/* Champs de l'AH réglementaire partagés par Carte et Base. */
(function (root, factory) {
  const relationSelector = typeof module === "object" && module.exports
    ? require("./relation-selector.js") : root.RelationSelector;
  const api = factory(relationSelector);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.AmenagementHydrauliqueForm = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (RelationSelector) {
  "use strict";
  const FormView = typeof module === "object" && module.exports
    ? require("./entity-form-view.js") : globalThis.EntityFormView;
  const PROFILES = Object.freeze({ amenagements_hydrauliques: Object.freeze({
    classId: "amenagements_hydrauliques", composition: "entity-form", resource: "amenagements-hydrauliques",
    observationResource: "amenagements_hydrauliques", apiPath: "/api/amenagements-hydrauliques",
    singular: "AH réglementaire", readPermission: "business.read",
    updatePermission: "business.update", relatedResources: ["zones-stockage", "troncons", "digues", "systemes", "desordres"],
  }) });
  const observedTronconVersions = new WeakMap();
  const SCALARS = ["designation", "libelle", "commentaire", "date_debut", "date_fin", "valid"];
  function profile(classId) {
    if (!PROFILES[classId]) throw new Error("Catégorie métier inconnue.");
    return PROFILES[classId];
  }
  function composition(settings = Object.values(PROFILES)[0], { geometryEditable = true } = {}) {
    return FormView.fromFields(settings, readFields(settings), {
      classification: [],
      attachments: ["troncon_ids", "zone_ids", "desordre_ids"],
      overrides: { valid: { options: [["", "Non renseignée"], ["true", "Valide"], ["false", "Invalide"]] } },
      geometry: null, children: ["sous-objets"],
    });
  }
  function readFields() {
    return [["designation", "Désignation", "text", true], ["libelle", "Libellé", "text", true],
      ["commentaire", "Commentaire", "textarea", true],
      ["date_debut", "Date de début", "date", true], ["date_fin", "Date de fin", "date", true],
      ["troncon_ids", "Tronçons liés", "relations", true],
      ["zone_ids", "Zones de stockage", "relations", true],
      ["desordre_ids", "Désordres associés", "relations", true],
      ["valid", "Validité", "tristate", true],
      ["zones_stockage", "Zones associées", "subobjects", false],
      ["troncons", "Tronçons associés", "unified_troncons", false]];
  }
  async function loadChoices(_settings, fetchJson, referenceData, feature = null) {
    const selectedIds = feature?.properties?.desordre_ids || [];
    const [response, disorderChoices] = await Promise.all([
      fetchJson("/api/amenagements-hydrauliques/relations/options"),
      RelationSelector.loadDisorders(fetchJson, selectedIds),
    ]);
    if (!Array.isArray(response?.troncons)) throw new Error("Liste des tronçons invalide.");
    if (!Array.isArray(response?.zones)) throw new Error("Liste des Zones invalide.");
    return { troncons: response.troncons, zones: response.zones, ...disorderChoices };
  }
  function fill(form, feature, choices, referenceData, relationSelector) {
    const p = feature?.properties || {};
    // Freeze the versions displayed when the form opens. Existing attachments
    // use the AH snapshot, even if the subsequent options request is newer.
    const versions = Object.fromEntries((choices.troncons || []).map(row => [String(row.id), row.version]));
    for (const row of p.troncons || []) versions[String(row.id)] = row.version;
    observedTronconVersions.set(form, versions);
    for (const key of SCALARS) {
      form.elements[key].value = key === "valid" && p.valid != null ? String(p.valid) : (p[key] ?? "");
    }
    relationSelector.fill(form.elements.troncon_ids, choices.troncons, p.troncon_ids || [], {
      multiple: true, preserveSelectedOutsideFilter: true,
      label: row => [row.digue_libelle, row.libelle].filter(Boolean).join(" — "),
      outsideLabel: label => `${label} (actuel, inactif)`,
    });
    relationSelector.fill(form.elements.zone_ids, choices.zones, p.zone_ids || [], {
      multiple: true, preserveSelectedOutsideFilter: true,
      label: row => row.libelle,
    });
    relationSelector.fillDisorders(form.elements.desordre_ids, choices, p.desordre_ids || []);
  }
  function isDirty(form, feature, relationSelector) {
    const p = feature?.properties || {};
    return SCALARS.some(key => String(p[key] ?? "") !== form.elements[key].value)
      || (p.troncon_ids || []).map(String).sort().join(",")
        !== relationSelector.values(form.elements.troncon_ids).sort().join(",")
      || (p.zone_ids || []).map(String).sort().join(",")
        !== relationSelector.values(form.elements.zone_ids).sort().join(",")
      || relationSelector.relationChanged(form.elements.desordre_ids, p.desordre_ids || []);
  }
  function payload(form, _settings, relationSelector = RelationSelector, feature = null) {
    const optional = key => form.elements[key].value.trim() || null;
    const result = {
      designation: optional("designation"),
      libelle: optional("libelle"), commentaire: optional("commentaire"),
      date_debut: optional("date_debut"), date_fin: optional("date_fin"),
      troncon_ids: relationSelector.values(form.elements.troncon_ids),
      troncon_versions: { ...(observedTronconVersions.get(form) || {}) },
      zone_ids: relationSelector.values(form.elements.zone_ids),
      valid: form.elements.valid.value === "" ? null : form.elements.valid.value === "true",
    };
    return relationSelector.assignChanged(result, "desordre_ids", form.elements.desordre_ids,
      feature?.properties?.desordre_ids || [], { force: !feature?.properties?.id });
  }
  function displayValue(field, values, form) {
    if (field[0] !== "desordre_ids") return null;
    const select = form?.elements?.desordre_ids;
    return select ? [...select.selectedOptions].map(option => option.textContent).join(" · ")
      : (values.desordre_ids || []).join(" · ");
  }
  return { PROFILES, profile, composition, readFields, loadChoices, fill, isDirty, payload,
    displayValue };
});
