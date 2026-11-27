# GEMAPI-INIT-02 — Frontière monobase et retrait du contrôle

La configuration privée du processus fournit le DSN d’instance. Les deux
connexions optionnelles de lecture SQL et IA restent distinctes du rôle
applicatif. Les gardes PostgreSQL et les restrictions de lecture issues du
donneur restent dans les composants différés. La vivacité du socle est
indépendante de PostgreSQL et aucun DDL ne se trouve sur le chemin HTTP actif.

`protected_routes.py` inventorie les méthodes, chemins courts et permissions
retrouvées dans les adaptateurs et handlers de la source. Chaque entrée protégée
renvoie 403 sans ouvrir de connexion, y compris uploads, médias, exports,
journal, replay, requêtes SQL et IA. Les anciens préfixes, le login Digues, la
découverte, les sélecteurs, le contrôle, les profils dynamiques et l’administration
de parc sont retirés du runtime. La vérification de l’identité, des permissions,
de la révocation, du CSRF et de l’auteur reste en attente d’arbitrage. Le navigateur
ne crée aucune session ni stockage métier dans le shell. Le transport conservé
utilise une clé instance, utilisateur et génération de données. Les mécanismes
d’annulation des flux et d’invalidation locale restent disponibles pour le futur
raccordement.

Les handlers, DTO, SQL et contrôles du donneur nécessaires à une adaptation
restent identifiables dans `deferred_handlers.py` et les modules adjacents.
Les templates des écrans Carte/Base et les scripts métier restent présents.
Seuls les composants publics du shell sont chargés. Les références au modèle
Digues dans les couches, le CRS, les options, les médias, les exports et les
permissions de lecture SQL/IA demandent une adaptation après livraison.
Les commandes de maintenance métier ne sont pas des points d’entrée de l’image.

Le Dockerfile construit les roues, sans migrateur ni modèle de géométries
obligatoire. Compose assemble FastAPI, Caddy et le renderer, avec volumes
médias et cache séparés. Le proxy local publie sur la boucle locale et refuse
les chemins internes. Le rôle de fonctionnement du conteneur est l’UID 10001.
Les essais de construction et de démarrage intégrés sont documentés en 04.
