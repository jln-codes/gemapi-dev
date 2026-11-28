# gemapi-dev — Feuille de route de dérivation

**Lot autorisé.** INIT-00 à 04 sont validés et clos à `e0240ff`. La livraison `90bf36947be15f834905f20f73148adc9c45d197` ouvre 05, 06 et QGIS-01. Arrêt après leurs audits et leur bilan. INIT-07, QGIS-02, la publication et le déploiement nécessitent un nouvel accord.

La webapp desktop/PWA et QGIS partagent le modèle SQL, sa démo et les référentiels. Les parcours peuvent évoluer indépendamment. Chaque instance utilise une seule connexion métier et un seul schéma configurable au déploiement. La démo permanente sur gemapi.dev et les productions indépendantes partagent le code. L’accueil sera un écran de connexion simple. La configuration propre à la démo pourra préremplir ses identifiants web de démonstration. Les comptes de production et les secrets PostgreSQL du backend sont exclus. Aucune sélection d’environnement, découverte, administration ou sélection de bases. Le migrateur reste externe. L’authentification web définitive reste différée.

La cible 05 accepte un schéma vide attribué dans une base partagée. La qualification couvre deux destinations coexistantes, deux emplacements de PostGIS, le refus d’une destination occupée, les objets tiers, les profils SQL et le démarrage sans DDL. Le port, le serveur, la base, le schéma et les paramètres privés sont indépendants. En 06, reconstruire depuis le clone et comparer les empreintes des données livrées.

| Filière QGIS | Acceptation |
|---|---|
| QGIS-01, autorisé après 06 | Reprendre le plugin 0.8.6, reconstruire le ZIP, générer le projet de référence et tester sous PyQGIS les couches, relations, widgets, éditions, rapports, exports et droits sur la démo. |
| QGIS-02, différé | Adapter connexions et schémas dans les couches, widgets, plugin et générateur. Décider avec le SIG de la connexion directe au réel. |
| QGIS-03, différé | Distribuer le ZIP et le QGZ configuré depuis la webapp pour l’unique environnement de l’instance. Exclure les secrets du backend. |
| QGIS-04, différé | Qualifier les échanges et l’interopérabilité webapp/QGIS sur le même modèle. |

Cette filière peut avancer en parallèle des futurs chantiers métier web après autorisation.

| Disponibilité | Chantiers | Résultat attendu |
|---|---|---|
| Avant le SQL définitif et la démo actualisée | 01 à 04 | Sources traçables, pile monobase, identité visuelle, socle testé en environnement local isolé. La décision d’authentification conditionne les accès protégés. |
| À réception du SQL stabilisé | 05 | Installation neuve et exploitation sans DDL par le runtime. Peut précéder la livraison des données. |
| À réception de la démo alignée | 06 à 09 | Jeu de référence reproductible, consultation puis édition en ligne, enfin travail terrain avec reprise. |
| Après recette et accord d’hébergement | 10 | Démonstration qualifiée et déploiement explicitement autorisé. |

### 01 — Reprendre le socle et ses témoins de comportement

**Objectif et périmètre.** Dans gemapi-dev, reprendre par lots revus frontend, FastAPI, cartographie, médias, composants PWA, packaging et tests utiles. Garder les modules métier nécessaires à l’adaptation ultérieure identifiables à leur révision source. Aucun import même transitoire du migrateur, de contenu privé ou de jeux réels. Privilégier la copie du code éprouvé aux nouvelles abstractions.

**Prérequis et dépendances.** INIT-00 validé. Relever l’état des trois dépôts, confirmer le SHA disponible de digues-app et revoir les fichiers à copier à partir de l’arbre Git de cette révision. Distinguer dépendances Python communes, JS, bibliothèques tierces embarquées et binaires du renderer. L’authentification et le SQL métier peuvent encore être en discussion.

**Acceptation et tests.** Retrouver la provenance, les notices et les modifications à partir de la livraison. Construire ou charger les sous-ensembles autonomes, dont le renderer, et exécuter ses tests de géométrie/cache et le test Node d’ordre des plans Leaflet. Comparer les fichiers repris et leurs différences intentionnelles à la source. Contrôler que branding, données sources et fichiers locaux préexistants restent intacts. Une route encore couplée peut attendre 02 ou 07 sans être publiée.

**Arrêt et décision humaine.** Confirmer le commit audité. Les 14 commits initialement en avance dans le donneur figurent dans sa référence de suivi locale au contrôle final. Suspendre la copie d’un élément dont l’origine, la licence ou la dépendance privée est incertaine. Reprendre les autres lots indépendants.

### 02 — Retirer multibase et import, rétablir une frontière d’accès monobase

**Objectif et périmètre.** Une connexion métier par instance, fixée côté déploiement. Reprendre l’injection des connexions et gardes des handlers, supprimer annuaire de bases, sélecteur, routage contextualisé, provisioning, opérations de parc, quotas par base, UI et documentation exclusivement associées. Éliminer les appels et dépendances résiduels du migrateur dans le build, les routes, les marqueurs SQL, les scripts frontend, les listes du SW, les tests et Compose. Conserver droits, révocation, rôle de lecture spécialisé si nécessaire et capacités de cache/diagnostic utiles.

**Prérequis et dépendances.** 01. Suivre pour chaque méthode/route ses contrôles, ses lecteurs et ses producteurs. Inventorier la clé d’identité des médias, canaux, paquets et intentions. Exécuter tout de suite le retrait de l’infrastructure dédiée, la construction locale et les tests de refus. Brancher l’identité effective après arbitrage.

**Acceptation et tests.** Tester depuis la wheel et l’image installée. Le build et le démarrage fonctionnent sans `digues_control`, fichier de profils, worker, catalogue SIRS ou modèle Digues obligatoire au démarrage. Vérifier les routes réellement enregistrées, les commandes de l’image, le Compose résolu et les requêtes du navigateur. Un sélecteur d’URL ne change jamais de base. Vérifier anonymat refusé, lecture autorisée, écriture interdite au lecteur, contrôle d’origine, révocation pendant une session et un flux. Tester deux utilisateurs successifs sur le même navigateur. Adapter `test_auth.py`, `test_business_context.py`, `business-startup.spec.js` et les contrôles de déploiement. Si l’identité reste indécise, maintenir les fonctions protégées fermées et déclarer cette partie du lot en attente.

**Arrêt et décision humaine.** Faire valider mécanisme d’authentification, autorisations, attribution de l’auteur et accès simplifié éventuel à la démo. Si une voie exige de réintroduire le contrôle, un accès anonyme ou un second schéma métier, arrêter cette voie. Les réglages photo, LiDAR et cache auparavant administrés restent à raccorder à un support approuvé.

### 03 — Intégrer le branding validé

**Objectif et périmètre.** Relier cartouche, favicon, icône Apple, manifeste et icônes PWA de `branding/` aux pages et à l’installation mobile. Respecter leurs proportions, couleurs, usages et emplacements. Préserver les notices d’origine du code.

**Prérequis et dépendances.** Shell et routes statiques de 01–02, conventions décrites dans `branding/README.md`. Le manifeste du branding prévoit `/` comme URL de départ et scope. La source Digues démarre à `/carte`. Vérifier l’entrée racine et ses redirections dans le flux monobase.

**Acceptation et tests.** Pages, dialogue d’accès et PWA présentent gemapi.dev. Une réponse valide pour chaque actif sur installation neuve et après mise à jour du Service Worker (SW). Contrôler tailles normales et maskables, accès hors ligne au shell, vue étroite, absence de logo tronqué. Exécuter les scénarios dérivés de `pwa-shell.spec.js` et `mobile-viewport-layout.spec.js`. Diff vide sur les fichiers sources du branding.

**Arrêt et décision humaine.** Un déploiement sous chemin ou une modification graphique demandant de changer les éléments validés appelle une décision. Le raccordement des actifs existants relève de Codex.

### 04 — Qualifier le socle technique indépendamment du métier

**Objectif et périmètre.** Valider localement FastAPI, pages Carte/Base, navigation, Docker Compose, Leaflet, MapLibre natif, IGN, caches, LiDAR sur échantillon, météo/hydro simulées, acquisition GPS, dessin/correction, conversions CRS, traitement binaire photo, bus inter-pages, installation et shell hors ligne. Les fixtures se limitent aux contrats techniques (position, tuile, image synthétique, réponse fournisseur).

**Prérequis et dépendances.** 01–03 pour une qualification intégrée. Les tests unitaires indépendants peuvent avancer dès 01. Utiliser les verrous de dépendances existants, une base jetable vide si nécessaire et des volumes de test isolés. Aucune base permanente comme cible de recette.

**Acceptation et tests.** Démarrer puis redémarrer l’image, ouvrir les deux pages par le proxy, vérifier disponibilité des assets et refus des chemins internes. Tester tuile froide/chaude, fournisseur lent ou absent, quotas de cache, lecture d’un bloc LiDAR, chronologie météo, pagination hydro, permission GPS refusée, fin de suivi, annulation d’un polygone, invalidation entre deux onglets, mise à jour SW et relance sans réseau. Exécuter les tests de `services/plan-ign-raster/`, les tests unitaires ciblés de `webapp/tests/` et les specs pertinentes du tableau PWA de l’audit. Rapporter ce qui a été simulé, testé sur navigateur et constaté sur appareil.

**Arrêt et décision humaine.** Masques patrimoniaux, emprise effective, persistance hydrométrique, fiches et replay métier passent aux lots suivants. Si un service nécessite une clé, l’automatisation travaille sur des réponses contrôlées. La qualification contre un fournisseur réel et sur les appareils cibles nécessite des accès et appareils convenus.

### 05 — Installer le schéma PostgreSQL/PostGIS stabilisé

**Objectif et périmètre.** Intégrer la livraison SQL métier identifiée par sa révision. Distinguer installation neuve, éventuelle mise à niveau technique explicite et exploitation courante. Vérifier extensions et schémas d’installation, SRID, permissions, fonctions, tables techniques de cache, versions, reçus et journal nécessaires aux parcours approuvés.

**Prérequis et dépendances.** SQL livré et validé par le responsable du modèle. Connexion monobase de 02. Un DBA identifie le rôle d’installation et les droits du rôle applicatif. La démo définitive est facultative pour ce lot.

**Acceptation et tests.** Reconstruire deux installations neuves indépendantes sur PG/PostGIS jetable. Vérifier par introspection les contrats livrés. Démarrer deux fois l’application sous un rôle non propriétaire sans SUPERUSER, CREATEDB, CREATEROLE ni CREATE sur les schémas, vérifier le schéma avant/après et le refus contrôlé quand un prérequis manque. Tester connectivité, lecture et transformations sans créer d’objets au démarrage. Les droits d’écriture réels seront éprouvés en 08.

**Arrêt et décision humaine.** SQL absent ou en contradiction avec sa documentation, extension indisponible, versionnement/attribution/journal non définis ou DDL imposé au runtime. Soumettre le manque au responsable du modèle et au DBA. Une évolution technique du schéma se fait sur mandat explicite, indépendamment des données historiques.

### 06 — Intégrer le jeu de démonstration de référence

**Objectif et périmètre.** Intégrer dans gemapi-dev la livraison fictive alignée, ses médias de démo autorisés et son mode de chargement reproductible. Elle devient la référence commune du développement, des tests métier et de la qualification.

**Prérequis et dépendances.** 05 et livraison démo identifiée, compatible avec cette version du SQL. Recevoir les résultats de référence connus (identifiants stables, relations, géométries, médias et effectifs disponibles).

**Acceptation et tests.** Charger deux bases jetables neuves, comparer effectifs, identifiants et relations, vérifier contraintes, SRID/types, objets sans géométrie lorsque permis, disponibilité et empreintes des médias fournis. Rejouer le chargement selon son mode documenté. Le clone cible suffit pour reconstituer la démo sans lire les dépôts voisins.

**Arrêt et décision humaine.** Provenance incertaine, donnée identifiable réelle, incohérence avec 05 ou résultat attendu manquant pour qualifier un parcours. Faire corriger ou préciser la livraison par son propriétaire. Ne pas réparer les données du donneur.

### 07 — Relier les couches et vues de consultation

**Objectif et périmètre.** Pour chaque parcours de lecture validé, relier catalogue de couches, requête SQL, API/GeoJSON, filtres, légende, fiche Base, relations et sélection Carte/Base. Raccorder les services territoriaux, les masques LiDAR et les exports de lecture retenus.

**Prérequis et dépendances.** 05–06, connexion et droits de lecture qualifiés. Faire préciser les objets à afficher, leur hiérarchie éventuelle, le contour autorisé et les attentes sur les géométries absentes. Utiliser les données réellement livrées.

**Acceptation et tests.** Sur la même démo, sélectionner un objet dans chaque page puis retrouver l’identifiant et ses relations dans l’autre. Comparer effectifs API/SQL, CRS, emprises et résultats des filtres. Contrôler état vide, objet non spatial, accès interdit, territoires sans résultat et crédits des fonds. Adapter `reader-spatial-detail.spec.js`, `base-related-entities.spec.js`, `lidar-map.spec.js` et les tests SQL d’hydrométrie. Tester les exports approuvés sur une sélection connue.

**Arrêt et décision humaine.** Un champ analogue à un champ Digues ne suffit pas à définir une couche, un parent, une couleur de classement ou une limite territoriale. Soumettre le seul point ambigu et poursuivre les lectures établies.

### 08 — Adapter formulaires et édition en ligne

**Objectif et périmètre.** Adapter un parcours à la fois, du formulaire aux mutations SQL et aux invalidations. Inclure création, correction des géométries, coordonnées/GPS et médias selon les règles livrées. Conserver l’ergonomie d’édition et la protection des brouillons.

**Prérequis et dépendances.** 06–07, matrice des ressources/actions, contraintes, référentiels, règles de version et d’auteur confirmés. Support SQL des mutations et des reçus approuvé. Attribution des droits selon 02.

**Acceptation et tests.** Créer, consulter puis modifier depuis les entrées Carte et Base, contrôler les données et relations en SQL puis rouvrir une nouvelle session. Tester point, ligne et polygone lorsqu’autorisés, saisie invalide conservée, édition concurrente, doublon de requête, rollback SQL, upload interrompu et rafraîchissement inter-onglets. Adapter les specs `form-regression-live`, `form-geometry-interactions`, `resource-concurrency` et `photos` et les intégrations PostGIS correspondantes.

**Arrêt et décision humaine.** Absence de règle sur la géométrie faisant autorité, le rattachement ou une action destructive, le devenir d’un brouillon ou l’auteur. Demander l’arbitrage métier avant d’activer l’action.

### 09 — Adapter et qualifier le travail métier hors ligne

**Objectif et périmètre.** Qualifier successivement préparation de périmètre, consultation IndexedDB, création/modification autorisée, photo locale, reconnexion et rattrapage du journal. Maintenir l’isolation du terrain par instance, utilisateur et génération des données.

**Prérequis et dépendances.** 04 pour le SW et les cartes locales, 07–08 pour un parcours en ligne de référence et la concurrence SQL. Valider le périmètre préparé, les mutations éligibles, la durée des accès locaux, la conservation des originaux et le traitement humain d’un conflit.

**Acceptation et tests.** Avec cache vide préparer la démo en ligne, passer en mode avion, fermer et rouvrir la PWA, lire une fiche, enregistrer une intention et une photo. Interrompre l’upload, perdre une réponse après commit, rejouer deux fois et depuis deux onglets. Vérifier une seule mutation confirmée, blob récupérable, curseur cohérent et brouillon visible en cas de conflit. Tester quota épuisé, révocation, nouvel utilisateur, changement de génération après restauration, mise à jour de l’application avec intentions pendantes. Exécuter les scénarios adaptés de `offline-operations`, `offline-projection`, `photo-offline-lot4*`, `sync-audit-fixes` puis vérifier le cycle sur un appareil cible.

**Arrêt et décision humaine.** Perte d’original ou d’intention, écrasement concurrent, réutilisation d’un contexte expiré, résultat ambigu après reprise. Suspendre la recette de la fonction touchée et convenir de la résolution des cas métier non définis.

### 10 — Qualifier et déployer la démonstration

**Objectif et périmètre.** Livrer une démonstration reproductible sur le scénario approuvé. Préparer l’image, la configuration de déploiement non secrète, la sauvegarde/restauration cohérente base + médias et les contrôles d’exploitation.

**Prérequis et dépendances.** Lots antérieurs qualifiés pour le périmètre retenu. Décisions d’hébergement, exposition de la démo, domaine, réseau et TLS vers PostgreSQL, accès fournisseurs, comptes d’exploitation et responsabilités de sauvegarde validés. Le scénario prioritaire est VPS applicatif et PostgreSQL/PostGIS de la collectivité. Tester le scénario retenu, sans dépendre d’API d’hébergeur.

**Acceptation et tests.** Depuis un clone propre reconstruire image, schéma et démo. Recette complète Playwright, API/PostGIS, cartes, photos, impressions/exports retenus, GPS/PWA sur matériels convenus, coupure et reprise du réseau vers PG, contrôle de droits, redémarrage sans DDL et restauration isolée. Vérifier Caddy, certificats, flux SSE, caches et persistance après redémarrage. Présenter résultats et réserves avant publication. Après accord explicite, déployer **la démo** et vérifier les accès et parcours sur l’instance livrée.

**Arrêt et décision humaine.** Tout effet sur un environnement permanent, toute publication et la mise en service demandent une autorisation visant cet environnement. Un échec d’intégrité, de confidentialité, de reprise ou de restauration suspend la diffusion. Les données historiques réelles relèvent d’un chantier externe ultérieur à la qualification de la webapp.
