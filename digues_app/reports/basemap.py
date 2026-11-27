"""Client raster TileJSON/XYZ des cartes de rapports, consommé par HTTP.

Le renderer PDF n'accède ni aux sources vectorielles PLAN.IGN, ni à MapLibre,
ni au volume de cache du service. Épuré reste la configuration implicite ;
``RasterXyzBasemap`` porte la même chaîne pour un futur fond raster publié et
autorisé côté serveur, sans changer la composition des rapports.

Chemin complet d'une carte :

    emprise EPSG:3857 (map_extent)
    → choix dynamique du zoom
    → indices XYZ couvrant l'emprise
    → GET /basemap/tiles/<cacheVersion>/{z}/{x}/{y}.webp
    → assemblage de la mosaïque (512×512 Épuré ; 256×256 WMTS)
    → crop exact sur l'emprise
    → PNG remis à ``map.render_map_image``

Le zoom est **dynamique** : il dépend de la largeur de l'emprise. La règle
retient le plus petit niveau dont la résolution permet au moins
``TARGET_WIDTH_PX`` pixels sur la largeur, avec un éventuel biais de détail,
plafonné au ``maxzoom`` annoncé par le TileJSON. Elle produit naturellement
z18, z17, z16, z15… selon l'emprise.

Aucun repli WMS n'existe : sans service configuré ou en cas d'échec, la fiche
est produite **sans fond**, jamais avec un fond partiel ou d'une autre source.

Le TileJSON est découvert dynamiquement et mis en cache process pendant
``TILEJSON_TTL_SECONDS``. Aucun cache Python de tuiles ou de mosaïques n'est
entretenu : le cache persistant du service (volume ``basemap-cache``) est la
seule garantie de performance.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any, Mapping, Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit
import logging
import math
import os
import time

import requests


LOGGER = logging.getLogger(__name__)

#: Largeur du monde Web Mercator en mètres (2π·6378137).
WORLD_MERCATOR_METERS = 40075016.685578488
#: Taille d'une tuile PLAN.IGN produite par le service.
TILE_SIZE_PX = 512
TILEJSON_PATH = "/basemap/plan-ign.json"
TILEJSON_TTL_SECONDS = 300.0
REQUEST_TIMEOUT_SECONDS = 10.0
#: Statuts transitoires du service : une seconde tentative est permise.
TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
DEFAULT_MAX_ZOOM = 18
#: Largeur cible de la mosaïque en pixels. 1024 couvre la sortie réelle 924 px.
DEFAULT_TARGET_WIDTH_PX = 1024
#: Biais de détail ajouté au zoom calculé par la résolution pure.
DEFAULT_ZOOM_DETAIL_BIAS = 0
TILE_SERVICE_URL_ENV = "BASEMAP_RASTER_URL"

_PROVIDERS: dict[str, "PlanIgnBasemap"] = {}


class BasemapUnavailable(RuntimeError):
    """Le fond PLAN.IGN ne peut pas être produit pour l'emprise demandée."""


class BasemapProvider(Protocol):
    """Contrat minimal attendu par ``map.render_map_image``."""

    @property
    def attribution(self) -> str: ...

    def image_for_extent(
        self, xmin: float, xmax: float, ymin: float, ymax: float
    ) -> bytes | None: ...


@dataclass(frozen=True)
class TileDocument:
    """TileJSON exploitable : gabarit d'URL, zooms et attribution."""

    template: str
    minzoom: int
    maxzoom: int
    attribution: str
    scheme: str = "xyz"


@dataclass(frozen=True)
class TileSpan:
    """Plage d'indices XYZ couvrant une emprise à un zoom donné."""

    zoom: int
    x_min: int
    x_max: int
    y_min: int
    y_max: int

    @property
    def columns(self) -> int:
        return self.x_max - self.x_min + 1

    @property
    def rows(self) -> int:
        return self.y_max - self.y_min + 1

    @property
    def tile_count(self) -> int:
        return self.columns * self.rows


def normalize_base_url(base_url: str) -> str:
    """Normalise l'URL de base du service : schéma, hôte, sans slash final."""

    value = (base_url or "").strip()
    if not value:
        raise ValueError("URL du service PLAN.IGN absente")
    if "://" not in value:
        value = f"http://{value}"
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"URL du service PLAN.IGN invalide : {base_url!r}")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def select_zoom(
    xmin: float,
    xmax: float,
    *,
    max_zoom: int = DEFAULT_MAX_ZOOM,
    target_width_px: int = DEFAULT_TARGET_WIDTH_PX,
    detail_bias: int = DEFAULT_ZOOM_DETAIL_BIAS,
    tile_size_px: int = TILE_SIZE_PX,
) -> int:
    """Choisit le zoom XYZ à partir de la seule largeur de l'emprise.

    Règle : plus petit ``z`` dont la résolution projetée donne au moins
    ``target_width_px`` pixels sur la largeur, puis ``detail_bias`` niveaux de
    détail supplémentaires, borné à ``[0, max_zoom]``. Elle est purement
    mathématique : aucune valeur de zoom n'est codée en dur pour une fiche.
    """

    width = float(xmax) - float(xmin)
    if width <= 0:
        raise ValueError("Emprise de largeur nulle ou négative")
    if target_width_px <= 0 or tile_size_px <= 0:
        raise ValueError("Largeur cible invalide")
    desired_resolution = width / target_width_px
    zoom = math.ceil(
        math.log2(WORLD_MERCATOR_METERS / (tile_size_px * desired_resolution))
    ) + int(detail_bias)
    return max(0, min(int(max_zoom), zoom))


def mercator_to_tile_fraction(x: float, y: float, zoom: int) -> tuple[float, float]:
    """Convertit une coordonnée EPSG:3857 en coordonnée XYZ fractionnaire."""

    world = 2**zoom
    fx = (float(x) + WORLD_MERCATOR_METERS / 2) / WORLD_MERCATOR_METERS * world
    fy = (WORLD_MERCATOR_METERS / 2 - float(y)) / WORLD_MERCATOR_METERS * world
    return fx, fy


def tile_mercator_bounds(x: int, y: int, zoom: int) -> tuple[float, float, float, float]:
    """Limites EPSG:3857 d'une tuile : ``(ouest, sud, est, nord)``."""

    world = 2**zoom
    size = WORLD_MERCATOR_METERS / world
    west = -WORLD_MERCATOR_METERS / 2 + x * size
    north = WORLD_MERCATOR_METERS / 2 - y * size
    return (west, north - size, west + size, north)


def covering_tiles(
    xmin: float, xmax: float, ymin: float, ymax: float, zoom: int
) -> TileSpan:
    """Indices XYZ couvrant l'emprise, bornés à la grille mondiale."""

    if ymin > ymax:
        raise ValueError("Emprise Y invalide")
    limit = 2**zoom - 1
    fx_min, fy_max = mercator_to_tile_fraction(xmin, ymax, zoom)
    fx_max, fy_min = mercator_to_tile_fraction(xmax, ymin, zoom)
    x0 = max(0, min(limit, math.floor(fx_min)))
    x1 = max(0, min(limit, math.floor(fx_max)))
    y0 = max(0, min(limit, math.floor(fy_max)))
    y1 = max(0, min(limit, math.floor(fy_min)))
    if x1 < x0 or y1 < y0:
        raise ValueError("Emprise hors de la grille XYZ")
    return TileSpan(zoom=zoom, x_min=x0, x_max=x1, y_min=y0, y_max=y1)


def mosaic_mercator_bounds(span: TileSpan) -> tuple[float, float, float, float]:
    """Limites EPSG:3857 de la mosaïque couvrant une plage de tuiles."""

    west, _, _, north = tile_mercator_bounds(span.x_min, span.y_min, span.zoom)
    _, south, east, _ = tile_mercator_bounds(span.x_max, span.y_max, span.zoom)
    return (west, south, east, north)


def crop_box(
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    span: TileSpan,
    bounds: tuple[float, float, float, float],
    tile_size_px: int = TILE_SIZE_PX,
    label: str = "PLAN.IGN",
) -> tuple[int, int, int, int]:
    """Boîte de crop en pixels de la mosaïque, alignée sur l'emprise demandée.

    Les tuiles sont carrées en projection : l'échelle X et Y est identique.
    """

    west, _south, east, north = bounds
    width_m = east - west
    if width_m <= 0:
        raise ValueError("Mosaïque de largeur nulle")
    pixels_per_meter = (span.columns * tile_size_px) / width_m
    left = round((xmin - west) * pixels_per_meter)
    right = round((xmax - west) * pixels_per_meter)
    top = round((north - ymax) * pixels_per_meter)
    bottom = round((north - ymin) * pixels_per_meter)
    image_width = span.columns * tile_size_px
    image_height = span.rows * tile_size_px
    left = max(0, min(image_width, left))
    right = max(0, min(image_width, right))
    top = max(0, min(image_height, top))
    bottom = max(0, min(image_height, bottom))
    if right - left < 1 or bottom - top < 1:
        raise BasemapUnavailable(f"Crop {label} vide")
    return (left, top, right, bottom)


def _tile_url(template: str, x: int, y: int, zoom: int, scheme: str) -> str:
    tile_y = y if scheme != "tms" else (2**zoom - 1 - y)
    return (
        template.replace("{z}", str(zoom))
        .replace("{x}", str(x))
        .replace("{y}", str(tile_y))
    )


def assemble_mosaic(
    tiles: Mapping[tuple[int, int], bytes], span: TileSpan,
    *, tile_size_px: int = TILE_SIZE_PX, label: str = "PLAN.IGN",
) -> Any:
    """Recolle les tuiles en une image RGB, dans l'ordre XYZ."""

    from PIL import Image

    expected = span.tile_count
    if len(tiles) != expected:
        raise BasemapUnavailable(
            f"Mosaïque {label} incomplète : {len(tiles)}/{expected} tuiles"
        )
    mosaic = Image.new(
        "RGB", (span.columns * tile_size_px, span.rows * tile_size_px), (255, 255, 255)
    )
    for (x, y), content in tiles.items():
        try:
            with Image.open(BytesIO(content)) as tile:
                if tile.size != (tile_size_px, tile_size_px):
                    raise BasemapUnavailable(
                        f"Tuile {label} de taille inattendue : {tile.size}"
                    )
                converted = tile.convert("RGB")
        except BasemapUnavailable:
            raise
        except Exception as error:
            raise BasemapUnavailable(
                f"Tuile {label} illisible ({x},{y}) : {error}"
            ) from error
        mosaic.paste(
            converted,
            ((x - span.x_min) * tile_size_px, (y - span.y_min) * tile_size_px),
        )
    return mosaic


def _validate_raster_tile(
    content: bytes, x: int, y: int, *, tile_format: str, tile_size_px: int, label: str
) -> bytes:
    from PIL import Image

    signatures = {
        "webp": len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP",
        "png": content.startswith(b"\x89PNG\r\n\x1a\n"),
        "jpeg": content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9"),
    }
    if not signatures[tile_format] or len(content) > 8 * 1024 * 1024:
        raise BasemapUnavailable(f"Tuile {label} non {tile_format.upper()} ({x},{y})")
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format != {"webp": "WEBP", "png": "PNG", "jpeg": "JPEG"}[tile_format]:
                raise BasemapUnavailable(f"Format de tuile {label} inattendu ({x},{y})")
            if image.size != (tile_size_px, tile_size_px):
                raise BasemapUnavailable(
                    f"Tuile {label} de taille inattendue : {image.size}"
                )
            image.load()
    except BasemapUnavailable:
        raise
    except Exception as error:
        raise BasemapUnavailable(
            f"Tuile {label} illisible ({x},{y}) : {error}"
        ) from error
    return content


class RasterXyzBasemap:
    """Client TileJSON/XYZ raster, sans cache Python ni choix de fournisseur externe."""

    def __init__(
        self,
        base_url: str,
        *,
        session: Any | None = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        tilejson_ttl: float = TILEJSON_TTL_SECONDS,
        target_width_px: int = DEFAULT_TARGET_WIDTH_PX,
        detail_bias: int = DEFAULT_ZOOM_DETAIL_BIAS,
        clock: Any = time.monotonic,
        tilejson_path: str = TILEJSON_PATH,
        tile_format: str = "webp",
        tile_size_px: int = TILE_SIZE_PX,
        label: str = "PLAN.IGN",
    ) -> None:
        self._path_prefix = '/basemap/'
        if not tilejson_path.startswith(self._path_prefix) or not tilejson_path.endswith(".json") or ".." in tilejson_path:
            raise ValueError("Chemin TileJSON cartographique invalide")
        if tile_format not in {"webp", "png", "jpeg"} or tile_size_px not in {256, 512}:
            raise ValueError("Format ou taille de tuile cartographique invalide")
        self._base_url = normalize_base_url(base_url)
        # Session HTTP par fournisseur : réutilise les connexions, sans être un
        # cache de tuiles.
        self._session = session if session is not None else requests.Session()
        self._timeout = float(timeout)
        self._tilejson_ttl = float(tilejson_ttl)
        self._target_width_px = int(target_width_px)
        self._detail_bias = int(detail_bias)
        self._clock = clock
        self._tilejson_path = tilejson_path
        self._tile_format = tile_format
        self._tile_size_px = tile_size_px
        self._label = label
        self._document: TileDocument | None = None
        self._document_at = 0.0

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def attribution(self) -> str:
        return self._document.attribution if self._document is not None else ""

    def _tilejson_url(self) -> str:
        return self._base_url + self._tilejson_path

    def _load_tilejson(self) -> TileDocument:
        url = self._tilejson_url()
        try:
            response = self._request(url)
        except Exception as error:
            raise BasemapUnavailable(f"TileJSON {self._label} injoignable : {error}") from error
        if response.status_code != 200:
            raise BasemapUnavailable(
                f"TileJSON {self._label} indisponible (HTTP {response.status_code})"
            )
        try:
            payload = response.json()
        except Exception as error:
            raise BasemapUnavailable(f"TileJSON {self._label} illisible : {error}") from error
        if not isinstance(payload, dict):
            raise BasemapUnavailable(f"TileJSON {self._label} inattendu")
        tiles = payload.get("tiles")
        template = next(
            (
                item
                for item in (tiles if isinstance(tiles, list) else [])
                if isinstance(item, str)
                and all(part in item for part in ("{z}", "{x}", "{y}"))
            ),
            None,
        )
        if template is None:
            raise BasemapUnavailable(f"TileJSON {self._label} sans gabarit de tuiles")
        try:
            minzoom = int(payload.get("minzoom", 0))
            maxzoom = int(payload.get("maxzoom", DEFAULT_MAX_ZOOM))
        except (TypeError, ValueError) as error:
            raise BasemapUnavailable(f"TileJSON {self._label} aux zooms invalides") from error
        if minzoom < 0 or maxzoom < minzoom:
            raise BasemapUnavailable(f"TileJSON {self._label} aux zooms incohérents")
        scheme = str(payload.get("scheme") or "xyz")
        if scheme not in ("xyz", "tms"):
            raise BasemapUnavailable(f"Schéma de tuiles {self._label} inconnu : {scheme}")
        resolved_template = urljoin(url, template)
        source = urlsplit(resolved_template)
        base = urlsplit(self._base_url)
        suffix = ".jpg" if self._tile_format == "jpeg" else f".{self._tile_format}"
        if (source.scheme, source.netloc) != (base.scheme, base.netloc) or not source.path.startswith(self._path_prefix) or \
                not source.path.endswith(suffix) or source.query or source.fragment:
            raise BasemapUnavailable(f"Gabarit de tuiles {self._label} non autorisé")
        return TileDocument(
            template=resolved_template,
            minzoom=minzoom,
            maxzoom=maxzoom,
            attribution=str(payload.get("attribution") or ""),
            scheme=scheme,
        )

    def tilejson(self, *, refresh: bool = False) -> TileDocument:
        """TileJSON courant, mis en cache process pendant le TTL configuré."""

        now = self._clock()
        if (
            not refresh
            and self._document is not None
            and now - self._document_at < self._tilejson_ttl
        ):
            return self._document
        document = self._load_tilejson()
        self._document = document
        self._document_at = now
        return document

    def tile_url(self, document: TileDocument, x: int, y: int, zoom: int) -> str:
        return _tile_url(document.template, x, y, zoom, document.scheme)

    def _request(self, url: str) -> Any:
        return self._session.get(url, timeout=self._timeout)

    def fetch_tile(self, x: int, y: int, zoom: int) -> bytes:
        """Octets WebP d'une tuile.

        Une seule nouvelle tentative est faite : après un ``410`` (cacheVersion
        périmée) elle relit le TileJSON, sinon pour un statut transitoire ou un
        timeout. Toute autre issue abandonne le fond.
        """

        document = self.tilejson()
        refreshed = False
        last_error = "indisponible"
        for attempt in range(2):
            url = self.tile_url(document, x, y, zoom)
            try:
                response = self._request(url)
            except Exception as error:
                last_error = f"réseau ({error})"
                if attempt == 0:
                    continue
                raise BasemapUnavailable(
                    f"Tuile {self._label} {zoom}/{x}/{y} {last_error}"
                ) from error
            if response.status_code == 200:
                return _validate_raster_tile(response.content, x, y,
                    tile_format=self._tile_format, tile_size_px=self._tile_size_px, label=self._label)
            if response.status_code == 410 and not refreshed:
                LOGGER.info("Cache %s périmé ; relecture du TileJSON.", self._label)
                document = self.tilejson(refresh=True)
                refreshed = True
                continue
            last_error = f"HTTP {response.status_code}"
            if response.status_code in TRANSIENT_STATUS and attempt == 0:
                continue
            raise BasemapUnavailable(f"Tuile {self._label} {zoom}/{x}/{y} {last_error}")
        raise BasemapUnavailable(f"Tuile {self._label} {zoom}/{x}/{y} {last_error}")

    def image_for_extent(
        self, xmin: float, xmax: float, ymin: float, ymax: float
    ) -> bytes | None:
        """PNG du fond sélectionné croppé sur l'emprise, ou lève ``BasemapUnavailable``.

        Toute tuile manquante abandonne **tout** le fond : aucune mosaïque
        partielle n'est jamais renvoyée.
        """

        for generation_attempt in range(2):
            document = self.tilejson()
            zoom = select_zoom(
                xmin, xmax, max_zoom=document.maxzoom,
                target_width_px=self._target_width_px, detail_bias=self._detail_bias,
                tile_size_px=self._tile_size_px,
            )
            if zoom < document.minzoom:
                raise BasemapUnavailable(f"Fond {self._label} absent à cette échelle")
            span = covering_tiles(xmin, xmax, ymin, ymax, zoom)
            tiles: dict[tuple[int, int], bytes] = {}
            generation_changed = False
            for x in range(span.x_min, span.x_max + 1):
                for y in range(span.y_min, span.y_max + 1):
                    tiles[(x, y)] = self.fetch_tile(x, y, zoom)
                    if self._document and self._document.template != document.template:
                        generation_changed = True
                        break
                if generation_changed:
                    break
            if generation_changed:
                continue  # Repartir de zéro : aucune mosaïque de générations mixtes.
            mosaic = assemble_mosaic(tiles, span, tile_size_px=self._tile_size_px, label=self._label)
            bounds = mosaic_mercator_bounds(span)
            cropped = mosaic.crop(crop_box(xmin, xmax, ymin, ymax, span, bounds,
                tile_size_px=self._tile_size_px, label=self._label))
            buffer = BytesIO()
            cropped.save(buffer, format="PNG")
            return buffer.getvalue()
        raise BasemapUnavailable(f"Génération du fond {self._label} instable")


class PlanIgnBasemap(RasterXyzBasemap):
    """Configuration historique Épuré du client raster commun."""


def get_basemap_provider() -> PlanIgnBasemap | None:
    """Fournisseur configuré par ``BASEMAP_RASTER_URL``, ou ``None``.

    L'instance est partagée par processus pour que le TileJSON reste en cache
    pendant son TTL ; ce n'est pas un cache de tuiles.
    """

    base_url = os.getenv(TILE_SERVICE_URL_ENV, "").strip()
    if not base_url:
        return None
    provider = _PROVIDERS.get(base_url)
    if provider is None:
        provider = PlanIgnBasemap(base_url)
        _PROVIDERS[base_url] = provider
    return provider


def reset_basemap_providers() -> None:
    """Vide les fournisseurs partagés (tests et rechargement de configuration)."""

    _PROVIDERS.clear()
