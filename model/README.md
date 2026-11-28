# Installer le modèle patrimonial v0.6.1

PostgreSQL 16+, PostGIS 3.3+, Python 3 et le client `psql`. Configurer séparément `PGHOST`, `PGPORT`, `PGUSER`, TLS et, si nécessaire, un fichier de mots de passe privé. Le DBA installe PostGIS une fois, dans le schéma de son choix. Il réserve les droits CREATE des schémas métier et PostGIS à leurs propriétaires de confiance. Sur une ancienne installation où PUBLIC a CREATE sur public, le DBA examine les impacts avant de changer les ACL.

Le compte d’installation est propriétaire du schéma vide confié par le DBA. Une destination absente demande CREATE sur la base. L’installation refuse les objets déjà présents, conserve les objets tiers et annule entièrement les changements en cas d’erreur.

```sh
python3 model/install.py install --database territoire --schema "Patrimoine SIG"
python3 model/install.py permissions --database territoire --schema "Patrimoine SIG" \
    --groups sig_lecture sig_prestations sig_patrimoine
```

La seconde commande est indépendante. Le DBA fournit trois groupes NOLOGIN distincts, accorde Consultation à Prestations puis Prestations à Patrimoine, et accorde au lecteur USAGE sur le schéma effectif de PostGIS. Il examine leurs appartenances et privilèges existants à l’échelle du cluster. Employer des groupes propres à chaque contexte si les accès doivent rester distincts. Le repère donneur `roles_milieux.sql` contient ses anciennes opérations de cluster. Utiliser la commande ci-dessus pour appliquer seulement les droits sur les objets métier. Voir la [matrice](role.md).

Le serveur web utilise `DATABASE_URL` et `DATABASE_SCHEMA` (défaut `gemapi`). Son compte de consultation a CONNECT, USAGE sur les deux schémas et les droits du lecteur. Laisser la propriété, CREATE, CREATEROLE et les tâches de maintenance à d’autres comptes. Le diagnostic explicite `python -m digues_webapp.database` depuis la wheel vérifie la connexion, la structure et les droits en lecture. Le démarrage du shell public est indépendant de la base.

Les trois fichiers `schema_v0.6.1.*` sont les références initiales inchangées. Le SQL exécutable par un opérateur passe par `install.py`. La commande adapte le préambule et le chemin de recherche, puis les identifiants des instructions, des fonctions et des deux formats SQL dynamiques. Une empreinte contrôle la révision source. Les valeurs des référentiels restent littérales. Les fonctions à privilèges élevés enregistrent un chemin explicite avec pg_catalog en tête et pg_temp en dernier. Toute nouvelle version requiert une revue de la transformation.
