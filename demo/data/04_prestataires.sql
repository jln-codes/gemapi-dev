-- SIG Milieux V0.11 - import prestataires (6 lignes)
-- A executer sur base neuve apres schema.sql. Conservation des UUID.
BEGIN;
SELECT pg_catalog.set_config('search_path', pg_catalog.format('pg_catalog,%I,%I', n.nspname, 'gemapi'), true) FROM pg_catalog.pg_extension AS e JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace WHERE e.extname = 'postgis';
INSERT INTO gemapi.prestataires (id, nom, actif) VALUES ('521e1a33-56ca-50cc-850e-56855e3c0b22', 'Entreprise Rivage Nord', TRUE);
INSERT INTO gemapi.prestataires (id, nom, actif) VALUES ('97f52698-ee36-504d-8c0d-31937769128a', 'Atelier des Eaux', TRUE);
INSERT INTO gemapi.prestataires (id, nom, actif) VALUES ('0b6985fb-89fa-574e-b40e-34a6468107a3', 'Paysages et Milieux', TRUE);
INSERT INTO gemapi.prestataires (id, nom, actif) VALUES ('4aea38bb-5efa-554a-8788-622366e162aa', 'Régie technique des milieux', TRUE);
INSERT INTO gemapi.prestataires (id, nom, actif) VALUES ('697daa7f-3a79-5296-bce0-75c0c0a5618a', 'Écologie Terrain', TRUE);
INSERT INTO gemapi.prestataires (id, nom, actif) VALUES ('c3db9c46-44d0-5b05-bba3-7063d24a52a3', 'Travaux hydrauliques du bassin', TRUE);
COMMIT;
