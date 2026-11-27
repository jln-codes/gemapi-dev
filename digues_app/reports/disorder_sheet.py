"""Construction des données métier d'une fiche de désordre.

Ce module est le portage de ``sirs/reports/disorder_sheet.py`` de ``sirs-suite``
(implémentation historique adossée à CouchDB). Il conserve le rôle, les règles
d'affichage et la mise en forme des valeurs de l'original, mais change la
provenance des données : les libellés de référentiel arrivent déjà résolus
depuis PostgreSQL, la photo est décrite par un identifiant média au lieu d'un
chemin de partage Windows, et le contexte cartographique porte des coordonnées
déjà projetées au lieu de documents CouchDB bruts.

Le modèle reste indépendant de toute base de données et de tout moteur de
rendu : il ne connaît ni CouchDB, ni PostgreSQL, ni ReportLab, ni FastAPI.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DisorderObservation:
    """Une ligne du tableau « Historique des observations »."""

    date: str
    observer: str
    comment: str


@dataclass(frozen=True)
class DisorderPhoto:
    """Référence abstraite de la photo de terrain affichée sur la fiche.

    Aucun chemin de stockage n'est porté par le modèle. ``storage_key`` est la
    clé technique opaque de ``MediaStorage`` (jamais un chemin de fichier) ; son
    contenu est obtenu plus tard, au moment du rendu, par l'adaptateur média
    injecté. ``identifier`` n'est conservé que pour le diagnostic.
    """

    identifier: str | None = None
    storage_key: str | None = None


@dataclass(frozen=True)
class DisorderMapContext:
    """Géométries de la carte de situation, déjà projetées en EPSG:3857.

    ``section_line`` correspond au ``LINESTRING`` du tronçon et ``marker`` au
    point de repérage du désordre, comme les deux entrées de
    ``fiche_desordre.generate_map`` dans sirs-suite. La projection est faite par
    PostGIS, ce qui évite d'embarquer ``pyproj`` dans digues-app.
    """

    section_line: tuple[tuple[float, float], ...] | None = None
    marker: tuple[float, float] | None = None


@dataclass(frozen=True)
class DisorderSheetData:
    """Données prêtes à rendre, sans dépendance à une source ou à un moteur."""

    designation: str
    section_label: str
    disorder_type: str
    comment: str
    observations: tuple[DisorderObservation, ...]
    photo: DisorderPhoto | None
    latitude: str
    longitude: str
    side: str
    position: str
    map_context: DisorderMapContext | None = None


def _display(value: Any, default: str = "-") -> str:
    return str(value) if value not in (None, "", []) else default


def _reference_label(value: Any, labels: Mapping[str, str]) -> str:
    if not value or ":" not in str(value):
        return _display(value)
    identifier = str(value).rsplit(":", 1)[-1]
    return labels.get(identifier, str(value))


def _coordinate(value: Any) -> str:
    if value in (None, ""):
        return "-"
    return str(round(float(value), 6))


def build_disorder_sheet(
    disorder: Mapping[str, Any],
    section: Mapping[str, Any],
    *,
    observer_name: Callable[[str | None], str],
    disorder_types: Mapping[str, str],
    sides: Mapping[str, str],
    positions: Mapping[str, str],
    observations: Sequence[Mapping[str, Any]] = (),
    photos: Sequence[DisorderPhoto] = (),
    map_context: DisorderMapContext | None = None,
) -> DisorderSheetData:
    """Transforme des lignes PostgreSQL en données prêtes à rendre.

    ``disorder`` porte les colonnes de ``public.desordres`` (dont ``latitude`` et
    ``longitude`` déjà calculées par PostGIS) et ``section`` celles de
    ``public.troncons``. Les trois tables de libellés sont indexées par le code
    du référentiel, comme dans l'implémentation historique, afin que
    ``_reference_label`` conserve exactement son comportement.

    Les observations, les photos et le contexte cartographique sont fournis à
    part : ils proviennent de tables distinctes en PostgreSQL alors qu'ils
    étaient imbriqués dans le document CouchDB.
    """

    observations_data = tuple(
        DisorderObservation(
            date=_display(item.get("date")),
            observer=observer_name(item.get("author")),
            comment=_display(item.get("commentaire")),
        )
        for item in observations
    )

    photo = photos[0] if photos else None

    return DisorderSheetData(
        designation=_display(disorder.get("designation")),
        section_label=_display(section.get("libelle")),
        disorder_type=_reference_label(
            disorder.get("type_desordre_id"), disorder_types
        ),
        comment=_display(disorder.get("commentaire")),
        observations=observations_data,
        photo=photo,
        latitude=_coordinate(disorder.get("latitude")),
        longitude=_coordinate(disorder.get("longitude")),
        side=_reference_label(disorder.get("cote_id"), sides),
        position=_reference_label(disorder.get("position_id"), positions),
        map_context=map_context,
    )
