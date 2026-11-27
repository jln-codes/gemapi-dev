(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.ObservationForm = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const labels = Object.freeze({
    desordres: "Désordre", troncons: "Tronçon", ouvrages_hydrauliques: "Ouvrage hydraulique",
    equipements_mesure: "Équipement de mesure", cheminements: "Cheminement", mobilier: "Mobilier",
    reseaux_techniques: "Réseau technique", amenagements_hydrauliques: "AH réglementaire",
    zones_stockage: "Zone de stockage",
    vegetation: "Végétation", prestations: "Prestation",
    ouvrages_particuliers: "Ouvrage particulier",
    structures_amenagements_hydrauliques: "Structure d’aménagement hydraulique",
    organes_protection_collective: "Organe de protection collective",
    ouvrages_telecom_energie: "Ouvrage télécom/énergie", ouvrages_voirie: "Ouvrage de voirie",
    reseaux_hydrauliques: "Réseau hydraulique", structures_digue: "Structure de digue",
    profils_travers: "Profil en travers", objets_mesures_hydrauliques: "Mesure hydraulique",
    structures_protection: "Structure de protection", segments_hydrographiques: "Segment hydrographique",
    berges: "Berge", composants_berges: "Composant de berge",
    traces_positions_troncons: "Tracé de position",
    elements_morphologiques_lit: "Élément morphologique du lit",
  });
  const common = [
    ["date", "Date", "date", true],
    ["designation", "Désignation", "text", true],
    ["evolution", "Évolution", "textarea", true],
    ["suite_apporter_id", "Suite à donner", "select", true, "suitesApporter", "suite_apporter_libelle"],
    ["suite", "Suite à donner — complément", "textarea", true],
  ];
  const disorder = [
    ["urgence_id", "Urgence", "select", true, "urgences", "urgence_libelle"],
    ["nombre_desordres", "Nombre de désordres", "number", true],
  ];
  function parent(observation) {
    const value = observation?.parent;
    if (!value?.id || !Object.hasOwn(labels, value.resource)) {
      throw new Error("Rattachement de l’observation incohérent.");
    }
    return value;
  }
  function parentText(observation) {
    const value = parent(observation);
    const designation = value.designation && value.designation !== String(value.id)
      ? value.designation : "Désignation indisponible";
    return `${labels[value.resource]} — ${designation}`;
  }
  function title(observation) {
    return `Observation — ${observation.designation || String(observation.id).slice(0, 8)}`;
  }
  function fields(observation) {
    return [...common, ...(parent(observation).resource === "desordres" ? disorder : [])];
  }
  function composition(observation) {
    const View = typeof module === "object" && module.exports ? require("./entity-form-view.js") : globalThis.EntityFormView;
    const descriptor = View.fromFields({ classId: "observations", singular: "Observation" }, fields(observation), {
      classification: ["suite_apporter_id", "urgence_id", "nombre_desordres"],
      overrides: { nombre_desordres: { min: "0", max: "2147483647", step: "1" } },
    });
    descriptor.fields.attachments.push({name:"parent_label",label:"Rattachée à",kind:"output",readOnly:true});
    return descriptor;
  }
  function changes(controls, observation) {
    return Object.fromEntries([...controls].map(([key, control]) => [key,
      control.type === "number" ? (control.value === "" ? null : Number(control.value))
        : control.value.trim() || null,
    ]).filter(([key, value]) => value !== (typeof observation[key] === "string"
      ? observation[key].trim() || null : observation[key] ?? null)));
  }
  return { fields, composition, changes, parent, parentText, title };
});
