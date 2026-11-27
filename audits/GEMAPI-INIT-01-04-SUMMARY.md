# GEMAPI-INIT-01 à 04 — Bilan du premier lot autonome

Le socle gemapi.dev a été dérivé de Digues App
`eecec0a44f25a908484b76f838a7e2be0c8cfee5` sur `development`.
Le chemin de démarrage sert Carte, Base, le catalogue des fonds et la PWA
sans catalogue de bases ni schéma métier. L’accès aux données reste fermé
dans l’application. La Base affiche l’état d’accès. Les moteurs et les
écrans protégés hérités restent identifiables pour leur adaptation.

## Travaux et jalons

| Jalon | Travail réalisé | Commit local |
|---|---|---|
| [INIT-01](GEMAPI-INIT-01.md) | Reprise sélective, provenance et licences, scripts et témoins techniques. Exclusion de la démo Digues et du migrateur. | `804b8ec` |
| [INIT-02](GEMAPI-INIT-02.md) | Connexion d’instance, retrait du contrôle et des workers, refus des entrées protégées et pile locale. | `165719b` |
| [INIT-03](GEMAPI-INIT-03.md) | Cartouche et actifs PWA livrés à l’identique, versionnés avec le shell. | `02c7c89` |
| [INIT-04](GEMAPI-INIT-04.md) | Qualification indépendante du métier, correctifs de démarrage en mode dégradé, de PWA et de branding mobile. | `7b8aac9` |

## État des fonctions

| Statut | Capacités |
|---|---|
| Opérationnel | Entrée racine et Carte, page Base fermée, catalogue Leaflet/IGN et repli, refus directs sur les API protégées. Les résultats de lancement du packaging, de la PWA et du mode terrain simulé sont détaillés dans le rapport 04. |
| Qualifié sur composants et fixtures | Moteur MapLibre/WMTS, cache global froid et chaud, budgets, bornes LiDAR et GeoTIFF synthétique, météo/hydro, projection CRS, contrôle des photos binaires, brouillon de géométrie, bus inter-pages et isolation des clés. PostgreSQL a été vérifié sur base jetable avec rôle restreint. |
| Conservé, parcours restant à qualifier | Préparation des paquets cartographiques, stockage en appareil, installation native et GPS physique, reconnexion, restauration SQL et médias, diagnostics opérateur. Les sources de l’impression et de la projection spatiale restent disponibles. |
| Différé avec le schéma et sa démo | Fiches, couches et masques patrimoniaux, territoire, référentiels, géométries métiers, formulaire et auteur, pièces rattachées, règles SQL, cache hydrométrique persistant, instantanés, intentions, journal, conflits et replay. |
| En attente d’arbitrage | Identité et autorisations, CSRF/révocation en session, rôle terrain, attribution d’auteur, périmètre public éventuel et exposition IA/SQL. |

La recette a passé 129 tests Python de socle, 17 tests unitaires SQL/IA,
12 fichiers de tests Node et la suite d’isolation du contexte, ainsi que
8 scénarios Chromium, dont 3 rejoués avec succès via le proxy de la pile Docker. Les 114 cas du renderer
sont couverts sur deux passages après reprise du délai natif dépassé sous
charge. La base PostGIS jetable a confirmé le rôle restreint et la transformation
CRS. La recette utilise des contrôles analytiques, fixtures et fournisseurs
simulés. Les résultats détaillés figurent dans le [rapport INIT-04](GEMAPI-INIT-04.md). Les difficultés observées
ont porté sur le logo mobile et sur l’arrêt du renderer lors d’une panne du
fournisseur initial. Ces chemins ont été corrigés et vérifiés en recette.

## Dépendances héritées et démarrage

Les distributions Python conservent les noms de code hérités
`digues_app` et `digues_webapp`. Le code différé porte encore les tables et relations Digues, les métadonnées
CRS `technique.metadonnees_base`, des formats de pièces et des règles de
lecture SQL/IA liées aux anciennes colonnes. Les handlers, DTO et scripts de
vue ne sont pas activés. Les tests couplés au modèle ou à l’ancien login restent
des témoins. Le modèle compilé, ses DDL, le seed, les jeux d’import et les
workers ne sont pas livrés comme prérequis.

Les chemins HTTP ne résolvent plus de base à partir d’une requête. Les
identifiants de l’ancien format de stockage binaire sont encore présents dans
les composants bas niveau et leurs témoins. La configuration active du stockage
utilise un volume privé par instance. Les désignations et contrats hérités
actifs dans le code différé attendent le raccordement du schéma et des permissions.

`docker compose up --build` lance la pile locale. Entrée à
`http://127.0.0.1:8080/`, port ajustable par `GEMAPI_PORT`. L’image FastAPI associe les paquets Python et GDAL de Debian trixie.
Le service tourne sous un utilisateur applicatif, Caddy garde les routes internes
privées et les volumes cache et médias restent séparés. La construction, le
démarrage et le redémarrage de la pile ont été vérifiés sur un projet
Compose jetable. Les conditions
d’usage et de construction sont dans le [README](../README.md).
L’utilisation future de `DATABASE_URL` et des rôles de lecture demande une
configuration privée par instance. L’application ne crée pas les objets SQL.

## Relevé Git de clôture

| Répertoire | Branche et révision | État |
|---|---|---|
| `gemapi-dev` | `development`, `7b8aac9` puis le commit « GEMAPI-INIT — Clore et documenter les lots 01 à 04 » | Les fichiers suivis sont propres. `main` reste sur `b9fe3b1`. La copie locale préexistante `GEMAPI-ROADMAP.md` reste non suivie. Les deux JPEG sous `assets/` restent locaux, ignorés et intacts. |
| `digues-app` | `development` · `eecec0a44f25a908484b76f838a7e2be0c8cfee5` | Arbre propre et branche inchangée. Source consultée en lecture seule. |
| `gemapi-app-sirs` | `gemapi-app-sirs` · `350d160eb7670611bac33b5380fb4b3cae7b94a6` | Arbre propre et branche inchangée. Worktree consulté seulement pour son état Git. |

Les commits sont locaux. Arrêt à INIT-04. INIT-05, intégration du schéma
et de la démo, traitement de données réelles et déploiement restent hors de
ce lot.
