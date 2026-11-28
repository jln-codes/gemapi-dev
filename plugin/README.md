# GEMAPI-App 0.8.6 pour QGIS

Cette extension utilise le modèle v0.6.1 et le projet fictif de référence. Le menu, les formulaires, les trois profils PostgreSQL, les parcours d'édition, les rapports et l’assistant sont décrits dans [l’aide distribuée dans le ZIP](gemapi_app/help.md). L’IA et la voix utilisent leurs paramètres privés de poste.

## Installation et dépendances

Depuis la racine du dépôt, exécuter `python3 plugin/tools/build_plugin.py`, puis installer
`plugin/dist/gemapi-app-0.8.6.zip` dans **Extensions > Installer/Gérer les
extensions > Installer depuis un ZIP**. Le plugin n'ajoute aucune dépendance
Python externe : il utilise PyQGIS, Qt via qgis.PyQt, les liaisons GDAL de QGIS et la bibliothèque standard. Le script
contrôle les fonctions requises et vérifie que chaque fichier du ZIP correspond
aux sources, sans transformation de namespace.

Après installation, redémarrer QGIS et consulter **Vue > Panneaux > Messages du
journal**, rubrique **GEMAPI-App** : le plugin affiche sa version et le chemin
du fichier `plugin.py` réellement chargé. Sous Windows, vérifier le profil actif
dans `%APPDATA%\QGIS\QGIS3\profiles\` ; un ZIP présent dans `plugin/dist/` ne
met pas à jour automatiquement l'extension déjà installée.

Le moteur PDF utilise la plateforme Qt native sous Windows et `offscreen` sous
Linux. Il choisit une police réellement visible par son processus et écrit dans
le journal QGIS la liste des polices, la substitution effective et les caractères
non couverts si l'export échoue.

Dans le formulaire, les cours d'eau de même nom sont proposés ensemble lorsque
leurs tronçons se suivent par une extrémité sans jonction ambiguë. Les homonymes
séparés restent distincts. Un groupe sélectionne ses
UUID individuels ; un groupe partiellement associé à une prestation est signalé
« partiel » et reste partiel tant que l'utilisateur ne le sélectionne pas
explicitement. Les filtres et les rapports continuent à lire les associations
par UUID.

## Projet de démonstration

Depuis le clone, reconstruire la base locale et définir le service libpq `gemapi_demo` selon [la procédure démo](../demo/README.md). Pour ce lot QGIS, le schéma doit porter le nom `gemapi`. Utiliser un compte direct approprié et un profil attribué par le DBA. Les secrets du backend web restent dans l’instance web.

Ouvrir `demo/prestations_demo.qgz` ou générer le projet dans le dossier de la démonstration pour conserver la résolution des chemins photographiques relatifs.

```sh
/usr/bin/python3 plugin/tools/projet_qgis.py --generer demo/mon_projet.qgz
```

La génération utilise le modèle QGZ versionné et le mécanisme XML/ZIP du donneur. Les styles embarqués, les widgets et les relations sont conservés. Une destination de génération doit être neuve. Sur deux copies de travail fictives compatibles, la commande suivante compare les réglages. Ajouter `--appliquer` pour synchroniser avec sauvegarde préalable dans le répertoire fourni.

```sh
/usr/bin/python3 plugin/tools/projet_qgis.py --simuler --source demo/prestations_demo.qgz demo/mon_projet.qgz
```

## Sections hydrographiques AH

Les actions de couche donnent accès aux sections et aux repères de digue. Les formulaires contrôlent les droits SQL et les révisions. Les enfants de sites sont directement rattachés par les widgets relationnels. Les conditions de qualification du support et de calcul de la géométrie figurent dans [la documentation démo](../demo/README.md). Les références cartographiques, styles, relations et chemins de connexion à traiter pour QGIS-02 sont inventoriés dans [l’audit QGIS-01](../audits/GEMAPI-QGIS-01.md).

Les rapports utilisent le logo fictif « Bassin pilote – Service des eaux », livré dans `gemapi_app/assets/logo_institutionnel.webp`.

## Licence du plugin

Le ZIP contient la notice GPL-2.0-or-later et le texte intégral de la GPL version 2. Les bibliothèques de l’installation QGIS et le socle web ont leurs propres licences. [Notice du plugin](gemapi_app/LICENSE) · [Texte de la GPL](gemapi_app/COPYING) · [Périmètres et dépendances](../audits/GEMAPI-PUBLICATION-01.md)

### Relations et widgets

Pour contrôler une relation, rapprocher les identifiants des couches et les couples de champs du QGZ de `conrelid`, `confrelid`, `conkey` et `confkey` dans `pg_constraint`. Une même FK peut relier plusieurs représentations cartographiques. Les vues de fiches et de consolidation disposent de relations QGIS de navigation.

Les identifiants internes sont aussi utilisés par les éléments `attributeEditorRelation`, les options `RelationReference` et les widgets. Les étiquettes visibles peuvent évoluer indépendamment. Les enfants de sites du projet de référence sont mobilier, cheminements et végétation. Les styles et les formulaires sont embarqués dans le projet. Cette méthode de vérification reprend la documentation opérationnelle `docs/qgis_relations.md` du donneur.
