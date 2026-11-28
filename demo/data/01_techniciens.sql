-- SIG Milieux V0.11 - import techniciens (4 lignes)
-- A executer sur base neuve apres schema.sql. Conservation des UUID.
BEGIN;
SELECT pg_catalog.set_config('search_path', pg_catalog.format('pg_catalog,%I,%I', n.nspname, 'gemapi'), true) FROM pg_catalog.pg_extension AS e JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace WHERE e.extname = 'postgis';
INSERT INTO gemapi.techniciens (id, nom, prenom, login, actif) VALUES ('0526cfb1-210a-523e-a7f4-caa810e49cda', 'Dupont', 'Jean', NULL, TRUE);
INSERT INTO gemapi.techniciens (id, nom, prenom, login, actif) VALUES ('8a0a60f2-fba1-52e1-b308-1fdfe78733c3', 'Petit', 'David', NULL, TRUE);
INSERT INTO gemapi.techniciens (id, nom, prenom, login, actif) VALUES ('fb34f330-dfe3-5175-b0d7-1a36ff9d712d', 'Martin', 'Eric', NULL, TRUE);
INSERT INTO gemapi.techniciens (id, nom, prenom, login, actif) VALUES ('e1e06f19-2e5b-5837-9ddf-331cb7871f3f', 'Dubois', 'Julien', 'postgres.zejhfmmupakuowqoywpx', TRUE);
COMMIT;
