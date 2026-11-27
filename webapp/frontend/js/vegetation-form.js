/* Contrat Végétation DB-2 partagé par Carte et Base. */
(function (root, factory) {
  const selector = typeof module === "object" && module.exports
    ? require("./relation-selector.js") : root.RelationSelector;
  const api = factory(selector);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.VegetationForm = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (RelationSelector) {
  "use strict";
  const FormView = typeof module === "object" && module.exports
    ? require("./entity-form-view.js") : globalThis.EntityFormView;
  const PROFILES = Object.freeze({ vegetation: Object.freeze({
    classId: "vegetation", composition: "entity-form", resource: "vegetation", observationResource: "vegetation",
    apiPath: "/api/vegetation", singular: "Végétation",
    readPermission: "business.read", updatePermission: "business.update",
    relatedResources: ["troncons", "digues", "systemes", "amenagements-hydrauliques", "desordres"],
  }) });
  const FIELDS = ["famille_vegetation", "disposition", "sous_type", "designation",
    "commentaire", "date_debut", "date_fin", "etat_sanitaire_id",
    "classe_hauteur_id", "classe_diametre_id", "espece_id",
    "type_cote_id", "type_position_id", "densite", "hauteur_m", "diametre_cm"];
  const CATALOGS = [
    ["etat_sanitaire_id", "etatsSanitairesVegetation", "etat_sanitaire_libelle", "Sans état sanitaire"],
    ["classe_hauteur_id", "classesHauteurVegetation", "classe_hauteur_libelle", "Sans classe de hauteur"],
    ["classe_diametre_id", "classesDiametreVegetation", "classe_diametre_libelle", "Sans classe de diamètre"],
    ["espece_id", "especesVegetation", "espece_libelle", "Sans espèce"],
    ["type_cote_id", "cotes", "type_cote_libelle", "Sans côté"],
    ["type_position_id", "positions", "type_position_libelle", "Sans position"],
  ];
  const LISTS = ["problematique_ids", "troncon_ids", "amenagement_hydraulique_ids", "desordre_ids"];
  const snapshots = new WeakMap();
  function profile(classId) {
    if (!PROFILES[classId]) throw new Error("Catégorie métier inconnue.");
    return PROFILES[classId];
  }
  function composition(settings = Object.values(PROFILES)[0], { geometryEditable = true } = {}) {
    return FormView.fromFields(settings, readFields(settings), {
      classification: ["famille_vegetation", "disposition", "sous_type", "invasif", "problematique_ids", "espece_id", "etat_sanitaire_id", "classe_hauteur_id", "classe_diametre_id", "hauteur_m", "diametre_cm", "densite", "type_cote_id", "type_position_id", "contact_eau"],
      attachments: ["troncon_ids", "amenagement_hydraulique_ids", "desordre_ids"],
      overrides: {"famille_vegetation": {"kind": "select", "required": true, "options": [["", "Choisir"], ["HERBACEES", "Herbacées"], ["BUISSONS_ARBUSTES", "Buissons et arbustes"], ["ARBRES", "Arbres"]]}, "disposition": {"kind": "select", "required": true, "options": [["", "Choisir"], ["ISOLE", "Isolé"], ["LIGNE", "Ligne"], ["SURFACE", "Surface"]]}, "sous_type": {"kind": "select", "context": "sous_type", "help": "Le sous-type est conservé pendant les changements de famille. Pour enregistrer une autre famille, retirez-le explicitement.", "options": [["", "Choisir"], ["ARBRE_SUR_PIED", "Arbre sur pied"], ["SOUCHE", "Souche"]]}, "invasif": {"value": false}},
      geometry: { types: ["Point", "LineString", "Polygon"], required: true, editable: geometryEditable, coordinates: geometryEditable, ...(geometryEditable ? { reperageParent: "troncon_ids" } : {}), controls: "draft" }, children: [],
    });
  }
  function readFields() {
    return [["famille_vegetation", "Famille", "text", true],
      ["disposition", "Disposition", "text", true],
      ["sous_type", "Sous-type", "text", true], ["invasif", "Invasif", "checkbox", true],
      ["problematique_ids", "Problématiques", "relations", true],
      ["espece_id", "Espèce", "select", true, "especesVegetation", "espece_libelle"],
      ["etat_sanitaire_id", "État sanitaire", "select", true, "etatsSanitairesVegetation", "etat_sanitaire_libelle"],
      ["classe_hauteur_id", "Classe de hauteur", "select", true, "classesHauteurVegetation", "classe_hauteur_libelle"],
      ["classe_diametre_id", "Classe de diamètre", "select", true, "classesDiametreVegetation", "classe_diametre_libelle"],
      ["hauteur_m", "Hauteur (m)", "number", true], ["diametre_cm", "Diamètre (cm)", "number", true],
      ["densite", "Densité", "number", true],
      ["type_cote_id", "Côté", "select", true, "cotes", "type_cote_libelle"],
      ["type_position_id", "Position", "select", true, "positions", "type_position_libelle"],
      ["contact_eau", "Contact avec l'eau", "tristate", true],
      ["designation", "Désignation", "text", true], ["commentaire", "Commentaire", "textarea", true],
      ["date_debut", "Date de début", "date", true], ["date_fin", "Date de fin", "date", true],
      ["troncon_ids", "Tronçons", "relations", true],
      ["amenagement_hydraulique_ids", "Aménagements hydrauliques", "relations", true],
      ["desordre_ids", "Désordres associés", "relations", true],
      ["valid", "Végétation valide", "checkbox", true],
      ["localisation", "Localisation", "text", false]];
  }
  function selectedIds(form, name, relationSelector) {
    return relationSelector.values(form.elements[name]).sort();
  }
  async function loadChoices(_settings, fetchJson, referenceData, feature = null) {
    const selected = feature?.properties?.desordre_ids || [];
    const [relations, problems, disorders, ...catalogs] = await Promise.all([
      fetchJson("/api/vegetation/relations/options"),
      referenceData.load("problematiquesVegetation", fetchJson),
      RelationSelector.loadDisorders(fetchJson, selected),
      ...CATALOGS.map(([, catalog]) => referenceData.load(catalog, fetchJson)),
    ]);
    if (!Array.isArray(relations?.troncons) || !Array.isArray(relations?.amenagements_hydrauliques)) {
      throw new Error("Relations Végétation indisponibles.");
    }
    const references = { problematique_ids: problems };
    CATALOGS.forEach(([name], index) => { references[name] = catalogs[index]; });
    return { references, troncons: relations.troncons,
      amenagements: relations.amenagements_hydrauliques, ...disorders };
  }
  function fillLinked(form, name, rows, ids, relationSelector) {
    relationSelector.fill(form.elements[name], rows, ids || [], {
      multiple: true, include: row => row.valid !== false, preserveSelectedOutsideFilter: true,
      label: row => row.libelle || row.id, outsideLabel: label => label + " (actuel, inactif)",
    });
  }
  function rawValues(form, selectors) {
    return {...Object.fromEntries(FIELDS.map(key => [key, String(form.elements[key].value)])),
      invasif: form.elements.invasif.checked, contact_eau: form.elements.contact_eau.value,
      valid: form.elements.valid.checked,
      ...Object.fromEntries(LISTS.map(key => [key, selectedIds(form,key,selectors)]))};
  }
  function updateContextualFields(form, containers) {
    const tree = form.elements.famille_vegetation.value === "ARBRES";
    if (containers?.sous_type) containers.sous_type.hidden = !tree && !form.elements.sous_type.value;
    const snapshot = snapshots.get(form);
    for (const key of ['famille_vegetation','disposition']) {
      form.elements[key].required = !snapshot?.editing || form.elements.valid.checked || Boolean(snapshot.values[key]);
    }
    form.elements.sous_type.required = tree;
  }
  function fill(form, feature, choices, referenceData, relationSelector, _geometryType, containers) {
    const p = feature?.properties || {};
    for (const name of FIELDS.filter(name => !CATALOGS.some(([key]) => key === name))) {
      form.elements[name].value = p[name] ?? "";
    }
    form.elements.invasif.checked = Boolean(p.invasif);
    form.elements.contact_eau.value = p.contact_eau == null ? "" : String(p.contact_eau);
    form.elements.valid.checked = p.valid !== false;
    for (const [name, , label, empty] of CATALOGS) {
      referenceData.fillSelect(form.elements[name], choices.references[name] || [], p[name], empty, p[label]);
    }
    fillLinked(form, "problematique_ids", choices.references.problematique_ids, p.problematique_ids, relationSelector);
    fillLinked(form, "troncon_ids", choices.troncons, p.troncon_ids, relationSelector);
    fillLinked(form, "amenagement_hydraulique_ids", choices.amenagements,
      p.amenagement_hydraulique_ids, relationSelector);
    relationSelector.fillDisorders(form.elements.desordre_ids, choices, p.desordre_ids || []);
    snapshots.set(form, {editing: Boolean(p.id), values: rawValues(form,relationSelector)});
    updateContextualFields(form, containers, relationSelector);
  }
  function isDirty(form, feature, relationSelector) {
    const p = feature?.properties || {};
    return FIELDS.some(key => String(p[key] ?? "") !== String(form.elements[key].value))
      || Boolean(p.invasif) !== form.elements.invasif.checked
      || String(p.contact_eau ?? "") !== form.elements.contact_eau.value
      || (p.valid !== false) !== form.elements.valid.checked
      || LISTS.some(key => (p[key] || []).map(String).sort().join(",")
        !== selectedIds(form, key, relationSelector).join(","));
  }
  function payload(form, _settings, relationSelector = RelationSelector, feature = null) {
    const optional = name => String(form.elements[name].value).trim() || null;
    const family = optional("famille_vegetation");
    const disposition = optional("disposition");
    const subType = optional("sous_type");
    const initial = snapshots.get(form), previous = feature?.properties;
    const editing = Boolean(previous?.id || initial?.editing);
    const active = form.elements.valid.checked;
    if ((!editing || active) && (!family || !disposition)) throw new Error("La famille et la disposition sont obligatoires pour créer ou valider une Végétation.");
    const result = Object.fromEntries(FIELDS.map(name => [name, optional(name)]));
    for (const name of ["densite", "hauteur_m", "diametre_cm"]) {
      result[name] = result[name] === null ? null : Number(result[name]);
    }
    result.invasif = form.elements.invasif.checked;
    result.contact_eau = optional("contact_eau") === null ? null : optional("contact_eau") === "true";
    Object.assign(result, {
      problematique_ids: selectedIds(form, "problematique_ids", relationSelector),
      troncon_ids: selectedIds(form, "troncon_ids", relationSelector),
      amenagement_hydraulique_ids: selectedIds(form, "amenagement_hydraulique_ids", relationSelector),
      valid: form.elements.valid.checked,
    });
    relationSelector.assignChanged(result, "desordre_ids", form.elements.desordre_ids,
      feature?.properties?.desordre_ids || [], { force: !feature?.properties?.id });
    if (editing) {
      const raw = rawValues(form,relationSelector);
      for (const key of Object.keys(result)) {
        const unchanged = initial ? JSON.stringify(raw[key]) === JSON.stringify(initial.values[key])
          : JSON.stringify(result[key]) === JSON.stringify(previous?.[key] ?? null);
        if (unchanged) delete result[key];
      }
      for (const key of ['famille_vegetation','disposition']) {
        if (Object.hasOwn(result,key) && result[key] === null) throw new Error('La famille et la disposition existantes ne peuvent pas être effacées.');
      }
    }
    if (!editing || active || Object.hasOwn(result,'famille_vegetation') || Object.hasOwn(result,'sous_type')) {
      if (!family && subType) throw new Error("Précisez la famille avant de modifier le sous-type.");
      if (family === 'ARBRES' && !subType) throw new Error("Précisez le sous-type de l'Arbre.");
      if (family && family !== 'ARBRES' && subType) throw new Error("Pour enregistrer cette famille, retirez explicitement le sous-type de l'Arbre ; sa valeur reste conservée dans le brouillon.");
    }
    return result;
  }
  function displayValue(field, values, form) {
    if (field[0] !== "desordre_ids") return null;
    const select = form?.elements?.desordre_ids;
    return select ? [...select.selectedOptions].map(option => option.textContent).join(" · ")
      : (values.desordre_ids || []).join(" · ");
  }
  return { PROFILES, profile, composition, readFields, loadChoices, fill,
    updateContextualFields, isDirty, payload, displayValue };
});
