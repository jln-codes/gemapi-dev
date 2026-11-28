-- Sept portions fictives de la géométrie figée de la Digue des Prés.
BEGIN;
SELECT pg_catalog.set_config('search_path',pg_catalog.format('pg_catalog,%I,gemapi',n.nspname),true) FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='postgis';
INSERT INTO gemapi.troncons (id, designation, type_troncon_id, digue_id, geometry, valid)
SELECT s.id::uuid, 'Digue des Prés · ' || s.type, s.type, r.digue_id,
       ST_LineSubstring(r.geom_reference, s.i/7.0, (s.i+1)/7.0), true
FROM (VALUES
    ('f41c613e-9039-5ab7-85a9-6e3d6d5042d9', 'BERGE', 0),
    ('1024598f-8f4c-5e57-8a28-22c942f6cc5a', 'CRETE', 1),
    ('24abd174-e7ba-567b-bdb1-ead66f10827e', 'TALUS', 2),
    ('e61e3cf4-07e9-5137-8473-a74ae9f8f442', 'TALUS_RISBERME', 3),
    ('283b5b3c-6d7d-5881-8765-5f56e681cf8b', 'RISBERME', 4),
    ('fed94aec-fcb8-5963-a467-d8022bbd6b13', 'FRANC_BORD', 5),
    ('d8029de6-0926-5c84-a3f0-5d7764db7b83', 'INDETERMINE', 6)
) AS s(id,type,i)
CROSS JOIN gemapi.digues_reperes r
WHERE r.id='70643eb8-d9d8-52eb-a2c3-e72116e2b5cb';

INSERT INTO gemapi.ouvrages_particuliers (id, type_id, designation, troncon_id, geometry)
SELECT '6fde92b1-c157-5c08-874f-ee65acaa302b', 'RefOuvrageParticulier:3', 'Escalier de visite', id, ST_LineInterpolatePoint(geometry, 0.5)
FROM gemapi.troncons WHERE id='1024598f-8f4c-5e57-8a28-22c942f6cc5a';

INSERT INTO gemapi.equipements_mesure (id, type_id, designation, troncon_id, geometry)
SELECT 'e43d23b2-5cc1-500c-a324-364729938bd2', 'ECH', 'Échelle de suivi', id, ST_LineInterpolatePoint(geometry, 0.5)
FROM gemapi.troncons WHERE id='f41c613e-9039-5ab7-85a9-6e3d6d5042d9';

INSERT INTO gemapi.ouvrages_hydrauliques (id, type_id, designation, troncon_id, geometry)
SELECT 'fa31f0ec-1a72-58e0-a7cb-ecc4851f7f1d', 'VAN', 'Vanne de régulation', id, ST_LineInterpolatePoint(geometry, 0.5)
FROM gemapi.troncons WHERE id='fed94aec-fcb8-5963-a467-d8022bbd6b13';
COMMIT;
