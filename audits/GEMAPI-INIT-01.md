# GEMAPI-INIT-01 — Reprise du socle

Source validée et lue dans l’arbre Git de `jln-codes/digues-app` à `eecec0a44f25a908484b76f838a7e2be0c8cfee5`. Le commit de ce jalon recense les chemins repris sous leur nom d’origine. Les lots couvrent la roue FastAPI et ses actifs, Leaflet et ses bibliothèques embarquées, le renderer IGN/LiDAR avec son verrou npm, les traitements Python, le packaging et les témoins Python/Node/Playwright. Les différences dans les fichiers mixtes retirent les points d’entrée de l’import, les états de transfert de photos historiques, les marqueurs d’import et les actifs correspondants. Le registre historique et les scripts d’installation Digues ne font pas partie de la reprise.

`LICENSE` Apache 2.0 est déjà présent. `NOTICE` reprend la mention Digues App et le copyright 2026 Julien Lorion. Les licences, en-têtes et métadonnées des bibliothèques embarquées restent avec les fichiers concernés. Leaflet.Editable 1.2.0 est attribué à Yohan Boniface sous WTFPL selon sa métadonnée embarquée. Le registre des fonds conserve les crédits IGN. Le rapport de chaque jalon décrit les modifications des fichiers dérivés.

La sélection porte sur les fichiers suivis de la révision validée. Les chemins `private/`, la démo Digues, les photos et contours, le modèle compilé, les DDL, les sources CouchDB, le migrateur et ses scripts dédiés sont exclus. Le GeoTIFF miniature du renderer sert exclusivement à son test de décodage. Les deux JPEG locaux sont exclus du suivi. `branding/` reste intact.

Le test Node d’ordre des plans Leaflet passe. La construction du renderer et les tests intégrés sont poursuivis en 04. Les tests historiques dépendant de l’identité Digues ou de son modèle sont des témoins à adapter. À ce stade, les routes héritées ne sont pas lancées.
