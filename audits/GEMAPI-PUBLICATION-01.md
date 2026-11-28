# GEMAPI-PUBLICATION-01 — Préparation de la publication

Le lot est prêt pour validation humaine sur `development`. Il conserve la version fonctionnelle 0.8.6 du plugin. Le ZIP de publication est identifié par son empreinte ci-dessous. La préparation a été réalisée localement, sans push, déploiement ni modification des dépôts donneurs.

## Licences et périmètres

| Périmètre | Conditions de redistribution |
|---|---|
| Socle web dérivé de Digues App | Apache-2.0. Le texte intégral à la racine conserve le blob Git `261eeb9`. NOTICE conserve le copyright de Julien Lorion et la révision donneuse. Les bibliothèques web et ressources tierces gardent leurs mentions. |
| Extension installable GEMAPI-App | L’auteur autorise la redistribution de ses apports originaux. Le crédit SIG Milieux est conservé. Le ZIP contient la déclaration GPL-2.0-or-later dans LICENSE, le texte complet anglais de la GPL v2 dans COPYING et l’identifiant dans metadata.txt. La déclaration couvre le code original, les formulaires, l’aide et les visuels originaux du plugin. |
| Dépendances du plugin | Les imports des modules distribués utilisent la bibliothèque standard Python, PyQGIS, Qt via qgis.PyQt (dont QtMultimedia pour la voix) et osgeo.gdal pour les métadonnées PDF. Le manifeste distribue les sources Python, les formulaires .ui, les visuels, l’aide, les métadonnées et les licences. QGIS, Qt, PyQt, GDAL, pilotes, SDK et services distants sont extérieurs au ZIP. |
| Autres éléments du produit | L’attribution du modèle, du jeu fictif et du projet QGIS au donneur est conservée. La déclaration de licence du plugin vise son paquet. Le dépôt donneur à `90bf369` ne fournit pas de licence générale pour les autres apports SIG. Ce lot ne leur attribue pas un nouveau régime de réutilisation. |

Le régime choisi répond à l’exigence de compatibilité GPL du dépôt d’extensions QGIS. Riverbank distribue PyQt libre sous GPLv3. L’option « ou ultérieure » permet de distribuer une combinaison avec cette version sous GPLv3. Les conditions de Qt dépendent de la distribution installée. GDAL documente principalement MIT et quelques autres licences permissives pour ses propres sources. [QGIS](https://plugins.qgis.org/docs/publish) · [Riverbank](https://www.riverbankcomputing.com/software/pyqt/intro/) · [GDAL](https://gdal.org/en/stable/license.html)

Le paquet QGIS ne contient aucun module du socle web. Aucune incompatibilité identifiée dans ce ZIP. Une combinaison de code Apache-2.0 avec du code imposant GPLv2 seule présenterait une incompatibilité. L’ASF confirme la compatibilité d’Apache-2.0 avec GPLv3. [Apache Software Foundation](https://www.apache.org/foundation/license-faq)

## Inventaire et remplacement du logo

L’inventaire des transferts retrouve une seule ressource source, `plugin/gemapi_app/assets/logo_cabbalr.webp`. Le constructeur la copiait dans le ZIP et le moteur PDF la chargeait dans trois en-têtes. Deux sont dans `report_pdf.py` (fiche détaillée et registre paysage). Le registre cartographique portrait de `report_synthetic_portrait.py` importe la même constante. Les rapports produits incorporent le visuel à l’export.

Le remplacement « Bassin pilote – Service des eaux » est une création fictive pour cette publication. Il est livré dans `plugin/gemapi_app/assets/logo_institutionnel.webp`. Les deux fichiers ont un canevas WebP de **1 280 × 307 px** avec transparence. Le nouveau logo a pour SHA-256 `d7785e9254bc1fea70b7b1c522bd0f021f3fdc3c8324293da932851c36e3e24b`. Le moteur charge ce fichier dans ses cadres existants. L’icône QGIS, le branding web et les crédits des cartes gardent leurs ressources propres. La personnalisation par installation, avec fichier externe à Git, figure dans la feuille de route.

Le contrôle a porté sur les objets Git accessibles depuis les références, les fichiers ZIP et QGZ décompressés, les visuels transférés et le manifeste livré. Le PDF de référence n’embarque aucune image matricielle selon `pdfimages -list`. Ni le blob de l’original, ni ses octets, ni sa représentation Base64 recherchée n’ont été retrouvés dans l’historique corrigé ou le ZIP reconstruit. Les mentions textuelles du nom de la collectivité dans l’inventaire et les comptes rendus documentent la provenance.

## Historique local et restauration

Avant intervention, `git ls-remote origin` donnait uniquement `main` à `b9fe3b1` et `development` à `e0240ff`, avec HEAD sur main. Les sept commits suivants étaient locaux. Le premier qui introduisait le logo était `f415299`. Les quatre premiers commits locaux, jusqu’à `350262d` inclus, gardent leurs identifiants. La réécriture a conservé les parents dans le même ordre, les auteurs, les dates et les messages. Pour chacun des trois commits concernés, la comparaison des arbres retrouve uniquement le remplacement du fichier image et les deux chemins adaptés dans le constructeur et le moteur de rapport.

| Jalon | Avant | Après |
|---|---|---|
| QGIS-01 | f415299 | 8fd32d1 |
| Contrôle INIT-05 du chemin de recherche | f8ef802 | c738d36 |
| Clôture INIT-05/06 et QGIS-01 | a2f8ee6 | 56c1f49 |

La sauvegarde privée `.git/GEMAPI-PUBLICATION-01-before.bundle` a été créée avant la correction et contrôlée avec `git bundle verify`. Elle nécessite l’ancêtre publié `e0240ff`. Son SHA-256 est `abb23dc83f8a5ca2c3c97bf9d8420c4166b91d5b5a8b021d9cf101d412152b84`. Elle contient volontairement l’ancien logo. Pour examiner l’état antérieur dans un **clone privé distinct** contenant cet ancêtre, utiliser la commande suivante avec le chemin absolu du bundle local.

```sh
git fetch /chemin/vers/GEMAPI-PUBLICATION-01-before.bundle refs/heads/development:refs/heads/restauration-privee
```

Le bundle, les reflogs, les sorties de recette et les anciennes constructions locales sont hors du contenu destiné à la publication. La branche corrigée descend toujours directement de l’histoire publiée. Les deux donneurs restent propres à `ebd3568` pour digues-app et `90bf369` pour gemapi-app-sirs. Le fichier local non suivi `GEMAPI-ROADMAP.md` a été préservé.

## Qualification

| Contrôle rejoué | Résultat |
|---|---|
| Construction et installation du ZIP | Deux constructions du test et une autre depuis un export des seuls fichiers indexés produisent les mêmes octets. **40 membres**, inventaire et contenu contrôlés, extraction dans un profil QGIS temporaire, chargement du formulaire, menu, assistant, aide et déchargement réussis. |
| Comparaison visuelle avant/après | Trois exports de la même prestation témoin avec PyQGIS, dont deux avec un fond local de contrôle. Le texte et les coordonnées de chaque mot extraits par Poppler sont identiques. Après rendu à 144 ppp, tous les pixels modifiés sont dans le cadre du logo. Inspection visuelle des trois pages effectuée. |
| Reconstruction modèle et démonstration | Trois bases jetables, dont une référence SQL directe. Les deux installations reconstruites concordent sur 48 empreintes de tables, six vues, colonnes et contraintes. PostGIS testé dans public et extensions. Le second lancement sur une base remplie est refusé. |
| Topologie et parcours QGIS | Contrôles du jeu fictif réussis. Le test PyQGIS sur PostGIS Docker jetable valide les couches et relations du projet, les droits Prestations, les 100 fiches, l’édition, cinq créations et les deux exports PDF. La génération et la synchronisation du projet QGZ passent également. |
| Intégrité des travaux | Les arbres du modèle, de la démonstration, de la webapp, du générateur QGZ et du packaging Docker ont été comparés à la clôture initiale. Les substitutions historiques sont limitées aux chemins et à l’image indiqués ci-dessus. La construction des images web et la recette Windows n’ont pas été rejouées dans ce lot. |

| Gabarit témoin | Pages | Mots comparés | Rectangle des différences, en pixels à 144 ppp (gauche, haut, droite, bas) |
|---|---:|---:|---|
| Fiche détaillée A4 portrait | 1 | 59 | 786, 56, 1134, 140 |
| Registre synthétique cartographique A4 portrait | 1 | 102 | 878, 28, 1134, 90 |
| Registre A4 paysage | 1 | 93 | 1263, 22, 1626, 110 |

**ZIP qualifié** — `plugin/dist/gemapi-app-0.8.6.zip`, SHA-256 `a6ee378191774f5def2c75deb018282ac94389c5f21e42b3dfc08a26bdea4079`. Le fichier est un produit de construction local. Pour le reproduire et rejouer les contrôles du paquet, depuis la racine du clone avec PyQGIS installé :

```sh
python3 plugin/tools/build_plugin.py
sha256sum plugin/dist/gemapi-app-0.8.6.zip
QT_QPA_PLATFORM=offscreen /usr/bin/python3 plugin/tests/test_package.py
```

Le point d’arrêt est la validation humaine de cette préparation.
