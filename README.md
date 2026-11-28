# gemapi.dev

Socle FastAPI, Leaflet et MapLibre Native pour Carte et Base. Les fonds publics
restent accessibles. L’accès aux données, aux médias et aux outils protégés
reste fermé jusqu’à la qualification de chaque opération métier.

## Démarrer sur la machine locale

```sh
docker compose up --build
```

L’image FastAPI utilise Python et GDAL de Debian trixie dans un environnement
virtuel dédié à l’application.

Ouvrir http://127.0.0.1:8080/ pour se connecter. Les pages /carte et /base servent aussi le shell technique public. Pour un autre
port local, renseigner `GEMAPI_PORT`. Le proxy publie seulement sur la boucle
locale. Le renderer et FastAPI restent sur le réseau Compose. Les fonds IGN
nécessitent un accès aux services cartographiques publics. Le sélecteur signale
l’indisponibilité d’un fond et dispose du repli OpenStreetMap. Dans le réseau
Compose, le renderer distingue la vivacité (`/live`) de la disponibilité du
moteur Épuré (`/health`). Les catalogues WMTS restent consultables pendant
une panne d’Épuré.

Le socle démarre sans PostgreSQL. La connexion métier de chaque instance utilise `DATABASE_URL` et
`DATABASE_SCHEMA` dans sa configuration privée. Les DSN facultatifs
`DATABASE_READONLY_URL` et `DATABASE_AI_READONLY_URL` doivent viser la même
base avec les rôles restreints correspondants. La production démarre sans connexion SQL. Seul le préremplissage optionnel de démonstration vérifie son compte public au démarrage. L’installation SQL est explicite. Voir le [contrat de connexion et d’exploitation](docs/GEMAPI-AUTH.md). Les instances démo et réelle
auront chacune leur origine, leur configuration et leurs volumes.

Le volume `media` est privé et distinct du volume de cache cartographique.
`GET /api/health` mesure la vivacité HTTP. Les conditions et résultats de
qualification figurent dans `audits/`.

## Modèle, démonstration et QGIS

Le produit partage le modèle patrimonial v0.6.1 entre la webapp et le plugin QGIS. La maintenance SQL et la reconstruction de la démo sont explicites. Voir [l’installation et les permissions](model/README.md), [la reconstruction de la démonstration](demo/README.md) et [le plugin et le projet QGIS](plugin/README.md).

## Licences

Le socle web dérivé de Digues App relève de la licence Apache 2.0. L’extension QGIS GEMAPI-App est diffusée sous GPL-2.0-or-later pour ses apports originaux. Les composants tiers conservent leurs licences et attributions. [Licence du socle](LICENSE) · [Mentions](NOTICE) · [Licence incluse dans le ZIP du plugin](plugin/gemapi_app/LICENSE)
