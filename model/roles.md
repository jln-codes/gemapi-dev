# Rôles PostgreSQL du modèle patrimonial GEMAPI-App-SIRS

Matrice cible : chaque utilisateur PostgreSQL possède un compte individuel `LOGIN`. Les rôles collectifs `NOLOGIN` sont hiérarchiques : Consultation → Gestion des prestations → Gestion du patrimoine ; chaque rôle hérite des droits du précédent.

Les trois profils couvrent le schéma applicatif unique `gemapi`. Leurs noms de rôles PostgreSQL conservent le préfixe `milieux_`.
Une prestation peut viser dix catégories de patrimoine. Le profil Prestations
peut choisir une cible existante. Les triggers prennent les verrous nécessaires
à la validation de la section AH et au calcul des mesures empruntées. Les
interventions sur une section AH conservent leur géométrie propre obligatoire.

| Tables du [schéma courant](schema_v0.6.1.sql) | Consultation | Gestion des prestations | Gestion du patrimoine |
| --- | --- | --- | --- |
| `prestations` | S | SIUD | SIUD |
| `ah`, `ah_stockage`, `ah_ouvrage`, `ah_sections`, `sites_terrestres`, `cours_eau`, `digues`, `digues_reperes` | S | S | SIUD |
| `techniciens`, `prestataires`, `communes`, `prestations_categorie`, `types_prestations`, `statuts_prestations` | S | S | S |
| 10 tables métier et 4 associations patrimoniales | S | S | SIUD |
| 19 catalogues patrimoniaux | S | S | S |
| Vues `prestations_carte`, `prestations_fiches`, `digues_carte`, `ah_sections_carte`, `prestations__ah`, `prestations__sites_terrestres` | S | S | S |
| `TRUNCATE` (toutes les tables) | — | — | — |

S = `SELECT`, I = `INSERT`, U = `UPDATE`, D = `DELETE` ; — = aucun droit. Le service SIG administre l'installation PostgreSQL, les référentiels réservés et la structure des tables. PostgreSQL garantit ces droits, indépendamment du plugin QGIS. `DELETE` peut supprimer plusieurs lignes.

Traçabilité : `modifie_par` enregistre le compte PostgreSQL authentifié (`session_user`). Le technicien responsable (`prestations.technicien_id`) est un attribut métier, qu'il dispose ou non d'un compte PostgreSQL ; il ne confère aucun droit.

L’opérateur utilise la commande `permissions` documentée dans [l’installation](README.md). La matrice ci-dessus provient du donneur. Les noms de groupes se choisissent avec le DBA, au niveau du cluster. Un même groupe utilisé dans deux schémas cumule leurs droits. Le propriétaire des objets applique uniquement les droits du schéma choisi. Les appartenances et l’USAGE sur le schéma de PostGIS sont préparés séparément par le DBA.

Les utilisateurs se connectent à PostgreSQL avec le même compte individuel depuis QGIS et, à terme, depuis la webapp. Les droits effectifs de ce compte font autorité. Les comptes techniques de diagnostic et de maintenance sont distincts. Le mécanisme de session web et la conservation de l’attribution SQL sont étudiés dans la [proposition INIT-06B](../docs/GEMAPI-AUTH-PROPOSAL.md).
