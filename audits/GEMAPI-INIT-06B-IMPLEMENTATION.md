# INIT-06B — Authentification et sessions PostgreSQL

## Périmètre et décisions

La révision de départ de `development` était `9e2a29ab120f5ca620b2f49c953c7aa84b9b07e1`, neuf commits devant la référence locale `origin/development`. Le fichier non suivi `GEMAPI-ROADMAP.md` à la racine était présent avant intervention. La feuille de route active est dans `docs/`.

L’option A a été implémentée. L’authentification individuelle par SCRAM est commune à QGIS et à la webapp. Chaque session possède son backend PostgreSQL et son identité SQL pendant toute sa vie. L’arbitrage remplace les durées proposées de 15 minutes et 8 heures par 2 heures et 12 heures. La future démonstration publique sera modifiable. Sa politique de restauration, ses quotas, ses limites d’abus et sa conservation restent à qualifier avant ouverture. Aucun changement du DDL, des concessions de `model/roles.sql`, du plugin ou du jeu de démonstration.

## Fonctionnement livré

- L’accueil reçoit le login et le mot de passe. L’origine et la configuration privée déterminent l’instance, le serveur, la base, le schéma et les paramètres TLS. Le succès ouvre Carte. Carte et Base présentent la déconnexion. Leur shell public reste accessible en accès direct et utilise exclusivement les ressources techniques qualifiées.
- L’admission exige SCRAM, les identités SQL attendues, les droits de consultation communs, les droits de schéma et l’absence de privilèges d’administration incompatibles. Les contrôles sont regroupés en SQL et revalidés dans la transaction de l’utilisateur. Le préremplissage de la démo nécessite son mode explicite, son compte dédié et un handshake de vérification au démarrage.
- Le registre en mémoire compte les sessions et les authentifications en cours. Il affecte une connexion exclusive par jeton, sérialise les utilisateurs de cette connexion, applique commit ou rollback et ferme aux échéances, sur perte de la connexion ou à l’arrêt. Un contexte non porteur protège contre l’utilisation d’une requête issue d’un ancien onglet. L’image impose un worker et le cycle de vie ASGI.
- Les plafonds initiaux sont de 20 sessions, 3 par compte, 3 authentifications SQL concurrentes et un budget applicatif de 21 places dont une réservée aux usages techniques. Le compte de démonstration a un réglage distinct. Le dimensionnement institutionnel reste à convenir avec la DSI. Le shell PWA préserve les données locales lors de l’invalidation du contexte et exclut les appels d’authentification du cache.
- Les 144 couples méthode/chemin métier hérités restent fermés. Le témoin d’écriture et d’auteur utilise directement le connecteur testé dans une transaction sur la base jetable. Il ne passe par aucune API de mutation.

Le [contrat d’exploitation](../docs/GEMAPI-AUTH.md) précise les variables, cookies, en-têtes, échéances, limites du pilote, révocations et interventions DBA. L’[étude préalable](GEMAPI-INIT-06B-AUDIT.md) conserve la comparaison des architectures.

## Qualification

La recette crée des conteneurs, volumes, certificats, utilisateurs et secrets éphémères. Le premier cluster est testé par socket Unix puis TCP/TLS. Le second est installé à neuf, avec un autre nom DNS et un autre certificat. Dans chaque cas, le schéma installé s’appelle `Territoire test`. Les essais SQL natifs complémentaires utilisent aussi le schéma `SIG essai` et PostGIS hors de public. Chaque login HTTP passe par Caddy, l’image FastAPI et le véritable serveur SQL. La production simulée et la démonstration utilisent le même code.

| Vérification | Observation |
|---|---|
| Python | 51 tests et 201 sous-cas réussis en une passe. Un témoin vérifie la fermeture et la réutilisation de la place sans trafic HTTP à l’échéance absolue. Registre, budgets, concurrence et fixation, échéance absolue malgré activité, inactivité, CSRF et origine par endpoint, taille du formulaire, messages, cookie HTTPS, refus du multi-worker, PWA, catalogue et CRS. Une dépréciation de la dépendance de TestClient a été signalée. |
| JavaScript | 8 fichiers de tests Node réussis pour l’isolation du contexte, les couches Leaflet, le LiDAR, les fonds, leur sélecteur, leur stockage et leur téléchargement. |
| Chromium | 13 exécutions réussies, 11 scénarios distincts. Une recette de production sur socket vérifie les champs vides et le refus visible. Sur le premier hôte SQL, deux scénarios de connexion et huit de non-régression passent en HTTP de boucle locale. Sur le second, les deux parcours de connexion passent en HTTPS avec un certificat de recette épinglé. Vérifications du cookie HttpOnly, Secure en HTTPS, SameSite=Strict, du changement de compte entre onglets, de la déconnexion depuis Base, des stockages et caches du navigateur. Les journaux du proxy et du backend ont été contrôlés avec les secrets éphémères des essais. |
| Trois profils SQL | Contrôle dans pg_stat_activity et pg_stat_ssl du login, d’un seul PID par session, de l’état idle entre appels, du transport et du retour à zéro après déconnexion. Le lecteur ne peut pas écrire. Le profil Patrimoine produit le triplet (gestion, gestion, gestion) pour session_user, current_user et modifie_par. Une erreur SQL forcée annule l’insertion, la connexion revient à IDLE et la session peut relire la table. Quatre opérations concurrentes sur deux sessions observent deux PID distincts et stables. |
| Refus et révocation | Rejet du mot de passe erroné, du compte extérieur, du rôle CREATEROLE, de HBA trust, d’un certificat ne couvrant pas l’hôte et d’une autorité inaccessible. Le retrait du groupe ou d’un SELECT accordé individuellement, NOLOGIN et VALID UNTIL expiré ferment la session au prochain contrôle. Le changement de mot de passe seul laisse la connexion existante valide et fait échouer un nouveau login avec l’ancien secret. |
| Ressources et redémarrage | Sur socket, avec échéance inactive de 3 secondes et sans trafic HTTP, pg_stat_activity revient à zéro. Les tests HTTP exercent le plafond individuel avec trois sessions du même compte et le plafond global de recette avec quatre sessions. Les sessions existantes restent utilisables. Une terminaison DBA et le redémarrage HTTP imposent une nouvelle authentification. |
| Interruption réseau | Déconnexion de l’interface réseau SQL du conteneur applicatif tout en préservant HTTP. La première utilisation reçoit 503, puis le jeton est refusé avec 401. Après rétablissement et login explicite, le compteur PostgreSQL revient à zéro. Mesures depuis la coupure, 25,3 secondes sur sql et 25,7 secondes sur sql-b avec une échéance de test de 25 secondes. |
| Frontière métier | 144 refus HTTP 403 avec un véritable cookie authentifié, en plus de la vérification de chaque couple en accès anonyme dans les tests Python. Les routes de sélection de base et d’administration multibase restent absentes. |
| Non-régression SQL | Le témoin natif de l’étude sur socket SCRAM a réussi. La matrice SQL des trois profils a réussi dans les deux installations dont PostGIS réside respectivement dans public et extensions. L’essai historique de délégation appartient exclusivement à la comparaison de l’étude. |
| Packaging | Construction de la wheel dans l’image, contrôle des dépendances et validation de Compose réussis. La configuration du serveur et le refus de WEB_CONCURRENCY=2 sont contrôlés. |

Deux tests historiques supplémentaires de `webapp/tests/test_app_header.py` échouent. Les mêmes deux échecs ont été reproduits en exécutant le test et le module d’en-tête extraits de `9e2a29a`. Leurs attentes incluent l’ancien bouton de session métier et l’interface admin supprimée. Les témoins sont conservés. Les parcours actuels de l’en-tête sont testés dans Chromium.

### Empreintes et incidents de recette

| Image observée sur le socket et les deux hôtes SQL | Empreinte Docker |
|---|---|
| Application FastAPI construite avec la révision `be358bc` | `sha256:51b55b9c03dcbe5b53e77c1b1c435c2bc1ed89e5ea25be425006350dbb47f65f` |
| Renderer | `sha256:466fa224805fc76a220850137e96e43a69a9fe5ab86262ad36883f15bb65076b` |
| Proxy | `sha256:6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b` |
| PostgreSQL/PostGIS | `sha256:44126d872ac91993766c341e369c539e8196614321765d36a6f1bab0419a5fa5` |

Le banc épingle l’application et le renderer par leur identifiant local avant le premier démarrage. Le pilote de l’image est psycopg 3.3.9 avec libpq 18.6. Au premier essai TLS, libpq cherchait un certificat dans le répertoire de root après le passage à l’UID 10001. Le point d’entrée utilise désormais le répertoire du compte effectif. Lors d’une première coupure franche, l’invalidation HTTP fonctionnait mais le PID SQL restait présent après 90 secondes. L’échéance d’inactivité appliquée également par PostgreSQL borne désormais cette rétention.

Des essais Chromium intermédiaires ont rencontré un changement de réseau et des délais de navigation ou d’activation du Service Worker. La passe de référence s’exécute en série avec les catalogues du renderer montés comme dans Compose et le réseau cartographique isolé des fournisseurs. Le contrôle consulte le véritable TileJSON via Caddy. Les scénarios de shell et leurs assertions sont conservés.

## Reproduire

Depuis un clone avec Docker, OpenSSL, Python, Node, les dépendances verrouillées Playwright et Chromium installé :

```sh
docker build -t gemapi-init06b-auth:qualification .
docker build -t gemapi-init04-renderer:local services/plan-ign-raster
python3 tests/auth/qualify_images.py
```

Le banc attend PostgreSQL/PostGIS `postgis/postgis:16-3.4` et `caddy:2-alpine` dans le cache Docker. Il lie les ports web à la boucle locale. Les réseaux, les certificats et les données sont propres à chaque exécution.

```sh
PYTHONPATH=webapp/backend:. python -m pytest -q webapp/tests/test_session_registry.py webapp/tests/test_auth_http.py webapp/tests/test_instance_access.py webapp/tests/test_pwa.py webapp/tests/test_basemap_catalog.py tests/test_business_crs.py
node --test tests/instance-context.test.cjs tests/leaflet-layer-order.test.cjs tests/lidar-map-store.test.cjs tests/lidar-prototype-core.test.cjs webapp/tests/test_basemap.js webapp/tests/test_basemap_selector.js webapp/tests/test_offline_basemap_store.js webapp/tests/test_offline_basemap_download.js
/usr/bin/python3 tests/model/test_session_identity.py
/usr/bin/python3 tests/model/test_roles_postgres.py
```

## Limites et suites

La séparation des hôtes Docker couvre DNS, vérification du serveur, SCRAM, TCP/TLS et la perte du réseau SQL sur une même machine physique. La recette WAN sur une autre machine et les mesures de latence institutionnelle sont reportées à DEPLOY-01, sans bloquer INIT-06B. Aucun serveur de digues-app, instance Supabase, compte de production ni pooler distant n’a été sollicité. L’interruption d’une connexion distante peut laisser un PID SQL jusqu’à la détection TCP ou au délai d’inactivité côté serveur. La mémoire de libpq reste sensible pendant la vie de la connexion.

Avant INIT-07, valider les couches et objets à exposer, les contrats de lecture, les droits effectivement nécessaires sur leurs dépendances, la sélection territoriale et le traitement des géométries absentes. Ouvrir et tester chaque opération individuellement sur la démonstration v0.6.1. INIT-09 conserve l’identité durable des brouillons, la réauthentification après coupure, le déverrouillage local et le replay. L’ouverture publique de la démonstration et tout déploiement restent soumis à validation.

## Git et clôture

| Commit local | Contenu |
|---|---|
| `be358bc` | Authentification et admission SQL, sessions, sécurité HTTP, écran de connexion, shell PWA et tests associés. |
| `598d39c` | Banc PostgreSQL, HTTP et Chromium, contrôles de réseau, de restitution et de nettoyage autonome. |
| Commit portant ce rapport | Contrat d’exploitation, contrat du dépôt, feuille de route et bilan de qualification. Retrouver son SHA avec `git log -1 --format=%h -- audits/GEMAPI-INIT-06B-IMPLEMENTATION.md`. |

Les trois commits sont locaux sur `development`. Le seul fichier de travail restant à la clôture est le fichier non suivi préexistant à la racine. Les donneurs lus pour l’état Git sont propres, `digues-app` sur `development` à `ebd356892621`, et `gemapi-app-sirs` sur `gemapi-app-sirs` à `90bf36947be1`. Le chantier s’arrête avant INIT-07. Aucune publication ni mise en service.
