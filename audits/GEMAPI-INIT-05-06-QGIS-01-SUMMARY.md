# Bilan du lot INIT-05, INIT-06 et QGIS-01

> Recette historique avant préparation publique. Les empreintes et références Git ci-dessous décrivent le lot initial. Le logo fictif, la licence du plugin, le nouveau ZIP et la correspondance des commits sont documentés dans [PUBLICATION-01](GEMAPI-PUBLICATION-01.md).

Le clone de gemapi-dev fournit le modèle patrimonial v0.6.1, sa démonstration fictive, le plugin GEMAPI-App 0.8.6 et le générateur du projet QGIS de référence. Le modèle peut être installé dans un schéma choisi au déploiement. La webapp continue de servir Carte, Base et le shell PWA. Les accès métier web restent fermés en attendant l’identité et les chantiers fonctionnels.

## Sources et organisation

La cible est partie de `e0240ffdf9093abf9b335f4ef3c4678d42da9b08`. Les transferts proviennent exclusivement de `gemapi-app-sirs@90bf36947be15f834905f20f73148adc9c45d197`. Le lot web antérieur conserve sa provenance Digues `eecec0a44f25a908484b76f838a7e2be0c8cfee5`. Le répertoire frère digues-app a depuis avancé et n’a fourni aucun nouvel élément.

| Emplacement | Contenu |
|---|---|
| model/ | PDF, Mermaid et SQL v0.6.1 renommés et inchangés, matrice des rôles, installateur et procédure. |
| demo/ | Huit GeoPackages, exports SQL, générateurs, JPEG, CSV de chemins fictifs, illustration, QGZ, procédure et import transactionnel. |
| plugin/ | 38 fichiers du paquet QGIS dont les formulaires, ressources et aide, constructeur ZIP, générateur/synchroniseur QGZ, tests PyQGIS et documentation. |
| tests/model/ | Comparaison du modèle, trois profils SQL, données, topologie et reconstruction indépendante du donneur. |
| webapp/backend/ et compose.yaml | Sélection du schéma par configuration privée et diagnostic de lecture. Les nouveaux outils de maintenance et PyQGIS sont extérieurs au runtime web. |

L’[inventaire de provenance](GEMAPI-INIT-05-06-QGIS-01-PROVENANCE.tsv) recense les 117 correspondances source/destination avec leur blob donneur, y compris les extraits et les adaptations. Les procédures nouvelles et les contrôles propres à gemapi-dev sont identifiés dans les rapports. Les sources antérieures du modèle, le projet d’exploitation du donneur et le migrateur historique restent hors du produit.

## Résultats de qualification

| Périmètre | Résultat |
|---|---|
| SQL | 28 tests réussis sous PostgreSQL 16.15/PostGIS 3.4.2. Les 22 tables métier, quatre associations et 22 référentiels donnent 48 tables, 431 colonnes, 109 FK internes, aucune FK externe, 298 valeurs et six vues. Le contrôle porte aussi sur les clés, index, déclencheurs, références, contraintes différées, concurrence et droits. |
| Installation en base partagée | PostGIS dans public et extensions. Deux schémas applicatifs coexistants, dont un préattribué à un propriétaire sans CREATEROLE ni CREATEDB. Le témoin dans public est préservé. Les destinations occupées ou insûres et l’absence de PostGIS sont refusées avec rollback. Les identifiants spéciaux utilisables sont cités selon leur contexte SQL. Le nom réservé $user est refusé. |
| Données | Deux reconstructions du chargeur comparées à une installation brute du donneur. Les empreintes des 48 tables, les colonnes, contraintes et effectifs des six vues concordent. Les 100 prestations ont 88 formes propres et 12 replis sur la végétation. Les 12 sites, 19 végétations, quatre mobiliers, deux cheminements, sept tronçons, deux désordres, quatre observations et trois références photographiques sont conservés. |
| QGIS Linux 3.44.15 | ZIP reproductible, installation en profil vierge, chargement et déchargement. Projet livré et projet généré ouverts sur PostGIS dans deux schémas d’extension différents. Les 32 couches PostgreSQL, le fond OSM et les 70 relations sont valides. Widgets, cinq créations de prestations, édition, conflits, droits, rapports et exports vérifiés. Assistant testé sur réponses simulées, digues et sections AH sur PostgreSQL jetable. |
| Indépendance du clone | Un export Git de f415299 contenant les seuls fichiers suivis, sans les fichiers locaux et sans chemins donneurs configurés, a reproduit la démo et le SHA-256 du ZIP. Génération, synchronisation du QGZ, chargement du plugin et profils SQL sur les deux implantations de PostGIS ont été rejoués depuis cet export. |
| Web et Docker | Images FastAPI et renderer construites. Pile FastAPI, Caddy, renderer et base temporaire sur réseau de recette. Démarrage et redémarrage, diagnostic sous compte non propriétaire et empreinte du dump de structure identique avant/après. Huit scénarios Chromium via Caddy et 32 tests Python, avec 192 sous-cas, ont réussi. Les contrôles couvrent Carte/Base, les octets du branding, le shell hors ligne et la fermeture des entrées protégées ou multibase. La wheel de l’image finale f8ef802 refuse également le nom de schéma réservé avant connexion. |
| Cartographie | 114 tests du renderer réussis en une passe complète, sans cas ignoré, avec MapLibre natif. Catalogues WMTS disponibles et refus contrôlé d’Épuré à froid sans fournisseur. |

**Divergences et reprises.** Les 48 fiches PDF gardent les anciens qualifiants milieux, public et referentiel. Leurs nombres de colonnes et de FK entrantes et sortantes correspondent au Mermaid et au SQL après regroupement. Un cliché patrimonial manquant et des chemins réseau fictifs font partie des témoins d’origine. Les premiers montages de tests renderer omettaient le verrou npm puis le répertoire temporaire attendu. La correction du montage a permis la passe complète de 114 tests. Le dialogue de PDF synthétique a demandé une reprise avec les sockets Qt disponibles. Une copie instrumentée du test hydrographique journalise un diagnostic geometry_columns malgré la réussite des contrôles de couches et d’édition. Aucune régression fonctionnelle confirmée sur les parcours qualifiés. [SQL](GEMAPI-INIT-05.md) · [Démonstration](GEMAPI-INIT-06.md) · [QGIS](GEMAPI-QGIS-01.md)

## Décisions, limites et arbitrages

- Chaque déploiement a une connexion métier et un schéma applicatif. La démo permanente sur gemapi.dev et les productions utilisent le même code dans des instances indépendantes. Le futur écran de connexion pourra préremplir les seuls identifiants web de la démo, par configuration de son déploiement. Le choix d’authentification, de rôles web et d’auteur reste ouvert.
- La maintenance transforme une source SQL figée et vérifiée. Elle détecte PostGIS, vérifie les droits CREATE des schémas utilisés par les fonctions et applique l’installation en une transaction. Le backend ne crée aucun objet au démarrage. Les groupes SQL de cluster et les appartenances sont fournis explicitement par le DBA. Ils définissent des droits de base et ne représentent pas les identités web.
- Le CRS métier livré est EPSG:3950. Les convertisseurs génériques hérités sont conservés. Les handlers web différés contiennent encore des dépendances Digues, notamment les anciennes métadonnées CRS, fiches, relations et politiques SQL/IA. Les mécanismes actifs ne résolvent aucune base via le navigateur. Les anciens formats de stockage et les composants différés du premier lot restent à adapter séparément.
- QGIS-01 cible le schéma gemapi et le service de poste gemapi_demo. L’inventaire des URI, widgets, actions, rapports et modules Python figure dans le rapport QGIS. Les schémas personnalisés dans QGIS, la distribution web des fichiers et l’interopérabilité sont différés. Le projet de chaque instance correspondra à son seul environnement. L’accès SQL direct de production et les modalités de redistribution des ressources SIG restent à préciser.
- La recette Windows, les appareils PWA/GPS, la voix, l’IA en ligne, les partages de photos et la disponibilité réelle des fournisseurs restent hors des résultats obtenus. Les données métier sont utilisables par PostgreSQL et par QGIS sur la démonstration. Les API métier, formulaires web et opérations hors ligne attendent leurs chantiers.

## Reconstruction locale

Pour une base neuve dédiée à la démo, choisir PGHOST (répertoire de socket local), PGPORT et PGUSER. L’étape CREATE EXTENSION ci-dessous est une action DBA explicite sur cette base locale. Sur un serveur partagé, le DBA choisit l’emplacement de PostGIS et attribue le schéma et les groupes selon [la procédure d’installation](../model/README.md).

```sh
createdb demo_locale
psql -X -v ON_ERROR_STOP=1 -d demo_locale -c 'CREATE EXTENSION postgis WITH SCHEMA public'
bash demo/data/importer_base_neuve.sh demo_locale --schema gemapi
/usr/bin/python3 plugin/tools/build_plugin.py
/usr/bin/python3 plugin/tools/projet_qgis.py --generer demo/mon_projet.qgz
```

Pour une installation du modèle seul, utiliser `python3 model/install.py install --database NOM_BASE --schema NOM_SCHEMA` dans une destination vide. Pour QGIS, attribuer le profil SQL, configurer le service de poste, installer le ZIP puis ouvrir le QGZ. Pour le web, `docker compose up --build` sert le shell local sur `http://127.0.0.1:8080/`. Le diagnostic volontaire de l’image s’exécute via `docker compose exec app python -m digues_webapp.database`, après configuration privée de DATABASE_URL et DATABASE_SCHEMA. [Démonstration](../demo/README.md) · [QGIS](../plugin/README.md)

## Commits et état Git de clôture

| Commit local | Contenu |
|---|---|
| 61cd655 | INIT-05, modèle configurable, contrôles et contrat initial du lot. |
| ce5570e | Amendement du responsable, une instance monobase par déploiement. |
| 0a4dd7e | INIT-06, données fictives, reconstruction et témoins donneurs. |
| 350262d | INIT-05, vérification des ACL effectives du nouveau schéma. |
| f415299 | QGIS-01, plugin, projets, inventaire et qualification Linux. |
| f8ef802 | INIT-05, exclusion du nom dynamique du search_path et comparaison des fiches PDF. |
| GEMAPI — Clore INIT-05, INIT-06 et QGIS-01 | Présent bilan. Son SHA se lit avec `git log -1 --format=%H -- audits/GEMAPI-INIT-05-06-QGIS-01-SUMMARY.md`. |

| Dépôt | État à la clôture |
|---|---|
| gemapi-dev | development, sept commits locaux depuis e0240ff. Les fichiers suivis sont propres. Le fichier local préexistant GEMAPI-ROADMAP.md reste non suivi. La feuille de route opérante est sous docs/. |
| digues-app | development à ebd35689262168a5f353ec7916176797e694b27f. Arbre propre, deux commits devant sa référence de suivi locale comme au départ. |
| gemapi-app-sirs | gemapi-app-sirs à 90bf36947be15f834905f20f73148adc9c45d197. Arbre propre, synchronisé avec sa référence de suivi locale. |

Arrêt pour validation humaine. Aucun push ni déploiement. INIT-07 et QGIS-02 à 04 attendent un nouveau mandat.
