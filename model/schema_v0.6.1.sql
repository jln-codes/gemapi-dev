-- SIG Prestations / PostgreSQL 16+ et PostGIS 3.3+ / installation NEUVE
-- Utilise l’extension PostGIS existante, ou l’installe dans public.
-- Ne pas executer sur une base existante avec ces objets.
BEGIN;
CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public;
CREATE SCHEMA IF NOT EXISTS gemapi;
DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_catalog.pg_depend WHERE refclassid='pg_catalog.pg_namespace'::regclass
             AND refobjid='gemapi'::regnamespace) THEN
    RAISE EXCEPTION 'Installation neuve requise : le schéma gemapi doit être vide';
  END IF;
  IF EXISTS (SELECT FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname IN ('gemapi','public','milieux','referentiel') AND c.relkind IN ('r','p','v','m','S','f')
               AND NOT EXISTS (SELECT FROM pg_catalog.pg_depend dep
                               WHERE dep.classid='pg_catalog.pg_class'::regclass
                                 AND dep.objid=c.oid AND dep.deptype='e')) THEN
    RAISE EXCEPTION 'Installation neuve requise : objets préexistants dans un des schémas du modèle';
  END IF;
END $$;
SELECT pg_catalog.set_config(
  'search_path',
  pg_catalog.format('pg_catalog,%I,%I,pg_temp', n.nspname, 'gemapi'),
  true
)
FROM pg_catalog.pg_extension AS e
JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace
WHERE e.extname = 'postgis';
CREATE TABLE gemapi.prestations_categorie (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    libelle text NOT NULL UNIQUE,
    ordre integer NOT NULL
);
CREATE TABLE gemapi.types_prestations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    categorie_id uuid NOT NULL REFERENCES gemapi.prestations_categorie(id) ON DELETE RESTRICT,
    code text NOT NULL UNIQUE,
    libelle text NOT NULL,
    actif boolean NOT NULL DEFAULT true,
    modifie_par text,
    date_modification timestamptz
);
CREATE TABLE gemapi.statuts_prestations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL UNIQUE CHECK (code IN ('PLANIFIE','EN_COURS','TERMINE','ANNULE')),
    libelle text NOT NULL UNIQUE,
    modifie_par text,
    date_modification timestamptz
);

CREATE TABLE gemapi.categories_desordre (
    id text PRIMARY KEY,
    libelle text,
    abrege text,
    designation text
);

CREATE TABLE gemapi.cotes (
    id text PRIMARY KEY,
    designation text,
    abrege text,
    libelle text
);

CREATE TABLE gemapi.fonctions_structure (
    id text PRIMARY KEY,
    code text UNIQUE,
    libelle text NOT NULL,
    designation text
);

CREATE TABLE gemapi.materiaux_structure (
    id text PRIMARY KEY,
    code text UNIQUE,
    libelle text NOT NULL,
    designation text
);

CREATE TABLE gemapi.natures_structure (
    id text PRIMARY KEY,
    code text UNIQUE,
    libelle text NOT NULL,
    designation text
);

CREATE TABLE gemapi.positions (
    id text PRIMARY KEY,
    designation text,
    abrege text,
    libelle text
);

CREATE TABLE gemapi.rives (
    id text PRIMARY KEY,
    designation text,
    abrege text,
    libelle text
);

CREATE TABLE gemapi.sources_information (
    id text PRIMARY KEY,
    designation text,
    abrege text,
    libelle text
);

CREATE TABLE gemapi.suites_apporter (
    id text PRIMARY KEY,
    designation text,
    abrege text,
    libelle text
);

CREATE TABLE gemapi.types_desordre (
    id text PRIMARY KEY,
    categorie_id text REFERENCES gemapi.categories_desordre(id) ON DELETE RESTRICT,
    libelle text,
    abrege text,
    designation text
);

CREATE TABLE gemapi.types_equipement_mesure (
    id text PRIMARY KEY,
    code text NOT NULL UNIQUE,
    abrege text NOT NULL UNIQUE,
    libelle text NOT NULL
);

CREATE TABLE gemapi.types_ouvrage_hydraulique (
    id text PRIMARY KEY,
    code text NOT NULL UNIQUE,
    abrege text NOT NULL UNIQUE,
    libelle text NOT NULL
);

CREATE TABLE gemapi.types_ouvrage_particulier (
    id text PRIMARY KEY,
    code text UNIQUE,
    libelle text NOT NULL,
    designation text
);

CREATE TABLE gemapi.types_troncon (
    id text PRIMARY KEY,
    code text UNIQUE,
    libelle text NOT NULL,
    designation text
);

CREATE TABLE gemapi.typologies_troncon (
    id text PRIMARY KEY,
    designation text,
    abrege text,
    libelle text
);

CREATE TABLE gemapi.urgences (
    id text PRIMARY KEY,
    libelle text,
    abrege text,
    designation text
);

CREATE TABLE gemapi.types_mobilier (
    id text PRIMARY KEY,
    code text NOT NULL UNIQUE,
    abrege text NOT NULL UNIQUE,
    libelle text NOT NULL
);

CREATE TABLE gemapi.types_cheminement (
    id text PRIMARY KEY,
    code text NOT NULL UNIQUE,
    abrege text NOT NULL UNIQUE,
    libelle text NOT NULL
);

CREATE TABLE gemapi.familles_vegetation (
    id text PRIMARY KEY,
    code text NOT NULL UNIQUE,
    libelle text NOT NULL,
    designation text
);

INSERT INTO gemapi.prestations_categorie (id, libelle, ordre) VALUES
    ('4946680e-3547-5f25-8508-2929a7f7a79e', 'Entretien manuel', 2),
    ('60410105-fbdd-5890-9d20-a331b4e61e88', 'Génie écologique', 3),
    ('ae1b972a-e127-5216-9660-7c66583eb893', 'Plantations', 4),
    ('cf2572fb-9365-5ac5-bdf4-0d66581d9aaa', 'Entretien mécanique', 1);

INSERT INTO gemapi.types_prestations (categorie_id, id, code, libelle, actif) VALUES
    ('cf2572fb-9365-5ac5-bdf4-0d66581d9aaa', '272003ca-7006-5f4b-b5ac-f6d84281a3e2', 'CURAGE', 'Curage', true),
    ('4946680e-3547-5f25-8508-2929a7f7a79e', '6d6fa494-e36e-5ba5-bc50-dbf256d4fc7d', 'ELAGAGE', 'Élagage', true),
    ('60410105-fbdd-5890-9d20-a331b4e61e88', 'aae7d72e-377e-5cd8-a0fb-326d7bd03c5a', 'SUIVI', 'Suivi de milieu', true),
    ('cf2572fb-9365-5ac5-bdf4-0d66581d9aaa', 'b6ea0cbd-54ef-5bb8-9cc4-42af05fe876e', 'FAUCHE', 'Fauche', true),
    ('4946680e-3547-5f25-8508-2929a7f7a79e', 'cf98e704-4949-54b9-aa02-b65b55e8b83d', 'EMBACLES', 'Retrait d’embâcles', true),
    ('60410105-fbdd-5890-9d20-a331b4e61e88', 'd2fa2842-9125-5f4a-ad3f-0adffead207c', 'ETUDE', 'Étude', true),
    ('cf2572fb-9365-5ac5-bdf4-0d66581d9aaa', 'd8737de6-7a5e-5892-afc4-b51f5e882e26', 'DEBROUSSAILLAGE', 'Débroussaillage', true),
    ('60410105-fbdd-5890-9d20-a331b4e61e88', 'da65d85b-ecf1-5481-bd2a-a47cf9b14544', 'RESTAURATION', 'Restauration écologique', true),
    ('ae1b972a-e127-5216-9660-7c66583eb893', 'e2659d31-52c3-5f87-884e-56eedd7e1ee9', 'PLANTATION', 'Plantation', true);

INSERT INTO gemapi.statuts_prestations (id, code, libelle) VALUES
    ('58554fd0-fcc2-45b6-aa19-cc4a897af3fb', 'PLANIFIE', 'Planifié'),
    ('9ee713ca-13a1-4358-b5d8-fa79627fc256', 'EN_COURS', 'En cours'),
    ('421801d8-30be-4a5a-8e18-cb2b9ac0ea37', 'TERMINE', 'Terminé'),
    ('fdd48590-aace-4693-946e-80c1f4d8693f', 'ANNULE', 'Annulé');

INSERT INTO gemapi.categories_desordre (id, libelle, abrege, designation) VALUES
    ('RefCategorieDesordre:1', 'Érosion externe', 'ERX', '1'),
    ('RefCategorieDesordre:2', 'Érosion interne', 'ERI', '2'),
    ('RefCategorieDesordre:3', 'Désordre affectant la structure', 'DAF', '3'),
    ('RefCategorieDesordre:4', 'Brèche', 'BRE', '4'),
    ('RefCategorieDesordre:5', 'Conditions d''entretien de la digue, détérioration des équipements, activités illégales, gêne à la gestion', 'ENT', '5'),
    ('RefCategorieDesordre:6', 'Autre', 'AUT', '6'),
    ('RefCategorieDesordre:7', 'Défaut sur dispositif de protection', 'DDP', '7');

INSERT INTO gemapi.cotes (id, designation, abrege, libelle) VALUES
    ('RefCote:1', '1', 'RIV', 'Rivière'),
    ('RefCote:2', '2', 'TER', 'Terre'),
    ('RefCote:3', '3', 'E/C', 'Etang/Canal'),
    ('RefCote:4', '4', 'MER', 'Mer'),
    ('RefCote:5', '5', 'DEU', 'Deux côtés de la digue'),
    ('RefCote:6', '6', 'CRE', 'Crête'),
    ('RefCote:7', '7', 'AMT', 'Amont'),
    ('RefCote:8', '8', 'AVL', 'Aval'),
    ('RefCote:99', '99', 'IND', 'Indéfini');

INSERT INTO gemapi.fonctions_structure (id, code, libelle, designation) VALUES
    ('RefFonction:1', NULL, 'Drainage', '1'),
    ('RefFonction:2', NULL, 'Protection', '2'),
    ('RefFonction:3', NULL, 'Etanchéité', '3'),
    ('RefFonction:4', NULL, 'Protection contre l''érosion interne', '4'),
    ('RefFonction:5', NULL, 'Anti-fouisseur', '5'),
    ('RefFonction:6', NULL, 'Stabilité d''ensemble', '6'),
    ('RefFonction:7', NULL, 'Dispositif de revanche', '7'),
    ('RefFonction:8', NULL, 'Fondation', '8'),
    ('RefFonction:9', NULL, 'Parafouille', '9'),
    ('RefFonction:10', NULL, 'Filtration', '10'),
    ('RefFonction:11', NULL, 'Accès/gestion', '11'),
    ('RefFonction:99', NULL, 'Indéfini', '99');

INSERT INTO gemapi.materiaux_structure (id, code, libelle, designation) VALUES
    ('RefMateriau:1', 'TEV', 'Terre végétale', '1'),
    ('RefMateriau:2', 'GRA', 'Graviers (concassé 0.33 mm)', '2'),
    ('RefMateriau:3', 'PMA', 'Pierres Maçonnées', '3'),
    ('RefMateriau:4', 'PSE', 'Pierres Sèches', '4'),
    ('RefMateriau:5', 'BET', 'Béton', '5'),
    ('RefMateriau:6', 'BOI', 'Bois', '6'),
    ('RefMateriau:7', 'VET', 'Revêtement végétalisé', '7'),
    ('RefMateriau:8', 'ACI', 'Acier', '8'),
    ('RefMateriau:9', 'ENB', 'Enrobé', '9'),
    ('RefMateriau:10', 'REM', 'Remblais', '10'),
    ('RefMateriau:11', 'ROD', 'Roche déversée', '11'),
    ('RefMateriau:12', 'ROA', 'Roche appareillée', '12'),
    ('RefMateriau:13', 'ROB', 'Roche bétonnée', '13'),
    ('RefMateriau:14', 'NAT', 'Lit naturel du fossé', '14'),
    ('RefMateriau:15', 'TVB', 'Tout Venant Brut', '15'),
    ('RefMateriau:16', 'CON', 'Concassé 0.80 mm', '16'),
    ('RefMateriau:17', 'DEC', 'Déchets de carrière en 0/400mm', '17'),
    ('RefMateriau:18', 'MOE', 'Moellons', '18'),
    ('RefMateriau:19', 'PBJ', 'Plaques de béton jointives', '19'),
    ('RefMateriau:21', 'PAV', 'Pavés', '21'),
    ('RefMateriau:22', 'SAB', 'Sables', '22'),
    ('RefMateriau:23', 'LIM', 'Limons', '23'),
    ('RefMateriau:24', 'LSB', 'Limons et sables', '24'),
    ('RefMateriau:25', 'SIL', 'Silts', '25'),
    ('RefMateriau:26', 'GOM', 'Géomembrane', '26'),
    ('RefMateriau:27', 'GEO', 'Géotextile', '27'),
    ('RefMateriau:28', 'DEB', 'Dé bétonné', '28'),
    ('RefMateriau:29', 'GAL', 'Galets 0/100 mm', '29'),
    ('RefMateriau:30', 'PJK', 'Panneaux JK', '30'),
    ('RefMateriau:31', 'FRA', 'Fraisat recyclé', '31'),
    ('RefMateriau:32', 'GRI', 'Grillage', '32'),
    ('RefMateriau:33', 'TUY', 'Tuyau drain', '33'),
    ('RefMateriau:34', 'SCH', 'Schiste 0/100 mm', '34'),
    ('RefMateriau:35', 'BEC', 'Bentonite-ciment', '35'),
    ('RefMateriau:36', 'VGE', 'Végétalisé, enherbé', '36'),
    ('RefMateriau:37', 'VGA', 'Végétalisé, arbustif', '37'),
    ('RefMateriau:38', 'VGB', 'Végétalisé, boisé', '38'),
    ('RefMateriau:39', 'ARG', 'Argile', '39'),
    ('RefMateriau:40', 'MAD', 'Matériau drainant', '40'),
    ('RefMateriau:99', 'IND', 'Indéfini', '99');

INSERT INTO gemapi.natures_structure (id, code, libelle, designation) VALUES
    ('RefNature:1', 'ABR', 'Absence de revêtement', '1'),
    ('RefNature:2', 'RVT', 'Revêtement', '2'),
    ('RefNature:3', 'PAL', 'Palplanche', '3'),
    ('RefNature:4', 'PIX', 'Pieux', '4'),
    ('RefNature:5', 'RVH', 'Ouvrage de revanche', '5'),
    ('RefNature:6', 'MUR', 'Mur de soutènement', '6'),
    ('RefNature:7', 'DEV', 'Déversoir', '7'),
    ('RefNature:8', 'GAB', 'Gabion', '8'),
    ('RefNature:9', 'ENR', 'Enrochement', '9'),
    ('RefNature:10', 'OUP', 'Ouvrage parafouille', '10'),
    ('RefNature:11', 'FOS', 'Fossé (ou contre-fossé) côté terre en pied de digue', '11'),
    ('RefNature:12', 'FOM', 'Fondation meuble', '12'),
    ('RefNature:13', 'FOR', 'Fondation rocheuse', '13'),
    ('RefNature:14', 'ZUR', 'Zone urbanisée', '14'),
    ('RefNature:15', 'CDD', 'Corps de digue', '15'),
    ('RefNature:16', 'PER', 'Perré', '16'),
    ('RefNature:17', 'REM', 'Remblai', '17'),
    ('RefNature:18', 'PAR', 'Paroi étanche', '18'),
    ('RefNature:19', 'ZDP', 'Zone de dissipation (déversoir)', '19'),
    ('RefNature:20', 'SEU', 'Seuil (déversoir)', '20'),
    ('RefNature:21', 'DIF', 'Dispositif fusible (déversoir)', '21'),
    ('RefNature:99', 'IND', 'Indéfini', '99');

INSERT INTO gemapi.positions (id, designation, abrege, libelle) VALUES
    ('RefPosition:3', '3', 'PDI', 'Pied de digue'),
    ('RefPosition:4', '4', 'TAR', 'Talus risberme'),
    ('RefPosition:5', '5', 'SOR', 'Sommet risberme'),
    ('RefPosition:6', '6', 'TAD', 'Talus digue'),
    ('RefPosition:7', '7', 'PLU', 'Plusieurs parties de la digue'),
    ('RefPosition:8', '8', 'CRE', 'Crête'),
    ('RefPosition:9', '9', 'HOR', 'Hors de la digue'),
    ('RefPosition:10', '10', 'FRB', 'Franc-bord'),
    ('RefPosition:11', '11', 'BER', 'Berge'),
    ('RefPosition:12', '12', 'TRP', 'Talus/Risberme/Pied de digue'),
    ('RefPosition:13', '13', 'COR', 'Coursier'),
    ('RefPosition:14', '14', 'PCO', 'Pied de Coursier'),
    ('RefPosition:15', '15', 'FOS', 'Fossé'),
    ('RefPosition:99', '99', 'IND', 'Indéfini');

INSERT INTO gemapi.rives (id, designation, abrege, libelle) VALUES
    ('RefRive:1', '1', NULL, 'Gauche'),
    ('RefRive:2', '2', NULL, 'Droite'),
    ('RefRive:3', '3', NULL, 'Les deux'),
    ('RefRive:99', '99', NULL, 'Indéfini');

INSERT INTO gemapi.sources_information (id, designation, abrege, libelle) VALUES
    ('RefSource:0', '0', 'NOB', 'Non observable'),
    ('RefSource:1', '1', 'DED', 'Déduction de l''observateur sur le terrain'),
    ('RefSource:2', '2', 'DOC', 'Document externe - archives'),
    ('RefSource:3', '3', 'TEM', 'Témoignage externe'),
    ('RefSource:4', '4', 'VUO', 'Vu sur le terrain par l''observateur'),
    ('RefSource:99', '99', 'IND', 'Indéfini');

INSERT INTO gemapi.suites_apporter (id, designation, abrege, libelle) VALUES
    ('RefSuiteApporter:1', '1', 'AUC', 'Aucune'),
    ('RefSuiteApporter:2', '2', 'SUR', 'Surveillance'),
    ('RefSuiteApporter:3', '3', 'ENT', 'Entretien'),
    ('RefSuiteApporter:4', '4', 'ETU', 'Etudes'),
    ('RefSuiteApporter:5', '5', 'TRA', 'Travaux'),
    ('RefSuiteApporter:6', '6', 'ARH', 'Archivé'),
    ('RefSuiteApporter:7', '7', 'PRI', 'Prioritaire'),
    ('RefSuiteApporter:8', '8', 'CST', 'Constat');

INSERT INTO gemapi.types_desordre (id, categorie_id, libelle, abrege, designation) VALUES
    ('RefTypeDesordre:1', 'RefCategorieDesordre:1', 'Erosion longitudinale diverse, autre que celle due au cours d''eau', 'ERD', '1'),
    ('RefTypeDesordre:2', 'RefCategorieDesordre:3', 'Fissure dans le terrain ou sur une structure rigide', 'FIS', '2'),
    ('RefTypeDesordre:3', 'RefCategorieDesordre:2', 'Fontis, indice d''activité karstique (infiltration)', 'FON', '3'),
    ('RefTypeDesordre:4', 'RefCategorieDesordre:3', 'Affaissement, glissement, tassement, tout indice de mouvement du terrain ou d''une structure rigide', 'MVT', '4'),
    ('RefTypeDesordre:5', 'RefCategorieDesordre:1', 'Piétinements d''animaux sur la digue', 'PIE', '5'),
    ('RefTypeDesordre:6', 'RefCategorieDesordre:1', 'Indice de ravinement sur talus ou plateforme (a priori dans le sens transversal)', 'RAV', '6'),
    ('RefTypeDesordre:7', 'RefCategorieDesordre:3', 'Débouché de TERrier ou galerie d''animaux fouisseurs', 'TRR', '7'),
    ('RefTypeDesordre:8', 'RefCategorieDesordre:3', 'Présence de Végétation dangereuse (arbustive et/ou arborescente, ou de souches) ', 'VED', '8'),
    ('RefTypeDesordre:9', 'RefCategorieDesordre:6', 'Dépression, étang, zone d''emprunt (au delà du pied de digue)', 'DEP', '9'),
    ('RefTypeDesordre:10', 'RefCategorieDesordre:2', 'Indice de fuite (ex: zone humide, laisse de fuite)', 'IFU', '10'),
    ('RefTypeDesordre:11', 'RefCategorieDesordre:1', 'Erosion (longitudinale) due au fleuve', 'ERF', '11'),
    ('RefTypeDesordre:12', 'RefCategorieDesordre:3', 'Rupture de réseaux', 'RUP', '12'),
    ('RefTypeDesordre:13', 'RefCategorieDesordre:3', 'Crête bombée (redant)', 'CEB', '13'),
    ('RefTypeDesordre:14', 'RefCategorieDesordre:3', 'Point bas', 'PTB', '14'),
    ('RefTypeDesordre:15', 'RefCategorieDesordre:7', 'Altération des pierres ou du béton d''un ouvrage de maçonnerie, corrosion d''un ouvrage métallique', 'ALT', '15'),
    ('RefTypeDesordre:16', 'RefCategorieDesordre:7', 'Décollement, dissociation, mauvais contact entre deux éléments d''ouvrage de nature différente', 'DEC', '16'),
    ('RefTypeDesordre:17', 'RefCategorieDesordre:7', 'Déjointement, pierres enlevées sur maçonnerie', 'DEJ', '17'),
    ('RefTypeDesordre:18', 'RefCategorieDesordre:3', 'Déstructuration d''un ouvrage (effondrement, démantèlement...)', 'DES', '18'),
    ('RefTypeDesordre:19', 'RefCategorieDesordre:5', 'Prélèvement de matériau de la digue', 'PRV', '19'),
    ('RefTypeDesordre:20', 'RefCategorieDesordre:5', 'Tête de piézomètre découverte ou observée lors de la visite', 'PZO', '20'),
    ('RefTypeDesordre:21', 'RefCategorieDesordre:6', 'Sondage (en principe à la tarière manuelle) effectué dans la digue au cours de la visite', 'SDG', '21'),
    ('RefTypeDesordre:22', 'RefCategorieDesordre:1', 'Surverse directe', 'SUD', '22'),
    ('RefTypeDesordre:23', 'RefCategorieDesordre:1', 'Surverse par retour', 'SUR', '23'),
    ('RefTypeDesordre:24', 'RefCategorieDesordre:4', 'Brèche par renard direct', 'BRD', '24'),
    ('RefTypeDesordre:25', 'RefCategorieDesordre:4', 'Brèche par renard en retour', 'BRR', '25'),
    ('RefTypeDesordre:26', 'RefCategorieDesordre:4', 'Brèche par surverse directe', 'BSD', '26'),
    ('RefTypeDesordre:27', 'RefCategorieDesordre:4', 'Brèche par surverse en retour', 'BSR', '27'),
    ('RefTypeDesordre:28', 'RefCategorieDesordre:5', 'Dépôt sauvage', 'DSA', '28'),
    ('RefTypeDesordre:29', 'RefCategorieDesordre:3', 'Renard Hydraulique', 'RDH', '29'),
    ('RefTypeDesordre:30', 'RefCategorieDesordre:3', 'Canalisation hors service', 'CHS', '30'),
    ('RefTypeDesordre:31', 'RefCategorieDesordre:4', 'Brèche par cause indéterminée', 'BRE', '31'),
    ('RefTypeDesordre:32', 'RefCategorieDesordre:3', 'Corps étranger affectant l''intégrité de la structure', 'COR', '32'),
    ('RefTypeDesordre:33', 'RefCategorieDesordre:4', 'Brèche provoquée', 'BPR', '33'),
    ('RefTypeDesordre:34', 'RefCategorieDesordre:4', 'Brèche par affouillement', 'BAF', '34'),
    ('RefTypeDesordre:35', 'RefCategorieDesordre:3', 'Arrachement d''une partie de la digue lié à une chute d''arbre', 'ARR', '35'),
    ('RefTypeDesordre:36', 'RefCategorieDesordre:5', 'Passage sauvage d''engins motorisés', 'PAS', '36'),
    ('RefTypeDesordre:37', 'RefCategorieDesordre:2', 'Fossé en pied de digue', 'FOS', '37'),
    ('RefTypeDesordre:38', 'RefCategorieDesordre:5', 'Vandalisme sur ouvrage de voirie', 'VAN', '38'),
    ('RefTypeDesordre:39', 'RefCategorieDesordre:5', 'Borne manquante', 'BMQ', '39'),
    ('RefTypeDesordre:40', 'RefCategorieDesordre:5', 'Borne détériorée', 'BDT', '40'),
    ('RefTypeDesordre:41', 'RefCategorieDesordre:6', 'Inondation par refoulement', 'INR', '41'),
    ('RefTypeDesordre:42', 'RefCategorieDesordre:5', 'Clôture "sauvage"sur la digue', 'CLO', '42'),
    ('RefTypeDesordre:43', 'RefCategorieDesordre:5', 'Bâtiment encastré dans la digue', 'BAT', '43'),
    ('RefTypeDesordre:44', 'RefCategorieDesordre:3', 'Vide important entre blocs d''enrochement', 'VID', '44'),
    ('RefTypeDesordre:45', 'RefCategorieDesordre:3', 'Nid de poule', 'NID', '45'),
    ('RefTypeDesordre:46', 'RefCategorieDesordre:6', 'Canalisation traversante', 'CAT', '46'),
    ('RefTypeDesordre:47', 'RefCategorieDesordre:6', 'Canalisation longitudinale', 'CAL', '47'),
    ('RefTypeDesordre:48', 'RefCategorieDesordre:5', 'Labour', 'LAB', '48'),
    ('RefTypeDesordre:49', 'RefCategorieDesordre:5', 'Remblais non autorisé contre la digue', 'REM', '49'),
    ('RefTypeDesordre:50', 'RefCategorieDesordre:5', 'Présence de végétation gênante', 'VEG', '50'),
    ('RefTypeDesordre:51', 'RefCategorieDesordre:1', 'Piétinements anthropiques', 'PIT', '51'),
    ('RefTypeDesordre:52', 'RefCategorieDesordre:5', 'Escalier sauvage', 'ESS', '52'),
    ('RefTypeDesordre:53', 'RefCategorieDesordre:3', 'Souches', 'SOU', '53'),
    ('RefTypeDesordre:54', 'RefCategorieDesordre:5', 'Escalier maçonné dans la digue', 'ESC', '54'),
    ('RefTypeDesordre:55', 'RefCategorieDesordre:5', 'Mur de soutènement', 'MUR', '55'),
    ('RefTypeDesordre:56', 'RefCategorieDesordre:4', 'En limite de surverse', 'ELS', '56'),
    ('RefTypeDesordre:57', 'RefCategorieDesordre:1', 'Affouillement', 'AFF', '57'),
    ('RefTypeDesordre:58', 'RefCategorieDesordre:7', 'Anomalie sur matelas de gabions', 'AMG', '58'),
    ('RefTypeDesordre:59', 'RefCategorieDesordre:7', 'Enrochement gélif', 'ERG', '59'),
    ('RefTypeDesordre:60', 'RefCategorieDesordre:7', 'Anomalie sur géo-grille', 'AGG', '60'),
    ('RefTypeDesordre:61', 'RefCategorieDesordre:7', 'Anomalie sur matériaux drainant', 'AMD', '61'),
    ('RefTypeDesordre:62', 'RefCategorieDesordre:7', 'Anomalie sur couvert végétal', 'ACV', '62'),
    ('RefTypeDesordre:63', 'RefCategorieDesordre:4', 'Défaut de bouclage de la digue (lien avec structure adjacente inexistant...)', 'DBD', '63'),
    ('RefTypeDesordre:64', 'RefCategorieDesordre:5', 'Défaut d''entretien de la végétation', 'DEV', '64'),
    ('RefTypeDesordre:65', 'RefCategorieDesordre:6', 'Ouvrage hydraulique privé (morceaux de digues, merlon...)', 'OHP', '65'),
    ('RefTypeDesordre:66', 'RefCategorieDesordre:5', 'Absence/désorganisation sucres bétons', 'SUC', '66'),
    ('RefTypeDesordre:67', 'RefCategorieDesordre:6', 'Dysfonctionnement d’ouvrage hydraulique', 'DOH', '67'),
    ('RefTypeDesordre:68', 'RefCategorieDesordre:6', 'Géométrie de la digue non adaptée', 'GDA', '68'),
    ('RefTypeDesordre:69', 'RefCategorieDesordre:5', 'Occupation sauvage de l’ouvrage', 'OSO', '69'),
    ('RefTypeDesordre:70', 'RefCategorieDesordre:5', 'Végétation invasive', 'VEI', '70'),
    ('RefTypeDesordre:71', 'RefCategorieDesordre:1', 'Détérioration/dégradation de surface (trous, bauges etc.)', 'DTS', '71'),
    ('RefTypeDesordre:72', 'RefCategorieDesordre:7', 'Anomalie sur Grillage Anti-fouisseur', 'AGA', '72'),
    ('RefTypeDesordre:73', 'RefCategorieDesordre:5', 'Défaut sur batardeau et/ou glissière (manquant, dégradé, cassé etc.)', 'DFB', '73'),
    ('RefTypeDesordre:99', 'RefCategorieDesordre:6', 'Indéfini', 'IND', '99');

INSERT INTO gemapi.types_equipement_mesure (id, code, abrege, libelle) VALUES
    ('PIE', 'piezometre', 'PIE', 'Piézomètre'),
    ('ECH', 'echelle_limnimetrique', 'ECH', 'Échelle limnimétrique'),
    ('STA', 'station_mesure', 'STA', 'Station de mesure'),
    ('RDN', 'repere_nivellement', 'RDN', 'Repère de nivellement'),
    ('AUT', 'autre', 'AUT', 'Autre équipement de mesure'),
    ('IND', 'indefini', 'IND', 'Équipement de mesure indéfini');

INSERT INTO gemapi.types_ouvrage_hydraulique (id, code, abrege, libelle) VALUES
    ('VAN', 'vanne', 'VAN', 'Vanne'),
    ('CLA', 'clapet', 'CLA', 'Clapet'),
    ('POS', 'poste_refoulement', 'POS', 'Poste de refoulement'),
    ('STP', 'station_pompage', 'STP', 'Station de pompage'),
    ('SIH', 'siphon', 'SIH', 'Siphon'),
    ('EXR', 'exutoire', 'EXR', 'Exutoire'),
    ('DEV', 'deversoir', 'DEV', 'Déversoir'),
    ('DVS', 'deversoir_securite', 'DVS', 'Déversoir de sécurité'),
    ('VBT', 'ouverture_batardable', 'VBT', 'Ouverture batardable'),
    ('PAF', 'porte_a_flot', 'PAF', 'Porte à flot'),
    ('BBN', 'barbacane', 'BBN', 'Barbacane'),
    ('TTB', 'tete_buse', 'TTB', 'Tête de buse'),
    ('TVI', 'tour_vidange', 'TVI', 'Tour de vidange'),
    ('FOD', 'fosse_decantation', 'FOD', 'Fosse de décantation'),
    ('PAS', 'passe_poissons', 'PAS', 'Passe à poissons'),
    ('SLT', 'seuil_lit', 'SLT', 'Seuil de lit'),
    ('PDL', 'plage_depot_lit', 'PDL', 'Plage de dépôt'),
    ('AUT', 'autre', 'AUT', 'Autre ouvrage hydraulique'),
    ('IND', 'indefini', 'IND', 'Ouvrage hydraulique indéfini'),
    ('PRISE_D_EAU', 'prise_d_eau', 'PDE', 'Prise d''eau'),
    ('OUVRAGE_HYDRAULIQUE', 'ouvrage_hydraulique', 'OHY', 'Ouvrage hydraulique');

INSERT INTO gemapi.types_ouvrage_particulier (id, code, libelle, designation) VALUES
    ('RefOuvrageParticulier:2', 'BAT', 'Bâtiment encastré ou très proche de la digue', '2'),
    ('RefOuvrageParticulier:3', 'ESC', 'Escalier', '3'),
    ('RefOuvrageParticulier:4', 'CAV', 'Cave encastrée dans la digue', '4'),
    ('RefOuvrageParticulier:6', 'CLO', 'Clôture privée (mur barbelé, grillage) encastrée dans la digue', '6'),
    ('RefOuvrageParticulier:7', 'PON', 'Ponton pour embarcation', '7'),
    ('RefOuvrageParticulier:8', 'PAN', 'Panneau d''information divers (EDF...)', '8'),
    ('RefOuvrageParticulier:10', 'OUI', 'Ouvrage indéfini', '10'),
    ('RefOuvrageParticulier:11', 'MOU', 'Monument (calvaire, monument aux morts, statue, ...)', '11'),
    ('RefOuvrageParticulier:12', 'BOK', 'Borne PK', '12'),
    ('RefOuvrageParticulier:14', 'SOB', 'Socle en béton', '14'),
    ('RefOuvrageParticulier:15', 'BRE', 'Borne réseau d''eau', '15'),
    ('RefOuvrageParticulier:17', 'PAS', 'Passe à poissons', '17'),
    ('RefOuvrageParticulier:19', 'OUA', 'Ouvrage d''alimentation de CIC', '19'),
    ('RefOuvrageParticulier:20', 'MRE', 'Mobilier à usage récréatif', '20'),
    ('RefOuvrageParticulier:21', 'PAA', 'Panneau d''alerte', '21'),
    ('RefOuvrageParticulier:99', 'IND', 'Indéfini', '99');

INSERT INTO gemapi.types_troncon (id, code, libelle, designation) VALUES
    ('BERGE', 'BERGE', 'Berge', NULL),
    ('TALUS', 'TALUS', 'Talus', NULL),
    ('TALUS_RISBERME', 'TALUS_RISBERME', 'Talus de risberme', NULL),
    ('RISBERME', 'RISBERME', 'Risberme', NULL),
    ('CRETE', 'CRETE', 'Crête', NULL),
    ('FRANC_BORD', 'FRANC_BORD', 'Franc-bord', NULL),
    ('INDETERMINE', 'INDETERMINE', 'Indéterminé', NULL);

INSERT INTO gemapi.typologies_troncon (id, designation, abrege, libelle) VALUES
    ('RefTypologieTroncon:1', '1', 'TER', 'Terre'),
    ('RefTypologieTroncon:2', '2', 'TEN', 'Terre – enrochement'),
    ('RefTypologieTroncon:3', '3', 'PAL', 'Palplanche'),
    ('RefTypologieTroncon:4', '4', 'GCI', 'Génie-Civil'),
    ('RefTypologieTroncon:99', '99', 'AUT', 'Autre');

INSERT INTO gemapi.urgences (id, libelle, abrege, designation) VALUES
    ('RefUrgence:1', 'Faible', '0', '1'),
    ('RefUrgence:3', 'Intermédiaire', '2', '3'),
    ('RefUrgence:4', 'Élevée', '3', '4'),
    ('RefUrgence:99', 'Indéfini', 'IND', '99');

INSERT INTO gemapi.types_mobilier (id, code, abrege, libelle) VALUES
    ('MRE', 'mobilier_recreatif', 'MRE', 'Mobilier récréatif'),
    ('PAN', 'panneau', 'PAN', 'Panneau'),
    ('CLO', 'cloture', 'CLO', 'Clôture'),
    ('MOU', 'monument', 'MOU', 'Monument'),
    ('SOB', 'socle', 'SOB', 'Socle'),
    ('PON', 'ponton', 'PON', 'Ponton'),
    ('AUT', 'autre', 'AUT', 'Autre mobilier'),
    ('IND', 'indefini', 'IND', 'Mobilier indéfini'),
    ('BAR', 'barriere_chaine', 'BAR', 'Barrière / chaîne'),
    ('POR', 'portail', 'POR', 'Portail'),
    ('BOR', 'borne_voirie', 'BOR', 'Borne de voirie'),
    ('OBS', 'obstruction_passage', 'OBS', 'Ouvrage d’obstruction du passage');

INSERT INTO gemapi.types_cheminement (id, code, abrege, libelle) VALUES
    ('PNT', 'pont', 'PNT', 'Pont'),
    ('RAM', 'rampe', 'RAM', 'Rampe'),
    ('TUN', 'tunnel', 'TUN', 'Tunnel'),
    ('PAS', 'passage_gue', 'PAS', 'Passage à gué'),
    ('ESC', 'escalier_acces', 'ESC', 'Escalier d''accès'),
    ('CAL', 'cale', 'CAL', 'Cale'),
    ('VAC', 'voie_acces', 'VAC', 'Voie d''accès'),
    ('CAC', 'chemin_acces_technique', 'CAC', 'Chemin d''accès technique'),
    ('CHE', 'voie_sur_digue', 'CHE', 'Voie sur digue'),
    ('AUT', 'autre', 'AUT', 'Autre cheminement'),
    ('IND', 'indefini', 'IND', 'Cheminement indéfini');

INSERT INTO gemapi.familles_vegetation (id, code, libelle, designation) VALUES
    ('HERBACEES', 'HERBACEES', 'Herbacées', NULL),
    ('BUISSONS_ARBUSTES', 'BUISSONS_ARBUSTES', 'Buissons et arbustes', NULL),
    ('ARBRES', 'ARBRES', 'Arbres', NULL);

CREATE TABLE gemapi.techniciens (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    nom text NOT NULL,
    prenom text,
    login text UNIQUE,
    actif boolean NOT NULL DEFAULT true,
    cree_le timestamptz NOT NULL DEFAULT clock_timestamp(),
    modifie_par text,
    date_modification timestamptz
);
CREATE TABLE gemapi.communes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    insee text NOT NULL UNIQUE,
    nom text NOT NULL,
    geom geometry(MultiPolygon,3950) NOT NULL,
    cree_le timestamptz NOT NULL DEFAULT clock_timestamp(),
    modifie_par text,
    date_modification timestamptz,
    CHECK(ST_IsValid(geom) AND NOT ST_IsEmpty(geom) AND ST_NDims(geom)=2)
);
CREATE TABLE gemapi.prestataires (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    nom text NOT NULL,
    actif boolean NOT NULL DEFAULT true,
    cree_le timestamptz NOT NULL DEFAULT clock_timestamp(),
    modifie_par text,
    date_modification timestamptz
);
CREATE TABLE gemapi.ah (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    nom text NOT NULL,
    cree_le timestamptz NOT NULL DEFAULT clock_timestamp(),
    modifie_par text,
    date_modification timestamptz
);
CREATE TABLE gemapi.ah_stockage (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ah_id uuid NOT NULL UNIQUE REFERENCES gemapi.ah(id) ON DELETE RESTRICT,
    nom text NOT NULL,
    categorie text,
    surface_ha numeric,
    geom geometry(Polygon,3950) NOT NULL,
    cree_le timestamptz NOT NULL DEFAULT clock_timestamp(),
    modifie_par text,
    date_modification timestamptz,
    CONSTRAINT ah_stockage_geom_contract CHECK (NOT ST_IsEmpty(geom) AND ST_IsValid(geom) AND ST_NDims(geom)=2)
);
CREATE TABLE gemapi.ah_ouvrage (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ah_id uuid NOT NULL REFERENCES gemapi.ah(id) ON DELETE RESTRICT,
    nom text,
    geom geometry(LineString,3950) NOT NULL,
    cree_le timestamptz NOT NULL DEFAULT clock_timestamp(),
    modifie_par text,
    date_modification timestamptz,
    CONSTRAINT ah_ouvrage_geom_contract CHECK (NOT ST_IsEmpty(geom) AND ST_IsValid(geom) AND ST_NDims(geom)=2)
);
-- Contrôle de type explicite : le typmod MultiPolygon convertirait un Polygon à l'écriture.
CREATE TABLE gemapi.sites_terrestres (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    nom text NOT NULL,
    geom geometry(Geometry,3950) NOT NULL,
    cree_le timestamptz NOT NULL DEFAULT clock_timestamp(),
    modifie_par text,
    date_modification timestamptz,
    CONSTRAINT sites_terrestres_geom_contract CHECK (NOT ST_IsEmpty(geom) AND ST_IsValid(geom) AND ST_NDims(geom)=2 AND ST_GeometryType(geom)='ST_MultiPolygon')
);
CREATE TABLE gemapi.cours_eau (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    nom text NOT NULL CONSTRAINT cours_eau_nom_non_vide CHECK (nom ~ '[^[:space:]]'),
    type_ecoulement text,
    direction boolean,
    section integer GENERATED ALWAYS AS (ST_NumGeometries(geom)) STORED,
    valide boolean GENERATED ALWAYS AS (direction IS TRUE AND ST_NumGeometries(geom)=1) STORED NOT NULL,
    source text NOT NULL,
    geom geometry(MultiLineString,3950) NOT NULL,
    cree_le timestamptz NOT NULL DEFAULT clock_timestamp(),
    modifie_par text,
    date_modification timestamptz,
    CHECK(ST_IsValid(geom) AND NOT ST_IsEmpty(geom) AND ST_NDims(geom)=2)
);
-- Digues et repères du modèle courant.
CREATE TABLE gemapi.digues (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    nom text NOT NULL CHECK (btrim(nom) <> ''),
    identifiant_externe text,
    commentaire text,
    cree_le timestamptz NOT NULL DEFAULT clock_timestamp(),
    modifie_par text,
    date_modification timestamptz
);

-- Les fractions portent sur l'orientation actuelle de la rivière. La
-- géométrie copiée demeure inchangée si le référentiel est redessiné.
CREATE TABLE gemapi.digues_reperes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    digue_id uuid NOT NULL REFERENCES gemapi.digues(id) ON DELETE RESTRICT,
    riviere_id uuid NOT NULL REFERENCES gemapi.cours_eau(id) ON DELETE RESTRICT,
    rive text NOT NULL CHECK (rive IN ('G', 'D')),
    debut_fraction double precision NOT NULL CHECK (debut_fraction BETWEEN 0 AND 1),
    fin_fraction double precision NOT NULL CHECK (fin_fraction BETWEEN 0 AND 1),
    geom_reference geometry(LineString, 3950) NOT NULL,
    geom_riviere_signature text NOT NULL,
    revision_validation integer NOT NULL DEFAULT 1 CHECK (revision_validation > 0),
    cree_le timestamptz NOT NULL DEFAULT clock_timestamp(),
    modifie_par text,
    date_modification timestamptz,
    CONSTRAINT digues_reperes_digue_unique UNIQUE (digue_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT digues_reperes_ordre CHECK (debut_fraction < fin_fraction),
    CONSTRAINT digues_reperes_geom_valide CHECK (
        ST_IsValid(geom_reference) AND NOT ST_IsEmpty(geom_reference)
        AND ST_Length(geom_reference) > 0
    )
);
-- Référencement hydrographique AH : décisions explicites, instantanés figés.
CREATE TABLE gemapi.ah_sections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ah_id uuid NOT NULL REFERENCES gemapi.ah(id) ON DELETE RESTRICT,
    cours_eau_id uuid NOT NULL REFERENCES gemapi.cours_eau(id) ON DELETE RESTRICT,
    debut_fraction double precision NOT NULL,
    fin_fraction double precision NOT NULL,
    geom_reference geometry(LineString,3950) NOT NULL,
    geom_cours_eau_signature text NOT NULL,
    revision_validation integer NOT NULL DEFAULT 1 CHECK (revision_validation > 0),
    valide_par text NOT NULL,
    date_validation timestamptz NOT NULL,
    commentaire text,
    cree_le timestamptz NOT NULL DEFAULT clock_timestamp(),
    modifie_par text,
    date_modification timestamptz,
    CONSTRAINT ah_sections_intervalle CHECK (
        debut_fraction >= 0 AND debut_fraction < fin_fraction AND fin_fraction <= 1),
    CONSTRAINT ah_sections_intervalle_unique UNIQUE (ah_id,cours_eau_id,debut_fraction,fin_fraction),
    CONSTRAINT ah_sections_geom_contract CHECK (
        NOT ST_IsEmpty(geom_reference) AND ST_IsValid(geom_reference)
        AND ST_NDims(geom_reference)=2 AND ST_Length(geom_reference)>0)
);
CREATE TABLE gemapi.troncons (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    type_troncon_id text NOT NULL REFERENCES gemapi.types_troncon(id) ON DELETE RESTRICT,
    digue_id uuid NOT NULL REFERENCES gemapi.digues(id) ON DELETE RESTRICT,
    date_debut date,
    date_fin date,
    commentaire text,
    designation text,
    type_rive_id text REFERENCES gemapi.rives(id) ON DELETE RESTRICT,
    typologie_troncon_id text REFERENCES gemapi.typologies_troncon(id) ON DELETE RESTRICT,
    geometry geometry(Geometry,3950),
    geometry_reperage geometry(LineString,3950),
    valid boolean NOT NULL DEFAULT false,
    version bigint NOT NULL DEFAULT 1 CONSTRAINT ck_version_bornee CHECK (version BETWEEN 1 AND 9007199254740991),
    source_id text REFERENCES gemapi.sources_information(id) ON DELETE RESTRICT,
    cote_id text REFERENCES gemapi.cotes(id) ON DELETE RESTRICT,
    position_id text REFERENCES gemapi.positions(id) ON DELETE RESTRICT,
    fonction_id text REFERENCES gemapi.fonctions_structure(id) ON DELETE RESTRICT,
    fonction_haut_id text REFERENCES gemapi.fonctions_structure(id) ON DELETE RESTRICT,
    fonction_bas_id text REFERENCES gemapi.fonctions_structure(id) ON DELETE RESTRICT,
    nature_id text REFERENCES gemapi.natures_structure(id) ON DELETE RESTRICT,
    nature_haut_id text REFERENCES gemapi.natures_structure(id) ON DELETE RESTRICT,
    nature_bas_id text REFERENCES gemapi.natures_structure(id) ON DELETE RESTRICT,
    materiau_id text REFERENCES gemapi.materiaux_structure(id) ON DELETE RESTRICT,
    materiau_haut_id text REFERENCES gemapi.materiaux_structure(id) ON DELETE RESTRICT,
    materiau_bas_id text REFERENCES gemapi.materiaux_structure(id) ON DELETE RESTRICT,
    num_couche integer,
    cree_le timestamptz NOT NULL DEFAULT clock_timestamp(),
    modifie_par text,
    date_modification timestamptz,
    CHECK (date_fin >= date_debut),
    CONSTRAINT uq_troncons_type UNIQUE (id, type_troncon_id),
    CONSTRAINT ck_troncons_type CHECK (type_troncon_id IN
        ('BERGE','TALUS','TALUS_RISBERME','RISBERME','CRETE','FRANC_BORD','INDETERMINE')),
    CONSTRAINT ck_troncons_geometry_b4 CHECK (
        NOT valid OR type_troncon_id NOT IN ('RISBERME','FRANC_BORD') OR
        (geometry IS NOT NULL AND ST_GeometryType(geometry) IN ('ST_LineString','ST_Polygon')
         AND ST_IsValid(geometry) AND NOT ST_IsEmpty(geometry))),
    CONSTRAINT ck_troncons_axe_reperage CHECK (geometry_reperage IS NULL OR
        (ST_IsValid(geometry_reperage) AND NOT ST_IsEmpty(geometry_reperage) AND ST_Length(geometry_reperage)>0)),
    CONSTRAINT ck_troncons_crete_fields CHECK (type_troncon_id <> 'CRETE' OR num_nonnulls(
        cote_id, position_id, fonction_haut_id, fonction_bas_id, nature_haut_id, nature_bas_id,
        materiau_haut_id, materiau_bas_id) = 0)
);

CREATE TABLE gemapi.mobilier (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    designation text NOT NULL,
    type_id text NOT NULL REFERENCES gemapi.types_mobilier(id) ON DELETE RESTRICT,
    commentaire text,
    date_debut date,
    date_fin date,
    geometry geometry(Geometry,3950) NOT NULL,
    troncon_id uuid REFERENCES gemapi.troncons(id) ON DELETE RESTRICT,
    ah_ouvrage_id uuid REFERENCES gemapi.ah_ouvrage(id) ON DELETE RESTRICT,
    site_terrestre_id uuid REFERENCES gemapi.sites_terrestres(id) ON DELETE RESTRICT,
    ah_stockage_id uuid REFERENCES gemapi.ah_stockage(id) ON DELETE RESTRICT,
    ah_section_id uuid REFERENCES gemapi.ah_sections(id) ON DELETE RESTRICT,
    valid boolean NOT NULL DEFAULT false,
    version bigint NOT NULL DEFAULT 1 CONSTRAINT ck_version_bornee CHECK (version BETWEEN 1 AND 9007199254740991),
    cree_le timestamptz NOT NULL DEFAULT clock_timestamp(),
    modifie_par text,
    date_modification timestamptz,
    CHECK (date_fin >= date_debut),
    CONSTRAINT mobilier_parent_unique CHECK (num_nonnulls(
        site_terrestre_id, troncon_id, ah_ouvrage_id, ah_stockage_id, ah_section_id) = 1),
    CONSTRAINT mobilier_geom_contract CHECK (NOT ST_IsEmpty(geometry) AND ST_IsValid(geometry) AND ST_GeometryType(geometry) IN
        ('ST_Point','ST_LineString','ST_Polygon','ST_MultiPoint','ST_MultiLineString','ST_MultiPolygon')),
    CHECK (designation ~ '[^[:space:]]')
);

CREATE TABLE gemapi.cheminements (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    designation text NOT NULL,
    type_cheminement_id text NOT NULL REFERENCES gemapi.types_cheminement(id) ON DELETE RESTRICT,
    commentaire text,
    date_debut date,
    date_fin date,
    geometry geometry(Geometry,3950) NOT NULL,
    troncon_id uuid REFERENCES gemapi.troncons(id) ON DELETE RESTRICT,
    ah_ouvrage_id uuid REFERENCES gemapi.ah_ouvrage(id) ON DELETE RESTRICT,
    site_terrestre_id uuid REFERENCES gemapi.sites_terrestres(id) ON DELETE RESTRICT,
    ah_stockage_id uuid REFERENCES gemapi.ah_stockage(id) ON DELETE RESTRICT,
    ah_section_id uuid REFERENCES gemapi.ah_sections(id) ON DELETE RESTRICT,
    valid boolean NOT NULL DEFAULT false,
    version bigint NOT NULL DEFAULT 1 CONSTRAINT ck_version_bornee CHECK (version BETWEEN 1 AND 9007199254740991),
    cree_le timestamptz NOT NULL DEFAULT clock_timestamp(),
    modifie_par text,
    date_modification timestamptz,
    CHECK (date_fin >= date_debut),
    CONSTRAINT cheminements_parent_unique CHECK (num_nonnulls(
        site_terrestre_id, troncon_id, ah_ouvrage_id, ah_stockage_id, ah_section_id) = 1),
    CONSTRAINT cheminements_geom_contract CHECK (NOT ST_IsEmpty(geometry) AND ST_IsValid(geometry) AND ST_GeometryType(geometry) IN
        ('ST_Point','ST_LineString','ST_Polygon','ST_MultiPoint','ST_MultiLineString','ST_MultiPolygon')),
    CHECK (designation ~ '[^[:space:]]')
);

CREATE TABLE gemapi.vegetation (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    designation text NOT NULL,
    famille_vegetation text NOT NULL REFERENCES gemapi.familles_vegetation(id) ON DELETE RESTRICT,
    commentaire text,
    date_debut date,
    date_fin date,
    geometry geometry(Geometry,3950) NOT NULL,
    troncon_id uuid REFERENCES gemapi.troncons(id) ON DELETE RESTRICT,
    ah_ouvrage_id uuid REFERENCES gemapi.ah_ouvrage(id) ON DELETE RESTRICT,
    site_terrestre_id uuid REFERENCES gemapi.sites_terrestres(id) ON DELETE RESTRICT,
    ah_stockage_id uuid REFERENCES gemapi.ah_stockage(id) ON DELETE RESTRICT,
    ah_section_id uuid REFERENCES gemapi.ah_sections(id) ON DELETE RESTRICT,
    valid boolean NOT NULL DEFAULT false,
    version bigint NOT NULL DEFAULT 1 CONSTRAINT ck_version_bornee CHECK (version BETWEEN 1 AND 9007199254740991),
    cree_le timestamptz NOT NULL DEFAULT clock_timestamp(),
    modifie_par text,
    date_modification timestamptz,
    CHECK (date_fin >= date_debut),
    CONSTRAINT vegetation_parent_unique CHECK (num_nonnulls(
        site_terrestre_id, troncon_id, ah_ouvrage_id, ah_stockage_id, ah_section_id) = 1),
    CONSTRAINT vegetation_geom_contract CHECK (NOT ST_IsEmpty(geometry) AND ST_IsValid(geometry) AND ST_GeometryType(geometry) IN
        ('ST_Point','ST_LineString','ST_Polygon','ST_MultiPoint','ST_MultiLineString','ST_MultiPolygon')),
    CHECK (designation ~ '[^[:space:]]')
);

CREATE TABLE gemapi.ouvrages_particuliers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    designation text,
    commentaire text,
    date_debut date,
    date_fin date,
    geometry geometry(Geometry,3950),
    troncon_id uuid REFERENCES gemapi.troncons(id) ON DELETE RESTRICT,
    valid boolean NOT NULL DEFAULT false,
    version bigint NOT NULL DEFAULT 1 CONSTRAINT ck_version_bornee CHECK (version BETWEEN 1 AND 9007199254740991),
    type_id text REFERENCES gemapi.types_ouvrage_particulier(id) ON DELETE RESTRICT,
    cree_le timestamptz NOT NULL DEFAULT clock_timestamp(),
    modifie_par text,
    date_modification timestamptz,
    CHECK (date_fin >= date_debut)
);

CREATE TABLE gemapi.equipements_mesure (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    type_id text NOT NULL REFERENCES gemapi.types_equipement_mesure(id) ON DELETE RESTRICT,
    designation text,
    commentaire text,
    date_debut date,
    date_fin date,
    cote_id text REFERENCES gemapi.cotes(id) ON DELETE RESTRICT,
    position_id text REFERENCES gemapi.positions(id) ON DELETE RESTRICT,
    source_id text REFERENCES gemapi.sources_information(id) ON DELETE RESTRICT,
    dimension_horizontale double precision,
    dimension_verticale double precision,
    geometry geometry(Geometry,3950),
    troncon_id uuid REFERENCES gemapi.troncons(id) ON DELETE RESTRICT,
    valid boolean,
    version bigint NOT NULL DEFAULT 1 CONSTRAINT ck_version_bornee CHECK (version BETWEEN 1 AND 9007199254740991),
    cree_le timestamptz NOT NULL DEFAULT clock_timestamp(),
    modifie_par text,
    date_modification timestamptz,
    CHECK (date_fin >= date_debut)
);

CREATE TABLE gemapi.ouvrages_hydrauliques (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    type_id text REFERENCES gemapi.types_ouvrage_hydraulique(id) ON DELETE RESTRICT,
    designation text,
    commentaire text,
    date_debut date,
    date_fin date,
    geometry geometry(Geometry,3950),
    troncon_id uuid REFERENCES gemapi.troncons(id) ON DELETE RESTRICT,
    ah_ouvrage_id uuid REFERENCES gemapi.ah_ouvrage(id) ON DELETE RESTRICT,
    ah_stockage_id uuid REFERENCES gemapi.ah_stockage(id) ON DELETE RESTRICT,
    valid boolean,
    longueur double precision,
    largeur_max double precision,
    hauteur double precision,
    annee_construction integer,
    cree_le timestamptz NOT NULL DEFAULT clock_timestamp(),
    modifie_par text,
    date_modification timestamptz,
    CHECK (date_fin >= date_debut),
    CONSTRAINT ck_ouvrage_parent_unique CHECK (num_nonnulls(troncon_id, ah_ouvrage_id, ah_stockage_id) = 1)
);

CREATE TABLE gemapi.desordres (
    troncon_id uuid REFERENCES gemapi.troncons(id) ON DELETE RESTRICT,
    digue_id uuid REFERENCES gemapi.digues(id) ON DELETE RESTRICT,
    ah_ouvrage_id uuid REFERENCES gemapi.ah_ouvrage(id) ON DELETE RESTRICT,
    ah_section_id uuid REFERENCES gemapi.ah_sections(id) ON DELETE RESTRICT,
    ah_stockage_id uuid REFERENCES gemapi.ah_stockage(id) ON DELETE RESTRICT,
    site_terrestre_id uuid REFERENCES gemapi.sites_terrestres(id) ON DELETE RESTRICT,
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    type_desordre_id text REFERENCES gemapi.types_desordre(id) ON DELETE RESTRICT,
    categorie_desordre_id text REFERENCES gemapi.categories_desordre(id) ON DELETE RESTRICT,
    designation text,
    commentaire text,
    lieu_dit text,
    cote_id text REFERENCES gemapi.cotes(id) ON DELETE RESTRICT,
    position_id text REFERENCES gemapi.positions(id) ON DELETE RESTRICT,
    source_id text REFERENCES gemapi.sources_information(id) ON DELETE RESTRICT,
    date_debut date,
    date_fin date,
    geometry geometry(Geometry,3950),
    valid boolean,
    version bigint NOT NULL DEFAULT 1 CONSTRAINT ck_version_bornee CHECK (version BETWEEN 1 AND 9007199254740991),
    cree_le timestamptz NOT NULL DEFAULT clock_timestamp(),
    mobilier_id uuid REFERENCES gemapi.mobilier(id) ON DELETE RESTRICT,
    cheminement_id uuid REFERENCES gemapi.cheminements(id) ON DELETE RESTRICT,
    vegetation_id uuid REFERENCES gemapi.vegetation(id) ON DELETE RESTRICT,
    modifie_par text,
    date_modification timestamptz,
    CHECK (date_fin >= date_debut),
    CONSTRAINT ck_desordre_cible_directe CHECK (num_nonnulls(
        troncon_id, digue_id, ah_ouvrage_id, ah_section_id, ah_stockage_id, site_terrestre_id,
        mobilier_id, cheminement_id, vegetation_id) <= 1)
);

CREATE TABLE gemapi.observations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    desordre_id uuid REFERENCES gemapi.desordres(id) ON DELETE RESTRICT,
    troncon_id uuid REFERENCES gemapi.troncons(id) ON DELETE RESTRICT,
    ouvrage_hydraulique_id uuid REFERENCES gemapi.ouvrages_hydrauliques(id) ON DELETE RESTRICT,
    equipement_mesure_id uuid REFERENCES gemapi.equipements_mesure(id) ON DELETE RESTRICT,
    cheminement_id uuid REFERENCES gemapi.cheminements(id) ON DELETE RESTRICT,
    mobilier_id uuid REFERENCES gemapi.mobilier(id) ON DELETE RESTRICT,
    vegetation_id uuid REFERENCES gemapi.vegetation(id) ON DELETE RESTRICT,
    ouvrage_particulier_id uuid REFERENCES gemapi.ouvrages_particuliers(id) ON DELETE RESTRICT,
    urgence_id text REFERENCES gemapi.urgences(id) ON DELETE RESTRICT,
    designation text,
    date_observation date NOT NULL,
    evolution text,
    suite text,
    suite_apporter_id text REFERENCES gemapi.suites_apporter(id) ON DELETE RESTRICT,
    nombre_desordres integer,
    valid boolean NOT NULL DEFAULT false,
    version bigint NOT NULL DEFAULT 1 CONSTRAINT ck_version_bornee CHECK (version BETWEEN 1 AND 9007199254740991),
    cree_le timestamptz NOT NULL DEFAULT clock_timestamp(),
    modifie_par text,
    date_modification timestamptz,
    ah_ouvrage_id uuid REFERENCES gemapi.ah_ouvrage(id) ON DELETE RESTRICT,
    ah_stockage_id uuid REFERENCES gemapi.ah_stockage(id) ON DELETE RESTRICT,
    ah_section_id uuid REFERENCES gemapi.ah_sections(id) ON DELETE RESTRICT,
    site_terrestre_id uuid REFERENCES gemapi.sites_terrestres(id) ON DELETE RESTRICT,
    observateur text,
    CONSTRAINT ck_cible_unique CHECK (num_nonnulls(
        desordre_id, troncon_id, ah_ouvrage_id, ah_stockage_id, ah_section_id, site_terrestre_id,
        mobilier_id, cheminement_id, vegetation_id, ouvrage_hydraulique_id, ouvrage_particulier_id, equipement_mesure_id) = 1)
);

CREATE TABLE gemapi.photos (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    observation_id uuid NOT NULL REFERENCES gemapi.observations(id) ON DELETE RESTRICT,
    date_photo date,
    designation text,
    commentaire text,
    valid boolean NOT NULL DEFAULT false,
    version bigint NOT NULL DEFAULT 1 CONSTRAINT ck_version_bornee CHECK (version BETWEEN 1 AND 9007199254740991),
    storage_key text UNIQUE,
    cree_le timestamptz NOT NULL DEFAULT clock_timestamp(),
    modifie_par text,
    date_modification timestamptz,
    photographe text
);

CREATE TABLE gemapi.equipements_mesure__desordres (
    equipement_mesure_id uuid NOT NULL REFERENCES gemapi.equipements_mesure(id) ON DELETE RESTRICT,
    desordre_id uuid NOT NULL REFERENCES gemapi.desordres(id) ON DELETE RESTRICT,
    PRIMARY KEY (equipement_mesure_id, desordre_id)
);

CREATE TABLE gemapi.ouvrages_hydrauliques__desordres (
    ouvrage_hydraulique_id uuid NOT NULL REFERENCES gemapi.ouvrages_hydrauliques(id) ON DELETE RESTRICT,
    desordre_id uuid NOT NULL REFERENCES gemapi.desordres(id) ON DELETE RESTRICT,
    PRIMARY KEY (ouvrage_hydraulique_id, desordre_id)
);

CREATE TABLE gemapi.ouvrages_particuliers__desordres (
    ouvrage_particulier_id uuid NOT NULL REFERENCES gemapi.ouvrages_particuliers(id) ON DELETE RESTRICT,
    desordre_id uuid NOT NULL REFERENCES gemapi.desordres(id) ON DELETE RESTRICT,
    PRIMARY KEY (ouvrage_particulier_id, desordre_id)
);

CREATE TABLE gemapi.ouvrages_hydrauliques__objets_lies (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ouvrage_hydraulique_id uuid NOT NULL REFERENCES gemapi.ouvrages_hydrauliques(id) ON DELETE RESTRICT,
    cheminement_id uuid REFERENCES gemapi.cheminements(id) ON DELETE RESTRICT,
    autre_ouvrage_hydraulique_id uuid REFERENCES gemapi.ouvrages_hydrauliques(id) ON DELETE RESTRICT,
    ouvrage_particulier_id uuid REFERENCES gemapi.ouvrages_particuliers(id) ON DELETE RESTRICT,
    equipement_mesure_id uuid REFERENCES gemapi.equipements_mesure(id) ON DELETE RESTRICT,
    berge_id uuid,
    berge_id_type text GENERATED ALWAYS AS ('BERGE'::text) STORED,
    digue_id uuid REFERENCES gemapi.digues(id) ON DELETE RESTRICT,
    CONSTRAINT fk_ouvrage_lie_berge FOREIGN KEY (berge_id, berge_id_type)
        REFERENCES gemapi.troncons(id, type_troncon_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_cible_ouvrage_hydraulique_liee CHECK (num_nonnulls(
        cheminement_id, autre_ouvrage_hydraulique_id, ouvrage_particulier_id, equipement_mesure_id, berge_id, digue_id) = 1)
);

CREATE FUNCTION gemapi.cours_eau_qualification() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF ST_AsEWKB(NEW.geom) IS DISTINCT FROM ST_AsEWKB(OLD.geom) THEN
        NEW.direction := NULL;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER hydro_qual_direction BEFORE UPDATE OF geom ON gemapi.cours_eau
FOR EACH ROW EXECUTE FUNCTION gemapi.cours_eau_qualification();
CREATE TABLE gemapi.prestations (
    cours_eau_id uuid REFERENCES gemapi.cours_eau(id) ON DELETE RESTRICT,
    digue_id uuid REFERENCES gemapi.digues(id) ON DELETE RESTRICT,
    ah_stockage_id uuid REFERENCES gemapi.ah_stockage(id) ON DELETE RESTRICT,
    ah_ouvrage_id uuid REFERENCES gemapi.ah_ouvrage(id) ON DELETE RESTRICT,
    site_terrestre_id uuid REFERENCES gemapi.sites_terrestres(id) ON DELETE RESTRICT,
    troncon_id uuid REFERENCES gemapi.troncons(id) ON DELETE RESTRICT,
    mobilier_id uuid REFERENCES gemapi.mobilier(id) ON DELETE RESTRICT,
    cheminement_id uuid REFERENCES gemapi.cheminements(id) ON DELETE RESTRICT,
    vegetation_id uuid REFERENCES gemapi.vegetation(id) ON DELETE RESTRICT,
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    intitule text,
    type_prestation_id uuid NOT NULL REFERENCES gemapi.types_prestations(id) ON DELETE RESTRICT,
    prestataire_id uuid NOT NULL REFERENCES gemapi.prestataires(id) ON DELETE RESTRICT,
    statut_id uuid NOT NULL REFERENCES gemapi.statuts_prestations(id) ON DELETE RESTRICT,
    date_debut date NOT NULL,
    date_fin date NOT NULL,
    quantite numeric,
    unite text,
    description text,
    position text NOT NULL DEFAULT 'Indéfini' CONSTRAINT prestations_position_values CHECK (position IN ('Berge','Talus côté rivière / stockage','Crête','Talus côté terre','Indéfini')),
    rive text NOT NULL DEFAULT 'Indéfinie' CONSTRAINT prestations_rive_values CHECK (rive IN ('Gauche','Droite','Les deux','Indéfinie')),
    photo_1 text,
    photo_1_commentaire text,
    photo_2 text,
    photo_2_commentaire text,
    geom geometry(Geometry,3950),
    technicien_id uuid NOT NULL REFERENCES gemapi.techniciens(id) ON DELETE RESTRICT,
    cree_le timestamptz NOT NULL DEFAULT clock_timestamp(),
    modifie_par text,
    date_modification timestamptz,
    CHECK(date_fin >= date_debut),
    CONSTRAINT prestations_geom_contract CHECK(geom IS NULL OR (NOT ST_IsEmpty(geom) AND ST_IsValid(geom) AND ST_SRID(geom)=3950 AND ST_NDims(geom)=2 AND ST_GeometryType(geom) IN ('ST_Point','ST_LineString','ST_Polygon','ST_MultiPoint','ST_MultiLineString','ST_MultiPolygon')))
);
-- L'ensemble des rattachements et la position sont contrôlés sur l'état final
-- de la transaction, sans ordre de saisie imposé au formulaire QGIS.
ALTER TABLE gemapi.prestations ADD COLUMN nombre_points integer,
ADD COLUMN longueur_m double precision,
ADD COLUMN surface_m2 double precision,
ADD COLUMN surface_ha double precision GENERATED ALWAYS AS(surface_m2/10000.0) STORED,
ADD COLUMN type_geometrie text,
ADD COLUMN type_geometrie_postgis text,
ADD COLUMN possede_geometrie boolean NOT NULL DEFAULT false,
ADD COLUMN origine_mesures text NOT NULL DEFAULT 'Aucune géométrie',
ADD COLUMN ah_section_id uuid REFERENCES gemapi.ah_sections(id) ON DELETE RESTRICT;
COMMENT ON COLUMN gemapi.prestations.ah_section_id IS
'Section AH directement rattachée ; géométrie propre de la prestation obligatoire, aucun héritage de geom_reference.';

ALTER TABLE gemapi.prestations ADD CONSTRAINT prestations_direct_modes
CHECK (num_nonnulls(cours_eau_id, digue_id, troncon_id, ah_stockage_id, ah_ouvrage_id, ah_section_id, site_terrestre_id, mobilier_id, cheminement_id, vegetation_id)<=1);
ALTER TABLE gemapi.prestations ADD CONSTRAINT prestations_direct_geometry
CHECK ((cours_eau_id IS NULL AND digue_id IS NULL AND troncon_id IS NULL AND site_terrestre_id IS NULL AND ah_section_id IS NULL) OR geom IS NOT NULL);
ALTER TABLE gemapi.prestations ADD CONSTRAINT prestations_site_multi_geometry
CHECK (site_terrestre_id IS NULL OR ST_GeometryType(geom) IN
       ('ST_MultiPoint','ST_MultiLineString','ST_MultiPolygon'));
CREATE FUNCTION gemapi.assert_prestation_relations(prestation_uuid uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $relations_assert$
DECLARE p gemapi.prestations%ROWTYPE;
BEGIN
    SELECT * INTO p FROM gemapi.prestations WHERE id=prestation_uuid FOR UPDATE;
    IF NOT FOUND THEN RETURN; END IF;
    IF num_nonnulls(p.cours_eau_id, p.digue_id, p.troncon_id, p.ah_stockage_id, p.ah_ouvrage_id, p.ah_section_id, p.site_terrestre_id, p.mobilier_id, p.cheminement_id, p.vegetation_id)<>1 THEN
        RAISE EXCEPTION 'Exactement un mode de rattachement patrimonial est obligatoire pour %',p.id USING ERRCODE='23514';
    END IF;
    IF (p.position<>'Indéfini' OR p.rive<>'Indéfinie') AND p.cours_eau_id IS NULL AND p.digue_id IS NULL AND p.ah_ouvrage_id IS NULL AND p.ah_section_id IS NULL THEN
        RAISE EXCEPTION 'Position ou rive incompatible avec le rattachement patrimonial' USING ERRCODE='23514';
    END IF;
END $relations_assert$;
CREATE FUNCTION gemapi.prestation_relations_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, gemapi, pg_temp AS $relations_trigger$
BEGIN
    IF TG_OP <> 'DELETE' THEN PERFORM gemapi.assert_prestation_relations(NEW.id); END IF;
    RETURN NULL;
END $relations_trigger$;
REVOKE ALL ON FUNCTION gemapi.assert_prestation_relations(uuid), gemapi.prestation_relations_complete() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER prestation_relations_parent AFTER INSERT OR UPDATE ON gemapi.prestations
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION gemapi.prestation_relations_complete();
-- session_user conserve le role de connexion individuel meme apres SET ROLE.
CREATE FUNCTION gemapi.stamp_modification() RETURNS trigger LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
 IF TG_OP='UPDATE' AND TG_ARGV[0]='metier' THEN
     IF NEW.cree_le IS DISTINCT FROM OLD.cree_le THEN
         RAISE EXCEPTION 'cree_le est immuable' USING ERRCODE='23514';
     END IF;
 END IF;
 NEW.modifie_par := session_user;
 NEW.date_modification := clock_timestamp();
 RETURN NEW;
END $$;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['techniciens','communes','prestataires','ah','ah_stockage','ah_ouvrage','sites_terrestres','cours_eau','prestations'] LOOP
   EXECUTE format('CREATE TRIGGER tr_stamp_%I BEFORE INSERT OR UPDATE ON gemapi.%I FOR EACH ROW EXECUTE FUNCTION gemapi.stamp_modification(''metier'')',t,t);
 END LOOP;
END $$;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['communes','ah_stockage','ah_ouvrage','sites_terrestres','cours_eau','prestations'] LOOP
   EXECUTE format('CREATE INDEX %I ON gemapi.%I USING gist(geom)',t||'_geom_idx',t);
 END LOOP;
END $$;
CREATE TRIGGER tr_stamp_types_prestations BEFORE INSERT OR UPDATE ON gemapi.types_prestations
FOR EACH ROW EXECUTE FUNCTION gemapi.stamp_modification();
CREATE TRIGGER tr_stamp_statuts_prestations BEFORE INSERT OR UPDATE ON gemapi.statuts_prestations
FOR EACH ROW EXECUTE FUNCTION gemapi.stamp_modification();

CREATE FUNCTION gemapi.avancer_version_ressource() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    NEW.version := OLD.version + 1;
    RETURN NEW;
END $$;

CREATE TRIGGER tr_stamp BEFORE INSERT OR UPDATE ON gemapi.troncons
FOR EACH ROW EXECUTE FUNCTION gemapi.stamp_modification('metier');
CREATE TRIGGER tr_version BEFORE UPDATE ON gemapi.troncons
FOR EACH ROW EXECUTE FUNCTION gemapi.avancer_version_ressource();
CREATE TRIGGER tr_stamp BEFORE INSERT OR UPDATE ON gemapi.mobilier
FOR EACH ROW EXECUTE FUNCTION gemapi.stamp_modification('metier');
CREATE TRIGGER tr_version BEFORE UPDATE ON gemapi.mobilier
FOR EACH ROW EXECUTE FUNCTION gemapi.avancer_version_ressource();
CREATE TRIGGER tr_stamp BEFORE INSERT OR UPDATE ON gemapi.cheminements
FOR EACH ROW EXECUTE FUNCTION gemapi.stamp_modification('metier');
CREATE TRIGGER tr_version BEFORE UPDATE ON gemapi.cheminements
FOR EACH ROW EXECUTE FUNCTION gemapi.avancer_version_ressource();
CREATE TRIGGER tr_stamp BEFORE INSERT OR UPDATE ON gemapi.vegetation
FOR EACH ROW EXECUTE FUNCTION gemapi.stamp_modification('metier');
CREATE TRIGGER tr_version BEFORE UPDATE ON gemapi.vegetation
FOR EACH ROW EXECUTE FUNCTION gemapi.avancer_version_ressource();
CREATE TRIGGER tr_stamp BEFORE INSERT OR UPDATE ON gemapi.ouvrages_particuliers
FOR EACH ROW EXECUTE FUNCTION gemapi.stamp_modification('metier');
CREATE TRIGGER tr_version BEFORE UPDATE ON gemapi.ouvrages_particuliers
FOR EACH ROW EXECUTE FUNCTION gemapi.avancer_version_ressource();
CREATE TRIGGER tr_stamp BEFORE INSERT OR UPDATE ON gemapi.equipements_mesure
FOR EACH ROW EXECUTE FUNCTION gemapi.stamp_modification('metier');
CREATE TRIGGER tr_version BEFORE UPDATE ON gemapi.equipements_mesure
FOR EACH ROW EXECUTE FUNCTION gemapi.avancer_version_ressource();
CREATE TRIGGER tr_stamp BEFORE INSERT OR UPDATE ON gemapi.ouvrages_hydrauliques
FOR EACH ROW EXECUTE FUNCTION gemapi.stamp_modification('metier');
CREATE TRIGGER tr_stamp BEFORE INSERT OR UPDATE ON gemapi.desordres
FOR EACH ROW EXECUTE FUNCTION gemapi.stamp_modification('metier');
CREATE TRIGGER tr_version BEFORE UPDATE ON gemapi.desordres
FOR EACH ROW EXECUTE FUNCTION gemapi.avancer_version_ressource();
CREATE TRIGGER tr_stamp BEFORE INSERT OR UPDATE ON gemapi.observations
FOR EACH ROW EXECUTE FUNCTION gemapi.stamp_modification('metier');
CREATE TRIGGER tr_version BEFORE UPDATE ON gemapi.observations
FOR EACH ROW EXECUTE FUNCTION gemapi.avancer_version_ressource();
CREATE TRIGGER tr_stamp BEFORE INSERT OR UPDATE ON gemapi.photos
FOR EACH ROW EXECUTE FUNCTION gemapi.stamp_modification('metier');
CREATE TRIGGER tr_version BEFORE UPDATE ON gemapi.photos
FOR EACH ROW EXECUTE FUNCTION gemapi.avancer_version_ressource();

-- Les deux extrémités d'une réaffectation portent la nouvelle version.
CREATE FUNCTION gemapi.avancer_version_desordre_lie() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE ancien uuid; nouveau uuid; cible uuid;
BEGIN
    IF TG_OP <> 'INSERT' THEN ancien := OLD.desordre_id; END IF;
    IF TG_OP <> 'DELETE' THEN nouveau := NEW.desordre_id; END IF;
    FOR cible IN SELECT DISTINCT id FROM unnest(ARRAY[ancien, nouveau]) AS d(id)
                 WHERE id IS NOT NULL ORDER BY id LOOP
        UPDATE gemapi.desordres SET version=version WHERE id=cible;
    END LOOP;
    RETURN NULL;
END $$;
CREATE TRIGGER tr_version_desordre AFTER INSERT OR UPDATE OR DELETE ON gemapi.equipements_mesure__desordres
FOR EACH ROW EXECUTE FUNCTION gemapi.avancer_version_desordre_lie();
CREATE TRIGGER tr_version_desordre AFTER INSERT OR UPDATE OR DELETE ON gemapi.ouvrages_hydrauliques__desordres
FOR EACH ROW EXECUTE FUNCTION gemapi.avancer_version_desordre_lie();
CREATE TRIGGER tr_version_desordre AFTER INSERT OR UPDATE OR DELETE ON gemapi.ouvrages_particuliers__desordres
FOR EACH ROW EXECUTE FUNCTION gemapi.avancer_version_desordre_lie();

CREATE INDEX prestations_ah_section_id_idx ON gemapi.prestations(ah_section_id);
CREATE INDEX prestations_cours_eau_id_idx ON gemapi.prestations(cours_eau_id);
CREATE INDEX prestations_digue_id_idx ON gemapi.prestations(digue_id);
CREATE INDEX prestations_ah_stockage_id_idx ON gemapi.prestations(ah_stockage_id);
CREATE INDEX prestations_ah_ouvrage_id_idx ON gemapi.prestations(ah_ouvrage_id);
CREATE INDEX prestations_site_terrestre_id_idx ON gemapi.prestations(site_terrestre_id);
CREATE INDEX ON gemapi.troncons (digue_id);
CREATE INDEX ON gemapi.troncons USING gist (geometry);
CREATE INDEX ON gemapi.troncons USING gist (geometry_reperage);
CREATE INDEX ON gemapi.mobilier USING gist (geometry);
CREATE INDEX ON gemapi.mobilier (troncon_id) WHERE troncon_id IS NOT NULL;
CREATE INDEX ON gemapi.mobilier (ah_ouvrage_id) WHERE ah_ouvrage_id IS NOT NULL;
CREATE INDEX ON gemapi.mobilier (site_terrestre_id) WHERE site_terrestre_id IS NOT NULL;
CREATE INDEX ON gemapi.mobilier (ah_stockage_id) WHERE ah_stockage_id IS NOT NULL;
CREATE INDEX ON gemapi.mobilier (ah_section_id) WHERE ah_section_id IS NOT NULL;
CREATE INDEX ON gemapi.cheminements USING gist (geometry);
CREATE INDEX ON gemapi.cheminements (troncon_id) WHERE troncon_id IS NOT NULL;
CREATE INDEX ON gemapi.cheminements (ah_ouvrage_id) WHERE ah_ouvrage_id IS NOT NULL;
CREATE INDEX ON gemapi.cheminements (site_terrestre_id) WHERE site_terrestre_id IS NOT NULL;
CREATE INDEX ON gemapi.cheminements (ah_stockage_id) WHERE ah_stockage_id IS NOT NULL;
CREATE INDEX ON gemapi.cheminements (ah_section_id) WHERE ah_section_id IS NOT NULL;
CREATE INDEX ON gemapi.vegetation USING gist (geometry);
CREATE INDEX ON gemapi.vegetation (troncon_id) WHERE troncon_id IS NOT NULL;
CREATE INDEX ON gemapi.vegetation (ah_ouvrage_id) WHERE ah_ouvrage_id IS NOT NULL;
CREATE INDEX ON gemapi.vegetation (site_terrestre_id) WHERE site_terrestre_id IS NOT NULL;
CREATE INDEX ON gemapi.vegetation (ah_stockage_id) WHERE ah_stockage_id IS NOT NULL;
CREATE INDEX ON gemapi.vegetation (ah_section_id) WHERE ah_section_id IS NOT NULL;
CREATE INDEX ON gemapi.ouvrages_particuliers USING gist (geometry);
CREATE INDEX ON gemapi.ouvrages_particuliers (troncon_id) WHERE troncon_id IS NOT NULL;
CREATE INDEX ON gemapi.equipements_mesure USING gist (geometry);
CREATE INDEX ON gemapi.equipements_mesure (troncon_id) WHERE troncon_id IS NOT NULL;
CREATE INDEX ON gemapi.ouvrages_hydrauliques USING gist (geometry);
CREATE INDEX ON gemapi.ouvrages_hydrauliques (troncon_id) WHERE troncon_id IS NOT NULL;
CREATE INDEX ON gemapi.ouvrages_hydrauliques (ah_ouvrage_id) WHERE ah_ouvrage_id IS NOT NULL;
CREATE INDEX ON gemapi.ouvrages_hydrauliques (ah_stockage_id) WHERE ah_stockage_id IS NOT NULL;
CREATE INDEX ON gemapi.desordres (troncon_id) WHERE troncon_id IS NOT NULL;
CREATE INDEX ON gemapi.desordres (digue_id) WHERE digue_id IS NOT NULL;
CREATE INDEX ON gemapi.desordres (ah_ouvrage_id) WHERE ah_ouvrage_id IS NOT NULL;
CREATE INDEX ON gemapi.desordres (ah_section_id) WHERE ah_section_id IS NOT NULL;
CREATE INDEX ON gemapi.desordres (ah_stockage_id) WHERE ah_stockage_id IS NOT NULL;
CREATE INDEX ON gemapi.desordres (site_terrestre_id) WHERE site_terrestre_id IS NOT NULL;
CREATE INDEX ON gemapi.desordres USING gist (geometry);
CREATE INDEX ON gemapi.desordres (mobilier_id) WHERE mobilier_id IS NOT NULL;
CREATE INDEX ON gemapi.desordres (cheminement_id) WHERE cheminement_id IS NOT NULL;
CREATE INDEX ON gemapi.desordres (vegetation_id) WHERE vegetation_id IS NOT NULL;
CREATE INDEX ON gemapi.observations (desordre_id) WHERE desordre_id IS NOT NULL;
CREATE INDEX ON gemapi.observations (troncon_id) WHERE troncon_id IS NOT NULL;
CREATE INDEX ON gemapi.observations (ouvrage_hydraulique_id) WHERE ouvrage_hydraulique_id IS NOT NULL;
CREATE INDEX ON gemapi.observations (equipement_mesure_id) WHERE equipement_mesure_id IS NOT NULL;
CREATE INDEX ON gemapi.observations (cheminement_id) WHERE cheminement_id IS NOT NULL;
CREATE INDEX ON gemapi.observations (mobilier_id) WHERE mobilier_id IS NOT NULL;
CREATE INDEX ON gemapi.observations (vegetation_id) WHERE vegetation_id IS NOT NULL;
CREATE INDEX ON gemapi.observations (ouvrage_particulier_id) WHERE ouvrage_particulier_id IS NOT NULL;
CREATE INDEX ON gemapi.observations (ah_ouvrage_id) WHERE ah_ouvrage_id IS NOT NULL;
CREATE INDEX ON gemapi.observations (ah_stockage_id) WHERE ah_stockage_id IS NOT NULL;
CREATE INDEX ON gemapi.observations (ah_section_id) WHERE ah_section_id IS NOT NULL;
CREATE INDEX ON gemapi.observations (site_terrestre_id) WHERE site_terrestre_id IS NOT NULL;
CREATE INDEX ON gemapi.photos (observation_id);
CREATE INDEX ON gemapi.equipements_mesure__desordres (desordre_id);
CREATE INDEX ON gemapi.ouvrages_hydrauliques__desordres (desordre_id);
CREATE INDEX ON gemapi.ouvrages_particuliers__desordres (desordre_id);
CREATE INDEX ON gemapi.ouvrages_hydrauliques__objets_lies (ouvrage_hydraulique_id);
CREATE INDEX ON gemapi.ouvrages_hydrauliques__objets_lies (cheminement_id) WHERE cheminement_id IS NOT NULL;
CREATE INDEX ON gemapi.ouvrages_hydrauliques__objets_lies (autre_ouvrage_hydraulique_id) WHERE autre_ouvrage_hydraulique_id IS NOT NULL;
CREATE INDEX ON gemapi.ouvrages_hydrauliques__objets_lies (ouvrage_particulier_id) WHERE ouvrage_particulier_id IS NOT NULL;
CREATE INDEX ON gemapi.ouvrages_hydrauliques__objets_lies (equipement_mesure_id) WHERE equipement_mesure_id IS NOT NULL;
CREATE INDEX ON gemapi.ouvrages_hydrauliques__objets_lies (berge_id) WHERE berge_id IS NOT NULL;
CREATE INDEX ON gemapi.ouvrages_hydrauliques__objets_lies (digue_id) WHERE digue_id IS NOT NULL;
CREATE INDEX ON gemapi.prestations (troncon_id) WHERE troncon_id IS NOT NULL;
CREATE INDEX ON gemapi.prestations (mobilier_id) WHERE mobilier_id IS NOT NULL;
CREATE INDEX ON gemapi.prestations (cheminement_id) WHERE cheminement_id IS NOT NULL;
CREATE INDEX ON gemapi.prestations (vegetation_id) WHERE vegetation_id IS NOT NULL;
CREATE INDEX prestations_prestataire_idx ON gemapi.prestations(prestataire_id);
CREATE INDEX prestations_type_idx ON gemapi.prestations(type_prestation_id);
CREATE INDEX prestations_statut_idx ON gemapi.prestations(statut_id);
CREATE INDEX prestations_technicien_idx ON gemapi.prestations(technicien_id);
-- MESURES-07 : colonnes canoniques déclarées avant les vues qui les exposent.
-- Le calcul et les triggers sont définis plus bas, après leurs dépendances.
-- Géométrie propre prioritaire, puis géométrie de la cible directe.
CREATE VIEW gemapi.prestations_carte
WITH (security_invoker = true) AS
SELECT
    p.id,
    p.intitule,
    p.type_prestation_id,
    p.prestataire_id,
    p.statut_id,
    p.date_debut,
    p.date_fin,
    p.quantite,
    p.unite,
    p.description,
    p.photo_1,
    p.photo_1_commentaire,
    p.photo_2,
    p.photo_2_commentaire,
    COALESCE(p.geom, s.geom, o.geom, m.geometry, ch.geometry, v.geometry)::geometry(Geometry,3950) AS geom,
    p.date_modification,
    p.modifie_par,
    p.technicien_id,
    p.position,
    p.rive,
    p.cours_eau_id,
    p.digue_id,
    p.ah_stockage_id,
    p.ah_ouvrage_id,
    p.site_terrestre_id,
    p.troncon_id, p.mobilier_id, p.cheminement_id, p.vegetation_id,
    p.nombre_points,
    p.longueur_m,
    p.surface_m2,
    p.surface_ha,
    p.type_geometrie,
    p.type_geometrie_postgis,
    p.possede_geometrie,
    p.origine_mesures,
    p.ah_section_id
FROM gemapi.prestations p
LEFT JOIN gemapi.mobilier m ON m.id=p.mobilier_id
LEFT JOIN gemapi.cheminements ch ON ch.id=p.cheminement_id
LEFT JOIN gemapi.vegetation v ON v.id=p.vegetation_id
LEFT JOIN gemapi.ah_stockage s ON s.id=p.ah_stockage_id
LEFT JOIN gemapi.ah_ouvrage o ON o.id=p.ah_ouvrage_id;
-- Vue tabulaire QGIS : une fiche par prestation, sans jointure N:N.
-- Les huit mesures sont celles enregistrées dans prestations, sans recalcul.
-- Les deux nouveaux champs sont ajoutés en fin pour préserver les positions.
CREATE VIEW gemapi.prestations_fiches
WITH (security_invoker = true) AS
SELECT
    p.id,
    p.intitule,
    tp.libelle AS type_prestation,
    pr.nom AS prestataire,
    s.libelle AS statut,
    NULLIF(concat_ws(' ', NULLIF(btrim(t.prenom), ''), NULLIF(btrim(t.nom), '')), '') AS technicien,
    p.date_debut,
    p.date_fin,
    p.quantite,
    p.unite,
    p.description,
    p.photo_1,
    p.photo_1_commentaire,
    p.photo_2,
    p.photo_2_commentaire,
    p.type_prestation_id,
    p.prestataire_id,
    p.statut_id,
    p.technicien_id,
    p.date_modification,
    p.type_geometrie,
    p.type_geometrie_postgis,
    p.possede_geometrie,
    p.longueur_m,
    p.surface_m2,
    p.surface_ha,
    p.modifie_par,
    p.position,
    p.rive,
    p.cours_eau_id, p.digue_id, p.ah_stockage_id, p.ah_ouvrage_id, p.site_terrestre_id, p.troncon_id, p.mobilier_id, p.cheminement_id, p.vegetation_id,
    p.nombre_points,
    p.origine_mesures,
    p.ah_section_id
FROM gemapi.prestations AS p
JOIN gemapi.types_prestations AS tp ON tp.id = p.type_prestation_id
JOIN gemapi.prestataires AS pr ON pr.id = p.prestataire_id
JOIN gemapi.statuts_prestations AS s ON s.id = p.statut_id
JOIN gemapi.techniciens AS t ON t.id = p.technicien_id;

CREATE INDEX digues_reperes_digue_idx ON gemapi.digues_reperes(digue_id);
CREATE INDEX digues_reperes_riviere_rive_idx
    ON gemapi.digues_reperes(riviere_id, rive);
CREATE INDEX digues_reperes_geom_gix
    ON gemapi.digues_reperes USING gist(geom_reference);

CREATE FUNCTION gemapi.assert_digue_repere(digue_uuid uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, gemapi, pg_temp AS $$
BEGIN
    -- Le verrou du parent sérialise les changements de repère concurrents.
    PERFORM 1 FROM gemapi.digues WHERE id = digue_uuid FOR UPDATE;
    IF FOUND AND NOT EXISTS (
        SELECT 1 FROM gemapi.digues_reperes WHERE digue_id = digue_uuid
    ) THEN
        RAISE EXCEPTION 'Digue % sans repère', digue_uuid USING ERRCODE = '23514';
    END IF;
END $$;
CREATE FUNCTION gemapi.digue_repere_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, gemapi, pg_temp AS $$
BEGIN
    IF TG_TABLE_NAME = 'digues' THEN
        IF TG_OP <> 'DELETE' THEN PERFORM gemapi.assert_digue_repere(NEW.id); END IF;
    ELSE
        IF TG_OP <> 'INSERT' THEN PERFORM gemapi.assert_digue_repere(OLD.digue_id); END IF;
        IF TG_OP <> 'DELETE' THEN PERFORM gemapi.assert_digue_repere(NEW.digue_id); END IF;
    END IF;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION gemapi.assert_digue_repere(uuid), gemapi.digue_repere_complete() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER digue_repere_parent AFTER INSERT OR UPDATE ON gemapi.digues
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION gemapi.digue_repere_complete();
CREATE CONSTRAINT TRIGGER digue_repere_child AFTER INSERT OR UPDATE OR DELETE ON gemapi.digues_reperes
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION gemapi.digue_repere_complete();

-- Fonction pure : aucune lecture de table, aucune qualification implicite.
CREATE FUNCTION gemapi.cours_eau_reference_admissible(p_geom geometry, p_direction boolean, p_valide boolean)
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE
SET search_path FROM CURRENT AS $admissible$
    SELECT CASE WHEN p_direction IS TRUE AND p_valide IS TRUE AND p_geom IS NOT NULL
                     AND ST_GeometryType(p_geom)='ST_MultiLineString'
                     AND ST_NumGeometries(p_geom)=1 THEN
        ST_SRID(p_geom)=3950 AND ST_NDims(p_geom)=2 AND NOT ST_IsEmpty(p_geom)
        AND ST_IsValid(p_geom) AND ST_IsSimple(ST_GeometryN(p_geom,1))
        AND NOT ST_IsClosed(ST_GeometryN(p_geom,1)) AND ST_Length(p_geom)>0
    ELSE false END
$admissible$;

CREATE FUNCTION gemapi.digues_reperes_prepare() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
DECLARE
    r_geom geometry;
    r_line geometry;
    r_direction boolean;
    r_valide boolean;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF (NEW.id,NEW.digue_id) IS DISTINCT FROM (OLD.id,OLD.digue_id) THEN
            RAISE EXCEPTION 'UUID du repère et identité de la digue immuables' USING ERRCODE='23514';
        END IF;
        IF NEW.revision_validation IS NOT DISTINCT FROM OLD.revision_validation THEN
            IF (NEW.riviere_id,NEW.rive,NEW.debut_fraction,NEW.fin_fraction,NEW.geom_riviere_signature)
                IS DISTINCT FROM
               (OLD.riviere_id,OLD.rive,OLD.debut_fraction,OLD.fin_fraction,OLD.geom_riviere_signature)
               OR ST_AsEWKB(NEW.geom_reference) IS DISTINCT FROM ST_AsEWKB(OLD.geom_reference) THEN
                RAISE EXCEPTION 'Référence figée : augmenter revision_validation de 1 pour revalider'
                    USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END IF;
        IF NEW.revision_validation IS DISTINCT FROM OLD.revision_validation+1 THEN
            RAISE EXCEPTION 'Revalidation : la révision doit augmenter exactement de 1' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.revision_validation IS DISTINCT FROM 1 THEN
        RAISE EXCEPTION 'Première validation : révision 1 obligatoire' USING ERRCODE='23514';
    END IF;
    IF NEW.debut_fraction IS NULL OR NEW.fin_fraction IS NULL
       OR NOT (NEW.debut_fraction>=0 AND NEW.debut_fraction<NEW.fin_fraction AND NEW.fin_fraction<=1) THEN
        RAISE EXCEPTION 'Fractions attendues : 0 <= début < fin <= 1' USING ERRCODE='23514';
    END IF;

    -- FOR SHARE empêche une correction concurrente du tracé entre la lecture
    -- et l'enregistrement de l'instantané dans cette transaction.
    SELECT geom,direction,valide INTO r_geom,r_direction,r_valide FROM gemapi.cours_eau
      WHERE id = NEW.riviere_id FOR SHARE;
    IF r_geom IS NULL THEN
        RAISE EXCEPTION 'Rivière de référence introuvable';
    END IF;
    IF NOT gemapi.cours_eau_reference_admissible(r_geom,r_direction,r_valide) THEN
        RAISE EXCEPTION 'Cours d eau non admissible : direction et valide TRUE, ligne unique simple ouverte et positive requises'
            USING ERRCODE='23514';
    END IF;
    r_line := ST_GeometryN(r_geom,1);

    NEW.geom_reference := ST_LineSubstring(
        r_line, NEW.debut_fraction, NEW.fin_fraction);
    NEW.geom_riviere_signature := md5(encode(ST_AsEWKB(r_geom), 'hex'));
    RETURN NEW;
END $$;
CREATE TRIGGER digues_reperes_prepare_trg
BEFORE INSERT OR UPDATE ON gemapi.digues_reperes
FOR EACH ROW EXECUTE FUNCTION gemapi.digues_reperes_prepare();
CREATE TRIGGER tr_stamp_digues
BEFORE INSERT OR UPDATE ON gemapi.digues
FOR EACH ROW EXECUTE FUNCTION gemapi.stamp_modification('metier');
CREATE TRIGGER tr_stamp_digues_reperes
BEFORE INSERT OR UPDATE ON gemapi.digues_reperes
FOR EACH ROW EXECUTE FUNCTION gemapi.stamp_modification('metier');

-- Parcours explicites : droits du compte connecté, aucune élévation.
-- Le jeton parent évite aussi d'écraser une modification native de ses attributs.
CREATE FUNCTION gemapi.digue_enregistrer(
    p_digue uuid, p_repere uuid, p_revision integer, p_parent_modification timestamptz,
    p_nom text, p_identifiant text, p_commentaire text,
    p_riviere uuid, p_rive text, p_debut double precision, p_fin double precision,
    p_signature text
) RETURNS uuid LANGUAGE plpgsql SECURITY INVOKER SET search_path FROM CURRENT AS $save$
DECLARE
    d gemapi.digues%ROWTYPE;
    s gemapi.digues_reperes%ROWTYPE;
    r gemapi.cours_eau%ROWTYPE;
BEGIN
    SELECT * INTO r FROM gemapi.cours_eau WHERE id=p_riviere FOR SHARE;
    IF NOT FOUND OR NOT gemapi.cours_eau_reference_admissible(r.geom,r.direction,r.valide) THEN
        RAISE EXCEPTION 'Cours d eau non admissible : prévisualisez un support qualifié' USING ERRCODE='23514';
    END IF;
    IF p_signature IS NULL OR p_signature IS DISTINCT FROM md5(encode(ST_AsEWKB(r.geom),'hex')) THEN
        RAISE EXCEPTION 'Le cours d eau a changé : prévisualisez à nouveau avant de confirmer' USING ERRCODE='40001';
    END IF;
    IF p_digue IS NULL THEN
        IF p_repere IS NOT NULL OR p_revision IS DISTINCT FROM 0 THEN
            RAISE EXCEPTION 'Nouvelle digue : identité ou révision incorrecte' USING ERRCODE='23514';
        END IF;
        INSERT INTO gemapi.digues(nom,identifiant_externe,commentaire)
        VALUES (p_nom,p_identifiant,p_commentaire) RETURNING * INTO d;
        INSERT INTO gemapi.digues_reperes(digue_id,riviere_id,rive,debut_fraction,fin_fraction)
        VALUES (d.id,p_riviere,p_rive,p_debut,p_fin) RETURNING id INTO p_repere;
    ELSE
        SELECT * INTO d FROM gemapi.digues WHERE id=p_digue FOR UPDATE;
        IF NOT FOUND OR d.date_modification IS DISTINCT FROM p_parent_modification THEN
            RAISE EXCEPTION 'La digue a changé : rechargez la fiche' USING ERRCODE='40001';
        END IF;
        SELECT * INTO s FROM gemapi.digues_reperes WHERE digue_id=p_digue FOR UPDATE;
        IF NOT FOUND OR s.id IS DISTINCT FROM p_repere OR s.revision_validation IS DISTINCT FROM p_revision THEN
            RAISE EXCEPTION 'Le repère a changé : rechargez la fiche' USING ERRCODE='40001';
        END IF;
        UPDATE gemapi.digues SET nom=p_nom,identifiant_externe=p_identifiant,commentaire=p_commentaire
        WHERE id=p_digue AND (nom,identifiant_externe,commentaire)
            IS DISTINCT FROM (p_nom,p_identifiant,p_commentaire);
        UPDATE gemapi.digues_reperes SET riviere_id=p_riviere,rive=p_rive,
            debut_fraction=p_debut,fin_fraction=p_fin,revision_validation=revision_validation+1 WHERE id=p_repere;
    END IF;
    RETURN p_repere;
END $save$;

CREATE FUNCTION gemapi.digue_supprimer(p_repere uuid,p_revision integer,p_parent_modification timestamptz)
RETURNS void LANGUAGE plpgsql SECURITY INVOKER SET search_path FROM CURRENT AS $delete$
DECLARE
    d gemapi.digues%ROWTYPE;
    s gemapi.digues_reperes%ROWTYPE;
    nombre bigint;
BEGIN
    -- Verrou du parent avant le repère, comme à l'enregistrement. Le verrou
    -- empêche aussi un rattachement FK concurrent pendant le contrôle.
    SELECT d0.* INTO d FROM gemapi.digues d0 JOIN gemapi.digues_reperes s0
        ON s0.digue_id=d0.id WHERE s0.id=p_repere FOR UPDATE OF d0;
    IF NOT FOUND OR d.date_modification IS DISTINCT FROM p_parent_modification THEN
        RAISE EXCEPTION 'La digue a changé : rechargez la fiche' USING ERRCODE='40001';
    END IF;
    SELECT * INTO s FROM gemapi.digues_reperes WHERE id=p_repere AND digue_id=d.id FOR UPDATE;
    IF NOT FOUND OR s.revision_validation IS DISTINCT FROM p_revision THEN
        RAISE EXCEPTION 'Le repère a changé : rechargez la fiche' USING ERRCODE='40001';
    END IF;
    SELECT count(*) INTO nombre FROM gemapi.prestations WHERE digue_id=d.id;
    IF nombre>0 THEN
        RAISE EXCEPTION 'Suppression impossible : % prestation(s) rattachée(s) à cette digue. Aucun objet supprimé.',nombre
            USING ERRCODE='23503';
    END IF;
    SELECT count(*) INTO nombre FROM gemapi.troncons WHERE digue_id=d.id;
    IF nombre>0 THEN RAISE EXCEPTION 'Digue référencée par % tronçon(s)',nombre USING ERRCODE='23503'; END IF;
    SELECT count(*) INTO nombre FROM gemapi.desordres WHERE digue_id=d.id;
    IF nombre>0 THEN RAISE EXCEPTION 'Digue référencée par % désordre(s)',nombre USING ERRCODE='23503'; END IF;
    DELETE FROM gemapi.digues_reperes WHERE id=p_repere;
    DELETE FROM gemapi.digues WHERE id=d.id;
END $delete$;
REVOKE ALL ON FUNCTION gemapi.digue_enregistrer(uuid,uuid,integer,timestamptz,text,text,text,uuid,text,double precision,double precision,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION gemapi.digue_supprimer(uuid,integer,timestamptz) FROM PUBLIC;
COMMENT ON COLUMN gemapi.digues_reperes.revision_validation IS
'Validation explicite : 1 à la création, puis +1, y compris à fractions identiques. UUID et parent immuables.';

-- Couche géographique de consultation ; la géométrie affichée est l'instantané.
CREATE VIEW gemapi.digues_carte
WITH (security_invoker = true) AS
SELECT s.id, s.digue_id, d.nom AS digue_nom, s.riviere_id, r.nom AS riviere_nom,
       s.rive, s.debut_fraction, s.fin_fraction,
       s.geom_reference AS geom,
       CASE
           WHEN md5(encode(ST_AsEWKB(r.geom), 'hex'))
                IS DISTINCT FROM s.geom_riviere_signature
           THEN 'REFERENTIEL_MODIFIE'
           WHEN NOT gemapi.cours_eau_reference_admissible(r.geom,r.direction,r.valide)
           THEN 'COURS_EAU_NON_QUALIFIE'
           ELSE 'CONFORME'
       END AS etat_referentiel
FROM gemapi.digues_reperes AS s
JOIN gemapi.digues AS d ON d.id = s.digue_id
JOIN gemapi.cours_eau AS r ON r.id = s.riviere_id;
-- DIG-01 END


CREATE INDEX ah_sections_cours_eau_idx ON gemapi.ah_sections(cours_eau_id);
CREATE INDEX ah_sections_geom_idx ON gemapi.ah_sections USING gist(geom_reference);

CREATE FUNCTION gemapi.ah_sections_prepare() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $prepare$
DECLARE
    r_geom geometry;
    r_direction boolean;
    r_valide boolean;
BEGIN
    IF TG_OP='UPDATE' THEN
        IF NEW.id IS DISTINCT FROM OLD.id THEN
            RAISE EXCEPTION 'UUID de section immuable' USING ERRCODE='23514';
        END IF;
        IF NEW.revision_validation IS NOT DISTINCT FROM OLD.revision_validation THEN
            IF (NEW.ah_id,NEW.cours_eau_id,NEW.debut_fraction,NEW.fin_fraction,
                NEW.geom_cours_eau_signature,NEW.valide_par,NEW.date_validation)
                 IS DISTINCT FROM
               (OLD.ah_id,OLD.cours_eau_id,OLD.debut_fraction,OLD.fin_fraction,
                OLD.geom_cours_eau_signature,OLD.valide_par,OLD.date_validation)
               OR ST_AsEWKB(NEW.geom_reference) IS DISTINCT FROM ST_AsEWKB(OLD.geom_reference) THEN
                RAISE EXCEPTION 'Référence figée : augmenter revision_validation de 1 pour revalider'
                    USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END IF;
        IF NEW.revision_validation IS DISTINCT FROM OLD.revision_validation+1 THEN
            RAISE EXCEPTION 'Revalidation : la révision doit augmenter exactement de 1' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.revision_validation IS DISTINCT FROM 1 THEN
        RAISE EXCEPTION 'Première validation : révision 1 obligatoire' USING ERRCODE='23514';
    END IF;
    IF NEW.debut_fraction IS NULL OR NEW.fin_fraction IS NULL
       OR NOT (NEW.debut_fraction>=0 AND NEW.debut_fraction<NEW.fin_fraction AND NEW.fin_fraction<=1) THEN
        RAISE EXCEPTION 'Fractions attendues : 0 <= début < fin <= 1' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS(SELECT FROM gemapi.ah WHERE id=NEW.ah_id) THEN
        RAISE EXCEPTION 'Parent AH introuvable' USING ERRCODE='23503';
    END IF;
    -- Verrou partagé jusqu'à la fin de transaction : empêche un tracé ou une
    -- qualification concurrents entre lecture et enregistrement de l'instantané.
    SELECT geom,direction,valide INTO r_geom,r_direction,r_valide FROM gemapi.cours_eau
        WHERE id=NEW.cours_eau_id FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Cours d eau de référence introuvable' USING ERRCODE='23503';
    END IF;
    IF NOT gemapi.cours_eau_reference_admissible(r_geom,r_direction,r_valide) THEN
        RAISE EXCEPTION 'Cours d eau non admissible : direction et valide TRUE, ligne unique simple ouverte et positive requises'
            USING ERRCODE='23514';
    END IF;
    NEW.geom_reference := ST_LineSubstring(ST_GeometryN(r_geom,1),NEW.debut_fraction,NEW.fin_fraction);
    NEW.geom_cours_eau_signature := md5(encode(ST_AsEWKB(r_geom),'hex'));
    NEW.valide_par := session_user;
    NEW.date_validation := clock_timestamp();
    RETURN NEW;
END $prepare$;
CREATE TRIGGER ah_sections_prepare_trg BEFORE INSERT OR UPDATE ON gemapi.ah_sections
FOR EACH ROW EXECUTE FUNCTION gemapi.ah_sections_prepare();
CREATE TRIGGER tr_stamp_ah_sections BEFORE INSERT OR UPDATE ON gemapi.ah_sections
FOR EACH ROW EXECUTE FUNCTION gemapi.stamp_modification('metier');

CREATE VIEW gemapi.ah_sections_carte WITH (security_invoker=true) AS
SELECT s.id,s.ah_id,s.cours_eau_id,s.debut_fraction,s.fin_fraction,s.geom_reference,
       s.geom_cours_eau_signature,s.revision_validation,s.valide_par,s.date_validation,
       s.commentaire,s.modifie_par,s.date_modification, s.geom_reference AS geom,
       md5(encode(ST_AsEWKB(r.geom),'hex'))=s.geom_cours_eau_signature AS signature_conforme,
       gemapi.cours_eau_reference_admissible(r.geom,r.direction,r.valide) AS cours_eau_qualifie,
       CASE WHEN md5(encode(ST_AsEWKB(r.geom),'hex')) IS DISTINCT FROM s.geom_cours_eau_signature
                THEN 'REFERENTIEL_MODIFIE'
            WHEN NOT gemapi.cours_eau_reference_admissible(r.geom,r.direction,r.valide)
                THEN 'COURS_EAU_NON_QUALIFIE'
            ELSE 'CONFORME' END AS etat_reference
FROM gemapi.ah_sections s JOIN gemapi.cours_eau r ON r.id=s.cours_eau_id;

-- L'admissibilité est vérifiée à l'écriture, sous verrous partagés.
-- Une ancienne association obsolète reste consultable et éditable sans réaffectation.
CREATE FUNCTION gemapi.prestations_section_admissible() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $section_check$
DECLARE section_row gemapi.ah_sections%ROWTYPE; reference_row gemapi.cours_eau%ROWTYPE;
BEGIN
    IF NEW.ah_section_id IS NULL THEN RETURN NEW; END IF;
    IF TG_OP='UPDATE' THEN
        IF NEW.ah_section_id IS NOT DISTINCT FROM OLD.ah_section_id
           AND ST_AsEWKB(NEW.geom) IS NOT DISTINCT FROM ST_AsEWKB(OLD.geom) THEN RETURN NEW; END IF;
    END IF;
    SELECT * INTO section_row FROM gemapi.ah_sections WHERE id=NEW.ah_section_id FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Section AH introuvable' USING ERRCODE='23503';
    END IF;
    SELECT * INTO reference_row FROM gemapi.cours_eau WHERE id=section_row.cours_eau_id FOR SHARE;
    IF NOT gemapi.cours_eau_reference_admissible(reference_row.geom,reference_row.direction,reference_row.valide)
       OR md5(encode(ST_AsEWKB(reference_row.geom),'hex')) IS DISTINCT FROM section_row.geom_cours_eau_signature
       OR NOT ST_Equals(section_row.geom_reference,ST_LineSubstring(ST_GeometryN(reference_row.geom,1),
                                              section_row.debut_fraction,section_row.fin_fraction)) THEN
        RAISE EXCEPTION 'Section AH non conforme ou cours d eau non qualifié : revalidation nécessaire'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $section_check$;
REVOKE ALL ON FUNCTION gemapi.prestations_section_admissible() FROM PUBLIC;
CREATE TRIGGER prestations_section_admissible_trg BEFORE INSERT OR UPDATE ON gemapi.prestations
FOR EACH ROW EXECUTE FUNCTION gemapi.prestations_section_admissible();

-- Propositions en lecture seule depuis l'unique stockage facultatif du parent.
-- Aucun INSERT ni validation métier : accepter exige une écriture explicite.
CREATE FUNCTION gemapi.ah_sections_candidates(p_ah_id uuid)
RETURNS TABLE (cours_eau_id uuid, debut_fraction double precision, fin_fraction double precision,
               geom_reference geometry, geom_cours_eau_signature text)
LANGUAGE sql STABLE SET search_path FROM CURRENT AS $candidates$
    WITH supports AS MATERIALIZED (
        SELECT r.id,r.geom,ST_GeometryN(r.geom,1) AS ligne,s.geom AS stockage_geom
        FROM gemapi.ah_stockage s JOIN gemapi.cours_eau r ON ST_Intersects(s.geom,r.geom)
        WHERE s.ah_id=p_ah_id AND gemapi.cours_eau_reference_admissible(r.geom,r.direction,r.valide)
    ), parties AS (
        SELECT r.id,r.geom,r.ligne,p.geom AS partie
        FROM supports r CROSS JOIN LATERAL ST_Dump(ST_LineMerge(ST_UnaryUnion(
            ST_CollectionExtract(ST_Intersection(r.stockage_geom,r.ligne),2)))) p
        WHERE NOT ST_IsEmpty(p.geom) AND ST_Length(p.geom)>0
    ), fractions AS (
        SELECT id,geom,ligne,partie,
               LEAST(ST_LineLocatePoint(ligne,ST_StartPoint(partie)),
                     ST_LineLocatePoint(ligne,ST_EndPoint(partie))) AS debut,
               GREATEST(ST_LineLocatePoint(ligne,ST_StartPoint(partie)),
                        ST_LineLocatePoint(ligne,ST_EndPoint(partie))) AS fin
        FROM parties
    )
    SELECT id,debut,fin,ST_LineSubstring(ligne,debut,fin),md5(encode(ST_AsEWKB(geom),'hex'))
    FROM fractions WHERE debut<fin AND ST_Equals(ST_LineSubstring(ligne,debut,fin),partie)
    ORDER BY id,debut,fin
$candidates$;
COMMENT ON TABLE gemapi.ah_sections IS
'Sections du parent AH définies par fractions manuelles sur un cours d eau qualifié. Géométrie calculée et figée, revalidation explicite ; aucune dépendance au stockage.';
COMMENT ON COLUMN gemapi.ah_sections.revision_validation IS
'1 à la création ; augmenter exactement de 1 pour accepter une nouvelle référence, même avec les mêmes fractions. Aucun recalcul sur modification du cours d eau.';
COMMENT ON FUNCTION gemapi.ah_sections_candidates(uuid) IS
'Propositions spatiales en lecture seule sur cours d eau qualifiés ; aucune décision métier enregistrée.';
-- Fin du référencement hydrographique AH.

-- Liaisons de consultation consolidées pour les formulaires QGIS des parents.
-- L'identifiant technique stable permet de charger chaque vue sans géométrie
-- avec une clé simple ; les couples métier restent les deux UUID exposés.
CREATE VIEW gemapi.prestations__ah
WITH (security_invoker = true) AS
SELECT md5('prestations__ah:' || links.prestation_id::text || ':' || links.ah_id::text)::uuid AS id,
       links.prestation_id, links.ah_id
FROM (
    SELECT l.id AS prestation_id, s.ah_id
    FROM gemapi.prestations AS l
    JOIN gemapi.ah_stockage AS s ON s.id = l.ah_stockage_id
    UNION
    SELECT l.id AS prestation_id, o.ah_id
    FROM gemapi.prestations AS l
    JOIN gemapi.ah_ouvrage AS o ON o.id = l.ah_ouvrage_id
    UNION
    SELECT l.id AS prestation_id, s.ah_id
    FROM gemapi.prestations AS l
    JOIN gemapi.ah_sections AS s ON s.id = l.ah_section_id
) AS links;

CREATE VIEW gemapi.prestations__sites_terrestres
WITH (security_invoker = true) AS
SELECT md5('prestations__sites_terrestres:' || links.prestation_id::text || ':' || links.site_terrestre_id::text)::uuid AS id,
       links.prestation_id, links.site_terrestre_id
FROM (
    SELECT id AS prestation_id, site_terrestre_id FROM gemapi.prestations WHERE site_terrestre_id IS NOT NULL
    UNION ALL
    SELECT p.id, enfant.site_terrestre_id FROM gemapi.prestations p
    JOIN gemapi.mobilier enfant ON enfant.id=p.mobilier_id WHERE enfant.site_terrestre_id IS NOT NULL
    UNION ALL
    SELECT p.id, enfant.site_terrestre_id FROM gemapi.prestations p
    JOIN gemapi.cheminements enfant ON enfant.id=p.cheminement_id WHERE enfant.site_terrestre_id IS NOT NULL
    UNION ALL
    SELECT p.id, enfant.site_terrestre_id FROM gemapi.prestations p
    JOIN gemapi.vegetation enfant ON enfant.id=p.vegetation_id WHERE enfant.site_terrestre_id IS NOT NULL
) AS links;

-- Mesures enregistrées : géométrie propre prioritaire, sinon patrimoine.
-- La quantité déclarée reste indépendante. Actualiser la fiche après sauvegarde.
SELECT pg_catalog.set_config('search_path',pg_catalog.format('pg_catalog,%I,gemapi,pg_temp',n.nspname),true)
FROM pg_catalog.pg_extension e JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace
WHERE e.extname='postgis';
CREATE FUNCTION gemapi.mesures07_resoudre(
    propre geometry, stockage uuid, ouvrage uuid, mobilier uuid, cheminement uuid, vegetation uuid,
    OUT nombre_points integer,OUT longueur_m double precision,
    OUT surface_m2 double precision,OUT surface_ha double precision,
    OUT type_geometrie text,OUT type_geometrie_postgis text,
    OUT possede_geometrie boolean,OUT origine_mesures text
) RETURNS record LANGUAGE plpgsql VOLATILE SECURITY INVOKER
SET search_path FROM CURRENT AS $resolve$
DECLARE g geometry;
BEGIN
    IF propre IS NOT NULL THEN g:=propre;
    ELSIF stockage IS NOT NULL THEN SELECT geom INTO g FROM gemapi.ah_stockage WHERE id=stockage;
    ELSIF ouvrage IS NOT NULL THEN SELECT geom INTO g FROM gemapi.ah_ouvrage WHERE id=ouvrage;
    ELSIF mobilier IS NOT NULL THEN SELECT obj.geometry INTO g FROM gemapi.mobilier obj WHERE obj.id=mobilier;
    ELSIF cheminement IS NOT NULL THEN SELECT obj.geometry INTO g FROM gemapi.cheminements obj WHERE obj.id=cheminement;
    ELSIF vegetation IS NOT NULL THEN SELECT obj.geometry INTO g FROM gemapi.vegetation obj WHERE obj.id=vegetation;
    END IF;
    possede_geometrie:=g IS NOT NULL AND NOT ST_IsEmpty(g);
    origine_mesures:=CASE WHEN NOT possede_geometrie THEN 'Aucune géométrie'
        WHEN propre IS NOT NULL THEN 'Prestation' ELSE 'Patrimoine de rattachement' END;
    IF NOT possede_geometrie THEN RETURN; END IF;
    type_geometrie_postgis:=ST_GeometryType(g);
    CASE type_geometrie_postgis
        WHEN 'ST_Point','ST_MultiPoint' THEN type_geometrie:='Point';nombre_points:=ST_NPoints(g);
        WHEN 'ST_LineString','ST_MultiLineString' THEN type_geometrie:='Ligne';longueur_m:=ST_Length(g);
        WHEN 'ST_Polygon','ST_MultiPolygon' THEN type_geometrie:='Polygone';surface_m2:=ST_Area(g);surface_ha:=surface_m2/10000.0;
        ELSE NULL;
    END CASE;
END $resolve$;
REVOKE ALL ON FUNCTION gemapi.mesures07_resoudre(geometry,uuid,uuid,uuid,uuid,uuid) FROM PUBLIC;

CREATE FUNCTION gemapi.mesures07_preparer() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $prepare$
DECLARE
    m record;
    mesures_modifiees boolean;
    champs_calcules CONSTANT text[] := ARRAY[
        'nombre_points','longueur_m','surface_m2','surface_ha','type_geometrie',
        'type_geometrie_postgis','possede_geometrie','origine_mesures','modifie_par','date_modification'];
BEGIN
    IF TG_OP='UPDATE' THEN
        mesures_modifiees := ROW(NEW.nombre_points, NEW.longueur_m, NEW.surface_m2, NEW.type_geometrie,
                                 NEW.type_geometrie_postgis, NEW.possede_geometrie, NEW.origine_mesures)
            IS DISTINCT FROM ROW(OLD.nombre_points, OLD.longueur_m, OLD.surface_m2, OLD.type_geometrie,
                                 OLD.type_geometrie_postgis, OLD.possede_geometrie, OLD.origine_mesures);
        -- Le stamp précède ce trigger. Un recalcul seul conserve l'auteur et la date de la fiche.
        IF mesures_modifiees AND (to_jsonb(NEW) - champs_calcules) = (to_jsonb(OLD) - champs_calcules) THEN
            NEW.modifie_par := OLD.modifie_par;
            NEW.date_modification := OLD.date_modification;
        END IF;
        IF NOT mesures_modifiees AND ST_AsEWKB(NEW.geom) IS NOT DISTINCT FROM ST_AsEWKB(OLD.geom)
           AND ROW(NEW.ah_stockage_id, NEW.ah_ouvrage_id, NEW.mobilier_id, NEW.cheminement_id, NEW.vegetation_id)
               IS NOT DISTINCT FROM
               ROW(OLD.ah_stockage_id, OLD.ah_ouvrage_id, OLD.mobilier_id, OLD.cheminement_id, OLD.vegetation_id) THEN
            RETURN NEW;
        END IF;
    END IF;
    -- FOR SHARE requiert UPDATE sur le parent : le trigger seul, non appelable
    -- directement, porte cette capacité. Aucun GRANT d'édition de parent au rôle Prestations.
    IF NEW.geom IS NULL THEN
        IF NEW.ah_stockage_id IS NOT NULL THEN PERFORM 1 FROM gemapi.ah_stockage WHERE id=NEW.ah_stockage_id FOR SHARE;
        ELSIF NEW.ah_ouvrage_id IS NOT NULL THEN PERFORM 1 FROM gemapi.ah_ouvrage WHERE id=NEW.ah_ouvrage_id FOR SHARE;
        ELSIF NEW.mobilier_id IS NOT NULL THEN PERFORM 1 FROM gemapi.mobilier WHERE id=NEW.mobilier_id FOR SHARE;
        ELSIF NEW.cheminement_id IS NOT NULL THEN PERFORM 1 FROM gemapi.cheminements WHERE id=NEW.cheminement_id FOR SHARE;
        ELSIF NEW.vegetation_id IS NOT NULL THEN PERFORM 1 FROM gemapi.vegetation WHERE id=NEW.vegetation_id FOR SHARE;
        END IF;
    END IF;
    SELECT * INTO m FROM gemapi.mesures07_resoudre(NEW.geom, NEW.ah_stockage_id, NEW.ah_ouvrage_id,
        NEW.mobilier_id, NEW.cheminement_id, NEW.vegetation_id);
    NEW.nombre_points:=m.nombre_points;
    NEW.longueur_m:=m.longueur_m;
    NEW.surface_m2:=m.surface_m2;
    NEW.type_geometrie:=m.type_geometrie;
    NEW.type_geometrie_postgis:=m.type_geometrie_postgis;
    NEW.possede_geometrie:=m.possede_geometrie;
    NEW.origine_mesures:=m.origine_mesures;
    RETURN NEW;
END $prepare$;
REVOKE ALL ON FUNCTION gemapi.mesures07_preparer() FROM PUBLIC;
CREATE TRIGGER zz_mesures07_preparer BEFORE INSERT OR UPDATE ON gemapi.prestations
FOR EACH ROW EXECUTE FUNCTION gemapi.mesures07_preparer();

-- Recalcul des seules prestations qui empruntent cette géométrie.
CREATE FUNCTION gemapi.mesures07_parent() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
SET search_path FROM CURRENT AS $parent$
BEGIN
    IF TG_TABLE_NAME='ah_stockage' THEN
        UPDATE gemapi.prestations SET origine_mesures=NULL WHERE geom IS NULL AND ah_stockage_id=NEW.id;
    ELSIF TG_TABLE_NAME='ah_ouvrage' THEN
        UPDATE gemapi.prestations SET origine_mesures=NULL WHERE geom IS NULL AND ah_ouvrage_id=NEW.id;
    ELSIF TG_TABLE_NAME='mobilier' THEN
        UPDATE gemapi.prestations SET origine_mesures=NULL WHERE geom IS NULL AND mobilier_id=NEW.id;
    ELSIF TG_TABLE_NAME='cheminements' THEN
        UPDATE gemapi.prestations SET origine_mesures=NULL WHERE geom IS NULL AND cheminement_id=NEW.id;
    ELSIF TG_TABLE_NAME='vegetation' THEN
        UPDATE gemapi.prestations SET origine_mesures=NULL WHERE geom IS NULL AND vegetation_id=NEW.id;
    END IF;
    RETURN NULL;
END $parent$;
REVOKE ALL ON FUNCTION gemapi.mesures07_parent() FROM PUBLIC;
CREATE TRIGGER mesures07_dependants AFTER UPDATE OF geom ON gemapi.ah_stockage FOR EACH ROW
WHEN(ST_AsEWKB(OLD.geom) IS DISTINCT FROM ST_AsEWKB(NEW.geom)) EXECUTE FUNCTION gemapi.mesures07_parent();
CREATE TRIGGER mesures07_dependants AFTER UPDATE OF geom ON gemapi.ah_ouvrage FOR EACH ROW
WHEN(ST_AsEWKB(OLD.geom) IS DISTINCT FROM ST_AsEWKB(NEW.geom)) EXECUTE FUNCTION gemapi.mesures07_parent();
CREATE TRIGGER mesures07_dependants AFTER UPDATE OF geometry ON gemapi.mobilier FOR EACH ROW
WHEN(ST_AsEWKB(OLD.geometry) IS DISTINCT FROM ST_AsEWKB(NEW.geometry)) EXECUTE FUNCTION gemapi.mesures07_parent();
CREATE TRIGGER mesures07_dependants AFTER UPDATE OF geometry ON gemapi.cheminements FOR EACH ROW
WHEN(ST_AsEWKB(OLD.geometry) IS DISTINCT FROM ST_AsEWKB(NEW.geometry)) EXECUTE FUNCTION gemapi.mesures07_parent();
CREATE TRIGGER mesures07_dependants AFTER UPDATE OF geometry ON gemapi.vegetation FOR EACH ROW
WHEN(ST_AsEWKB(OLD.geometry) IS DISTINCT FROM ST_AsEWKB(NEW.geometry)) EXECUTE FUNCTION gemapi.mesures07_parent();

COMMIT;
