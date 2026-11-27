/* Contrats de consultation propres à Base. Aucun descripteur de formulaire. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.BaseDetailRegistry = api;
})(globalThis, function () {
  "use strict";
  const field = (key, label, format, section, extra = {}) => Object.freeze({
    key, label, format, section, unit: null, importance: section === "identification" ? "primary" : "secondary",
    applicability: "always", ...extra,
  });
  const definitions = {
    "mobilier": {
      label: "Mobilier", endpoint: "/api/mobilier", mutationResource: "mobilier",
      observations: "mobilier", map: true, fields: [
        field("designation","Désignation","text","identification"),
        field("type_id","Type","reference","classification",{"labelKey":"type_libelle","catalog":"typesMobilier"}),
        field("commentaire","Commentaire","text","additional"),
        field("date_debut","Date de début","date","identification"),
        field("troncon_id","Tronçon","relations","attachments",{"resource":"troncons","source":"troncon_id","labelKey":"troncon_libelle"}),
        field("desordre_ids","Désordres associés","relations","relations",{"resource":"desordres","source":"desordre_ids"}),
        field("valid","Mobilier valide","boolean","identification"),
        field("localisation","Localisation","text","geometry"),
      ],
    },
    "equipements_mesure": {
      label: "Équipement de mesure", endpoint: "/api/equipements-mesure", mutationResource: "equipements-mesure",
      observations: "equipements_mesure", map: true, fields: [
        field("designation","Désignation","text","identification"),
        field("type_id","Type","reference","classification",{"labelKey":"type_libelle","catalog":"typesEquipementMesure"}),
        field("commentaire","Commentaire","text","additional"),
        field("date_debut","Date de début","date","identification"),
        field("troncon_id","Tronçon","relations","attachments",{"resource":"troncons","source":"troncon_id","labelKey":"troncon_libelle"}),
        field("desordre_ids","Désordres associés","relations","relations",{"resource":"desordres","source":"desordre_ids"}),
        field("valid","Équipement valide","boolean","identification"),
        field("localisation","Localisation","text","geometry"),
      ],
    },
    "reseaux_techniques": {
      label: "Réseau technique", endpoint: "/api/reseaux-techniques", mutationResource: "reseaux-techniques",
      observations: "reseaux_techniques", map: true, fields: [
        field("designation","Désignation","text","identification"),
        field("type_id","Type","reference","classification",{"labelKey":"type_libelle","catalog":"typesReseauTechnique"}),
        field("commentaire","Commentaire","text","additional"),
        field("date_debut","Date de début","date","identification"),
        field("troncon_id","Tronçon","relations","attachments",{"resource":"troncons","source":"troncon_id","labelKey":"troncon_libelle"}),
        field("desordre_ids","Désordres associés","relations","relations",{"resource":"desordres","source":"desordre_ids"}),
        field("valid","Réseau valide","boolean","identification"),
        field("localisation","Localisation","text","geometry"),
      ],
    },
    "ouvrages_particuliers": {
      label: "Ouvrage particulier", endpoint: "/api/ouvrages-particuliers", mutationResource: "ouvrages-particuliers",
      observations: "ouvrages_particuliers", map: true, fields: [
        field("designation","Désignation","text","identification"),
        field("type_id","Type","reference","classification",{"labelKey":"type_libelle"}),
        field("libelle","Libellé","text","identification"),
        field("commentaire","Commentaire","text","additional"),
        field("date_debut","Date de début","date","identification"),
        field("date_fin","Date de fin","date","identification"),
        field("troncon_id","Tronçon","relations","attachments",{"resource":"troncons","source":"troncon_id","labelKey":"troncon_libelle"}),
        field("desordre_ids","Désordres associés","relations","relations",{"resource":"desordres","source":"desordre_ids"}),
        field("valid","Ouvrage valide","boolean","identification"),
        field("localisation","Localisation","text","geometry"),
      ],
    },
    "cheminements": {
      label: "Cheminement", endpoint: "/api/cheminements", mutationResource: "cheminements",
      observations: "cheminements", map: true, fields: [
        field("type_cheminement_id","Type","reference","classification",{"labelKey":"type_libelle","catalog":"typesCheminement"}),
        field("designation","Désignation","text","identification"),
        field("libelle","Libellé","text","identification"),
        field("commentaire","Commentaire","text","additional"),
        field("date_debut","Date de début","date","identification"),
        field("date_fin","Date de fin","date","identification"),
        field("largeur","Largeur","number","characteristics",{"unit":"m"}),
        field("dimension_horizontale","Dimension horizontale","number","characteristics",{"unit":"m"}),
        field("dimension_verticale","Dimension verticale","number","characteristics",{"unit":"m"}),
        field("numero_secteur","Numéro de secteur","number","characteristics"),
        field("statut_acces","Statut d’accès","text","classification"),
        field("materiau_id","Matériau","reference","classification",{"labelKey":"materiau_libelle","catalog":"materiauxCheminement"}),
        field("revetement_id","Revêtement","reference","classification",{"labelKey":"revetement_libelle","catalog":"revetementsCheminement"}),
        field("usage_id","Usage","reference","classification",{"labelKey":"usage_libelle","catalog":"usagesCheminement"}),
        field("type_voie_id","Type de voie","reference","classification",{"labelKey":"type_voie_libelle","catalog":"typesVoie"}),
        field("position_id","Position","reference","classification",{"labelKey":"position_libelle","catalog":"positions"}),
        field("cote_id","Côté","reference","classification",{"labelKey":"cote_libelle","catalog":"cotes"}),
        field("securite_id","Sécurité","reference","classification",{"labelKey":"securite_libelle","catalog":"securites"}),
        field("orientation_id","Orientation","reference","classification",{"labelKey":"orientation_libelle","catalog":"orientationsOuvrage"}),
        field("position_haut_id","Position haute","reference","classification",{"labelKey":"position_haut_libelle","catalog":"positions"}),
        field("position_bas_id","Position basse","reference","classification",{"labelKey":"position_bas_libelle","catalog":"positions"}),
        field("revetement_haut_id","Revêtement haut","reference","classification",{"labelKey":"revetement_haut_libelle","catalog":"revetementsCheminement"}),
        field("revetement_bas_id","Revêtement bas","reference","classification",{"labelKey":"revetement_bas_libelle","catalog":"revetementsCheminement"}),
        field("source_id","Source d’information","reference","classification",{"labelKey":"source_libelle","catalog":"sources"}),
        field("troncon_ids","Tronçons liés","relations","attachments",{"resource":"troncons","source":"troncon_ids"}),
        field("desordre_ids","Désordres associés","relations","relations",{"resource":"desordres","source":"desordre_ids"}),
        field("valid","Cheminement valide","boolean","identification"),
        field("localisation","Localisation","text","geometry"),
      ],
    },
    "vegetation": {
      label: "Végétation", endpoint: "/api/vegetation", mutationResource: "vegetation",
      observations: "vegetation", map: true, fields: [
        field("famille_vegetation","Famille","text","classification"),
        field("disposition","Disposition","text","classification"),
        field("sous_type","Sous-type","text","classification"),
        field("invasif","Invasif","boolean","classification"),
        field("problematique_ids","Problématiques","labels","classification",{"catalog":"problematiquesVegetation"}),
        field("espece_id","Espèce","reference","classification",{"labelKey":"espece_libelle","catalog":"especesVegetation"}),
        field("etat_sanitaire_id","État sanitaire","reference","classification",{"labelKey":"etat_sanitaire_libelle","catalog":"etatsSanitairesVegetation"}),
        field("classe_hauteur_id","Classe de hauteur","reference","classification",{"labelKey":"classe_hauteur_libelle","catalog":"classesHauteurVegetation"}),
        field("classe_diametre_id","Classe de diamètre","reference","classification",{"labelKey":"classe_diametre_libelle","catalog":"classesDiametreVegetation"}),
        field("hauteur_m","Hauteur","number","characteristics",{"unit":"m"}),
        field("diametre_cm","Diamètre","number","characteristics",{"unit":"cm"}),
        field("densite","Densité","number","characteristics"),
        field("type_cote_id","Côté","reference","classification",{"labelKey":"type_cote_libelle","catalog":"cotes"}),
        field("type_position_id","Position","reference","classification",{"labelKey":"type_position_libelle","catalog":"positions"}),
        field("contact_eau","Contact avec l'eau","boolean","characteristics"),
        field("designation","Désignation","text","identification"),
        field("commentaire","Commentaire","text","additional"),
        field("date_debut","Date de début","date","identification"),
        field("date_fin","Date de fin","date","identification"),
        field("troncon_ids","Tronçons","relations","attachments",{"resource":"troncons","source":"troncon_ids"}),
        field("amenagement_hydraulique_ids","Aménagements hydrauliques","relations","attachments",{"resource":"amenagements_hydrauliques","source":"amenagement_hydraulique_ids"}),
        field("desordre_ids","Désordres associés","relations","relations",{"resource":"desordres","source":"desordre_ids"}),
        field("valid","Végétation valide","boolean","identification"),
        field("localisation","Localisation","text","geometry"),
      ],
    },
    "amenagements_hydrauliques": {
      label: "AH réglementaire", endpoint: "/api/amenagements-hydrauliques", mutationResource: "amenagements-hydrauliques",
      observations: "amenagements_hydrauliques", map: true, fields: [
        field("designation","Désignation","text","identification"),
        field("libelle","Libellé","text","identification"),
        field("commentaire","Commentaire","text","additional"),
        field("date_debut","Date de début","date","identification"),
        field("date_fin","Date de fin","date","identification"),
        field("desordre_ids","Désordres associés","relations","relations",{"resource":"desordres","source":"desordre_ids"}),
        field("valid","Validité","boolean","identification"),
        field("zones_stockage","Zones associées","relations","relations",{"resource":"zones_stockage","source":"zones_stockage","idsSource":"zone_ids"}),
        field("troncons","Tronçons associés","relations","relations",{"resource":"troncons","source":"troncons","idsSource":"troncon_ids"}),
      ],
    },
    "zones_stockage": {
      label: "Zone de stockage", endpoint: "/api/zones-stockage", mutationResource: "zones-stockage",
      observations: "zones_stockage", map: true, fields: [
        field("type_id","Type","reference","classification",{"labelKey":"type_libelle","catalog":"typesAmenagementHydraulique"}),
        field("designation","Désignation","text","identification"),
        field("libelle","Libellé","text","identification"),
        field("commentaire","Commentaire","text","additional"),
        field("date_debut","Date de début","date","identification"),
        field("date_fin","Date de fin","date","identification"),
        field("fonctionnement_id","Fonctionnement","reference","classification",{"labelKey":"fonctionnement_libelle","catalog":"fonctionnementsAmenagementHydraulique"}),
        field("capacite_stockage","Capacité","number","characteristics"),
        field("superficie","Superficie","number","characteristics"),
        field("profondeur_moyenne","Profondeur moyenne","number","characteristics"),
        field("collectivite_competente_id","Collectivité compétente","relations","relations",{"resource":"organismes","source":"collectivite_competente_id","labelKey":"collectivite_competente_libelle"}),
        field("amenagement_hydraulique_id","AH réglementaire","relations","attachments",{"resource":"amenagements_hydrauliques","source":"amenagement_hydraulique_id","labelKey":"amenagement_hydraulique_libelle"}),
        field("reseau_hydraulique_ids","Réseaux hydrauliques","relations","relations",{"resource":"reseaux_hydrauliques","source":"reseau_hydraulique_ids"}),
        field("desordre_ids","Désordres","relations","relations",{"resource":"desordres","source":"desordre_ids"}),
        field("prestation_ids","Prestations","relations","relations",{"resource":"prestations","source":"prestation_ids"}),
        field("vegetation_ids","Végétations","relations","relations",{"resource":"vegetation","source":"vegetation_ids"}),
        field("gestionnaire_ids","Gestionnaires","relations","relations",{"resource":"organismes","source":"gestionnaire_ids"}),
        field("organes","Organes de protection collective","relations","relations",{"resource":"organes_protection_collective","source":"organes"}),
        field("ouvrages_hydrauliques","Ouvrages hydrauliques","relations","relations",{"resource":"ouvrages_hydrauliques","source":"ouvrages_hydrauliques"}),
        field("structures","Structures","relations","relations",{"resource":"structures_amenagements_hydrauliques","source":"structures"}),
        field("geometry_status","Qualification géométrique","text","technical"),
        field("valid","Validité","boolean","identification"),
      ],
    },
    "prestations": {
      label: "Prestation", endpoint: "/api/prestations", mutationResource: "prestations",
      observations: "prestations", map: true, fields: [
        field("type_prestation_id","Type","reference","classification",{"labelKey":"type_libelle","catalog":"typesPrestation"}),
        field("designation","Désignation","text","identification"),
        field("libelle","Libellé","text","identification"),
        field("commentaire","Commentaire","text","additional"),
        field("date_debut","Date de début","date","identification"),
        field("date_fin","Date de fin","date","identification"),
        field("cout_global","Coût global","number","characteristics"),
        field("cout_metre","Coût par mètre","number","characteristics"),
        field("realisation_interne","Réalisation interne","boolean","characteristics"),
        field("valid","Validité","boolean","identification"),
        field("troncon_ids","Tronçons","relations","attachments",{"resource":"troncons","source":"troncon_ids"}),
        field("intervenant_ids","Intervenants","relations","relations",{"resource":"contacts","source":"intervenant_ids"}),
        field("localisation","Localisation","text","geometry"),
        field("objects_desordres","Désordres","relations","relations",{"source":"object_ids.desordres","resource":"desordres"}),
        field("objects_ouvrages_hydrauliques","Ouvrages hydrauliques","relations","relations",{"source":"object_ids.ouvrages_hydrauliques","resource":"ouvrages_hydrauliques"}),
        field("objects_ouvrages_particuliers","Ouvrages particuliers","relations","relations",{"source":"object_ids.ouvrages_particuliers","resource":"ouvrages_particuliers"}),
        field("objects_mobilier","Mobilier","relations","relations",{"source":"object_ids.mobilier","resource":"mobilier"}),
        field("objects_equipements_mesure","Équipements de mesure","relations","relations",{"source":"object_ids.equipements_mesure","resource":"equipements_mesure"}),
        field("objects_reseaux_techniques","Réseaux techniques","relations","relations",{"source":"object_ids.reseaux_techniques","resource":"reseaux_techniques"}),
        field("objects_cheminements","Cheminements","relations","relations",{"source":"object_ids.cheminements","resource":"cheminements"}),
        field("objects_vegetation","Végétation","relations","relations",{"source":"object_ids.vegetation","resource":"vegetation"}),
        field("objects_amenagements_hydrauliques","Aménagements hydrauliques","relations","relations",{"source":"object_ids.amenagements_hydrauliques","resource":"amenagements_hydrauliques"}),
      ],
    },
    "troncons_lit": {
      label: "Tronçon de lit", endpoint: "/api/troncons-lit", mutationResource: "troncons-lit",
      observations: null, map: true, fields: [
        field("designation","Désignation","text","identification"),
        field("libelle","Libellé","text","identification"),
        field("commentaire","Commentaire","text","additional"),
        field("date_debut","Date de début","date","identification"),
        field("date_fin","Date de fin","date","identification"),
        field("digue_ids","Digues associées","relations","attachments",{"resource":"digues","source":"digue_ids"}),
        field("valid","Objet valide","boolean","identification"),
        field("localisation","Localisation","text","geometry"),
        field("geometry_diagnostics","Diagnostic géométrique","text","technical"),
        field("elements_morphologiques_lit","Éléments morphologiques","relations","relations",{"source":"elements_morphologiques_lit","resource":"elements_morphologiques_lit"}),
      ],
    },
    "elements_morphologiques_lit": {
      label: "Élément morphologique du lit", endpoint: "/api/elements-morphologiques-lit", mutationResource: "elements-morphologiques-lit",
      observations: null, map: true, fields: [
        field("type_morphologie","Sous-type","reference","classification",{"labelKey":"type_morphologie_libelle"}),
        field("designation","Désignation","text","identification"),
        field("libelle","Libellé","text","identification"),
        field("commentaire","Commentaire","text","additional"),
        field("date_debut","Date de début","date","identification"),
        field("date_fin","Date de fin","date","identification"),
        field("segment_id","Tronçon de lit","relations","attachments",{"resource":"troncons_lit","source":"segment_id","labelKey":"segment_libelle"}),
        field("longueur","Longueur","number","characteristics",{"unit":"m"}),
        field("largeur","Largeur","number","characteristics",{"unit":"m"}),
        field("rattache_berge","Rattaché à une berge","boolean","characteristics"),
        field("vegetalise","Végétalisé","boolean","characteristics"),
        field("type_zone_id","Type d'atterrissement","reference","classification",{"labelKey":"type_zone_libelle"}),
        field("valid","Objet valide","boolean","identification"),
        field("localisation","Localisation","text","geometry"),
        field("geometry_diagnostics","Diagnostic géométrique","text","technical"),
        field("pr_debut","PR de début conservé","text","geometry"),
        field("pr_fin","PR de fin conservé","text","geometry"),
      ],
    },
    "observations": {
      label: "Observation", endpoint: "/api/observations", mutationResource: "observations",
      observations: null, map: false, fields: [
        field("date","Date","date","identification"),
        field("designation","Désignation","text","identification"),
        field("evolution","Évolution","text","additional"),
        field("suite_apporter_id","Suite à donner","reference","classification",{"labelKey":"suite_apporter_libelle"}),
        field("suite","Suite à donner — complément","text","additional"),
        field("urgence_id","Urgence","reference","classification",{"labelKey":"urgence_libelle","applicability":"disorder-parent"}),
        field("nombre_desordres","Nombre de désordres","number","characteristics",{"applicability":"disorder-parent"}),
        field("parent","Rattachée à","relations","attachments",{"source":"parent"}),
      ],
    },
    "systemes": {
      label: "Système d’endiguement", endpoint: "/api/systemes-endiguement", mutationResource: "systemes",
      observations: null, map: true, fields: [
        field("libelle","Libellé","text","identification"),
        field("designation","Désignation","text","identification"),
        field("population_protegee","Population protégée","number","characteristics"),
        field("classement","Classement","text","classification"),
        field("niveau_protection","Niveau de protection","text","classification"),
        field("commentaire","Commentaire","text","additional"),
        field("valid","Objet valide","boolean","identification"),
        field("digues","Digues","relations","relations",{"source":"digues","resource":"digues"}),
      ],
    },
    "digues": {
      label: "Digue", endpoint: "/api/digues", mutationResource: "digues",
      observations: null, map: true, fields: [
        field("libelle","Libellé","text","identification"),
        field("designation","Désignation","text","identification"),
        field("commentaire","Commentaire","text","additional"),
        field("systeme_endiguement_libelle","Système d’endiguement","relations","attachments",{"resource":"systemes","source":"systeme_endiguement_id","labelKey":"systeme_endiguement_libelle"}),
        field("troncons","Tronçons physiques","relations","relations",{"resource":"troncons","source":"troncons"}),
        field("valid","Objet valide","boolean","identification"),
        field("troncons_lit","Tronçons de lit","relations","relations",{"source":"troncons_lit","resource":"troncons_lit"}),
      ],
    },
    "troncons": {
      label: "Tronçon", endpoint: "/api/troncons", mutationResource: "troncons",
      observations: "troncons", map: true, fields: [
        field("type_physique_id","Sous-type","reference","classification",{"labelKey":"type_physique_libelle"}),
        field("libelle","Libellé","text","identification"),
        field("designation","Désignation","text","identification"),
        field("commentaire","Commentaire","text","additional",{"applicability":"physical-profile"}),
        field("date_debut","Date de début","date","identification"),
        field("date_fin","Date de fin","date","identification"),
        field("cote_physique_id","Côté physique","reference","classification",{"labelKey":"cote_physique_libelle","applicability":"physical-profile"}),
        field("type_rive_id","Rive","reference","classification",{"labelKey":"type_rive_libelle","applicability":"physical-profile"}),
        field("type_troncon_id","Type de tronçon","reference","classification",{"labelKey":"type_troncon_libelle","applicability":"physical-profile"}),
        field("typologie_troncon_id","Typologie","reference","classification",{"labelKey":"typologie_troncon_libelle","applicability":"physical-profile"}),
        field("source_id","Source d’information","reference","classification",{"labelKey":"source_libelle","applicability":"physical-profile"}),
        field("cote_id","Côté","reference","classification",{"labelKey":"cote_libelle","applicability":"physical-profile"}),
        field("position_id","Position","reference","classification",{"labelKey":"position_libelle","applicability":"physical-profile"}),
        field("fonction_id","Fonction","reference","classification",{"labelKey":"fonction_libelle","applicability":"physical-profile"}),
        field("fonction_haut_id","Fonction — haut","reference","classification",{"labelKey":"fonction_haut_libelle","applicability":"physical-profile"}),
        field("fonction_bas_id","Fonction — bas","reference","classification",{"labelKey":"fonction_bas_libelle","applicability":"physical-profile"}),
        field("nature_id","Nature","reference","classification",{"labelKey":"nature_libelle","applicability":"physical-profile"}),
        field("nature_haut_id","Nature — haut","reference","classification",{"labelKey":"nature_haut_libelle","applicability":"physical-profile"}),
        field("nature_bas_id","Nature — bas","reference","classification",{"labelKey":"nature_bas_libelle","applicability":"physical-profile"}),
        field("materiau_id","Matériau","reference","classification",{"labelKey":"materiau_libelle","applicability":"physical-profile"}),
        field("materiau_haut_id","Matériau — haut","reference","classification",{"labelKey":"materiau_haut_libelle","applicability":"physical-profile"}),
        field("materiau_bas_id","Matériau — bas","reference","classification",{"labelKey":"materiau_bas_libelle","applicability":"physical-profile"}),
        field("largeur_franc_bord_id","Classe de largeur du franc-bord","reference","classification",{"labelKey":"largeur_franc_bord_libelle","applicability":"physical-profile"}),
        field("num_couche","Numéro de couche","number","characteristics",{"applicability":"physical-profile"}),
        field("epaisseur","Épaisseur","number","characteristics",{"applicability":"physical-profile"}),
        field("largeur_moyenne","Largeur moyenne","number","characteristics",{"applicability":"physical-profile"}),
        field("epaisseur_sommet","Épaisseur au sommet","number","characteristics",{"applicability":"physical-profile"}),
        field("pente_interieure","Pente intérieure","number","characteristics",{"applicability":"physical-profile"}),
        field("longueur_rampant_haut","Longueur de rampant haut","number","characteristics",{"applicability":"physical-profile"}),
        field("longueur_rampant_bas","Longueur de rampant bas","number","characteristics",{"applicability":"physical-profile"}),
        field("hauteur_murette","Hauteur de murette","number","characteristics",{"applicability":"physical-profile"}),
        field("largeur","Largeur","number","characteristics",{"applicability":"physical-profile"}),
        field("digue_id","Digue","relations","attachments",{"resource":"digues","source":"digue_id","labelKey":"digue_libelle","applicability":"physical-profile"}),
        field("amenagement_hydraulique_id","Aménagement hydraulique","relations","attachments",{"resource":"amenagements_hydrauliques","source":"amenagement_hydraulique_id","labelKey":"amenagement_hydraulique_libelle","applicability":"physical-profile"}),
        field("troncon_support_id","Tronçon support classique","relations","attachments",{"resource":"troncons","source":"troncon_support_id","labelKey":"troncon_support_libelle","applicability":"physical-profile"}),
        field("segment_hydrographique_support_id","Segment hydrographique support","relations","attachments",{"resource":"troncons_lit","source":"segment_hydrographique_support_id","labelKey":"segment_hydrographique_support_libelle","applicability":"physical-profile"}),
        field("berge_support_id","Berge support","relations","attachments",{"resource":"troncons","source":"berge_support_id","labelKey":"berge_support_libelle","applicability":"physical-profile"}),
        field("valid","Objet valide","boolean","identification"),
        field("localisation","Géométrie physique","text","geometry"),
        field("geometry_type","Type géométrique conservé","text","technical"),
        field("geometry_srid","SCR de la géométrie","number","technical"),
        field("geometry_dimensions","Dimensions conservées","number","technical"),
        field("geometry_valide","Géométrie valide","boolean","technical"),
        field("systeme_endiguement_id","Système d’endiguement","relations","attachments",{"source":"systeme_endiguement_id","resource":"systemes","labelKey":"systeme_endiguement_libelle"}),
      ],
    },
    "desordres": {
      label: "Désordre", endpoint: "/api/desordres", mutationResource: "desordres",
      observations: null, map: false, fields: [
        field("designation","Désignation","text","identification"),
        field("type_desordre_id","Type","reference","classification",{"labelKey":"type_desordre_libelle","catalog":"typesDesordre"}),
        field("categorie_desordre_id","Catégorie","reference","classification",{"labelKey":"categorie_desordre_libelle","catalog":"categoriesDesordre"}),
        field("cote_id","Côté","reference","classification",{"labelKey":"cote_libelle","catalog":"cotes"}),
        field("position_id","Position","reference","classification",{"labelKey":"position_libelle","catalog":"positions"}),
        field("source_id","Source","reference","classification",{"labelKey":"source_libelle","catalog":"sources"}),
        field("lieu_dit","Lieu-dit","text","classification"),
        field("date_debut","Date de début","date","identification"),
        field("date_fin","Date de fin","date","identification"),
        field("commentaire","Commentaire","text","additional"),
        field("valid","Objet valide","boolean","identification"),
        field("troncon_ids","Tronçon(s)","relations","attachments",{"resource":"troncons","source":"troncon_ids"}),
        field("digues","Digue(s)","relations","relations",{"resource":"digues","source":"digues"}),
        field("systemes","Système(s)","relations","relations",{"resource":"systemes","source":"systemes"}),
        field("zones_stockage_labels","Zone(s) de stockage","relations","relations",{"resource":"zones_stockage","source":"zones_stockage"}),
        field("localisation","Géométrie","text","geometry"),
      ],
    },
  };
  const SECTIONS = Object.freeze([
    { key: "identification", title: "Identification", column: "primary" },
    { key: "classification", title: "Classification", column: "primary" },
    { key: "characteristics", title: "Caractéristiques", column: "primary" },
    { key: "additional", title: "Informations complémentaires", column: "primary" },
    { key: "historical", title: "Informations historiques complémentaires", column: "primary" },
    { key: "attachments", title: "Rattachements patrimoniaux", column: "context" },
    { key: "relations", title: "Relations", column: "context" },
    { key: "geometry", title: "Géométrie et repérage", column: "context" },
    { key: "technical", title: "Informations techniques", column: "context", collapsed: true },
  ].map(Object.freeze));
  const physicalProfiles = {
    INDETERMINE: ["type_rive_id", "type_troncon_id", "typologie_troncon_id"],
    BERGE: ["type_rive_id", "type_troncon_id", "typologie_troncon_id"],
    CRETE: ["source_id", "fonction_id", "nature_id", "materiau_id", "num_couche", "epaisseur", "largeur_moyenne", "troncon_support_id"],
    TALUS: ["source_id", "fonction_haut_id", "fonction_bas_id", "nature_haut_id", "nature_bas_id", "materiau_haut_id", "materiau_bas_id", "num_couche", "epaisseur_sommet", "pente_interieure", "longueur_rampant_haut", "longueur_rampant_bas", "cote_id", "troncon_support_id", "segment_hydrographique_support_id", "berge_support_id"],
    RISBERME: ["source_id", "fonction_id", "nature_id", "materiau_id", "cote_id", "num_couche", "epaisseur", "troncon_support_id", "segment_hydrographique_support_id", "berge_support_id"],
    FRANC_BORD: ["source_id", "largeur_franc_bord_id", "troncon_support_id", "segment_hydrographique_support_id", "berge_support_id"],
  };
  physicalProfiles.TALUS_RISBERME = physicalProfiles.TALUS;
  const populated = value => value !== null && value !== undefined && value !== "";
  function applies(spec, values) {
    if (spec.applicability === "disorder-parent") return values.parent?.resource === "desordres";
    if (spec.applicability === "physical-profile") return ["cote_physique_id", "digue_id", "amenagement_hydraulique_id"].includes(spec.key)
      || (physicalProfiles[values.type_physique_id] || []).includes(spec.key);
    // A populated atypical value remains in the historical section below.
    if (spec.key === "type_zone_id" && values.type_morphologie) return values.type_morphologie === "ATTERISSEMENT";
    if (spec.key === "sous_type" && values.famille_vegetation) return values.famille_vegetation === "ARBRES";
    return true;
  }
  function fields(resource, values) {
    const result = get(resource).fields.flatMap(spec => {
      if (applies(spec, values)) return [spec];
      return populated(values[spec.key]) ? [{ ...spec, section: "historical", applicability: "historical" }] : [];
    });
    if (resource === "troncons") for (const spec of result.slice()) {
      if (spec.key.endsWith("_id") && populated(values[spec.key]) && ["reference", "relations"].includes(spec.format))
        result.push(field(`identifier_${spec.key}`, `${spec.label} — identifiant technique`, "text", "technical", { source: spec.key, importance: "diagnostic" }));
    }
    return result;
  }
  function get(resource) {
    const definition = definitions[resource];
    if (!definition) throw new Error("Famille de fiche inconnue.");
    return definition;
  }
  for (const [resource, definition] of Object.entries(definitions)) {
    definition.resource = resource;
    definition.fields = Object.freeze([...definition.fields,
      field("id", "Identifiant", "text", "technical", { importance: "diagnostic" }),
      field("version", "Version", "number", "technical", { importance: "diagnostic" }),
    ]);
    definition.sections = SECTIONS;
    Object.freeze(definition);
  }
  return { definitions: Object.freeze(definitions), SECTIONS, get, fields, populated };
});
