-- roles_milieux.sql — PostgreSQL 16+ / schéma applicatif gemapi
-- À exécuter par l'administrateur de la base, APRÈS model/schema.sql.
-- Ne crée aucun utilisateur individuel et ne modifie aucune fiche technicien.
BEGIN;
SELECT pg_catalog.set_config('search_path',pg_catalog.format('pg_catalog,%I,gemapi,pg_temp',n.nspname),true)
FROM pg_catalog.pg_extension e JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace
WHERE e.extname='postgis';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='milieux_consultation') THEN
    CREATE ROLE milieux_consultation NOLOGIN INHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='milieux_prestations') THEN
    CREATE ROLE milieux_prestations NOLOGIN INHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='milieux_patrimoine') THEN
    CREATE ROLE milieux_patrimoine NOLOGIN INHERIT;
  END IF;
END $$;

-- Hiérarchie ; les droits sont cumulés.
GRANT milieux_consultation TO milieux_prestations;
GRANT milieux_prestations TO milieux_patrimoine;

GRANT USAGE ON SCHEMA gemapi TO milieux_consultation;
-- PostGIS peut être installé dans public ou extensions : ne pas le supposer.
DO $$ DECLARE extension_schema text; BEGIN
  SELECT n.nspname INTO STRICT extension_schema FROM pg_extension e
  JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='postgis';
  EXECUTE format('GRANT USAGE ON SCHEMA %I TO milieux_consultation',extension_schema);
END $$;

-- Droit de lecture commun aux trois profils (par héritage).
GRANT SELECT ON TABLE
  gemapi.troncons,
  gemapi.mobilier,
  gemapi.cheminements,
  gemapi.vegetation,
  gemapi.ouvrages_particuliers,
  gemapi.equipements_mesure,
  gemapi.ouvrages_hydrauliques,
  gemapi.desordres,
  gemapi.observations,
  gemapi.photos,
  gemapi.equipements_mesure__desordres,
  gemapi.ouvrages_hydrauliques__desordres,
  gemapi.ouvrages_particuliers__desordres,
  gemapi.ouvrages_hydrauliques__objets_lies,
  gemapi.prestations,
  gemapi.ah, gemapi.ah_stockage, gemapi.ah_ouvrage,
  gemapi.sites_terrestres, gemapi.cours_eau,
  gemapi.digues, gemapi.digues_reperes, gemapi.ah_sections,
  gemapi.techniciens, gemapi.prestataires, gemapi.communes,
  gemapi.prestations_categorie, gemapi.types_prestations, gemapi.statuts_prestations,
  gemapi.prestations_fiches, gemapi.prestations_carte, gemapi.digues_carte,
  gemapi.ah_sections_carte,
  gemapi.prestations__ah, gemapi.prestations__sites_terrestres
TO milieux_consultation;

GRANT SELECT ON TABLE
  gemapi.categories_desordre,
  gemapi.cotes,
  gemapi.fonctions_structure,
  gemapi.materiaux_structure,
  gemapi.natures_structure,
  gemapi.positions,
  gemapi.rives,
  gemapi.sources_information,
  gemapi.suites_apporter,
  gemapi.types_desordre,
  gemapi.types_equipement_mesure,
  gemapi.types_ouvrage_hydraulique,
  gemapi.types_ouvrage_particulier,
  gemapi.types_troncon,
  gemapi.typologies_troncon,
  gemapi.urgences,
  gemapi.types_mobilier,
  gemapi.types_cheminement,
  gemapi.familles_vegetation TO milieux_consultation;

GRANT EXECUTE ON FUNCTION
  gemapi.cours_eau_reference_admissible(geometry,boolean,boolean),
  gemapi.ah_sections_candidates(uuid)
TO milieux_consultation;

GRANT EXECUTE ON FUNCTION gemapi.mesures07_resoudre(geometry,uuid,uuid,uuid,uuid,uuid) TO milieux_consultation;

-- Le gestionnaire de prestations modifie uniquement les prestations.
GRANT INSERT, UPDATE, DELETE ON TABLE
  gemapi.prestations
TO milieux_prestations;

-- Le gestionnaire du patrimoine modifie en plus les objets patrimoniaux.
GRANT INSERT, UPDATE, DELETE ON TABLE
  gemapi.troncons,
  gemapi.mobilier,
  gemapi.cheminements,
  gemapi.vegetation,
  gemapi.ouvrages_particuliers,
  gemapi.equipements_mesure,
  gemapi.ouvrages_hydrauliques,
  gemapi.desordres,
  gemapi.observations,
  gemapi.photos,
  gemapi.equipements_mesure__desordres,
  gemapi.ouvrages_hydrauliques__desordres,
  gemapi.ouvrages_particuliers__desordres,
  gemapi.ouvrages_hydrauliques__objets_lies
TO milieux_patrimoine;
GRANT INSERT, UPDATE, DELETE ON TABLE
  gemapi.ah, gemapi.ah_stockage, gemapi.ah_ouvrage,
  gemapi.sites_terrestres, gemapi.cours_eau,
  gemapi.digues, gemapi.digues_reperes, gemapi.ah_sections
TO milieux_patrimoine;

GRANT EXECUTE ON FUNCTION
  gemapi.digue_enregistrer(uuid,uuid,integer,timestamptz,text,text,text,uuid,text,double precision,double precision,text),
  gemapi.digue_supprimer(uuid,integer,timestamptz)
TO milieux_patrimoine;

-- Supprime les privilèges TRUNCATE éventuellement accordés directement
-- à CES TROIS RÔLES (ne révoque pas les droits hérités d'autres rôles).
REVOKE TRUNCATE ON ALL TABLES IN SCHEMA gemapi
FROM milieux_consultation, milieux_prestations, milieux_patrimoine;

COMMIT;

-- NOTE : vérifier les privilèges déjà accordés à PUBLIC, anon, authenticated
-- ou aux comptes individuels ; les GRANT ci-dessus ne les annulent pas.
-- Pour les futures tables, mettre à jour ce script et les privilèges par défaut.
