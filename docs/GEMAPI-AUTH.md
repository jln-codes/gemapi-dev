# Authentification web PostgreSQL

Une instance dessert une base et un schéma. Chaque session web possède une connexion physique exclusive au nom du login PostgreSQL individuel utilisé dans QGIS. PostgreSQL vérifie le mot de passe et applique ses propres permissions. Les écritures futures conserveront l’auteur SQL natif `session_user`, y compris dans un déclencheur à privilèges élevés. Le registre est en mémoire dans un processus FastAPI. Le modèle v0.6.1 et les trois profils restent ceux du [modèle commun](../model/roles.md).

## Configurer une instance

Définir l’origine publique exacte, sans chemin ni barre oblique finale. Terminer HTTPS sur un proxy de confiance. L’exception HTTP concerne uniquement le développement sur une origine de boucle locale. Le Compose fourni n’expose que le proxy sur cette boucle. L’image lance Uvicorn avec un worker et le cycle de vie ASGI obligatoire. Conserver une seule réplique. Un redémarrage met fin à toutes ses sessions.

| Paramètre | Défaut | Usage |
|---|---|---|
| `AUTH_ORIGIN` | `http://127.0.0.1:8080` | Origine contrôlée pour les POST. Compose adapte le port à `GEMAPI_PORT`. |
| `DATABASE_URL`, `DATABASE_SCHEMA` | destination à fournir, schéma `gemapi` | Hôte ou répertoire du socket, port (5432 par défaut), base explicite, options TLS et schéma privé de l’instance. |
| `AUTH_READER_GROUP` | `milieux_consultation` | Groupe NOLOGIN de consultation du modèle effectivement installé. |
| `AUTH_MAX_SESSIONS`, `AUTH_PER_ACCOUNT` | 20, 3 | Plafonds du registre de cette instance. Une place représente une connexion physique. |
| `AUTH_CONNECTION_BUDGET`, `AUTH_TECHNICAL_RESERVE` | 21, 1 pour les valeurs initiales | Capacité applicative disponible et places réservées aux usages techniques. Le minimum entre le plafond des sessions et le budget diminué de la réserve borne les connexions en ouverture et établies. |
| `AUTH_MAX_HANDSHAKES` | 3 | Nombre maximal d’ouvertures SQL simultanées, incluses dans le budget. |
| `AUTH_IDLE_SECONDS`, `AUTH_ABSOLUTE_SECONDS` | 7200, 43200 | Inactivité et durée absolue, en secondes entières positives. |
| `AUTH_ATTEMPTS_PER_MINUTE`, `AUTH_GLOBAL_ATTEMPTS_PER_MINUTE` | 10, 60 | Tentatives sur une fenêtre de 60 secondes pour la source ou le login, puis pour toute l’instance. |
| `AUTH_DEPLOYMENT`, `AUTH_DEMO_PREFILL` | `production`, `0` | Seule la combinaison `demo` et `1` autorise la distribution du compte public dédié. |
| `AUTH_DEMO_LOGIN`, `AUTH_DEMO_PASSWORD`, `AUTH_DEMO_PER_ACCOUNT` | vides, plafond initial 3 | Identifiants publics fournis à l’exécution et plafond propre à ce compte sur l’instance de démonstration. |

Exemple de destination TCP privée sans identifiants techniques : `host=postgres.exemple.invalid port=5432 dbname=territoire sslmode=verify-full sslrootcert=/run/certs/ca.pem`. Monter le certificat de confiance en lecture seule, lisible par l’UID 10001 de l’image. Sur socket Unix, monter le répertoire privé et configurer par exemple `host=/run/postgresql port=5432 dbname=territoire`. Le navigateur fournit uniquement le login et le mot de passe. Les DSN techniques facultatifs de diagnostic et de lecture restreinte restent exclusivement côté serveur.

Configurer PostgreSQL pour SCRAM-SHA-256 avec vérificateurs SCRAM et HBA adapté au transport. La libpq doit prendre en charge `require_auth` (16 ou ultérieure). En TCP le certificat doit couvrir le nom configuré et remonter à une autorité approuvée. Le client impose `verify-full`, la vérification SCRAM et une seule destination. Utiliser les paramètres explicites. Les redirections par service, options libres ou adresse distincte du nom TLS sont refusées. Un socket Unix demande la protection du répertoire et un HBA SCRAM. Les installations managées doivent offrir les mêmes garanties. Qualifier séparément tout intermédiaire qui termine l’authentification ou réaffecte les connexions. Les modes de pooling par transaction ou instruction et les comptes serveur forcés ne conviennent pas au contrat.

La DSI provisionne les logins et les appartenances. Le contrôle d’admission exige les identités `session_user` et `current_user` attendues, LOGIN et une échéance valide, CONNECT, USAGE sur les deux schémas, les droits de consultation du modèle et l’héritage effectif du groupe de lecture. Il écarte les comptes techniques configurés, les rôles administratifs, les appartenances à des rôles prédéfinis, les options d’administration des appartenances et les propriétaires des objets contrôlés. La connexion initiale vérifie l’empreinte de structure v0.6.1. L’installation, les extensions et les modifications de droits restent des opérations de maintenance explicites.

## Cycle de session et contrat HTTP

1. `POST /api/auth/login` reçoit un JSON limité à 8 Kio. Après vérification SQL, le serveur ferme l’éventuelle session présentée et émet un jeton aléatoire de 256 bits. Il stocke son empreinte SHA-256. Sous HTTPS le cookie `__Host-gemapi_session` a les attributs HttpOnly, Secure, SameSite=Strict, Path=/ et une durée limitée par l’échéance absolue. La boucle locale HTTP utilise un nom de cookie distinct.
2. Le navigateur reçoit le login et un identifiant de contexte neuf. Cet identifiant sert à détecter le changement de session. Le jeton porteur reste dans le cookie. `GET /api/auth/me` revalide la connexion et l’admission. Les réponses du service d’authentification portent `Cache-Control: no-store`.
3. Chaque POST exige une origine égale à `AUTH_ORIGIN` et l’en-tête non simple `X-Gemapi-CSRF: 1`. L’activité et la déconnexion exigent aussi le contexte courant. `POST /api/auth/activity` prolonge le délai d’inactivité après validation. Le shell l’envoie sur une interaction de l’utilisateur au plus une fois par 30 secondes avec les valeurs initiales. Pour une échéance courte, l’intervalle est ramené au tiers du délai d’inactivité. Le polling d’identité ne prolonge aucune échéance.
4. Pour une future opération protégée, utiliser `Sessions.transaction` avec le jeton du cookie et le contexte attendu, un contrôle applicatif spécifique et les droits SQL requis. Le verrou de session limite l’attente à 2 secondes. La transaction revalide l’admission, applique un chemin de recherche local protégé et borne les attentes SQL (instruction 15 secondes, verrou 5 secondes). Commit ou rollback précède la libération du verrou. Un état final incertain entraîne fermeture et invalidation.
5. `POST /api/auth/logout` invalide le jeton, ferme la connexion et expire le cookie. Le nettoyage autonome s’exécute au plus toutes les 5 secondes. Les connexions sont fermées à l’arrêt. En cas de partition, le serveur SQL dispose aussi d’un `idle_session_timeout` égal au minimum des échéances configurées, dans la plage du paramètre PostgreSQL. Une transaction inactive est bornée à 15 secondes. La libération observée à distance dépend de la détection réseau et de l’échéance serveur.
6. Une session expirée, une connexion perdue ou un redémarrage exigent un nouveau login. Une saturation refuse la tentative et préserve les autres sessions. HTTP 401 signale des identifiants incorrects ou une session absente, 403 une admission ou une origine refusée, 409 un contexte périmé, 429 la limitation des tentatives et 503 la saturation ou une indisponibilité SQL. Le message distingue ces deux derniers cas sans détail du DSN.

Conserver un budget pour les utilisateurs QGIS, la maintenance DBA, les autres applications et la marge d’exploitation du cluster. La réserve applicative couvre les connexions techniques autorisées à côté du registre. Fixer le budget de production avec la DSI. Configurer les adresses de proxy autorisées par Uvicorn (FORWARDED_ALLOW_IPS) au niveau du déploiement. Le limiteur utilise l’adresse fournie par le serveur HTTP. Si le proxy ne transmet pas une adresse cliente issue d’une chaîne de confiance, plusieurs visiteurs partagent le même quota réseau.

## Permissions, attribution et révocation

Les permissions SQL s’appliquent à chaque requête sous le login individuel. Définir, opération par opération, les objets, colonnes, vues, fonctions et ressources nécessaires ainsi que les contrôles applicatifs. Vérifier les capacités utiles dans la même transaction que l’action. Masquer une commande guide l’utilisateur ; PostgreSQL et le serveur continuent de contrôler l’exécution. Les règles d’intégrité, d’auteur et de version relèvent du modèle commun. Les 144 couples méthode/chemin hérités renvoient 403 même pour un utilisateur authentifié.

Un changement de mot de passe affecte les prochains handshakes. Une connexion établie peut continuer jusqu’à déconnexion, expiration ou interruption. NOLOGIN et VALID UNTIL ne terminent pas les backends existants. La webapp relit ces états, les appartenances et les concessions avant l’opération suivante. La perte de l’admission ferme la session. Une opération déjà validée en SQL reste acquise.

Pour une révocation urgente, le DBA habilité retire d’abord le droit d’ouvrir de nouvelles connexions ou l’accès en cause. Depuis la base concernée, il identifie les connexions par leur base, leur login et leur PID. Exemple dans psql, après avoir renseigné la variable `login_cible` avec le compte à révoquer :

```sql
SELECT pid, usename, datname, application_name, state
FROM pg_catalog.pg_stat_activity
WHERE datname = current_database() AND usename = :'login_cible';

SELECT pg_catalog.pg_terminate_backend(pid)
FROM pg_catalog.pg_stat_activity
WHERE datname = current_database() AND usename = :'login_cible'
  AND application_name = 'gemapi-web' AND pid <> pg_catalog.pg_backend_pid();
```

Vérifier la disparition des PID concernés. Pour une révocation de toutes les interfaces, le DBA examine également les connexions QGIS et les autres bases concernées. Le champ application_name sert au diagnostic ; le DBA vérifie la portée du filtre. Le backend web ne reçoit aucun droit de terminaison.

## Secrets, démonstration et PWA

Le mot de passe individuel vit dans le formulaire et dans le traitement de l’ouverture SQL. Le code vide le champ à l’envoi, ne copie pas le mot de passe dans le registre et ne reconnecte jamais automatiquement une session. Python ne garantit pas l’effacement des chaînes. Libpq peut garder les paramètres en mémoire jusqu’à la fermeture physique. Restreindre l’accès à la mémoire, aux dumps et aux diagnostics. Exclure les corps et secrets des journaux du proxy, de l’APM et des captures navigateur. Les réponses d’authentification et les identifiants sont exclus du cache PWA et des stockages applicatifs de l’appareil.

L’instance de démo vérifie son compte public au démarrage quand le préremplissage est activé. Elle contrôle l’admission et la séparation avec les identifiants des DSN techniques. Si la vérification échoue, les champs restent vides. Après correction de la configuration ou de l’accès SQL, redémarrer l’instance de démonstration. La DSI isole ce compte et son réseau de toute production et vérifie les droits transitifs, PUBLIC et les accès aux autres schémas et bases. Chaque visiteur connaît intentionnellement ces identifiants. Les mutations futures sur ce compte auront toutes le même auteur SQL. L’ouverture publique modifiable attend la remise à zéro coordonnée, les quotas médias, la prévention des abus et la politique de conservation.

Les onglets se signalent le changement de compte et relisent l’identité. Le shell invalide le contexte précédent, ignore les réponses obsolètes, conserve les données locales et exclut les appels d’authentification du Service Worker. Une déconnexion volontaire demande le réseau. L’accueil hors ligne peut servir le shell cartographique public déjà préparé. Les données métier, photographies et intentions ne sont pas synchronisées. INIT-09 doit qualifier le propriétaire durable des brouillons, le déverrouillage local, la réauthentification, les droits et les conflits avant tout replay.

La [recette INIT-06B](../audits/GEMAPI-INIT-06B-IMPLEMENTATION.md) couvre le socket Unix et TCP/TLS local entre conteneurs. La recette sur machine physiquement distante est inscrite à DEPLOY-01. L’[étude initiale](GEMAPI-AUTH-PROPOSAL.md) conserve la comparaison des stratégies.
