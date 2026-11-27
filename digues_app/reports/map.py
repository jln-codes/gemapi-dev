"""Carte de situation d'une fiche de désordre.

Portage de ``generate_map``, ``map_extent`` et ``add_map_decorations`` de
``sirs-suite/fiche_desordre.py``. Le dessin, les couleurs des overlays, les
dimensions, les décorations (échelle métrique et flèche du nord) et les
paramètres d'enregistrement sont conservés à l'identique afin de ne pas modifier
la fiche.

Adaptations imposées par le changement de source :

* les coordonnées arrivent déjà projetées en EPSG:3857 (la transformation est
  faite par PostGIS avec ``ST_Transform``) au lieu d'être reprojetées par
  ``pyproj`` depuis les documents CouchDB ;
* l'image est produite en mémoire et non dans un fichier temporaire global
  ``_tmp_map.png``, qui n'est pas sûr pour un service concurrent ;
* le fond n'est plus l'orthophoto WMS historique mais le PLAN.IGN Épuré
  rasterisé par le service ``services/plan-ign-raster``, consommé en TileJSON +
  XYZ (voir ``digues_app.reports.basemap``).

Le fond est **entièrement abandonné** s'il ne peut pas être produit (service
absent, tuile manquante, réponse invalide) : la fiche reste produite avec ses
overlays, sans mosaïque partielle ni repli vers une autre source. Les overlays
— ligne de tronçon bleue, repère rouge, échelle, nord — sont inchangés : leurs
couleurs sont fonctionnelles et indépendantes de l'accent du template PDF.
"""

from __future__ import annotations

from io import BytesIO
import logging
import math

from .basemap import BasemapProvider, get_basemap_provider


LOGGER = logging.getLogger(__name__)

MIN_MAP_WIDTH_METERS = 500
MAP_ASPECT_RATIO = 4 / 3
MAP_FIGURE_SIZE = (8, 6)
MAP_DPI = 150
ATTRIBUTION_COLOR = "#4d4d4d"
ATTRIBUTION_FONT_SIZE = 6


def map_extent(
    x: list[float],
    y: list[float],
    point_x: float | None = None,
    point_y: float | None = None,
) -> tuple[float, float, float, float]:
    """Calcule une emprise 4:3 avec assez de contexte pour les petits tronçons."""

    all_x, all_y = list(x), list(y)
    if point_x is not None and point_y is not None:
        all_x.append(point_x)
        all_y.append(point_y)

    xmin, xmax = min(all_x), max(all_x)
    ymin, ymax = min(all_y), max(all_y)
    center_x, center_y = (xmin + xmax) / 2, (ymin + ymax) / 2
    width = max((xmax - xmin) * 1.4, MIN_MAP_WIDTH_METERS)
    height = max((ymax - ymin) * 1.4, width / MAP_ASPECT_RATIO)
    width = max(width, height * MAP_ASPECT_RATIO)
    return (
        center_x - width / 2,
        center_x + width / 2,
        center_y - height / 2,
        center_y + height / 2,
    )


def add_map_decorations(ax, xmin, xmax, ymin, ymax) -> None:
    """Ajoute une échelle minimaliste et une flèche du nord."""

    import matplotlib.patheffects as path_effects

    width, height = xmax - xmin, ymax - ymin
    center_y = (ymin + ymax) / 2
    latitude = math.degrees(
        2 * math.atan(math.exp(center_y / 6378137.0)) - math.pi / 2
    )
    scale_factor = math.cos(math.radians(latitude))
    target_length = width * scale_factor / 5
    magnitude = 10 ** math.floor(math.log10(target_length))
    scale_length = max(
        v * magnitude for v in (1, 2, 5) if v * magnitude <= target_length
    )
    projected_length = scale_length / scale_factor

    outline = [
        path_effects.Stroke(linewidth=3, foreground="black"),
        path_effects.Normal(),
    ]
    bar_x, bar_y = xmin + width * 0.06, ymin + height * 0.07
    tick_height = height * 0.012
    bar, = ax.plot(
        [bar_x, bar_x + projected_length],
        [bar_y, bar_y],
        color="white",
        linewidth=2.5,
        zorder=4,
    )
    bar.set_path_effects(outline)
    for tick_x in (bar_x, bar_x + projected_length):
        tick, = ax.plot(
            [tick_x, tick_x],
            [bar_y - tick_height, bar_y + tick_height],
            color="white",
            linewidth=2,
            zorder=4,
        )
        tick.set_path_effects(outline)

    label = (
        f"{scale_length / 1000:g} km"
        if scale_length >= 1000
        else f"{scale_length:g} m"
    )
    label_artist = ax.text(
        bar_x + projected_length / 2,
        bar_y + height * 0.025,
        label,
        color="white",
        fontsize=9,
        fontweight="bold",
        ha="center",
        va="bottom",
        zorder=4,
    )
    label_artist.set_path_effects(outline)

    north = ax.annotate(
        "N",
        xy=(0.93, 0.91),
        xytext=(0.93, 0.77),
        xycoords="axes fraction",
        textcoords="axes fraction",
        ha="center",
        va="center",
        color="white",
        fontsize=12,
        fontweight="bold",
        zorder=4,
        arrowprops={"arrowstyle": "-|>", "color": "white", "lw": 2},
    )
    north.set_path_effects(outline)
    north.arrow_patch.set_path_effects(outline)


def _draw_attribution(ax, text: str) -> None:
    """Ajoute discrètement l'attribution du fond, en bas à droite."""

    if not text:
        return
    import matplotlib.patheffects as path_effects

    artist = ax.text(
        0.995,
        0.012,
        text,
        transform=ax.transAxes,
        ha="right",
        va="bottom",
        color=ATTRIBUTION_COLOR,
        fontsize=ATTRIBUTION_FONT_SIZE,
        zorder=5,
    )
    artist.set_path_effects(
        [
            path_effects.Stroke(linewidth=1.6, foreground="white"),
            path_effects.Normal(),
        ]
    )


def _background_for(
    provider: BasemapProvider | None,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
) -> tuple[bytes | None, str]:
    """Fond et attribution, ou ``(None, "")`` si le fond est abandonné."""

    if provider is None:
        return None, ""
    try:
        content = provider.image_for_extent(xmin, xmax, ymin, ymax)
    except Exception as error:  # Fond indisponible : fiche sans fond.
        LOGGER.warning("Fond cartographique indisponible, fiche sans fond : %s", error)
        return None, ""
    if not content:
        return None, ""
    return content, getattr(provider, "attribution", "") or ""


def render_map_image(
    context, *, basemap: BasemapProvider | None = None
) -> bytes | None:
    """Produit la carte de situation en PNG, ou ``None`` si elle est impossible.

    ``context.section_line`` doit être un ``LINESTRING`` projeté, comme la
    géométrie de tronçon attendue par l'implémentation historique. ``basemap``
    permet d'injecter un fournisseur de fond pour les tests ; sans lui, le
    fournisseur configuré par ``BASEMAP_RASTER_URL`` est utilisé, ou aucun fond.
    """

    line = context.section_line
    if not line:
        return None

    import matplotlib

    try:  # Le rendu serveur doit être non interactif.
        matplotlib.use("Agg")
    except Exception:  # pragma: no cover - dépend de l'état d'import de matplotlib
        pass
    import matplotlib.pyplot as plt

    x = [float(point[0]) for point in line]
    y_c = [float(point[1]) for point in line]

    marker = context.marker
    marker_x = float(marker[0]) if marker is not None else None
    marker_y = float(marker[1]) if marker is not None else None

    xmin, xmax, ymin, ymax = map_extent(x, y_c, marker_x, marker_y)

    provider = basemap if basemap is not None else get_basemap_provider()
    content, attribution = _background_for(provider, xmin, xmax, ymin, ymax)

    figure, ax = plt.subplots(figsize=MAP_FIGURE_SIZE)
    try:
        # Cadrage explicitement métrique : les dimensions et le cadrage de la
        # carte ne dépendent pas de la présence du fond.
        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymin, ymax)
        ax.set_aspect("equal")
        if content:
            from PIL import Image

            ax.imshow(
                Image.open(BytesIO(content)),
                extent=[xmin, xmax, ymin, ymax],
                origin="upper",
                zorder=0,
            )
            _draw_attribution(ax, attribution)

        ax.plot(x, y_c, color="blue", linewidth=2, zorder=2)
        if marker_x:
            ax.scatter(marker_x, marker_y, color="red", s=100, edgecolors="white", zorder=3)
        add_map_decorations(ax, xmin, xmax, ymin, ymax)
        ax.axis("off")

        buffer = BytesIO()
        figure.savefig(buffer, format="png", dpi=MAP_DPI, bbox_inches="tight", pad_inches=0)
        return buffer.getvalue()
    finally:
        plt.close(figure)
