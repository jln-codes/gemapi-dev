# GEMAPI-INIT-04 — Qualification du socle technique

## Vérifications terminées

| Périmètre | Exécution et résultat |
|---|---|
| Wheel FastAPI | Démarrage depuis la distribution installée hors du checkout. Carte, manifeste et refus d’une lecture protégée contrôlés. Le paquet installé ne charge pas l’ancien contrôle. |
| Python | 129 tests réussis et 214 sous-cas réussis. Catalogue, garde HTTP sur 144 couples méthode/chemin, version du shell, photo binaire, CRS, LiDAR opt-in, diffusion du bus simulée, météo et hydrométrie sur des réponses synthétiques. Le GDAL système a été mis à disposition du venv de test. |
| JavaScript | 12 fichiers de tests Node réussis et la suite d’isolation du contexte réussie. Ordre des plans Leaflet, GeoTIFF/LiDAR, sélecteur et repli cartographiques, projection, dessin et session de formulaire, impression par tuiles, téléchargement et lecture de paquets simulés. |
| PostgreSQL/PostGIS | Base jetable PostGIS 16-3.4, compte applicatif non propriétaire, connexion monobase, transaction de lecture seule, transformation 4326 → 2154 et refus de CREATE. Aucun schéma métier. Le conteneur a été supprimé après l’essai. |
| Renderer | 114 cas couverts. Dernier passage complet sur l’image finale et fichiers courants, 113 réussis et un délai MapLibre dépassé sous charge. Reprise isolée des deux tests natifs, deux réussis. Le cas supplémentaire confirme que la panne Épuré n’interrompt pas une tuile WMTS synthétique. Dans le conteneur sans réseau externe, vivacité 200, Épuré 503, catalogues WMTS 200. |
| Chromium | 8 scénarios réussis face à FastAPI local. Navigation, octets du branding, contrôles mobiles, réouverture du shell hors ligne, suivi et refus GPS simulés, annulation d’un polygone et invalidation dans deux onglets. |
| Proxy et Compose | Configuration Caddy valide. L’essai du démarrage et du redémarrage de la pile complète reste en cours dans la recette locale. |

Les tests météo et hydro ont couvert notamment le fournisseur indisponible,
la chronologie et les reprises de requêtes. Ils ne qualifient pas l’emprise
d’un territoire, la persistance PostGIS des caches ni un accès fournisseur.
Le test d’activation de deux versions du Service Worker simule les évènements
et vérifie que les anciens caches de shell sont remplacés après acceptation, que
les paquets restent en place et que la page ne redémarre qu’une fois.
Le démarrage et la réouverture de la PWA sur matériel restent à qualifier.

## Correctifs pendant la recette

- Le packaging assemble les roues dans un venv Debian trixie. Python et
  GDAL sont fournis par la même distribution. Ce choix évite la compilation
  locale des bindings C++ et conserve les outils GDAL de l’image.

- Le CSS de Digues masquait le cartouche en vue étroite. La vue technique
  conserve le cartouche, sans retoucher l’image. Le contrôle GPS conserve
  l’état `hidden` jusqu’à détection du mode standalone.
- À froid et sans réponse du fournisseur, l’initialisation d’IGN Épuré
  arrêtait le processus du renderer. Le service écoute dès validation de sa
  configuration. WMTS, observations de cache et distribution interne restent
  indépendants. La vivacité répond sur `/live`, la disponibilité Épuré sur
  `/health`. Épuré répond 503 pendant l’indisponibilité et réessaie toutes les
  30 secondes. La génération de cache n’est fixée qu’après chargement du
  style et des métadonnées.
- Le collecteur d’attribution de stockage par base et son lecteur HTTP
  exclusivement lié au contrôle ont été retirés. Les vérifications du cache
  public et les observations de services restent conservées.
- Le témoin CRS analytique provient de `tests/fixtures/metropolitan-crs-controls.json`
  au SHA validé. Les tests photo binaires ont été isolés du montage
  PostgreSQL historique. Les assertions multibase remplacées couvrent le
  chemin court, les refus et l’indépendance du catalogue public. Le runner
  Playwright cible la recette active, les specs métier historiques restent
  disponibles pour l’adaptation de leurs fixtures. Lancer tout le répertoire
  historique en bloc rencontre encore des imports et prérequis Digues.

## Réserves de qualification

- Installation sur iOS/Android, permissions et suivi GPS sur appareil, mise
  à niveau PWA avec une session et des intentions actives.
- Paquets cartographiques sur appareil et séparation de deux sessions
  successives. Les tests des stores, du téléchargement et du Service Worker
  vérifient le mécanisme sur entrées synthétiques.
- Projection locale, préparation, replay, conflits, journaux SSE et photos
  locales ou rattachées. Les composants conservés nécessitent le contrat
  métier et l’identité. La réception conjointe SQL/médias reste à qualifier.
- Emprise, masques LiDAR patrimoniaux, caches hydrométriques persistants,
  impressions et exports métier, formulaires, règles SQL et auteur. Reprise
  après livraison du modèle et de la démo alignée.
- Authentification, rôles, accès éventuel à la démo, politiques de diffusion
  IA/SQL et d’exploitation, règles terrain et autorité de la génération.
