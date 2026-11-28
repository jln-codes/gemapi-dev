# Démonstration patrimoniale GEMAPI-App-SIRS v0.6.1

Le jeu fictif se reconstruit sur une base PostgreSQL/PostGIS locale neuve avec le schéma de la branche SIRS. Le projet QGIS résout ses couches PostgreSQL avec le service libpq `gemapi_demo`. Dans le fichier de services de l’utilisateur (ou celui désigné par `PGSERVICEFILE`), ajouter la section suivante et adapter la connexion à la base reconstruite :

```ini
[gemapi_demo]
host=localhost
port=5432
dbname=demo_sirs_locale
```

Utiliser son compte PostgreSQL de consultation ou de saisie. Ouvrir ensuite `prestations_demo.qgz`. Les couches de mobilier, cheminements et végétation affichent les enfants directs des sites en lecture seule.

## Reconstruction

Créer explicitement une base **locale dédiée et vide**. Le DBA y installe PostGIS dans le schéma de son choix. Choisir le socket Unix local avec `PGHOST`, le port et le compte avec `PGPORT` et `PGUSER`. Exemple réservé à une nouvelle base de démonstration.

```sh
createdb demo_locale
psql -X -v ON_ERROR_STOP=1 -d demo_locale -c 'CREATE EXTENSION postgis WITH SCHEMA public'
bash demo/data/importer_base_neuve.sh demo_locale --schema gemapi
```

Le modèle, les catalogues et les 11 exports sont chargés en une transaction. La commande vérifie la base nommée, le socket local et l’absence de relations applicatives dans toute la base. Une reconstruction se fait sur une autre base vide. Les générateurs des GeoPackages produisent les exports canoniques. Le chargeur adapte les identifiants au schéma demandé sans toucher aux valeurs. Les contraintes sont contrôlées entre les fichiers.

Pour QGIS-01, choisir `gemapi`. Installer les profils avec le propriétaire selon [la procédure SQL](../model/README.md). Un service local libpq `gemapi_demo` résout les sources. Si nécessaire, définir `options=-csearch_path=pg_catalog,extensions,pg_temp` pour le schéma effectif de PostGIS.

## Sources et scénarios

- `gpkg/sites_terrestres.gpkg` contient les 12 sites et trois couches enfants, toutes directement rattachées à un site. Les 25 emprises sont conservées. Elles décrivent 19 objets de végétation, 4 mobiliers et 2 cheminements. `data/generer_sites.py` réexporte ces couches.
- `gpkg/prestations.gpkg` contient les 100 interventions, avec leurs dix colonnes de cible possibles. `data/generer_prestations.py` exporte les attributs et les coordonnées enregistrées. Les quantités décimales sont conservées dans leur représentation texte source jusqu’au chargement en NUMERIC. Les 12 débroussaillages sur enfant empruntent la géométrie de la végétation. Les 88 autres prestations gardent leur forme propre.
- `data/14_patrimoine.sql` est la source des sept tronçons fictifs et des trois objets techniques. Leurs formes sont définies sur le repère figé de la Digue des Prés. `data/16_suivi.sql` fournit deux désordres, quatre observations et trois références photographiques. Les clés des photos patrimoniales sont relatives à `demo/`. Deux désignent des illustrations locales existantes et une représente un fichier manquant.
- Les photos des prestations conservent leurs deux champs directs et leurs commentaires. 25 interventions utilisent les JPEG locaux, 25 les références réseau de `photos/photos_serveur.csv`, 50 n’ont aucune photographie. L’accès au lecteur distant dépend du poste.
- Exécuter les générateurs avec Python et les liaisons GDAL/OGR. La qualification des sources se lance avec `/usr/bin/python3 tests/model/test_demo_topologie.py`, celle des deux reconstructions avec `/usr/bin/python3 tests/model/test_demo_import.py` (Docker). L’audit spatial `data/auditer_prestations_spatial.py` prend sa connexion via libpq. En lecture seule, il distingue forme propre, type de localisation effective et origine des mesures.

Les identités, les scénarios de remplacement et les résultats des contrôles sont détaillés dans le [rapport de reprise de la démonstration](../audits/GEMAPI-INIT-06.md).

## Contenu

Un AH peut posséder zéro ou un stockage et zéro à plusieurs ouvrages. Ces
enfants sont facultatifs et indépendants ; chacun référence obligatoirement
un parent AH existant. L’unicité de `ah_stockage.ah_id` est garantie par
PostgreSQL et configurée comme contrainte forte dans le projet QGIS.

### Sections hydrographiques des AH

`gemapi.ah_sections` référence le **parent AH**. L'utilisateur choisit un cours
d'eau qualifié et ses fractions de début/fin. Plusieurs sections et plusieurs
rivières sont possibles, y compris sans stockage ou au-delà de son emprise.
La géométrie est exclusivement calculée côté serveur avec `ST_LineSubstring`.
Le doublon exact
`(ah_id,cours_eau_id,debut_fraction,fin_fraction)` est interdit.

Le contrôle serveur commun `cours_eau_reference_admissible(geom,direction,valide)`
est utilisé par les repères de digues et les sections AH. Le cours d’eau doit
avoir `direction = TRUE` **et** `valide = TRUE`, une seule partie linéaire simple,
ouverte, valide, 2D et de longueur positive en EPSG:3950. Les fractions doivent
respecter `0 <= début < fin <= 1`. Aucune orientation n’est inférée ou corrigée.
DEMO-AH-01 conserve 57 directions non vérifiées. Le **Ruisseau du Pré Neuf** est
qualifié selon une convention fictive explicitement validée : l'ordre actuel
des sommets définit l'amont vers l'aval du scénario, sans observation de terrain.
Ses UUID et sommets sont inchangés. Une intersection complète avec le stockage
du **RC du Pré des Saules** est explicitement acceptée dans `ah_sections`.

La convention porte exclusivement sur le cours d'eau
`90368380-5d42-4a28-9889-6ae657f84a75`, depuis EPSG:3950
(1692727.2447354454, 9233907.130167473) vers
(1692361.6105395271, 9235579.986351149). Les codes de provenance fictifs existants
ne sont pas des preuves d'orientation. Ne pas étendre cette qualification aux
autres rivières. Le Courant des Saules reste non qualifié ; le référencement
manuel ne fournit aucune preuve de son orientation.

La couche `ah_sections` de `gpkg/ah.gpkg` conserve la décision acceptée, ses fractions de référence,
sa géométrie et la signature hydrographique. `generer_ah_sections.py` régénère
`12_ah_sections.sql` depuis cette couche. L'import restaure les fractions acceptées,
fait calculer la géométrie côté serveur et exige la signature et le WKB identiques ; il refuse des sources
modifiées. Sa réexécution est sans doublon ni modification de la traçabilité.
Pour accepter initialement une décision dans un PostGIS jetable local qualifié,
`generer_ah_sections.py --accept-local PORT --database demo_jetable --schema gemapi --user installateur` remplit cette couche seulement si
elle est absente ou vide, en conservant les autres couches du GeoPackage AH ;
ce mode ne qualifie aucun cours d'eau.

Parcours de contrôle : rapport parent RC du Pré des Saules, **11 prestations**
(10 directes et l'élagage des boisements de rive gauche indirect, quantité
déclarée 96 m²). Rapport stockage : 8 directes. Rapport Remblai1 : 1 directe
(et une autre sur Remblai2).
Rapport Ruisseau du Pré Neuf : 1 prestation, conservant sa FK rivière et sa
géométrie propre. Rapport global : 100 UUID distincts.

Le contrôle QGIS compare les appartenances du rapport et de la gestion à celles de la vue PostgreSQL. Le site témoin compte huit prestations, enfants directs compris. Voir [la qualification du plugin](../audits/GEMAPI-QGIS-01.md).

Pour obtenir des **propositions sans écriture**, utiliser
`SELECT * FROM gemapi.ah_sections_candidates(:ah_id)`. L’intersection de
l’unique stockage facultatif avec les cours d’eau admissibles produit des
fractions calculées par le serveur ; les parties disjointes restent séparées,
les contacts ponctuels sont exclus. Sans stockage, aucune proposition spatiale
n’est retournée. Cela n'empêche pas de créer une section manuelle.

Une décision explicite s’enregistre par
`INSERT INTO gemapi.ah_sections(ah_id,cours_eau_id,debut_fraction,fin_fraction)
VALUES (:ah,:cours_eau,:debut,:fin)`. Le serveur calcule la sous-ligne avec
`ST_LineSubstring`, en respectant `0 <= début < fin <= 1`,
l'empreinte MD5 de l'EWKB du cours d'eau, l’auteur
(`session_user`) et la date de validation. Une proposition seule ne crée jamais
de référence acceptée.

`ah_sections_carte` expose l’instantané et `etat_reference` : `CONFORME`,
`COURS_EAU_NON_QUALIFIE` ou `REFERENTIEL_MODIFIE` (prioritaire si l’EWKB a changé).
Une modification du cours d’eau ne réécrit aucune section. L’empreinte tient
compte de l’ordre et de la densité des sommets, pas seulement de l’égalité
topologique. Un changement ou une suppression du stockage n'a aucun effet sur
la conformité de la section. Aucune signature du stockage n'est enregistrée.

Après examen métier, revalider explicitement par
`UPDATE gemapi.ah_sections SET revision_validation=revision_validation+1
WHERE id=:section`, avec les fractions manuelles examinées. Les changements
de parent, de cours d’eau ou de fractions exigent ce même incrément. Les simples
commentaires ne recalculent pas la référence. La révision et les dates/auteurs
conservent la trace de la dernière validation et modification ; il n’y a pas de
table d’historique des anciennes validations.

Toutes ces fonctions utilisent les droits de l’appelant. Lire la vue exige
`SELECT` sur elle et sur ses tables sources. Valider exige les droits d’écriture
sur `ah_sections`, `SELECT` et un droit `UPDATE` sur au moins une colonne de
`cours_eau`, nécessaire au verrou `FOR SHARE` conservé jusqu’à la fin de la
transaction. Aucun droit sur les cours d’eau n’est accordé automatiquement.
Les opérations respectent commit/rollback ; le code ne valide aucune transaction
du client. Aucun rôle applicatif ni accès public supplémentaire n’est créé.

Le référencement ne change ni les six rattachements directs des prestations,
ni LOC-03. Les rapports des parents AH utilisent uniquement les sections conformes
sur cours d’eau qualifié et l’intersection exacte avec la géométrie propre de la
prestation directement rattachée à ce cours d’eau. Les rapports enfants restent directs.

Le projet QGIS courant charge les sections acceptées et leur vue cartographique.
Dans la fiche native d’un AH, le bloc **Sections hydrographiques** propose la
consultation relationnelle, le zoom et la recherche de propositions. Le bouton
Tableau du `Relation Editor` affiche leurs colonnes. Les actions d’acceptation,
de modification et de revalidation sont fournies par le plugin **0.7.19**,
sans macro. Voir [le parcours utilisateur](../plugin/README.md#sections-hydrographiques-ah).
Les propositions restent temporaires jusqu’à confirmation explicite ; les
58 cours d’eau non qualifiés de Supabase n’en produisent aucune admissible.

Tests reproductibles sur un PostGIS jetable :
`/usr/bin/python3 plugin/tests/test_ah_sections.py`.

### Qualification des cours d’eau

Qualification HYDRO-QUAL-01 : `direction` est une validation humaine nullable
(TRUE : amont → aval vérifié, FALSE : sens contraire vérifié, NULL : non vérifié).
`section` compte les parties ; `valide` vaut uniquement `direction IS TRUE AND
section = 1`. Les multiparties restent éditables. La qualification ne garantit
ni la simplicité ni la validité topologique requises séparément par les digues.

PostgreSQL 16 utilise deux colonnes générées stockées ; l’expression de `valide`
répète `ST_NumGeometries`, une colonne générée ne pouvant en référencer une autre.
Un trigger BEFORE UPDATE invalide la direction si `ST_AsEWKB` change. Cette
comparaison conserve l’ordre des sommets et le SRID, contrairement à une égalité
topologique qui masquerait notamment une inversion du sens.

Le jeu livré contient déjà les qualifications attendues. La reconstruction utilise les exports suivis dans ce dépôt.

## Traçabilité

Chaque table métier conserve automatiquement `modifie_par` (compte PostgreSQL `session_user`) et `date_modification`. Chaque prestation porte un `technicien_id` obligatoire, indépendant du compte auteur.


## Repères de digues et projet QGIS (DIG-02)

La création d’un repère ou un changement de rivière/fractions exige le même
contrôle de qualification que les sections AH. Les changements attributaires
sans recalcul (par exemple rive ou nom) restent possibles pour un repère existant.
La vue conserve sa géométrie figée et expose `COURS_EAU_NON_QUALIFIE` si sa
signature est inchangée mais que son support n’est pas admissible. Si l’EWKB
diffère, `REFERENTIEL_MODIFIE` reste prioritaire. Aucun tracé n’est inversé.

Les trois repères fictifs existants sont conservés sur des supports non vérifiés.
`gpkg/digues.gpkg` conserve leur géométrie et leur `geom_riviere_signature` acceptées.
`generer_digues.py` exporte ces instantanés sans recalculer leur signature ou leur
forme depuis le cours d’eau actuel. L’import SQL administratif exige des tables
vides et les droits du propriétaire : dans une transaction verrouillée, il suspend
uniquement `digues_reperes_prepare_trg`, restaure les trois instantanés, exécute
les contraintes différées, puis réactive ce trigger avant commit. Les CHECK, FK,
contraintes de complétude des digues et triggers de traçabilité restent actifs.
Une erreur annule toute la restauration, y compris la suspension du trigger.
Ce chemin de reconstruction ne sert jamais à créer ou modifier un repère métier ;
aucun mode de contournement n’est ajouté au serveur. La seule qualification
fictive DEMO-AH-01 ne concerne aucun des trois supports des digues existantes.

Le projet `prestations_demo.qgz` contient la vue de consultation
`gemapi.digues_carte` au-dessus des cours d'eau. La couche est en
lecture seule et sa visibilité se commande dans le groupe **Milieux**. Elle est disponible après une installation neuve de la base.

La rivière conserve son style bleu. Les sections en rive gauche sont vertes
et décalées de **−1,6 mm** ; celles en rive droite sont orange et décalées de
**+1,6 mm**. Une référence devenue différente du tracé actuel ou portée par un cours d’eau
non qualifié reçoit en plus un trait rouge discontinu. Le symbole apparaît jusqu'à l'échelle
1:75 000 et le nom de la digue est étiqueté aux échelles de détail jusqu'à
1:25 000. La table attributaire montre par défaut la digue, la rivière, la
rive et l'état du référencement.

Le décalage est **uniquement graphique** : les sections restent des portions
de l'axe de la rivière, pas les géométries physiques des ouvrages. Les rives
G/D sont relatives au sens de numérisation. Avant une lecture hydraulique des
rives, vérifier que chaque rivière suit bien le sens amont → aval. La capture
`images/dig02_sections_rives.png` utilise une rivière et deux sections
fictives, posées sur le même intervalle.
