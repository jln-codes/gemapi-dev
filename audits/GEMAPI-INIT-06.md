# INIT-06 — Démonstration v0.6.1

La source est l’arbre Git de gemapi-app-sirs à `90bf36947be15f834905f20f73148adc9c45d197`. Les huit GeoPackages, les cinq JPEG, le CSV des chemins photographiques fictifs, l’illustration, le QGZ témoin des widgets photo et les douze fichiers SQL de `demo/` sont conservés à leurs chemins d’origine. Les cinq générateurs et l’audit spatial viennent de `demo/data/`. Les exports des cinq générateurs ont été reproduits octet pour octet. Le témoin 05_types_prestations.sql accompagne les sources. L’importeur charge les nomenclatures par le DDL et utilise onze exports de données.

La commande `demo/data/importer_base_neuve.sh` appelle le chargeur Python local. Une transaction installe le DDL et charge la démo, sous verrou de reconstruction, avec validation des contraintes entre fichiers. La destination est une base explicitement nommée sur socket Unix local. Les bases système, une connexion TCP ou une base comportant des relations applicatives sont refusées. Le rôle et le port sont ceux de la configuration libpq privée. Le schéma est un argument indépendant. Les exports conservent leur représentation initiale et les attributs SQL restent littéraux. Les données de suivi sont fictives.

| Témoin | Résultat |
|---|---:|
| Prestations distinctes et cartographiées | 100 |
| Avec géométrie propre / avec géométrie empruntée à la végétation | 88 / 12 |
| Sites / végétations / mobiliers / cheminements directement rattachés | 12 / 19 / 4 / 2 |
| Stockages / ouvrages AH | 10 / 13 |
| Tronçons / désordres / observations / références photographiques patrimoniales | 7 / 2 / 4 / 3 |

Le test adapté de `plugin/tests/test_demo_import.py` vérifie les UUID, quantités, unités et géométries des prestations contre les GeoPackages, les références de photos, les parents, les types, les mesures persistées et les valeurs des référentiels. Les deux installations avec PostGIS dans public et extensions ont passé les contrôles. La topologie est vérifiée dans les sources avec GDAL/OGR. Les vues localisent les 12 prestations par repli. Leur quantité déclarée est conservée indépendamment de la surface ou de la longueur du parent.

Un second essai, `tests/model/test_demo_local.py`, exécute le DDL et les exports **donneurs** sur une base témoin temporaire, puis lance la **commande publique** dans deux autres bases neuves, sous `gemapi` et `Territoire d'essai`. Les 48 empreintes SHA-256 des lignes, les effectifs des six vues, les colonnes et les clés sont identiques. L’empreinte des 100 prestations est `c21b4f4701fa4786ad153c2e15824e71fa77e2ed745bba5e488ce56853652656`. Les quatre champs d’audit alimentés par l’heure ou le compte de l’installation sont exclus de cette comparaison. Les dates métier, versions, identifiants, champs photographiques et géométries sont inclus. Une seconde tentative d’import est refusée et les empreintes restent identiques. Les tests de profils contrôlent séparément l’attribution et la traçabilité SQL.

Deux des trois références photographiques patrimoniales correspondent à des JPEG fournis. Le fichier manquant de la troisième est un cas de test livré. Les 25 prestations portant des références réseau fictives conservent leurs chemins. Aucun partage réseau n’est nécessaire pour reconstruire la base et aucun original distant n’a été recherché. Les vues patrimoniales, contraintes et sources locales sont qualifiées. Leur exploitation par les formulaires web est différée.

**Rejouer** depuis la racine avec le Python système équipé de psycopg2, GDAL et PostgreSQL/PostGIS. La suite d’import du donneur utilise en plus Docker et l’image postgis/postgis:16-3.4.

```sh
/usr/bin/python3 -B tests/model/test_demo_topologie.py
/usr/bin/python3 -B tests/model/test_demo_local.py
/usr/bin/python3 -B tests/model/test_demo_import.py
```

[Procédure opérateur](../demo/README.md)
