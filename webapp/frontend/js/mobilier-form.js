/* Champs métier communs des objets typés : Mobilier, Équipement et Réseau. */
(function (root, factory) {
  const relationSelector = typeof module === "object" && module.exports
    ? require("./relation-selector.js") : root.RelationSelector;
  const api = factory(relationSelector);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.MobilierForm = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (RelationSelector) {
  "use strict";

  const PROFILES = Object.freeze({
    mobilier: Object.freeze({
      classId: "mobilier", resource: "mobilier", observationResource: "mobilier",
      apiPath: "/api/mobilier", referenceName: "typesMobilier",
      singular: "Mobilier", plural: "Mobilier", newLabel: "Nouveau mobilier",
      validLabel: "Mobilier valide", domPrefix: "mobilier",
      composition: "entity-form",
      readPermission: "business.read", updatePermission: "business.update",
      relatedResources: ["troncons", "digues", "systemes", "desordres"],
    }),
    equipements_mesure: Object.freeze({
      classId: "equipements_mesure", resource: "equipements-mesure",
      observationResource: "equipements_mesure", apiPath: "/api/equipements-mesure",
      referenceName: "typesEquipementMesure", singular: "Équipement de mesure",
      plural: "Équipements de mesure", newLabel: "Nouvel équipement",
      validLabel: "Équipement valide", domPrefix: "equipement-mesure",
      composition: "entity-form",
      readPermission: "business.read", updatePermission: "business.update",
      relatedResources: ["troncons", "digues", "systemes", "desordres"],
    }),
    reseaux_techniques: Object.freeze({
      classId: "reseaux_techniques", resource: "reseaux-techniques",
      observationResource: "reseaux_techniques", apiPath: "/api/reseaux-techniques",
      referenceName: "typesReseauTechnique", singular: "Réseau technique",
      plural: "Réseaux techniques", newLabel: "Nouveau réseau",
      composition: "entity-form", validLabel: "Réseau valide", domPrefix: "reseau-technique",
      readPermission: "business.read", updatePermission: "business.update",
      relatedResources: ["troncons", "digues", "systemes", "desordres"],
    }),
  });

  function profile(classId) {
    const settings = PROFILES[classId];
    if (!settings) throw new Error("Catégorie métier inconnue.");
    return settings;
  }

  function composition(settings, { geometryEditable = true } = {}) {
    const View = typeof module === "object" && module.exports ? require("./entity-form-view.js") : globalThis.EntityFormView;
    return View.fromFields(settings, readFields(settings), {
      classification: ["type_id"], attachments: ["troncon_id", "desordre_ids"],
      overrides: { type_id: { required: true }, troncon_id: { kind: "relation" } },
      geometry: { types: settings.classId === "reseaux_techniques" ? ["Point", "LineString"] : ["Point"],
        required: settings.classId !== "reseaux_techniques", editable: geometryEditable, coordinates: geometryEditable, ...(geometryEditable ? { reperageParent: "troncon_id" } : {}),
        controls: settings.classId === "reseaux_techniques" ? "draft" : "point" },
    });
  }

  const readFields = settings => [
    ["designation", "Désignation", "text", true],
    ["type_id", "Type", "select", true, settings.referenceName, "type_libelle"],
    ["commentaire", "Commentaire", "textarea", true],
    ["date_debut", "Date de début", "date", true],
    ["troncon_id", "Tronçon", "select", true, null, "troncon_libelle"],
    ["desordre_ids", "Désordres associés", "relations", true],
    ["valid", settings.validLabel, "checkbox", true],
    ["localisation", "Localisation", "text", false],
  ];

  async function loadChoices(settings, fetchJson, referenceData, feature = null) {
    const selectedIds = feature?.properties?.desordre_ids || [];
    const [types, response, disorderChoices] = await Promise.all([
      referenceData.load(settings.referenceName, fetchJson),
      fetchJson("/api/troncons/options"),
      RelationSelector.loadDisorders(fetchJson, selectedIds),
    ]);
    if (!Array.isArray(response?.troncons)) throw new Error("Liste des tronçons invalide.");
    return { types, troncons: response.troncons, ...disorderChoices };
  }

  function fill(form, feature, choices, referenceData, relationSelector) {
    const properties = feature?.properties || {};
    form.elements.designation.value = properties.designation || "";
    form.elements.commentaire.value = properties.commentaire || "";
    form.elements.date_debut.value = properties.date_debut || "";
    form.elements.valid.checked = Boolean(properties.valid);
    referenceData.fillSelect(form.elements.type_id, choices.types, properties.type_id,
      "Choisir un type", properties.type_libelle);
    relationSelector.fill(form.elements.troncon_id, choices.troncons, properties.troncon_id ? [properties.troncon_id] : [], {
      multiple: false, emptyLabel: "Sans tronçon", include: row => row.valid !== false,
      preserveSelectedOutsideFilter: true,
      label: row => [row.digue_libelle, row.libelle].filter(Boolean).join(" — "),
      outsideLabel: label => `${label} (actuel, inactif)`,
    });
    relationSelector.fillDisorders(form.elements.desordre_ids, choices,
      properties.desordre_ids || []);
  }

  function initialValues(feature) {
    const properties = feature?.properties || {};
    return {
      designation: properties.designation || "",
      commentaire: properties.commentaire || "",
      date_debut: properties.date_debut || "",
      type_id: properties.type_id || "",
      troncon_id: properties.troncon_id || "",
      valid: Boolean(properties.valid),
    };
  }

  function currentValues(form) {
    return {
      designation: form.elements.designation.value,
      commentaire: form.elements.commentaire.value,
      date_debut: form.elements.date_debut.value,
      type_id: form.elements.type_id.value,
      troncon_id: form.elements.troncon_id.value,
      valid: form.elements.valid.checked,
    };
  }

  function isDirty(form, feature, relationSelector = RelationSelector) {
    const before = initialValues(feature);
    const after = currentValues(form);
    return Object.keys(before).some(key => before[key] !== after[key])
      || relationSelector.relationChanged(form.elements.desordre_ids,
        feature?.properties?.desordre_ids || []);
  }

  function payload(form, settings, relationSelector = RelationSelector, feature = null) {
    const values = currentValues(form);
    if (!values.type_id) throw new Error(`Le type de ${settings.singular} est obligatoire.`);
    const optional = value => value.trim() || null;
    const result = {
      designation: optional(values.designation), commentaire: optional(values.commentaire),
      date_debut: optional(values.date_debut), type_id: values.type_id,
      troncon_id: values.troncon_id || null, valid: values.valid,
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

  return { PROFILES, profile, composition, readFields, loadChoices, fill,
    initialValues, currentValues, isDirty, payload, displayValue };
});
