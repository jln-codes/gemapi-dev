-- Suivi fictif de la Digue des Prés. Les clés de fichier sont relatives à demo/.
BEGIN;
INSERT INTO gemapi.desordres (id, designation, troncon_id, date_debut, date_fin) VALUES
    ('c3dcff2d-fd33-5cac-9f92-07656d9e9b7e', 'Érosion en berge', 'f41c613e-9039-5ab7-85a9-6e3d6d5042d9', '2026-01-10', NULL),
    ('81ad283b-11cd-5436-9668-bfcda8a84de5', 'Affaissement de crête réparé', '1024598f-8f4c-5e57-8a28-22c942f6cc5a', '2026-01-12', '2026-03-12');

INSERT INTO gemapi.observations (id, designation, desordre_id, date_observation) VALUES
    ('da1449a0-5d93-541e-bed9-65321f37ebfd', 'Érosion constatée', 'c3dcff2d-fd33-5cac-9f92-07656d9e9b7e', '2026-01-10'),
    ('874788f1-5fda-596f-8f13-22bcdece79f0', 'Extension à surveiller', 'c3dcff2d-fd33-5cac-9f92-07656d9e9b7e', '2026-02-10'),
    ('ca660b66-9d16-527c-ba57-12787936d687', 'Réparation constatée', '81ad283b-11cd-5436-9668-bfcda8a84de5', '2026-03-12');
INSERT INTO gemapi.observations (id, designation, equipement_mesure_id, date_observation)
VALUES ('c44dbca9-78d0-5b76-828e-fd0f56c5d15f', 'Lecture de routine', 'e43d23b2-5cc1-500c-a324-364729938bd2', '2026-04-10');

INSERT INTO gemapi.photos (id, observation_id, date_photo, designation, storage_key) VALUES
    ('7712471f-a011-54a5-af2d-a16e98babc1a', 'da1449a0-5d93-541e-bed9-65321f37ebfd', '2026-01-10', 'Illustration du constat', 'photos/photo_4x3.jpg'),
    ('3b25243b-2bfc-5132-bd55-7c1f58d37b88', 'da1449a0-5d93-541e-bed9-65321f37ebfd', '2026-01-10', 'Illustration de détail', 'photos/photo_3x4.jpg'),
    ('b12353a9-ed23-53c2-8693-d6d49e10435b', 'ca660b66-9d16-527c-ba57-12787936d687', '2026-03-12', 'Fichier attendu non fourni', 'photos/constat_reparation_absent.jpg');
COMMIT;
