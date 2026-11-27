"""Découverte et observations hydrométriques relatives à un territoire.

Le catalogue métier est structuré par site Hub'Eau. BD TOPAGE sert uniquement
à calculer les relations ``inside``, ``upstream`` et ``downstream`` ; le graphe
et les chemins complets ne sont jamais exposés dans le dataset métier.
"""

from __future__ import annotations

from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import logging
import math
from threading import Condition
from time import monotonic, sleep
from typing import Any, Iterable
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree

import requests

from .service_observations import observe
from .territoire import fetch_territoire_administratif
from .vigicrues import (
    HUBEAU_OBSERVATIONS_URL,
    OBSERVATION_DAYS,
    OBSERVATION_TARGET_HOURS,
    OBSERVATION_TOLERANCE,
    VigicruesClient,
    VigicruesDataError,
    VigicruesError,
    VigicruesServiceUnavailableError,
    _clean_string,
    _coordinate,
    _format_utc,
    _number,
    _parse_datetime,
    _territory_geometry,
    observation_targets,
    point_in_territory,
    territory_bbox,
)


HUBEAU_STATIONS_URL = (
    "https://hubeau.eaufrance.fr/api/v2/hydrometrie/referentiel/stations"
)
HUBEAU_SITES_URL = (
    "https://hubeau.eaufrance.fr/api/v2/hydrometrie/referentiel/sites"
)
TOPAGE_WFS_URL = "https://services.sandre.eaufrance.fr/geo/topage2026"
TOPAGE_TYPE_NAME = "sa:TronconHydrographique_FXX_Topage2026"
TOPAGE_VERSION = "2026"
TOPAGE_PAGE_SIZE = 10_000
TOPAGE_PROPERTY_NAMES = (
    "msGeometry,CdOH,StatutOH,TopoOH,NatureTH,SensEcoulementTH,"
    "CdCoursEau_1,CdCoursEau_2,CdCoursEau_3,CdNoeudDebut,CdNoeudFin"
)
TOPAGE_INITIAL_PADDING_DEGREES = 0.4
TOPAGE_MAX_FEATURES = 250_000
TOPAGE_MAX_TRAVERSED_EDGES = 20_000
TOPAGE_RESPONSE_MAX_BYTES = 30_000_000
TOPAGE_TIMEOUT_SECONDS = 60
ATTACHMENT_TOLERANCE_M = 50.0
ACTIVITY_LOOKBACK = timedelta(hours=24)
ACTIVITY_POLICY_ID = "recent-h-or-q-within-24h/v1"
SERIES_POLICY_ID = "hydrometric-series-resolution/v1"
DISCOVERY_ALGORITHM_VERSION = "topage-first-active-stop/v1"
TOPOLOGY_ALGORITHM_VERSION = "topage-directed-boundary-graph/v1"
ATTACHMENT_POLICY_ID = "nearest-topage-edge-50m/v1"
REFERENCE_PAGE_SIZE = 10_000
OBSERVATION_PAGE_SIZE = 20_000
NETWORK_WORKERS = 8
ACTIVITY_BATCH_SIZE = 20
ACTIVITY_NETWORK_WORKERS = 4
ACTIVITY_MAX_PAGES = 20
DISCOVERY_CACHE_PAYLOAD_SCHEMA_VERSION = 2
STRUCTURAL_CACHE_PAYLOAD_SCHEMA_VERSION = 1
DISCOVERY_CACHE_LOCK_SEED = 730_420_261_002
DISCOVERY_CACHE_LOCK_TIMEOUT_SECONDS = 180.0
DISCOVERY_CACHE_LOCK_POLL_SECONDS = 0.25

LOGGER = logging.getLogger(__name__)

DIRECT_FLOW = "dans direction tronçon"
REVERSE_FLOW = "inverse direction tronçon"


CONDITIONAL_CONNECTIONS = ({
    "connection_id": "goeulzin-sensee-petite-sensee",
    "topage_node_id": "01N0000002486819487",
    "topage_edge_ids": (
        "01T0000002287816425",
        "01T0000002005072266",
    ),
    "kind": "regulated_transfer",
    "hydraulic_continuity": "conditional",
    "fact": (
        "Le transfert par l'écluse de Gœulzin est régulé et sa continuité "
        "permanente dans le sens parcouru n'est pas démontrée."
    ),
    "qualification_source": {
        "provider": "SAGE Scarpe aval / DREAL Hauts-de-France",
        "references": [
            (
                "https://www.sage-scarpe-aval.fr/"
                "etat-des-lieux-carte-4-le-bassin-versant-scarpe-aval-par-"
                "rapport-aux-bassins-versants-des-sage"
            ),
            (
                "https://www.hauts-de-france.developpement-durable.gouv.fr/"
                "IMG/pdf/rapport_phase_1_tri_douai_valenciennes.pdf"
            ),
        ],
        "version_or_date": "2026-10-02",
    },
},)
CONNECTION_QUALIFICATION_VERSION = "cabbalr-regulated-connections/2026-10-02"


class HydrometryDataError(VigicruesDataError):
    """Une source hydrométrique ou TOPAGE est structurellement invalide."""


class HydrometryServiceUnavailableError(VigicruesServiceUnavailableError):
    """Une source nécessaire à l'hydrométrie n'a pas répondu."""


@dataclass(frozen=True)
class TopageEdge:
    code: str
    upstream_node: str
    downstream_node: str
    length_m: float
    geometry: dict[str, Any]
    course_codes: tuple[str, ...] = ()
    name: str | None = None
    nature: str | None = None
    status: str | None = None
    direction_status: str = "directed"
    flow_reversed: bool = False


@dataclass(frozen=True)
class SiteAttachment:
    site_code: str
    edge_code: str
    flow_fraction: float
    distance_m: float
    station_code: str | None = None
    inside_territory: bool = False


@dataclass(frozen=True)
class BoundaryEvent:
    identifier: str
    edge_code: str
    flow_fraction: float
    direction: str
    coordinate: tuple[float, float]


@dataclass
class SelectedSite:
    site_code: str
    distance_m: float
    traversed_edges: int
    path_fingerprint: str
    conditional_connections: list[dict[str, Any]] = field(default_factory=list)
    attachment: SiteAttachment | None = None


@dataclass
class TraversalResult:
    selected: list[SelectedSite] = field(default_factory=list)
    visited_edges: set[str] = field(default_factory=set)
    cycle_edges: set[str] = field(default_factory=set)
    ambiguous_edges: set[str] = field(default_factory=set)
    incomplete_nodes: set[str] = field(default_factory=set)
    limit_reached: bool = False


@dataclass
class CacheEntry:
    status: str = "loading"
    payload: dict[str, Any] | None = None


_discovery_condition = Condition()
_discovery_cache: dict[str, CacheEntry] = {}
_structural_condition = Condition()
_structural_cache: dict[str, CacheEntry] = {}
_observation_condition = Condition()
_observation_cache: dict[tuple[Any, ...], CacheEntry] = {}
_forecast_condition = Condition()
_forecast_cache: dict[tuple[Any, ...], CacheEntry] = {}


def _json_response(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any],
    allowed_url: str,
    timeout: float,
    maximum_bytes: int,
    source: str,
) -> dict[str, Any]:
    try:
        with observe(source):
            response = session.get(
                url,
                params=params,
                headers={"Accept": "application/json"},
                timeout=timeout,
                stream=True,
            )
    except requests.Timeout as exc:
        raise HydrometryServiceUnavailableError(
            f"{source} n’a pas répondu à temps."
        ) from exc
    except requests.RequestException as exc:
        raise HydrometryServiceUnavailableError(
            f"{source} est temporairement indisponible."
        ) from exc
    try:
        final_url = response.url.split("?", 1)[0]
        parsed = urlsplit(final_url)
        if (
            final_url != allowed_url
            or parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
        ):
            raise HydrometryServiceUnavailableError(
                "Destination du service hydrométrique refusée."
            )
        if response.status_code not in {200, 206}:
            raise HydrometryServiceUnavailableError(
                f"{source} est temporairement indisponible."
            )
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(chunk_size=65_536):
            if not chunk:
                continue
            size += len(chunk)
            if size > maximum_bytes:
                raise HydrometryDataError(
                    f"La réponse {source} est trop volumineuse."
                )
            chunks.append(chunk)
        try:
            payload = json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise HydrometryDataError(f"Réponse {source} invalide.") from exc
        if not isinstance(payload, dict):
            raise HydrometryDataError(f"Réponse {source} invalide.")
        return payload
    except requests.Timeout as exc:
        raise HydrometryServiceUnavailableError(
            f"{source} n’a pas répondu à temps."
        ) from exc
    except requests.RequestException as exc:
        raise HydrometryServiceUnavailableError(
            f"{source} est temporairement indisponible."
        ) from exc
    finally:
        response.close()


class HydrometryClient(VigicruesClient):
    """Client Hub'Eau + Vigicrues sans secret, borné et testable."""

    def _reference(self, url: str, bbox: tuple[float, float, float, float]) -> list[dict[str, Any]]:
        payload = self._get_json(
            url,
            params={
                "bbox": ",".join(f"{coordinate:.7f}" for coordinate in bbox),
                "page": 1,
                "size": REFERENCE_PAGE_SIZE,
            },
            allowed_urls={url},
        )
        rows = payload.get("data")
        count = payload.get("count")
        if not isinstance(rows, list):
            raise HydrometryDataError("Référentiel Hub'Eau invalide.")
        if isinstance(count, int) and count > len(rows):
            raise HydrometryDataError(
                "L'emprise contient trop d'objets Hub'Eau pour une page."
            )
        return [row for row in rows if isinstance(row, dict)]

    def reference_stations(
        self, bbox: tuple[float, float, float, float]
    ) -> list[dict[str, Any]]:
        return self._reference(HUBEAU_STATIONS_URL, bbox)

    def reference_sites(
        self, bbox: tuple[float, float, float, float]
    ) -> list[dict[str, Any]]:
        return self._reference(HUBEAU_SITES_URL, bbox)

    def recent_activity(
        self,
        station_codes: Iterable[str],
        *,
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, dict[str, dict[str, Any] | None]]:
        codes = sorted({code for code in station_codes if code})
        activity = {
            code: {"H": None, "Q": None}
            for code in codes
        }
        errors: list[dict[str, Any]] = []
        batches = [
            codes[offset:offset + ACTIVITY_BATCH_SIZE]
            for offset in range(0, len(codes), ACTIVITY_BATCH_SIZE)
        ]
        jobs = [
            (index, batch, quantity)
            for index, batch in enumerate(batches)
            for quantity in ("H", "Q")
        ]
        results: dict[
            tuple[int, str], tuple[list[dict[str, Any]] | None, str | None]
        ] = {}
        if jobs:
            with ThreadPoolExecutor(
                max_workers=min(ACTIVITY_NETWORK_WORKERS, len(jobs)),
                thread_name_prefix="hubeau-activity",
            ) as executor:
                futures = {
                    executor.submit(
                        self._recent_activity_batch,
                        batch,
                        quantity,
                        start_time=start_time,
                        end_time=end_time,
                    ): (index, quantity)
                    for index, batch, quantity in jobs
                }
                for future in as_completed(futures):
                    key = futures[future]
                    try:
                        results[key] = (future.result(), None)
                    except VigicruesError:
                        results[key] = (None, "unavailable")

        successful_requests = 0
        for index, batch, requested_quantity in jobs:
            rows, error = results[(index, requested_quantity)]
            if error is not None:
                errors.append({
                    "quantity": requested_quantity,
                    "station_count": len(batch),
                    "error": error,
                })
                continue
            successful_requests += 1
            for row in rows or ():
                if not isinstance(row, dict):
                    continue
                code = _clean_string(row.get("code_station"))
                quantity = _clean_string(row.get("grandeur_hydro"))
                if code not in activity or quantity != requested_quantity:
                    continue
                # observations_tr est demandé en ordre décroissant. Une fois la
                # première valeur valide trouvée pour une série, les milliers de
                # valeurs plus anciennes de la fenêtre n'ont plus à être reparsés.
                if activity[code][quantity] is not None:
                    continue
                parsed = parse_hubeau_measurement(row, quantity)
                if parsed is not None:
                    activity[code][quantity] = parsed
        self.last_activity_errors = errors
        if codes and not successful_requests:
            raise HydrometryServiceUnavailableError(
                "Hub'Eau Hydrométrie est temporairement indisponible."
            )
        return activity

    def _recent_activity_batch(
        self,
        station_codes: list[str],
        quantity: str,
        *,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        """Charge intégralement un lot borné via le curseur officiel Hub'Eau."""

        params = {
            "code_entite": ",".join(station_codes),
            "grandeur_hydro": quantity,
            "date_debut_obs": _format_utc(start_time),
            "date_fin_obs": _format_utc(end_time),
            "sort": "desc",
            "size": OBSERVATION_PAGE_SIZE,
            "format": "json",
        }
        rows: list[dict[str, Any]] = []
        expected_count: int | None = None
        seen_cursors: set[str] = set()
        cursor: str | None = None
        for _page in range(ACTIVITY_MAX_PAGES):
            page_params = dict(params)
            if cursor is not None:
                page_params["cursor"] = cursor
            payload = self._get_json(
                HUBEAU_OBSERVATIONS_URL,
                params=page_params,
                allowed_urls={HUBEAU_OBSERVATIONS_URL},
            )
            page_rows = payload.get("data")
            if not isinstance(page_rows, list):
                raise HydrometryDataError(
                    "Observations d'activité Hub'Eau invalides."
                )
            rows.extend(row for row in page_rows if isinstance(row, dict))
            count = payload.get("count")
            if isinstance(count, int):
                if expected_count is not None and count != expected_count:
                    raise HydrometryDataError(
                        "Pagination d'activité Hub'Eau incohérente."
                    )
                expected_count = count
            cursor = _hubeau_next_cursor(payload.get("next"))
            if cursor is None:
                if expected_count is not None and len(rows) < expected_count:
                    raise HydrometryDataError(
                        "Réponse d'activité Hub'Eau incomplète."
                    )
                return rows
            if cursor in seen_cursors:
                raise HydrometryDataError(
                    "Curseur d'activité Hub'Eau cyclique."
                )
            seen_cursors.add(cursor)
        raise HydrometryDataError(
            "Pagination d'activité Hub'Eau au-delà de la garde technique."
        )

    def observation_series(
        self,
        station_code: str,
        quantity: str,
        *,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        payload = self._get_json(
            HUBEAU_OBSERVATIONS_URL,
            params={
                "code_entite": station_code,
                "grandeur_hydro": quantity,
                "date_debut_obs": _format_utc(start_time),
                "date_fin_obs": _format_utc(end_time),
                "sort": "asc",
                "size": OBSERVATION_PAGE_SIZE,
                "format": "json",
            },
            allowed_urls={HUBEAU_OBSERVATIONS_URL},
        )
        rows = payload.get("data")
        count = payload.get("count")
        if not isinstance(rows, list):
            raise HydrometryDataError("Série d'observations Hub'Eau invalide.")
        if isinstance(count, int) and count > len(rows):
            raise HydrometryDataError(
                "La série d'observations Hub'Eau dépasse la page bornée."
            )
        return parse_hubeau_series(payload, quantity)


class TopageClient:
    """Client WFS BD TOPAGE 2026 par emprise technique paginée."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout: float = TOPAGE_TIMEOUT_SECONDS,
    ):
        self.session = session or requests.Session()
        self.timeout = timeout

    def matched_count(
        self, bbox: tuple[float, float, float, float]
    ) -> int | None:
        min_lon, min_lat, max_lon, max_lat = bbox
        try:
            with observe("topage"):
                response = self.session.get(
                    TOPAGE_WFS_URL,
                    params={
                        "service": "WFS", "version": "2.0.0",
                        "request": "GetFeature", "resultType": "hits",
                        "typeNames": TOPAGE_TYPE_NAME,
                        "bbox": (
                            f"{min_lat:.7f},{min_lon:.7f},{max_lat:.7f},"
                            f"{max_lon:.7f},urn:ogc:def:crs:EPSG::4326"
                        ),
                    },
                    headers={"Accept": "application/xml"},
                    timeout=self.timeout,
                )
        except requests.RequestException as exc:
            raise HydrometryServiceUnavailableError(
                "BD TOPAGE est temporairement indisponible."
            ) from exc
        try:
            if response.url.split("?", 1)[0] != TOPAGE_WFS_URL:
                raise HydrometryServiceUnavailableError(
                    "Destination du service hydrométrique refusée."
                )
            if response.status_code != 200 or len(response.content) > 100_000:
                raise HydrometryServiceUnavailableError(
                    "BD TOPAGE est temporairement indisponible."
                )
            try:
                root = ElementTree.fromstring(response.content)
            except ElementTree.ParseError as exc:
                raise HydrometryDataError("Réponse WFS BD TOPAGE invalide.") from exc
            matched = root.attrib.get("numberMatched")
            return int(matched) if matched and matched.isdigit() else None
        finally:
            response.close()

    def features(
        self, bbox: tuple[float, float, float, float]
    ) -> list[dict[str, Any]]:
        min_lon, min_lat, max_lon, max_lat = bbox
        matched = self.matched_count(bbox)
        if matched is not None and matched > TOPAGE_MAX_FEATURES:
            raise HydrometryDataError(
                "La garde technique de volume BD TOPAGE est atteinte."
            )
        features: list[dict[str, Any]] = []
        start_index = 0
        while True:
            payload = _json_response(
                self.session,
                TOPAGE_WFS_URL,
                params={
                    "service": "WFS",
                    "version": "2.0.0",
                    "request": "GetFeature",
                    "typeNames": TOPAGE_TYPE_NAME,
                    "outputFormat": "application/json; subtype=geojson",
                    "srsName": "urn:ogc:def:crs:EPSG::4326",
                    # EPSG:4326 impose latitude/longitude avec WFS 2.0.
                    "bbox": (
                        f"{min_lat:.7f},{min_lon:.7f},{max_lat:.7f},"
                        f"{max_lon:.7f},urn:ogc:def:crs:EPSG::4326"
                    ),
                    "count": TOPAGE_PAGE_SIZE,
                    "startIndex": start_index,
                    "propertyName": TOPAGE_PROPERTY_NAMES,
                },
                allowed_url=TOPAGE_WFS_URL,
                timeout=self.timeout,
                maximum_bytes=TOPAGE_RESPONSE_MAX_BYTES,
                source="topage",
            )
            page = payload.get("features")
            if not isinstance(page, list):
                raise HydrometryDataError("Réponse WFS BD TOPAGE invalide.")
            matched = payload.get("numberMatched")
            if isinstance(matched, int) and matched > TOPAGE_MAX_FEATURES:
                raise HydrometryDataError(
                    "La garde technique de volume BD TOPAGE est atteinte."
                )
            features.extend(item for item in page if isinstance(item, dict))
            if len(features) > TOPAGE_MAX_FEATURES:
                raise HydrometryDataError(
                    "La garde technique de volume BD TOPAGE est atteinte."
                )
            returned = payload.get("numberReturned")
            returned_count = returned if isinstance(returned, int) else len(page)
            if returned_count < TOPAGE_PAGE_SIZE or not page:
                break
            start_index += returned_count
        return features


def parse_hubeau_measurement(
    item: dict[str, Any], quantity: str
) -> dict[str, Any] | None:
    if quantity not in {"H", "Q"}:
        raise ValueError("quantity doit valoir H ou Q")
    source_time = _parse_datetime(item.get("date_obs"))
    native_value = _number(item.get("resultat_obs"))
    station_code = _clean_string(item.get("code_station"))
    if source_time is None or native_value is None:
        return None
    return {
        "value": native_value / 1000,
        "unit": "m" if quantity == "H" else "m3/s",
        "native_value": native_value,
        "native_unit": "mm" if quantity == "H" else "l/s",
        "source_time": _format_utc(source_time),
        "station_code": station_code,
        "status": {
            "code": item.get("code_statut"),
            "label": _clean_string(item.get("libelle_statut")),
        },
        "qualification": {
            "code": item.get("code_qualification_obs"),
            "label": _clean_string(item.get("libelle_qualification_obs")),
        },
        "method": {
            "code": item.get("code_methode_obs"),
            "label": _clean_string(item.get("libelle_methode_obs")),
        },
    }


def _hubeau_next_cursor(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise HydrometryDataError("Pagination d'activité Hub'Eau invalide.")
    parsed = urlsplit(value)
    expected = urlsplit(HUBEAU_OBSERVATIONS_URL)
    try:
        port = parsed.port
    except ValueError as exc:
        raise HydrometryDataError(
            "Destination de pagination Hub'Eau refusée."
        ) from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected.hostname
        or parsed.path != expected.path
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise HydrometryDataError("Destination de pagination Hub'Eau refusée.")
    cursors = parse_qs(parsed.query).get("cursor", [])
    if len(cursors) != 1 or not cursors[0]:
        raise HydrometryDataError("Curseur d'activité Hub'Eau invalide.")
    return cursors[0]


def parse_hubeau_series(
    payload: dict[str, Any], quantity: str
) -> list[dict[str, Any]]:
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise HydrometryDataError("Série d'observations Hub'Eau invalide.")
    parsed = [
        measurement
        for row in rows
        if isinstance(row, dict) and row.get("grandeur_hydro") == quantity
        if (measurement := parse_hubeau_measurement(row, quantity)) is not None
    ]
    parsed.sort(key=lambda item: item["source_time"])
    return parsed


def _iter_lines(geometry: dict[str, Any]) -> Iterable[list[list[float]]]:
    if geometry.get("type") == "LineString":
        coordinates = geometry.get("coordinates")
        if isinstance(coordinates, list):
            yield coordinates
    elif geometry.get("type") == "MultiLineString":
        coordinates = geometry.get("coordinates")
        if isinstance(coordinates, list):
            yield from (line for line in coordinates if isinstance(line, list))


def _xy(point: tuple[float, float] | list[float], latitude: float) -> tuple[float, float]:
    return (
        float(point[0]) * 111_320.0 * math.cos(math.radians(latitude)),
        float(point[1]) * 110_574.0,
    )


def _segment_length_m(start: list[float], end: list[float]) -> float:
    latitude = (float(start[1]) + float(end[1])) / 2
    ax, ay = _xy(start, latitude)
    bx, by = _xy(end, latitude)
    return math.hypot(bx - ax, by - ay)


def _line_length_m(geometry: dict[str, Any]) -> float:
    return sum(
        _segment_length_m(start, end)
        for line in _iter_lines(geometry)
        for start, end in zip(line, line[1:])
    )


def _geometry_bbox(geometry: dict[str, Any]) -> tuple[float, float, float, float] | None:
    points = [point for line in _iter_lines(geometry) for point in line]
    if not points:
        return None
    return (
        min(float(point[0]) for point in points),
        min(float(point[1]) for point in points),
        max(float(point[0]) for point in points),
        max(float(point[1]) for point in points),
    )


def _point_segment_distance_m(
    point: tuple[float, float], start: list[float], end: list[float]
) -> tuple[float, float]:
    px, py = _xy(point, point[1])
    ax, ay = _xy(start, point[1])
    bx, by = _xy(end, point[1])
    dx, dy = bx - ax, by - ay
    denominator = dx * dx + dy * dy
    factor = 0.0 if denominator == 0 else ((px - ax) * dx + (py - ay) * dy) / denominator
    factor = min(1.0, max(0.0, factor))
    return math.hypot(px - (ax + factor * dx), py - (ay + factor * dy)), factor


def _point_line_location(
    point: tuple[float, float], geometry: dict[str, Any]
) -> tuple[float, float]:
    lines = list(_iter_lines(geometry))
    if len(lines) != 1 or len(lines[0]) < 2:
        return math.inf, 0.0
    line = lines[0]
    lengths = [_segment_length_m(a, b) for a, b in zip(line, line[1:])]
    total = sum(lengths)
    best_distance = math.inf
    best_along = 0.0
    travelled = 0.0
    for (start, end), length in zip(zip(line, line[1:]), lengths):
        distance, factor = _point_segment_distance_m(point, start, end)
        if distance < best_distance:
            best_distance = distance
            best_along = travelled + factor * length
        travelled += length
    return best_distance, best_along / total if total else 0.0


def _property(properties: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in properties:
            return properties[name]
    folded = {str(key).casefold(): value for key, value in properties.items()}
    for name in names:
        if name.casefold() in folded:
            return folded[name.casefold()]
    return None


def parse_topage_edges(features: Iterable[dict[str, Any]]) -> dict[str, TopageEdge]:
    edges: dict[str, TopageEdge] = {}
    for feature in features:
        properties = feature.get("properties")
        geometry = feature.get("geometry")
        if not isinstance(properties, dict) or not isinstance(geometry, dict):
            continue
        code = _clean_string(_property(properties, "CdOH", "cdoh"))
        start = _clean_string(_property(properties, "CdNoeudDebut"))
        end = _clean_string(_property(properties, "CdNoeudFin"))
        flow = _clean_string(_property(properties, "SensEcoulementTH"))
        lines = list(_iter_lines(geometry))
        if not code or not start or not end or len(lines) != 1 or len(lines[0]) < 2:
            continue
        if flow == DIRECT_FLOW:
            upstream, downstream = start, end
            direction_status = "directed"
            flow_reversed = False
        elif flow == REVERSE_FLOW:
            upstream, downstream = end, start
            direction_status = "directed"
            flow_reversed = True
        else:
            upstream, downstream = start, end
            direction_status = "ambiguous"
            flow_reversed = False
        course_codes = tuple(
            code_value
            for key in ("CdCoursEau_1", "CdCoursEau_2", "CdCoursEau_3")
            if (code_value := _clean_string(_property(properties, key)))
        )
        edges[code] = TopageEdge(
            code=code,
            upstream_node=upstream,
            downstream_node=downstream,
            length_m=_line_length_m(geometry),
            geometry=geometry,
            course_codes=course_codes,
            name=_clean_string(_property(properties, "TopoOH")),
            nature=_clean_string(_property(properties, "NatureTH")),
            status=_clean_string(_property(properties, "StatutOH")),
            direction_status=direction_status,
            flow_reversed=flow_reversed,
        )
    return edges


def _flow_line(edge: TopageEdge) -> list[list[float]]:
    line = list(_iter_lines(edge.geometry))[0]
    return list(reversed(line)) if edge.flow_reversed else line


def _point_at_fraction(line: list[list[float]], fraction: float) -> tuple[float, float]:
    lengths = [_segment_length_m(a, b) for a, b in zip(line, line[1:])]
    total = sum(lengths)
    if not total:
        return float(line[0][0]), float(line[0][1])
    target = min(1.0, max(0.0, fraction)) * total
    travelled = 0.0
    for (start, end), length in zip(zip(line, line[1:]), lengths):
        if travelled + length >= target:
            local = 0.0 if not length else (target - travelled) / length
            return (
                float(start[0]) + local * (float(end[0]) - float(start[0])),
                float(start[1]) + local * (float(end[1]) - float(start[1])),
            )
        travelled += length
    return float(line[-1][0]), float(line[-1][1])


def _point_at_degree_fraction(
    line: list[list[float]], fraction: float
) -> tuple[float, float]:
    lengths = [
        math.hypot(
            float(end[0]) - float(start[0]),
            float(end[1]) - float(start[1]),
        )
        for start, end in zip(line, line[1:])
    ]
    total = sum(lengths)
    if not total:
        return float(line[0][0]), float(line[0][1])
    target = min(1.0, max(0.0, fraction)) * total
    travelled = 0.0
    for (start, end), length in zip(zip(line, line[1:]), lengths):
        if travelled + length >= target:
            local = 0.0 if not length else (target - travelled) / length
            return (
                float(start[0]) + local * (float(end[0]) - float(start[0])),
                float(start[1]) + local * (float(end[1]) - float(start[1])),
            )
        travelled += length
    return float(line[-1][0]), float(line[-1][1])


def _expanded_bbox(
    bbox: tuple[float, float, float, float], padding: float
) -> tuple[float, float, float, float]:
    min_lon, min_lat, max_lon, max_lat = bbox
    return (
        max(-180.0, min_lon - padding),
        max(-90.0, min_lat - padding),
        min(180.0, max_lon + padding),
        min(90.0, max_lat + padding),
    )


def _territory_identity(
    territory: dict[str, Any], bbox: tuple[float, float, float, float]
) -> tuple[str | None, str | None, str]:
    feature = territory["features"][0]
    properties = feature.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    territory_id = _clean_string(
        properties.get("id")
        or properties.get("code")
        or properties.get("siren")
    )
    label = _clean_string(properties.get("libelle") or properties.get("nom"))
    payload = json.dumps(
        feature["geometry"], sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return territory_id, label, hashlib.sha256(payload).hexdigest()


def _territory_rings(geometry: dict[str, Any]) -> list[list[list[float]]]:
    coordinates = geometry.get("coordinates")
    if geometry.get("type") == "Polygon" and isinstance(coordinates, list):
        return [ring for ring in coordinates if isinstance(ring, list)]
    if geometry.get("type") == "MultiPolygon" and isinstance(coordinates, list):
        return [
            ring for polygon in coordinates if isinstance(polygon, list)
            for ring in polygon if isinstance(ring, list)
        ]
    return []


class _BoundaryIndex:
    def __init__(self, geometry: dict[str, Any], cell_size: float = 0.01):
        self.cell_size = cell_size
        self.segments: list[tuple[list[float], list[float]]] = []
        self.cells: dict[tuple[int, int], set[int]] = defaultdict(set)
        for ring in _territory_rings(geometry):
            for start, end in zip(ring, ring[1:]):
                if len(start) < 2 or len(end) < 2:
                    continue
                identifier = len(self.segments)
                self.segments.append((start, end))
                min_lon, max_lon = sorted((float(start[0]), float(end[0])))
                min_lat, max_lat = sorted((float(start[1]), float(end[1])))
                for x in range(
                    math.floor(min_lon / cell_size),
                    math.floor(max_lon / cell_size) + 1,
                ):
                    for y in range(
                        math.floor(min_lat / cell_size),
                        math.floor(max_lat / cell_size) + 1,
                    ):
                        self.cells[(x, y)].add(identifier)

    def near_segment(
        self, start: list[float], end: list[float]
    ) -> Iterable[tuple[list[float], list[float]]]:
        min_lon, max_lon = sorted((float(start[0]), float(end[0])))
        min_lat, max_lat = sorted((float(start[1]), float(end[1])))
        identifiers: set[int] = set()
        for x in range(
            math.floor(min_lon / self.cell_size),
            math.floor(max_lon / self.cell_size) + 1,
        ):
            for y in range(
                math.floor(min_lat / self.cell_size),
                math.floor(max_lat / self.cell_size) + 1,
            ):
                identifiers.update(self.cells.get((x, y), ()))
        return (self.segments[identifier] for identifier in identifiers)


class _PreparedTerritory:
    """Point-dans-polygone exact préparé pour les deux côtés des franchissements."""

    def __init__(self, geometry: dict[str, Any]):
        coordinates = geometry.get("coordinates")
        raw_polygons = (
            [coordinates]
            if geometry.get("type") == "Polygon" and isinstance(coordinates, list)
            else coordinates
            if geometry.get("type") == "MultiPolygon" and isinstance(coordinates, list)
            else []
        )
        self.polygons = [
            [
                [(float(point[0]), float(point[1])) for point in ring]
                for ring in polygon if isinstance(ring, list)
            ]
            for polygon in raw_polygons if isinstance(polygon, list)
        ]

    @staticmethod
    def _inside_ring(longitude: float, latitude: float, ring: list[tuple[float, float]]) -> bool:
        inside = False
        previous_x, previous_y = ring[-1]
        for current_x, current_y in ring:
            if (previous_y > latitude) != (current_y > latitude):
                intersection = (
                    (current_x - previous_x) * (latitude - previous_y)
                    / (current_y - previous_y) + previous_x
                )
                if longitude < intersection:
                    inside = not inside
            previous_x, previous_y = current_x, current_y
        return inside

    def contains(self, longitude: float, latitude: float) -> bool:
        for polygon in self.polygons:
            if not polygon or not self._inside_ring(longitude, latitude, polygon[0]):
                continue
            if not any(
                self._inside_ring(longitude, latitude, hole)
                for hole in polygon[1:]
            ):
                return True
        return False


def _segment_intersection_fraction(
    start: list[float],
    end: list[float],
    boundary_start: list[float],
    boundary_end: list[float],
) -> tuple[float | None, bool]:
    px, py = float(start[0]), float(start[1])
    rx, ry = float(end[0]) - px, float(end[1]) - py
    qx, qy = float(boundary_start[0]), float(boundary_start[1])
    sx = float(boundary_end[0]) - qx
    sy = float(boundary_end[1]) - qy
    cross_rs = rx * sy - ry * sx
    cross_qp_r = (qx - px) * ry - (qy - py) * rx
    epsilon = 1e-14
    if abs(cross_rs) <= epsilon:
        if abs(cross_qp_r) > epsilon:
            return None, False
        overlaps = not (
            max(px, px + rx) < min(qx, qx + sx)
            or max(qx, qx + sx) < min(px, px + rx)
            or max(py, py + ry) < min(qy, qy + sy)
            or max(qy, qy + sy) < min(py, py + ry)
        )
        return None, overlaps
    qpx, qpy = qx - px, qy - py
    factor = (qpx * sy - qpy * sx) / cross_rs
    boundary_factor = (qpx * ry - qpy * rx) / cross_rs
    if -1e-12 <= factor <= 1 + 1e-12 and -1e-12 <= boundary_factor <= 1 + 1e-12:
        return min(1.0, max(0.0, factor)), False
    return None, False


def boundary_events(
    edges: dict[str, TopageEdge],
    territory_geometry: dict[str, Any],
    *,
    territory_bounds: tuple[float, float, float, float] | None = None,
) -> tuple[list[BoundaryEvent], list[dict[str, Any]]]:
    """Classe les transitions de tronçons au travers du contour exact.

    Les lignes TOPAGE étant détaillées, chaque segment est échantillonné puis
    les transitions sont localisées par dichotomie. Une tangence ou un tronçon
    non dirigé reste un diagnostic ambigu au lieu de recevoir un sens inventé.
    """

    events: list[BoundaryEvent] = []
    ambiguous: list[dict[str, Any]] = []
    boundary_index = _BoundaryIndex(territory_geometry)
    prepared_territory = _PreparedTerritory(territory_geometry)
    for edge in edges.values():
        if territory_bounds is not None:
            edge_bounds = _geometry_bbox(edge.geometry)
            if edge_bounds is None:
                continue
            min_lon, min_lat, max_lon, max_lat = territory_bounds
            if (
                edge_bounds[2] < min_lon or edge_bounds[0] > max_lon
                or edge_bounds[3] < min_lat or edge_bounds[1] > max_lat
            ):
                continue
        line = _flow_line(edge)
        segment_lengths = [
            _segment_length_m(start, end)
            for start, end in zip(line, line[1:])
        ]
        total = sum(segment_lengths)
        if not total:
            continue
        crossing_fractions: list[float] = []
        travelled = 0.0
        collinear = False
        for (start, end), length in zip(zip(line, line[1:]), segment_lengths):
            for boundary_start, boundary_end in boundary_index.near_segment(start, end):
                local_fraction, overlaps = _segment_intersection_fraction(
                    start, end, boundary_start, boundary_end
                )
                collinear = collinear or overlaps
                if local_fraction is not None:
                    crossing_fractions.append(
                        (travelled + local_fraction * length) / total
                    )
            travelled += length
        unique_fractions = []
        for fraction in sorted(crossing_fractions):
            if not unique_fractions or abs(fraction - unique_fractions[-1]) > 1e-8:
                unique_fractions.append(fraction)
        transitions: list[tuple[float, tuple[float, float], bool, bool]] = []
        contact_count = 0
        for index, fraction in enumerate(unique_fractions):
            previous_fraction = unique_fractions[index - 1] if index else 0.0
            next_fraction = (
                unique_fractions[index + 1]
                if index + 1 < len(unique_fractions)
                else 1.0
            )
            # Reprendre l'échantillonnage local validé par le POC OGR : une
            # très petite distance le long de la géométrie, afin qu'un contact
            # ou un étroit méandre voisin ne devienne pas un franchissement.
            degree_length = sum(
                math.hypot(
                    float(end[0]) - float(start[0]),
                    float(end[1]) - float(start[1]),
                )
                for start, end in zip(line, line[1:])
            )
            epsilon_degrees = min(
                1e-6, max(degree_length * 1e-5, 1e-9)
            )
            desired_offset = epsilon_degrees / degree_length if degree_length else 0
            offset = min(
                desired_offset,
                (fraction - previous_fraction) / 3,
                (next_fraction - fraction) / 3,
            )
            if offset <= 1e-12:
                continue
            before_point = _point_at_degree_fraction(line, fraction - offset)
            after_point = _point_at_degree_fraction(line, fraction + offset)
            before = prepared_territory.contains(*before_point)
            after = prepared_territory.contains(*after_point)
            if before != after:
                transitions.append((
                    fraction, _point_at_fraction(line, fraction), before, after
                ))
            else:
                contact_count += 1

        if contact_count:
            ambiguous.append({
                "edge_code": edge.code,
                "reason": "contact sans transition extérieur/intérieur démontrée",
                "contact_count": contact_count,
                "topology_status": "ambiguous",
            })

        if not transitions:
            if collinear:
                ambiguous.append({
                    "edge_code": edge.code,
                    "reason": "tronçon superposé au contour administratif",
                    "topology_status": "ambiguous",
                })
            continue
        if edge.direction_status != "directed":
            ambiguous.append({
                "edge_code": edge.code,
                "reason": "franchissement sans sens d'écoulement univoque",
                "topology_status": "ambiguous",
            })
            continue
        for occurrence, (fraction, point, before, after) in enumerate(transitions):
            direction = "upstream" if not before and after else "downstream"
            events.append(BoundaryEvent(
                identifier=(
                    f"{edge.code}:{occurrence}:{direction}"
                ),
                edge_code=edge.code,
                flow_fraction=fraction,
                direction=direction,
                coordinate=point,
            ))
    events.sort(key=lambda item: item.identifier)
    return events, ambiguous


def _station_coordinates(item: dict[str, Any]) -> tuple[float, float] | None:
    longitude = _coordinate(item.get("longitude_station"), -180, 180)
    latitude = _coordinate(item.get("latitude_station"), -90, 90)
    if longitude is not None and latitude is not None:
        return longitude, latitude
    geometry = item.get("geometry")
    coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        return None
    longitude = _coordinate(coordinates[0], -180, 180)
    latitude = _coordinate(coordinates[1], -90, 90)
    if longitude is None or latitude is None:
        return None
    return longitude, latitude


def _site_coordinates(item: dict[str, Any]) -> tuple[float, float] | None:
    longitude = _coordinate(item.get("longitude_site"), -180, 180)
    latitude = _coordinate(item.get("latitude_site"), -90, 90)
    if longitude is not None and latitude is not None:
        return longitude, latitude
    geometry = item.get("geometry")
    coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        return None
    longitude = _coordinate(coordinates[0], -180, 180)
    latitude = _coordinate(coordinates[1], -90, 90)
    return (
        (longitude, latitude)
        if longitude is not None and latitude is not None
        else None
    )


def _station_metadata(
    item: dict[str, Any], activity: dict[str, dict[str, Any] | None]
) -> dict[str, Any] | None:
    code_station = _clean_string(item.get("code_station"))
    code_site = _clean_string(item.get("code_site"))
    coordinates = _station_coordinates(item)
    if not code_station or not code_site or coordinates is None:
        return None
    return {
        "code_station": code_station,
        "code_site": code_site,
        "name": _clean_string(item.get("libelle_station")) or code_station,
        "watercourse_name": _clean_string(item.get("libelle_cours_eau")),
        "status": "in_service" if item.get("en_service") is True else "inactive",
        "in_service": item.get("en_service") is True,
        "opened_at": _clean_string(item.get("date_ouverture_station")),
        "closed_at": _clean_string(item.get("date_fermeture_station")),
        "type": _clean_string(item.get("type_station")),
        "producer_network_codes": item.get("code_sandre_reseau_station") or [],
        "qualification_code": item.get("qualification_donnees_station"),
        "altimetric_reference": {
            "zero_altitude_native": _number(item.get("altitude_ref_alti_station")),
            "system_code": item.get("code_systeme_alti_site"),
            "valid_from": _clean_string(item.get("date_debut_ref_alti_station")),
            "activated_at": _clean_string(item.get("date_activation_ref_alti_station")),
        },
        "position": {
            "longitude": coordinates[0],
            "latitude": coordinates[1],
            "crs": "EPSG:4326",
        },
        "recent_quantities": [
            quantity
            for quantity in ("H", "Q")
            if activity.get(quantity) is not None
        ],
    }


def build_active_sites(
    station_rows: Iterable[dict[str, Any]],
    site_rows: Iterable[dict[str, Any]],
    activity: dict[str, dict[str, dict[str, Any] | None]],
    territory_geometry: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Déduplique par site tout en gardant chaque station source."""

    reference_sites = build_reference_sites(
        station_rows, site_rows, territory_geometry
    )
    return apply_activity_to_sites(reference_sites, activity)


def build_reference_sites(
    station_rows: Iterable[dict[str, Any]],
    site_rows: Iterable[dict[str, Any]],
    territory_geometry: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Construit les candidats de rattachement sans appliquer l'activité H/Q."""

    site_reference = {
        code: row
        for row in site_rows
        if isinstance(row, dict)
        if (code := _clean_string(row.get("code_site")))
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in station_rows:
        if not isinstance(row, dict):
            continue
        station = _station_metadata(row, {"H": None, "Q": None})
        if station is None:
            continue
        grouped[station["code_site"]].append(station)

    sites: dict[str, dict[str, Any]] = {}
    for code_site, stations in grouped.items():
        reference = site_reference.get(code_site, {})
        coordinates = _site_coordinates(reference)
        if coordinates is None:
            first_position = stations[0]["position"]
            coordinates = (
                first_position["longitude"], first_position["latitude"]
            )
        municipality_names = reference.get("libelle_commune")
        municipality_codes = reference.get("code_commune_site")
        municipality_name = (
            municipality_names[0]
            if isinstance(municipality_names, list) and municipality_names
            else _clean_string(municipality_names)
        )
        municipality_code = (
            municipality_codes[0]
            if isinstance(municipality_codes, list) and municipality_codes
            else _clean_string(municipality_codes)
        )
        sites[code_site] = {
            "code_site": code_site,
            "name": _clean_string(reference.get("libelle_site"))
            or stations[0]["name"],
            "watercourse": {
                "name": _clean_string(reference.get("libelle_cours_eau"))
                or next((
                    station["watercourse_name"] for station in stations
                    if station.get("watercourse_name")
                ), None),
                "official_ids": [
                    value
                    for value in (
                        _clean_string(reference.get("code_cours_eau")),
                        _clean_string(reference.get("code_entite_hydro_site")),
                        _clean_string(reference.get("code_troncon_hydro_site")),
                    )
                    if value
                ],
            },
            "position": {
                "longitude": coordinates[0],
                "latitude": coordinates[1],
                "crs": "EPSG:4326",
            },
            "municipality": {
                "code": municipality_code,
                "name": municipality_name,
            },
            "inside_territory": (
                point_in_territory(
                    coordinates[0], coordinates[1], territory_geometry
                )
                if territory_geometry is not None
                else False
            ),
            "stations": sorted(stations, key=lambda item: item["code_station"]),
        }
    return sites


def apply_activity_to_sites(
    reference_sites: dict[str, dict[str, Any]],
    activity: dict[str, dict[str, dict[str, Any] | None]],
) -> dict[str, dict[str, Any]]:
    """Filtre les candidats actifs sans perdre l'origine station des grandeurs."""

    active: dict[str, dict[str, Any]] = {}
    for code_site, site in reference_sites.items():
        stations = []
        for station in site.get("stations", []):
            if station.get("in_service") is not True:
                continue
            values = activity.get(
                station["code_station"], {"H": None, "Q": None}
            )
            quantities = [
                quantity for quantity in ("H", "Q")
                if values.get(quantity) is not None
            ]
            if quantities:
                stations.append({**station, "recent_quantities": quantities})
        if stations:
            active[code_site] = {**site, "stations": stations}
    return active


class _EdgeIndex:
    def __init__(self, edges: dict[str, TopageEdge], cell_size: float = 0.02):
        self.edges = edges
        self.cell_size = cell_size
        self.cells: dict[tuple[int, int], set[str]] = defaultdict(set)
        for edge in edges.values():
            for line in _iter_lines(edge.geometry):
                for start, end in zip(line, line[1:]):
                    min_lon, max_lon = sorted((float(start[0]), float(end[0])))
                    min_lat, max_lat = sorted((float(start[1]), float(end[1])))
                    for x in range(
                        math.floor(min_lon / cell_size),
                        math.floor(max_lon / cell_size) + 1,
                    ):
                        for y in range(
                            math.floor(min_lat / cell_size),
                            math.floor(max_lat / cell_size) + 1,
                        ):
                            self.cells[(x, y)].add(edge.code)

    def near(self, point: tuple[float, float]) -> list[TopageEdge]:
        x = math.floor(point[0] / self.cell_size)
        y = math.floor(point[1] / self.cell_size)
        codes: set[str] = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                codes.update(self.cells.get((x + dx, y + dy), ()))
        return [self.edges[code] for code in codes]


def attach_sites_to_edges(
    sites: dict[str, dict[str, Any]],
    edges: dict[str, TopageEdge],
    *,
    tolerance_m: float = ATTACHMENT_TOLERANCE_M,
) -> tuple[dict[str, list[SiteAttachment]], list[dict[str, Any]]]:
    index = _EdgeIndex(edges)
    attachments: dict[str, list[SiteAttachment]] = defaultdict(list)
    ambiguous: list[dict[str, Any]] = []
    for site in sites.values():
        for station in site.get("stations", []):
            position = station["position"]
            point = (position["longitude"], position["latitude"])
            candidates: list[tuple[float, str, float]] = []
            for edge in index.near(point):
                distance, fraction = _point_line_location(point, edge.geometry)
                if edge.flow_reversed:
                    fraction = 1.0 - fraction
                if distance <= tolerance_m:
                    candidates.append((distance, edge.code, fraction))
            candidates.sort()
            if not candidates:
                ambiguous.append({
                    "code_site": site["code_site"],
                    "code_station": station["code_station"],
                    "reason": f"aucun tronçon TOPAGE à {tolerance_m:g} m",
                    "selection_state": "pending_review",
                })
                continue
            nearest = candidates[0][0]
            plausible = [item for item in candidates if item[0] <= nearest + 2.0]
            if len(plausible) > 1:
                ambiguous.append({
                    "code_site": site["code_site"],
                    "code_station": station["code_station"],
                    "reason": "plusieurs tronçons TOPAGE spatialement équivalents",
                    "candidate_edges": [item[1] for item in plausible],
                    "nearest_distance_m": round(nearest, 2),
                    "selection_state": "pending_review",
                })
                continue
            for distance, edge_code, fraction in plausible:
                attachment = SiteAttachment(
                    site_code=site["code_site"],
                    edge_code=edge_code,
                    flow_fraction=fraction,
                    distance_m=distance,
                    station_code=station["code_station"],
                    inside_territory=site["inside_territory"],
                )
                if attachment not in attachments[edge_code]:
                    attachments[edge_code].append(attachment)
    for values in attachments.values():
        values.sort(key=lambda item: (item.flow_fraction, item.site_code))
    return attachments, ambiguous


def _edge_indexes(
    edges: dict[str, TopageEdge],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    incoming: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges.values():
        if edge.direction_status == "directed":
            incoming[edge.downstream_node].append(edge.code)
            outgoing[edge.upstream_node].append(edge.code)
        else:
            for node in {edge.upstream_node, edge.downstream_node}:
                incoming[node].append(edge.code)
                outgoing[node].append(edge.code)
    for values in (*incoming.values(), *outgoing.values()):
        values.sort()
    return incoming, outgoing


def _reachable_structural_edges(
    events: Iterable[BoundaryEvent],
    edges: dict[str, TopageEdge],
    *,
    max_edges: int = TOPAGE_MAX_TRAVERSED_EDGES,
) -> tuple[set[str], list[dict[str, Any]]]:
    """Isole le sous-graphe dirigé utile sans appliquer de STOP d'activité."""

    incoming, outgoing = _edge_indexes(edges)
    reachable: set[str] = set()
    diagnostics: list[dict[str, Any]] = []
    for event in events:
        queue = deque([event.edge_code])
        visited: set[str] = set()
        limit_reached = False
        while queue:
            edge_code = queue.popleft()
            if edge_code in visited or edge_code not in edges:
                continue
            visited.add(edge_code)
            reachable.add(edge_code)
            if len(visited) > max_edges:
                limit_reached = True
                break
            edge = edges[edge_code]
            if edge.direction_status != "directed":
                continue
            if event.direction == "upstream":
                next_edges = incoming.get(edge.upstream_node, [])
            else:
                next_edges = outgoing.get(edge.downstream_node, [])
            queue.extend(code for code in next_edges if code not in visited)
        if limit_reached:
            diagnostics.append({
                "crossing_event_id": event.identifier,
                "reason": "limite technique du sous-graphe structurel atteinte",
                "technical_limit_reached": True,
            })
            break
    return reachable, diagnostics


def _conditional_transition(
    node: str, first_edge: str, second_edge: str
) -> dict[str, Any] | None:
    pair = {first_edge, second_edge}
    for connection in CONDITIONAL_CONNECTIONS:
        if (
            connection["topage_node_id"] == node
            and pair == set(connection["topage_edge_ids"])
        ):
            return {
                **connection,
                "topage_edge_ids": list(connection["topage_edge_ids"]),
            }
    return None


def _candidate_attachments(
    attachments: dict[str, list[SiteAttachment]],
    edge_code: str,
    fraction: float,
    direction: str,
) -> list[SiteAttachment]:
    values = [
        attachment
        for attachment in attachments.get(edge_code, [])
        if not attachment.inside_territory
        and (
            attachment.flow_fraction < fraction
            if direction == "upstream"
            else attachment.flow_fraction > fraction
        )
    ]
    return sorted(
        values,
        key=lambda item: item.flow_fraction,
        reverse=direction == "upstream",
    )


def traverse_to_first_sites(
    event: BoundaryEvent,
    edges: dict[str, TopageEdge],
    attachments: dict[str, list[SiteAttachment]],
    *,
    extract_bbox: tuple[float, float, float, float] | None = None,
    max_edges: int = TOPAGE_MAX_TRAVERSED_EDGES,
    edge_indexes: tuple[dict[str, list[str]], dict[str, list[str]]] | None = None,
) -> TraversalResult:
    if event.direction not in {"upstream", "downstream"}:
        raise ValueError("direction de parcours invalide")
    incoming, outgoing = edge_indexes or _edge_indexes(edges)
    result = TraversalResult()
    queue = deque([(
        event.edge_code,
        event.flow_fraction,
        0.0,
        tuple(),
        frozenset(),
        tuple(),
    )])
    completed: set[tuple[str, str, tuple[str, ...]]] = set()
    while queue:
        (
            edge_code,
            start_fraction,
            distance,
            path,
            ancestors,
            conditional,
        ) = queue.popleft()
        if edge_code in ancestors:
            result.cycle_edges.add(edge_code)
            continue
        edge = edges[edge_code]
        if edge.direction_status != "directed":
            result.ambiguous_edges.add(edge_code)
            continue
        conditional_ids = tuple(item["connection_id"] for item in conditional)
        state = (edge_code, event.direction, conditional_ids)
        if state in completed and start_fraction in {0.0, 1.0}:
            continue
        completed.add(state)
        result.visited_edges.add(edge_code)
        if len(result.visited_edges) > max_edges:
            result.limit_reached = True
            break
        candidates = _candidate_attachments(
            attachments, edge_code, start_fraction, event.direction
        )
        if candidates:
            first_fraction = candidates[0].flow_fraction
            nearest = [
                item for item in candidates
                if abs(item.flow_fraction - first_fraction) <= 1e-9
            ]
            for attachment in nearest:
                path_codes = (*path, edge_code)
                path_hash = hashlib.sha256(
                    "\n".join(path_codes).encode("utf-8")
                ).hexdigest()
                result.selected.append(SelectedSite(
                    site_code=attachment.site_code,
                    distance_m=(
                        distance
                        + abs(start_fraction - attachment.flow_fraction)
                        * edge.length_m
                    ),
                    traversed_edges=len(path_codes),
                    path_fingerprint=path_hash,
                    conditional_connections=[dict(item) for item in conditional],
                    attachment=attachment,
                ))
            continue
        if event.direction == "upstream":
            travelled = start_fraction * edge.length_m
            node = edge.upstream_node
            next_edges = incoming.get(node, [])
            next_fraction = 1.0
        else:
            travelled = (1.0 - start_fraction) * edge.length_m
            node = edge.downstream_node
            next_edges = outgoing.get(node, [])
            next_fraction = 0.0
        if not next_edges:
            if extract_bbox is not None:
                endpoint = (
                    _flow_line(edge)[0]
                    if event.direction == "upstream"
                    else _flow_line(edge)[-1]
                )
                min_lon, min_lat, max_lon, max_lat = extract_bbox
                if (
                    abs(float(endpoint[0]) - min_lon) <= 0.01
                    or abs(float(endpoint[0]) - max_lon) <= 0.01
                    or abs(float(endpoint[1]) - min_lat) <= 0.01
                    or abs(float(endpoint[1]) - max_lat) <= 0.01
                ):
                    result.incomplete_nodes.add(node)
            continue
        next_path = (*path, edge_code)
        next_ancestors = ancestors | {edge_code}
        for next_edge in next_edges:
            if next_edge in next_ancestors:
                result.cycle_edges.add(next_edge)
                continue
            transition = _conditional_transition(node, edge_code, next_edge)
            next_conditional = (
                (*conditional, transition)
                if transition is not None
                and transition["connection_id"] not in conditional_ids
                else conditional
            )
            queue.append((
                next_edge,
                next_fraction,
                distance + travelled,
                next_path,
                next_ancestors,
                next_conditional,
            ))
    deduplicated: dict[tuple[str, bool], SelectedSite] = {}
    for selected in result.selected:
        key = (selected.site_code, bool(selected.conditional_connections))
        current = deduplicated.get(key)
        if current is None or selected.distance_m < current.distance_m:
            deduplicated[key] = selected
    result.selected = sorted(
        deduplicated.values(),
        key=lambda item: (item.distance_m, item.site_code),
    )
    return result


def _discovery_identity(
    territory: dict[str, Any], reference_date: date
) -> dict[str, Any]:
    bbox = territory_bbox(territory)
    territory_id, _, fingerprint = _territory_identity(territory, bbox)
    return {
        "territory_id": territory_id,
        "territory_fingerprint": fingerprint,
        "topage_source": TOPAGE_WFS_URL,
        "topage_version": TOPAGE_VERSION,
        "algorithm_version": DISCOVERY_ALGORITHM_VERSION,
        "activity_policy_id": ACTIVITY_POLICY_ID,
        "reference_date": reference_date.isoformat(),
        "structural_cache_key": _structural_cache_key(territory),
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _discovery_cache_key(
    territory: dict[str, Any], reference_date: date
) -> str:
    identity = _canonical_json(_discovery_identity(territory, reference_date))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _structural_identity(territory: dict[str, Any]) -> dict[str, Any]:
    bbox = territory_bbox(territory)
    territory_id, _, fingerprint = _territory_identity(territory, bbox)
    return {
        "territory_id": territory_id,
        "territory_fingerprint": fingerprint,
        "topage_source": TOPAGE_WFS_URL,
        "topage_version": TOPAGE_VERSION,
        "topology_algorithm_version": TOPOLOGY_ALGORITHM_VERSION,
        "attachment_policy_id": ATTACHMENT_POLICY_ID,
    }


def _structural_cache_key(territory: dict[str, Any]) -> str:
    identity = _canonical_json(_structural_identity(territory))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _structural_cache_key_from_identity(identity: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()


def _reference_attachment_fingerprint(
    sites: dict[str, dict[str, Any]],
) -> str:
    projection = [
        {
            "code_site": site["code_site"],
            "position": site["position"],
            "stations": [
                {
                    "code_station": station["code_station"],
                    "position": station["position"],
                }
                for station in site.get("stations", [])
            ],
        }
        for site in sorted(sites.values(), key=lambda item: item["code_site"])
    ]
    return hashlib.sha256(_canonical_json(projection).encode("utf-8")).hexdigest()


def _serialize_edge(edge: TopageEdge) -> dict[str, Any]:
    line = _flow_line(edge)
    return {
        "code": edge.code,
        "upstream_node": edge.upstream_node,
        "downstream_node": edge.downstream_node,
        "length_m": edge.length_m,
        "upstream_coordinate": list(line[0]),
        "downstream_coordinate": list(line[-1]),
        "course_codes": list(edge.course_codes),
        "name": edge.name,
        "nature": edge.nature,
        "status": edge.status,
        "direction_status": edge.direction_status,
    }


def _deserialize_edges(values: Any) -> dict[str, TopageEdge]:
    if not isinstance(values, list):
        raise PersistentDiscoveryPayloadError("Tronçons structurels invalides.")
    edges: dict[str, TopageEdge] = {}
    for value in values:
        if not isinstance(value, dict):
            raise PersistentDiscoveryPayloadError("Tronçon structurel invalide.")
        upstream_coordinate = value.get("upstream_coordinate")
        downstream_coordinate = value.get("downstream_coordinate")
        if not (
            isinstance(upstream_coordinate, list)
            and len(upstream_coordinate) >= 2
            and isinstance(downstream_coordinate, list)
            and len(downstream_coordinate) >= 2
        ):
            raise PersistentDiscoveryPayloadError(
                "Coordonnées structurelles invalides."
            )
        edge = TopageEdge(
            code=str(value["code"]),
            upstream_node=str(value["upstream_node"]),
            downstream_node=str(value["downstream_node"]),
            length_m=float(value["length_m"]),
            geometry={
                "type": "LineString",
                "coordinates": [upstream_coordinate, downstream_coordinate],
            },
            course_codes=tuple(value.get("course_codes") or ()),
            name=value.get("name"),
            nature=value.get("nature"),
            status=value.get("status"),
            direction_status=str(value.get("direction_status") or "ambiguous"),
            flow_reversed=False,
        )
        edges[edge.code] = edge
    return edges


def _serialize_attachments(
    attachments: dict[str, list[SiteAttachment]],
) -> list[dict[str, Any]]:
    return [
        {
            "site_code": attachment.site_code,
            "edge_code": attachment.edge_code,
            "flow_fraction": attachment.flow_fraction,
            "distance_m": attachment.distance_m,
            "station_code": attachment.station_code,
            "inside_territory": attachment.inside_territory,
        }
        for edge_code in sorted(attachments)
        for attachment in attachments[edge_code]
    ]


def _deserialize_attachments(values: Any) -> dict[str, list[SiteAttachment]]:
    if not isinstance(values, list):
        raise PersistentDiscoveryPayloadError("Rattachements structurels invalides.")
    attachments: dict[str, list[SiteAttachment]] = defaultdict(list)
    for value in values:
        if not isinstance(value, dict):
            raise PersistentDiscoveryPayloadError("Rattachement structurel invalide.")
        attachment = SiteAttachment(
            site_code=str(value["site_code"]),
            edge_code=str(value["edge_code"]),
            flow_fraction=float(value["flow_fraction"]),
            distance_m=float(value["distance_m"]),
            station_code=_clean_string(value.get("station_code")),
            inside_territory=value.get("inside_territory") is True,
        )
        attachments[attachment.edge_code].append(attachment)
    return attachments


def _serialize_events(events: Iterable[BoundaryEvent]) -> list[dict[str, Any]]:
    return [
        {
            "identifier": event.identifier,
            "edge_code": event.edge_code,
            "flow_fraction": event.flow_fraction,
            "direction": event.direction,
            "coordinate": list(event.coordinate),
        }
        for event in events
    ]


def _deserialize_events(values: Any) -> list[BoundaryEvent]:
    if not isinstance(values, list):
        raise PersistentDiscoveryPayloadError("Franchissements structurels invalides.")
    return [
        BoundaryEvent(
            identifier=str(value["identifier"]),
            edge_code=str(value["edge_code"]),
            flow_fraction=float(value["flow_fraction"]),
            direction=str(value["direction"]),
            coordinate=(float(value["coordinate"][0]), float(value["coordinate"][1])),
        )
        for value in values
        if isinstance(value, dict)
    ]


def _cache_day(day_provider: Any = None) -> date:
    value = day_provider() if day_provider else datetime.now(timezone.utc)
    if isinstance(value, datetime):
        value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).date()
    if isinstance(value, date):
        return value
    raise TypeError("Le fournisseur de date doit retourner date ou datetime.")


class PersistentDiscoveryPayloadError(ValueError):
    """Une entrée persistante ne respecte pas le contrat sérialisé courant."""


class DiscoveryCacheLockTimeout(TimeoutError):
    """Un autre processus construit encore cette identité de discovery."""


def discovery_completeness(payload: dict[str, Any]) -> str:
    """Qualifie l'exhaustivité sans confondre revue humaine et panne fournisseur."""

    source_extract = payload.get("source_extract")
    if not isinstance(source_extract, dict) or (
        source_extract.get("complete_for_traversed_branches") is not True
    ):
        return "partial"
    diagnostics = payload.get("diagnostics")
    if isinstance(diagnostics, dict) and diagnostics.get("activity"):
        return "degraded"
    return "complete"


def _validate_discovery_relations(payload: dict[str, Any]) -> None:
    for site in payload.get("sites", []):
        if not isinstance(site, dict) or not site.get("code_site"):
            raise PersistentDiscoveryPayloadError("Site discovery invalide.")
        seen: set[tuple[str | None, str, str]] = set()
        relations = site.get("territory_relations")
        if not isinstance(relations, list):
            raise PersistentDiscoveryPayloadError("Relations territoire absentes.")
        for relation in relations:
            if not isinstance(relation, dict):
                raise PersistentDiscoveryPayloadError("Relation territoire invalide.")
            territory = relation.get("territory")
            role = relation.get("role")
            if not isinstance(territory, dict) or role not in {
                "inside", "upstream", "downstream",
            }:
                raise PersistentDiscoveryPayloadError("Clé de relation invalide.")
            key = (territory.get("id"), territory.get("fingerprint"), role)
            if not key[1] or key in seen:
                raise PersistentDiscoveryPayloadError(
                    "Relation territoire non normalisée."
                )
            seen.add(key)
            proofs = relation.get("proofs")
            summary = relation.get("proof_summary")
            if not isinstance(proofs, list) or not proofs or not isinstance(
                summary, dict
            ):
                raise PersistentDiscoveryPayloadError("Preuves relation absentes.")
            if any(not isinstance(proof, dict) for proof in proofs) or len(proofs) != len({
                _canonical_json(proof) for proof in proofs
            }):
                raise PersistentDiscoveryPayloadError("Preuves dupliquées ou invalides.")
            selected_count = sum(
                proof.get("selection_state") == "selected" for proof in proofs
            )
            pending_count = sum(
                proof.get("selection_state") == "pending_review" for proof in proofs
            )
            conditional_count = sum(
                proof.get("hydraulic_continuity") == "conditional"
                for proof in proofs
            )
            proof_connection_ids = sorted({
                connection_id
                for proof in proofs
                for connection_id in proof.get("conditional_connection_ids", [])
                if isinstance(connection_id, str) and connection_id
            })
            connections = relation.get("conditional_connections")
            if not isinstance(connections, list) or any(
                not isinstance(connection, dict)
                or not connection.get("connection_id")
                for connection in connections
            ):
                raise PersistentDiscoveryPayloadError(
                    "Registre de connexions conditionnelles invalide."
                )
            registered_connection_ids = sorted({
                connection["connection_id"] for connection in connections
            })
            if (
                selected_count + pending_count != len(proofs)
                or summary.get("count") != len(proofs)
                or summary.get("selected_count") != selected_count
                or summary.get("pending_review_count") != pending_count
                or summary.get("conditional_count") != conditional_count
                or summary.get("conditional_connection_ids")
                != proof_connection_ids
                or registered_connection_ids != proof_connection_ids
                or len(registered_connection_ids) != len(connections)
                or relation.get("selection", {}).get("state")
                != ("selected" if selected_count else "pending_review")
            ):
                raise PersistentDiscoveryPayloadError(
                    "Synthèse de preuves incohérente."
                )


def serialize_discovery_cache_payload(payload: dict[str, Any]) -> str:
    """Produit une enveloppe JSON déterministe, sans objet Python opaque."""

    identity = payload.get("identity")
    if not isinstance(identity, dict):
        raise PersistentDiscoveryPayloadError("Identité discovery absente.")
    if payload.get("kind") != "hydrometric_discovery":
        raise PersistentDiscoveryPayloadError("Type de dataset discovery invalide.")
    if not isinstance(payload.get("sites"), list) or not isinstance(
        payload.get("pending_review"), list
    ):
        raise PersistentDiscoveryPayloadError("Structure discovery invalide.")
    _validate_discovery_relations(payload)
    return _canonical_json({
        "schema_version": DISCOVERY_CACHE_PAYLOAD_SCHEMA_VERSION,
        "identity": identity,
        "dataset": payload,
    })


def deserialize_discovery_cache_payload(
    value: Any,
    expected_identity: dict[str, Any],
) -> dict[str, Any]:
    """Valide strictement l'enveloppe persistée avant toute réutilisation."""

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise PersistentDiscoveryPayloadError("JSON discovery invalide.") from exc
    if not isinstance(value, dict):
        raise PersistentDiscoveryPayloadError("Payload discovery non objet.")
    if value.get("schema_version") != DISCOVERY_CACHE_PAYLOAD_SCHEMA_VERSION:
        raise PersistentDiscoveryPayloadError("Version de payload discovery inconnue.")
    if value.get("identity") != expected_identity:
        raise PersistentDiscoveryPayloadError("Identité persistée incohérente.")
    dataset = value.get("dataset")
    if not isinstance(dataset, dict) or dataset.get("identity") != expected_identity:
        raise PersistentDiscoveryPayloadError("Dataset persisté incohérent.")
    # Réutilise les validations structurelles et JSON du sérialiseur.
    serialize_discovery_cache_payload(dataset)
    return dataset


def discovery_payload_size_bytes(payload: dict[str, Any]) -> int:
    return len(serialize_discovery_cache_payload(payload).encode("utf-8"))


def _read_persistent_discovery(
    connection: Any,
    cache_key: str,
    expected_identity: dict[str, Any],
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT payload_schema_version, completeness, payload "
        "FROM technique.cache_decouverte_hydrometrie WHERE cache_key = %s",
        (cache_key,),
    ).fetchone()
    if row is None:
        return None
    schema_version, completeness, payload = row
    if schema_version != DISCOVERY_CACHE_PAYLOAD_SCHEMA_VERSION:
        raise PersistentDiscoveryPayloadError("Version SQL discovery inconnue.")
    if completeness not in {"complete", "partial", "degraded"}:
        raise PersistentDiscoveryPayloadError("État discovery persistant inconnu.")
    dataset = deserialize_discovery_cache_payload(payload, expected_identity)
    if discovery_completeness(dataset) != completeness:
        raise PersistentDiscoveryPayloadError(
            "État discovery persistant incohérent avec son payload."
        )
    return dataset


def _write_persistent_discovery(
    connection: Any,
    cache_key: str,
    payload: dict[str, Any],
) -> bool:
    completeness = discovery_completeness(payload)
    identity = payload["identity"]
    serialized = serialize_discovery_cache_payload(payload)
    cursor = connection.execute(
        "INSERT INTO technique.cache_decouverte_hydrometrie ("
        "cache_key, territory_id, territory_fingerprint, topage_source, "
        "topage_version, algorithm_version, activity_policy_id, reference_date, "
        "payload_schema_version, completeness, payload) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
        "ON CONFLICT (cache_key) DO UPDATE SET "
        "territory_id = EXCLUDED.territory_id, "
        "territory_fingerprint = EXCLUDED.territory_fingerprint, "
        "topage_source = EXCLUDED.topage_source, "
        "topage_version = EXCLUDED.topage_version, "
        "algorithm_version = EXCLUDED.algorithm_version, "
        "activity_policy_id = EXCLUDED.activity_policy_id, "
        "reference_date = EXCLUDED.reference_date, "
        "payload_schema_version = EXCLUDED.payload_schema_version, "
        "completeness = EXCLUDED.completeness, payload = EXCLUDED.payload, "
        "created_at = clock_timestamp() "
        "WHERE cache_decouverte_hydrometrie.payload_schema_version "
        "<> EXCLUDED.payload_schema_version "
        "OR cache_decouverte_hydrometrie.completeness <> 'complete' "
        "OR EXCLUDED.completeness = 'complete'",
        (
            cache_key,
            identity.get("territory_id"),
            identity["territory_fingerprint"],
            identity["topage_source"],
            identity["topage_version"],
            identity["algorithm_version"],
            identity["activity_policy_id"],
            identity["reference_date"],
            DISCOVERY_CACHE_PAYLOAD_SCHEMA_VERSION,
            completeness,
            serialized,
        ),
    )
    return cursor.rowcount != 0


def structural_completeness(payload: dict[str, Any]) -> str:
    source_extract = payload.get("source_extract")
    if not isinstance(source_extract, dict) or (
        source_extract.get("complete_for_traversed_branches") is not True
    ):
        return "partial"
    diagnostics = payload.get("diagnostics")
    if isinstance(diagnostics, dict) and diagnostics.get("reference"):
        return "degraded"
    return "complete"


def serialize_structural_cache_payload(payload: dict[str, Any]) -> str:
    identity = payload.get("identity")
    if not isinstance(identity, dict):
        raise PersistentDiscoveryPayloadError("Identité structurelle absente.")
    if payload.get("kind") != "hydrometric_structural_discovery":
        raise PersistentDiscoveryPayloadError("Type structurel invalide.")
    if (
        not isinstance(payload.get("reference_fingerprint"), str)
        or not isinstance(payload.get("candidate_sites"), list)
        or not isinstance(payload.get("edges"), list)
        or not isinstance(payload.get("boundary_events"), list)
        or not isinstance(payload.get("attachments"), list)
    ):
        raise PersistentDiscoveryPayloadError("Structure TOPAGE invalide.")
    return _canonical_json({
        "schema_version": STRUCTURAL_CACHE_PAYLOAD_SCHEMA_VERSION,
        "identity": identity,
        "dataset": payload,
    })


def deserialize_structural_cache_payload(
    value: Any,
    expected_identity: dict[str, Any],
) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise PersistentDiscoveryPayloadError("JSON structurel invalide.") from exc
    if not isinstance(value, dict):
        raise PersistentDiscoveryPayloadError("Payload structurel non objet.")
    if value.get("schema_version") != STRUCTURAL_CACHE_PAYLOAD_SCHEMA_VERSION:
        raise PersistentDiscoveryPayloadError("Version structurelle inconnue.")
    if value.get("identity") != expected_identity:
        raise PersistentDiscoveryPayloadError("Identité structurelle incohérente.")
    dataset = value.get("dataset")
    if not isinstance(dataset, dict) or dataset.get("identity") != expected_identity:
        raise PersistentDiscoveryPayloadError("Dataset structurel incohérent.")
    if (
        dataset.get("kind") != "hydrometric_structural_discovery"
        or not isinstance(dataset.get("reference_fingerprint"), str)
        or not isinstance(dataset.get("candidate_sites"), list)
        or not isinstance(dataset.get("edges"), list)
        or not isinstance(dataset.get("boundary_events"), list)
        or not isinstance(dataset.get("attachments"), list)
    ):
        raise PersistentDiscoveryPayloadError("Structure TOPAGE invalide.")
    _deserialize_edges(dataset["edges"])
    _deserialize_events(dataset["boundary_events"])
    _deserialize_attachments(dataset.get("attachments"))
    return dataset


def _read_persistent_structure(
    connection: Any,
    cache_key: str,
    expected_identity: dict[str, Any],
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT payload_schema_version, completeness, payload "
        "FROM technique.cache_structure_hydrometrie WHERE cache_key = %s",
        (cache_key,),
    ).fetchone()
    if row is None:
        return None
    schema_version, completeness, payload = row
    if schema_version != STRUCTURAL_CACHE_PAYLOAD_SCHEMA_VERSION:
        raise PersistentDiscoveryPayloadError("Version SQL structurelle inconnue.")
    if completeness not in {"complete", "partial", "degraded"}:
        raise PersistentDiscoveryPayloadError("État structurel inconnu.")
    dataset = deserialize_structural_cache_payload(payload, expected_identity)
    if structural_completeness(dataset) != completeness:
        raise PersistentDiscoveryPayloadError("État structurel incohérent.")
    return dataset


def _write_persistent_structure(
    connection: Any,
    cache_key: str,
    payload: dict[str, Any],
) -> bool:
    completeness = structural_completeness(payload)
    identity = payload["identity"]
    cursor = connection.execute(
        "INSERT INTO technique.cache_structure_hydrometrie ("
        "cache_key, territory_id, territory_fingerprint, topage_source, "
        "topage_version, topology_algorithm_version, attachment_policy_id, "
        "reference_fingerprint, payload_schema_version, completeness, payload) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
        "ON CONFLICT (cache_key) DO UPDATE SET "
        "territory_id = EXCLUDED.territory_id, "
        "territory_fingerprint = EXCLUDED.territory_fingerprint, "
        "topage_source = EXCLUDED.topage_source, "
        "topage_version = EXCLUDED.topage_version, "
        "topology_algorithm_version = EXCLUDED.topology_algorithm_version, "
        "attachment_policy_id = EXCLUDED.attachment_policy_id, "
        "reference_fingerprint = EXCLUDED.reference_fingerprint, "
        "payload_schema_version = EXCLUDED.payload_schema_version, "
        "completeness = EXCLUDED.completeness, payload = EXCLUDED.payload, "
        "created_at = clock_timestamp() "
        "WHERE cache_structure_hydrometrie.completeness <> 'complete' "
        "OR EXCLUDED.completeness = 'complete'",
        (
            cache_key,
            identity.get("territory_id"),
            identity["territory_fingerprint"],
            identity["topage_source"],
            identity["topage_version"],
            identity["topology_algorithm_version"],
            identity["attachment_policy_id"],
            payload["reference_fingerprint"],
            STRUCTURAL_CACHE_PAYLOAD_SCHEMA_VERSION,
            completeness,
            serialize_structural_cache_payload(payload),
        ),
    )
    return cursor.rowcount != 0


def _acquire_discovery_lock(
    connection: Any,
    cache_key: str,
    *,
    timeout_seconds: float = DISCOVERY_CACHE_LOCK_TIMEOUT_SECONDS,
    clock: Any = monotonic,
    sleeper: Any = sleep,
) -> None:
    deadline = clock() + timeout_seconds
    while True:
        row = connection.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s, %s))",
            (cache_key, DISCOVERY_CACHE_LOCK_SEED),
        ).fetchone()
        if row == (True,):
            return
        if clock() >= deadline:
            raise DiscoveryCacheLockTimeout(
                "Un discovery hydrométrique identique est encore en construction."
            )
        sleeper(DISCOVERY_CACHE_LOCK_POLL_SECONDS)


def _release_discovery_lock(connection: Any, cache_key: str) -> None:
    connection.execute(
        "SELECT pg_advisory_unlock(hashtextextended(%s, %s))",
        (cache_key, DISCOVERY_CACHE_LOCK_SEED),
    ).fetchone()


def _relation(
    *,
    event: BoundaryEvent | None,
    selected: SelectedSite | None,
    role: str,
    site_attachment: SiteAttachment | None,
    territory_id: str | None,
    fingerprint: str,
    calculated_at: str,
) -> dict[str, Any]:
    conditional = selected.conditional_connections if selected else []
    effective_attachment = (
        selected.attachment
        if selected is not None and selected.attachment is not None
        else site_attachment
    )
    return {
        "territory": {"id": territory_id, "fingerprint": fingerprint},
        "role": role,
        "selection": {
            "method": (
                "inside_exact_polygon"
                if role == "inside"
                else f"first_active_{role}"
            ),
            "state": "pending_review" if conditional else "selected",
            "activity_policy_id": ACTIVITY_POLICY_ID,
            "calculated_at": calculated_at,
        },
        "network_evidence": {
            "source": "BD TOPAGE",
            "dataset_version": TOPAGE_VERSION,
            "discovery_algorithm_version": DISCOVERY_ALGORITHM_VERSION,
            "direction": None if role == "inside" else role,
            "topology_status": "not_applicable" if role == "inside" else "directed",
            "hydraulic_continuity": (
                "not_assessed" if not conditional else "conditional"
            ),
            "crossing": None if event is None else {
                "crossing_event_id": event.identifier,
                "reference_edge_id": event.edge_code,
                "coordinate": list(event.coordinate),
            },
            "site_attachment": None if effective_attachment is None else {
                "reference_edge_id": effective_attachment.edge_code,
                "method": "spatial_with_explicit_tolerance",
                "distance_m": round(effective_attachment.distance_m, 2),
                "tolerance_m": ATTACHMENT_TOLERANCE_M,
                "station_code": effective_attachment.station_code,
            },
            "traversed_edge_count": selected.traversed_edges if selected else 0,
            "hydrographic_distance_m": (
                round(selected.distance_m, 2) if selected else 0.0
            ),
            "path_fingerprint": selected.path_fingerprint if selected else None,
            "conditional_connections": conditional,
        },
    }


def _relation_proof(relation: dict[str, Any]) -> dict[str, Any]:
    """Détache la preuve propre à un chemin de la relation métier."""

    selection = relation["selection"]
    evidence = relation["network_evidence"]
    proof_payload = {
        "selection_state": selection["state"],
        "direction": evidence.get("direction"),
        "topology_status": evidence["topology_status"],
        "hydraulic_continuity": evidence["hydraulic_continuity"],
        "crossing": evidence.get("crossing"),
        "site_attachment": evidence.get("site_attachment"),
        "traversed_edge_count": evidence.get("traversed_edge_count", 0),
        "hydrographic_distance_m": evidence.get("hydrographic_distance_m", 0.0),
        "path_fingerprint": evidence.get("path_fingerprint"),
        "conditional_connection_ids": sorted({
            connection["connection_id"]
            for connection in evidence.get("conditional_connections", [])
            if connection.get("connection_id")
        }),
    }
    return proof_payload


def _aggregate_topology_status(
    role: str,
    proofs: list[dict[str, Any]],
) -> str:
    if role == "inside":
        return "not_applicable"
    selected = [proof for proof in proofs if proof["selection_state"] == "selected"]
    relevant = selected or proofs
    if any(proof["topology_status"] == "directed" for proof in relevant):
        return "directed"
    return "ambiguous"


def _aggregate_hydraulic_continuity(
    role: str,
    proofs: list[dict[str, Any]],
) -> str:
    if role == "inside":
        return "not_assessed"
    selected = [proof for proof in proofs if proof["selection_state"] == "selected"]
    relevant = selected or proofs
    statuses = {proof["hydraulic_continuity"] for proof in relevant}
    # L'état synthétique décrit une preuve suffisante indépendante, pas le
    # meilleur ou le pire état de tous les chemins. Une relation n'est dite
    # conditionnelle que lorsque toutes les preuves pertinentes le sont ; les
    # états exacts restent portés par chaque proof.
    if "supported" in statuses:
        return "supported"
    if "not_assessed" in statuses:
        return "not_assessed"
    if statuses == {"conditional"}:
        return "conditional"
    return "unknown"


def normalize_territory_relations(
    code_site: str,
    relations: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Produit une relation métier unique par territoire, site et rôle."""

    grouped: dict[tuple[str | None, str, str], list[dict[str, Any]]] = defaultdict(list)
    for relation in relations:
        territory = relation["territory"]
        grouped[(
            territory.get("id"),
            territory["fingerprint"],
            relation["role"],
        )].append(relation)

    normalized = []
    role_order = {"inside": 0, "upstream": 1, "downstream": 2}
    for (territory_id, fingerprint, role), raw_relations in sorted(
        grouped.items(),
        key=lambda item: (
            role_order.get(item[0][2], 99),
            item[0][0] or "",
            item[0][1],
        ),
    ):
        unique_proofs = {
            hashlib.sha256(_canonical_json({
                "code_site": code_site,
                "role": role,
                "proof": proof,
            }).encode("utf-8")).hexdigest(): proof
            for proof in (
                _relation_proof(relation)
                for relation in raw_relations
            )
        }
        proofs = [unique_proofs[key] for key in sorted(unique_proofs)]
        selected_count = sum(
            proof["selection_state"] == "selected" for proof in proofs
        )
        pending_count = len(proofs) - selected_count
        conditional_count = sum(
            proof["hydraulic_continuity"] == "conditional" for proof in proofs
        )
        conditional_connections: dict[str, dict[str, Any]] = {}
        for relation in raw_relations:
            for connection in relation["network_evidence"].get(
                "conditional_connections", []
            ):
                connection_id = connection.get("connection_id")
                if not connection_id:
                    continue
                previous = conditional_connections.get(connection_id)
                if previous is not None and previous != connection:
                    raise HydrometryDataError(
                        "Qualifications conditionnelles incohérentes pour "
                        f"{connection_id}."
                    )
                conditional_connections[connection_id] = connection
        connection_ids = sorted(conditional_connections)
        first = raw_relations[0]
        evidence = first["network_evidence"]
        calculated_at = max(
            relation["selection"]["calculated_at"] for relation in raw_relations
        )
        normalized.append({
            "territory": {"id": territory_id, "fingerprint": fingerprint},
            "role": role,
            "selection": {
                "method": first["selection"]["method"],
                "state": "selected" if selected_count else "pending_review",
                "activity_policy_id": first["selection"]["activity_policy_id"],
                "calculated_at": calculated_at,
            },
            "topology_status": _aggregate_topology_status(role, proofs),
            "hydraulic_continuity": _aggregate_hydraulic_continuity(role, proofs),
            "proof_summary": {
                "count": len(proofs),
                "selected_count": selected_count,
                "pending_review_count": pending_count,
                "conditional_count": conditional_count,
                "conditional_connection_ids": connection_ids,
            },
            "proofs": proofs,
            "conditional_connections": [
                conditional_connections[key] for key in connection_ids
            ],
            "provenance": {
                "source": (
                    "territory_exact_geometry"
                    if role == "inside"
                    else evidence.get("source")
                ),
                "dataset_version": (
                    None if role == "inside" else evidence.get("dataset_version")
                ),
                "discovery_algorithm_version": evidence.get(
                    "discovery_algorithm_version"
                ),
            },
        })
    return normalized


def _finalize_territory_relations(
    active_sites: dict[str, dict[str, Any]],
    relations: dict[str, list[dict[str, Any]]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, set[str]],
    dict[str, int],
]:
    candidate_sites = []
    for code_site, site_relations in sorted(relations.items()):
        site = dict(active_sites[code_site])
        site.pop("inside_territory", None)
        site["territory_relations"] = normalize_territory_relations(
            code_site, site_relations
        )
        candidate_sites.append(site)

    relation_reviews = []
    pending_proof_count = 0
    pending_relation_count = 0
    mixed_relation_count = 0
    pending_sites: set[str] = set()
    relation_count = 0
    proof_count = 0
    for site in candidate_sites:
        for relation in site["territory_relations"]:
            relation_count += 1
            summary = relation["proof_summary"]
            proof_count += summary["count"]
            pending_proof_count += summary["pending_review_count"]
            if not summary["pending_review_count"]:
                continue
            pending_sites.add(site["code_site"])
            relation_pending = relation["selection"]["state"] == "pending_review"
            pending_relation_count += int(relation_pending)
            mixed_relation_count += int(not relation_pending)
            relation_reviews.append({
                "scope": "relation" if relation_pending else "proofs",
                "code_site": site["code_site"],
                "territory": relation["territory"],
                "role": relation["role"],
                "relation_state": relation["selection"]["state"],
                "pending_proofs": [
                    {
                        "crossing_event_id": (
                            proof.get("crossing") or {}
                        ).get("crossing_event_id"),
                        "path_fingerprint": proof.get("path_fingerprint"),
                    }
                    for proof in relation["proofs"]
                    if proof["selection_state"] == "pending_review"
                ],
                "reason": (
                    "aucune preuve suffisamment établie"
                    if relation_pending
                    else "preuve supplémentaire à examiner ; relation établie indépendamment"
                ),
                **({"relation": relation} if relation_pending else {}),
            })

    selected_sites = [
        site for site in candidate_sites
        if any(
            relation["selection"]["state"] == "selected"
            for relation in site["territory_relations"]
        )
    ]
    roles: dict[str, set[str]] = defaultdict(set)
    for site in selected_sites:
        for relation in site["territory_relations"]:
            if relation["selection"]["state"] == "selected":
                roles[relation["role"]].add(site["code_site"])
    selected_relation_count = sum(
        len(site["territory_relations"]) for site in selected_sites
    )
    selected_proof_count = sum(
        relation["proof_summary"]["count"]
        for site in selected_sites
        for relation in site["territory_relations"]
    )
    statistics = {
        "territory_relations": selected_relation_count,
        "territory_proofs": selected_proof_count,
        "candidate_territory_relations": relation_count,
        "candidate_territory_proofs": proof_count,
        "pending_review_proofs": pending_proof_count,
        "pending_review_relations": pending_relation_count,
        "mixed_review_relations": mixed_relation_count,
        "pending_review_sites": len(pending_sites),
    }
    return candidate_sites, selected_sites, relation_reviews, roles, statistics


def build_structural_dataset(
    territory: dict[str, Any],
    client: HydrometryClient,
    topage_client: TopageClient,
    *,
    now: datetime | None = None,
    topage_padding: float = TOPAGE_INITIAL_PADDING_DEGREES,
    expansion_attempt: int = 0,
    station_rows: list[dict[str, Any]] | None = None,
    site_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Prépare le sous-graphe et les rattachements, sans activité H/Q."""

    built_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    bbox = territory_bbox(territory)
    territory_geometry = _territory_geometry(territory)
    territory_id, territory_label, fingerprint = _territory_identity(territory, bbox)
    extract_bbox = _expanded_bbox(bbox, topage_padding)
    features = topage_client.features(extract_bbox)
    edges = parse_topage_edges(features)
    if not edges:
        raise HydrometryDataError("Aucun tronçon BD TOPAGE exploitable sur l'emprise.")
    current_station_rows = (
        station_rows
        if station_rows is not None
        else client.reference_stations(extract_bbox)
    )
    current_site_rows = (
        site_rows if site_rows is not None else client.reference_sites(extract_bbox)
    )
    reference_sites = build_reference_sites(
        current_station_rows, current_site_rows, territory_geometry
    )
    attachments, attachment_diagnostics = attach_sites_to_edges(
        reference_sites, edges
    )
    events, boundary_diagnostics = boundary_events(
        edges, territory_geometry, territory_bounds=bbox
    )
    reachable_codes, structural_diagnostics = _reachable_structural_edges(
        events, edges
    )
    structural_edges = {
        code: edges[code] for code in sorted(reachable_codes)
    }
    structural_attachments = {
        code: attachments[code]
        for code in sorted(reachable_codes)
        if code in attachments
    }
    retained_site_codes = {
        attachment.site_code
        for values in structural_attachments.values()
        for attachment in values
    } | {
        code for code, site in reference_sites.items()
        if site["inside_territory"]
    }
    return {
        "kind": "hydrometric_structural_discovery",
        "generated_at": _format_utc(built_at),
        "identity": _structural_identity(territory),
        "territory": {
            "id": territory_id,
            "label": territory_label,
            "bbox": list(bbox),
            "fingerprint": fingerprint,
        },
        "reference_fingerprint": _reference_attachment_fingerprint(
            reference_sites
        ),
        "candidate_sites": sorted(
            (
                site for code, site in reference_sites.items()
                if code in retained_site_codes
            ),
            key=lambda item: item["code_site"],
        ),
        "edges": [
            _serialize_edge(structural_edges[code])
            for code in sorted(structural_edges)
        ],
        "boundary_events": _serialize_events(events),
        "attachments": _serialize_attachments(structural_attachments),
        "source_extract": {
            "bbox": list(extract_bbox),
            "topage_feature_count": len(features),
            "topage_edge_count": len(edges),
            "structural_edge_count": len(structural_edges),
            "hubeau_station_count": len(current_station_rows),
            "hubeau_site_count": len(current_site_rows),
            "spatial_padding_degrees": topage_padding,
            "spatial_padding_is_business_rule": False,
            "expansion_attempt": expansion_attempt,
            "complete_for_traversed_branches": not structural_diagnostics,
        },
        "diagnostics": {
            "boundary": boundary_diagnostics,
            "attachment": attachment_diagnostics,
            "structural": structural_diagnostics,
            "reference": [],
        },
        "provenance": {
            "reference": {
                "provider": "Hub'Eau Hydrométrie",
                "api_version": "v2",
                "endpoints": ["referentiel/sites", "referentiel/stations"],
            },
            "network": {
                "provider": "BD TOPAGE",
                "version": TOPAGE_VERSION,
                "service": "WFS 2.0",
                "type_name": TOPAGE_TYPE_NAME,
            },
        },
    }


def _active_attachments(
    structural: dict[str, Any],
    active_sites: dict[str, dict[str, Any]],
) -> dict[str, list[SiteAttachment]]:
    station_codes = {
        station["code_station"]
        for site in active_sites.values()
        for station in site.get("stations", [])
    }
    return {
        edge_code: selected
        for edge_code, values in _deserialize_attachments(
            structural["attachments"]
        ).items()
        if (selected := [
            item for item in values
            if item.site_code in active_sites
            and item.station_code in station_codes
        ])
    }


def build_discovery_from_structure(
    structural: dict[str, Any],
    reference_sites: dict[str, dict[str, Any]],
    activity: dict[str, dict[str, dict[str, Any] | None]],
    reference_date: date,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    """Applique l'activité et STOP sur une structure déjà calculée."""

    built_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    active_sites = apply_activity_to_sites(reference_sites, activity)
    edges = _deserialize_edges(structural["edges"])
    events = _deserialize_events(structural["boundary_events"])
    attachments = _active_attachments(structural, active_sites)
    extract_bbox = tuple(structural["source_extract"]["bbox"])
    territory = structural["territory"]
    relations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    calculated_at = _format_utc(built_at)
    for site in active_sites.values():
        if site["inside_territory"]:
            relations[site["code_site"]].append(_relation(
                event=None,
                selected=None,
                role="inside",
                site_attachment=None,
                territory_id=territory.get("id"),
                fingerprint=territory["fingerprint"],
                calculated_at=calculated_at,
            ))

    traversal_diagnostics: list[dict[str, Any]] = []
    graph_indexes = _edge_indexes(edges)
    for event in events:
        result = traverse_to_first_sites(
            event,
            edges,
            attachments,
            extract_bbox=extract_bbox,
            edge_indexes=graph_indexes,
        )
        for selected in result.selected:
            relations[selected.site_code].append(_relation(
                event=event,
                selected=selected,
                role=event.direction,
                site_attachment=None,
                territory_id=territory.get("id"),
                fingerprint=territory["fingerprint"],
                calculated_at=calculated_at,
            ))
        if (
            result.incomplete_nodes or result.ambiguous_edges
            or result.cycle_edges or result.limit_reached
        ):
            traversal_diagnostics.append({
                "crossing_event_id": event.identifier,
                "selection_state": "pending_review",
                "incomplete_nodes": sorted(result.incomplete_nodes),
                "ambiguous_edge_ids": sorted(result.ambiguous_edges),
                "cycle_edge_ids": sorted(result.cycle_edges),
                "technical_limit_reached": result.limit_reached,
            })

    (
        candidate_sites,
        selected_sites,
        pending_review,
        roles,
        relation_statistics,
    ) = _finalize_territory_relations(active_sites, relations)
    pending_review.extend(structural["diagnostics"].get("attachment", []))
    pending_review.extend(structural["diagnostics"].get("structural", []))
    pending_review.extend(traversal_diagnostics)
    needs_expansion = any(
        diagnostic["incomplete_nodes"] for diagnostic in traversal_diagnostics
    )
    identity = {
        "territory_id": territory.get("id"),
        "territory_fingerprint": territory["fingerprint"],
        "topage_source": TOPAGE_WFS_URL,
        "topage_version": TOPAGE_VERSION,
        "algorithm_version": DISCOVERY_ALGORITHM_VERSION,
        "activity_policy_id": ACTIVITY_POLICY_ID,
        "reference_date": reference_date.isoformat(),
        "structural_cache_key": _structural_cache_key_from_identity(
            structural["identity"]
        ),
    }
    source_extract = dict(structural["source_extract"])
    source_extract["complete_for_traversed_branches"] = (
        source_extract.get("complete_for_traversed_branches") is True
        and not needs_expansion
    )
    return ({
        "kind": "hydrometric_discovery",
        "generated_at": calculated_at,
        "identity": identity,
        "territory": territory,
        "source_extract": source_extract,
        "counts": {
            "inside": len(roles["inside"]),
            "upstream": len(roles["upstream"]),
            "downstream": len(roles["downstream"]),
            "selected_sites": len(selected_sites),
            "pending_review": len(pending_review),
            **relation_statistics,
        },
        "sites": selected_sites,
        "pending_review": pending_review,
        "diagnostics": {
            "boundary": structural["diagnostics"].get("boundary", []),
            "attachment": structural["diagnostics"].get("attachment", []),
            "traversal": traversal_diagnostics,
            "activity": [],
        },
        "provenance": structural["provenance"],
    }, needs_expansion)


def _reference_snapshot(
    structural: dict[str, Any],
    territory: dict[str, Any],
    client: HydrometryClient,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]], str]:
    bbox = tuple(structural["source_extract"]["bbox"])
    # Ces deux ressources sont indépendantes et bornées par la même emprise.
    # Deux appels simultanés évitent d'additionner leur latence sans augmenter
    # la concurrence au-delà d'un niveau respectueux du service public.
    with ThreadPoolExecutor(
        max_workers=2, thread_name_prefix="hubeau-reference"
    ) as executor:
        station_future = executor.submit(client.reference_stations, bbox)
        site_future = executor.submit(client.reference_sites, bbox)
        station_rows = station_future.result()
        site_rows = site_future.result()
    # L'appartenance au territoire est structurelle : la recalculer pour tous
    # les objets du référentiel coûtait plusieurs secondes à chaque changement
    # de jour. Le fingerprint couvre toujours tous les sites, stations et
    # positions. Une apparition, disparition ou relocalisation force donc le
    # rebuild structurel qui refera les tests géométriques exacts.
    reference_sites = build_reference_sites(station_rows, site_rows, None)
    return (
        station_rows,
        site_rows,
        reference_sites,
        _reference_attachment_fingerprint(reference_sites),
    )


def _selection_reference_sites(
    structural: dict[str, Any],
    reference_sites: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    retained = {
        site["code_site"]: site
        for site in structural.get("candidate_sites", [])
        if isinstance(site, dict) and site.get("code_site")
    }
    return {
        code: {
            **site,
            "inside_territory": retained[code].get("inside_territory") is True,
        }
        for code, site in reference_sites.items()
        if code in retained
    }


def fetch_hydrometric_structure(
    territory: dict[str, Any],
    *,
    client: HydrometryClient,
    topage_client: TopageClient,
    persistence: Any,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Charge la structure stable et valide le référentiel de rattachement."""

    key = _structural_cache_key(territory)
    identity = _structural_identity(territory)
    with _structural_condition:
        entry = _structural_cache.get(key)
        while entry is not None and entry.status != "ready":
            _structural_condition.wait()
            entry = _structural_cache.get(key)
        structural = entry.payload if entry is not None else None

    persistence_available = True
    if structural is None:
        try:
            structural = _read_persistent_structure(persistence, key, identity)
        except Exception as exc:
            LOGGER.warning(
                "structural_cache=persistence_error cache_key=%s operation=read "
                "error=%s",
                key[:12],
                type(exc).__name__,
            )
            persistence_available = False

    if structural is not None:
        station_rows, site_rows, reference_sites, reference_fingerprint = (
            _reference_snapshot(structural, territory, client)
        )
        if reference_fingerprint == structural.get("reference_fingerprint"):
            with _structural_condition:
                _structural_cache[key] = CacheEntry("ready", structural)
                _structural_condition.notify_all()
            LOGGER.info("structural_cache=hit cache_key=%s", key[:12])
            return structural, _selection_reference_sites(
                structural, reference_sites
            )
    else:
        station_rows = site_rows = None

    with _structural_condition:
        _structural_cache[key] = CacheEntry()

    lock_key = f"structural:{key}"
    lock_acquired = False
    try:
        if persistence_available:
            try:
                _acquire_discovery_lock(persistence, lock_key)
                lock_acquired = True
                refreshed = _read_persistent_structure(
                    persistence, key, identity
                )
                if refreshed is not None:
                    snapshot = _reference_snapshot(refreshed, territory, client)
                    if snapshot[3] == refreshed.get("reference_fingerprint"):
                        structural = refreshed
                        reference_sites = _selection_reference_sites(
                            refreshed, snapshot[2]
                        )
                        LOGGER.info(
                            "structural_cache=persistent_hit cache_key=%s", key[:12]
                        )
                        with _structural_condition:
                            _structural_cache[key] = CacheEntry("ready", structural)
                            _structural_condition.notify_all()
                        return structural, reference_sites
                    station_rows, site_rows, reference_sites = snapshot[:3]
            except DiscoveryCacheLockTimeout:
                try:
                    refreshed = _read_persistent_structure(
                        persistence, key, identity
                    )
                except Exception:
                    refreshed = None
                if refreshed is None:
                    raise HydrometryServiceUnavailableError(
                        "La structure hydrométrique est encore en construction."
                    )
                snapshot = _reference_snapshot(refreshed, territory, client)
                if snapshot[3] != refreshed.get("reference_fingerprint"):
                    raise HydrometryServiceUnavailableError(
                        "La structure hydrométrique est en cours de renouvellement."
                    )
                structural = refreshed
                reference_sites = _selection_reference_sites(
                    refreshed, snapshot[2]
                )
                return structural, reference_sites
            except Exception as exc:
                LOGGER.warning(
                    "structural_cache=persistence_error cache_key=%s "
                    "operation=lock error=%s",
                    key[:12],
                    type(exc).__name__,
                )
                persistence_available = False

        padding = (
            float(structural["source_extract"]["spatial_padding_degrees"])
            if structural is not None
            else TOPAGE_INITIAL_PADDING_DEGREES
        )
        structural = build_structural_dataset(
            territory,
            client,
            topage_client,
            topage_padding=padding,
            station_rows=station_rows,
            site_rows=site_rows,
        )
        reference_sites = {
            site["code_site"]: site for site in structural["candidate_sites"]
        }
        if persistence_available:
            try:
                _write_persistent_structure(persistence, key, structural)
            except Exception as exc:
                LOGGER.warning(
                    "structural_cache=persistence_error cache_key=%s "
                    "operation=write error=%s",
                    key[:12],
                    type(exc).__name__,
                )
        LOGGER.info("structural_cache=miss_rebuild cache_key=%s", key[:12])
    except Exception:
        with _structural_condition:
            _structural_cache.pop(key, None)
            _structural_condition.notify_all()
        raise
    finally:
        if lock_acquired:
            try:
                _release_discovery_lock(persistence, lock_key)
            except Exception as exc:
                LOGGER.warning(
                    "structural_cache=persistence_error cache_key=%s "
                    "operation=unlock error=%s",
                    key[:12],
                    type(exc).__name__,
                )
    with _structural_condition:
        _structural_cache[key] = CacheEntry("ready", structural)
        _structural_condition.notify_all()
    return structural, reference_sites


def expand_hydrometric_structure(
    territory: dict[str, Any],
    structural: dict[str, Any],
    *,
    client: HydrometryClient,
    topage_client: TopageClient,
    persistence: Any,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Étend l'extrait sous le même verrou structurel, sans critère métier de rayon."""

    key = _structural_cache_key(territory)
    identity = _structural_identity(territory)
    lock_key = f"structural:{key}"
    current_padding = float(
        structural["source_extract"]["spatial_padding_degrees"]
    )
    lock_acquired = False
    persistence_available = True
    try:
        try:
            _acquire_discovery_lock(persistence, lock_key)
            lock_acquired = True
            persisted = _read_persistent_structure(persistence, key, identity)
            if persisted is not None and float(
                persisted["source_extract"]["spatial_padding_degrees"]
            ) > current_padding:
                snapshot = _reference_snapshot(persisted, territory, client)
                if snapshot[3] == persisted.get("reference_fingerprint"):
                    return persisted, _selection_reference_sites(
                        persisted, snapshot[2]
                    )
        except DiscoveryCacheLockTimeout:
            persisted = _read_persistent_structure(persistence, key, identity)
            if persisted is None or float(
                persisted["source_extract"]["spatial_padding_degrees"]
            ) <= current_padding:
                raise HydrometryServiceUnavailableError(
                    "L'extension structurelle hydrométrique est encore en cours."
                )
            snapshot = _reference_snapshot(persisted, territory, client)
            return persisted, _selection_reference_sites(
                persisted, snapshot[2]
            )
        except Exception as exc:
            LOGGER.warning(
                "structural_cache=persistence_error cache_key=%s "
                "operation=expand_lock error=%s",
                key[:12],
                type(exc).__name__,
            )
            persistence_available = False

        expanded = build_structural_dataset(
            territory,
            client,
            topage_client,
            topage_padding=current_padding * 2,
            expansion_attempt=int(
                structural["source_extract"].get("expansion_attempt", 0)
            ) + 1,
        )
        if persistence_available:
            try:
                _write_persistent_structure(persistence, key, expanded)
            except Exception as exc:
                LOGGER.warning(
                    "structural_cache=persistence_error cache_key=%s "
                    "operation=expand_write error=%s",
                    key[:12],
                    type(exc).__name__,
                )
        reference_sites = {
            site["code_site"]: site for site in expanded["candidate_sites"]
        }
        with _structural_condition:
            _structural_cache[key] = CacheEntry("ready", expanded)
            _structural_condition.notify_all()
        return expanded, reference_sites
    finally:
        if lock_acquired:
            try:
                _release_discovery_lock(persistence, lock_key)
            except Exception as exc:
                LOGGER.warning(
                    "structural_cache=persistence_error cache_key=%s "
                    "operation=expand_unlock error=%s",
                    key[:12],
                    type(exc).__name__,
                )


def build_daily_discovery_selection(
    territory: dict[str, Any],
    reference_date: date,
    *,
    client: HydrometryClient,
    topage_client: TopageClient,
    persistence: Any,
) -> dict[str, Any]:
    """Rafraichit l'activite puis applique STOP sans reconstruire TOPAGE."""

    structural, reference_sites = fetch_hydrometric_structure(
        territory,
        client=client,
        topage_client=topage_client,
        persistence=persistence,
    )
    expansion_failure = None
    while True:
        built_at = datetime.now(timezone.utc)
        station_codes = [
            station["code_station"]
            for site in reference_sites.values()
            for station in site.get("stations", [])
            if station.get("in_service") is True
        ]
        activity = client.recent_activity(
            station_codes,
            start_time=built_at - ACTIVITY_LOOKBACK,
            end_time=built_at,
        )
        payload, needs_expansion = build_discovery_from_structure(
            structural,
            reference_sites,
            activity,
            reference_date,
            now=built_at,
        )
        payload["diagnostics"]["activity"] = getattr(
            client, "last_activity_errors", []
        )
        attempt = int(structural["source_extract"].get("expansion_attempt", 0))
        if not needs_expansion or attempt >= 4:
            if needs_expansion:
                expansion_failure = "technical_expansion_limit"
            break
        try:
            structural, reference_sites = expand_hydrometric_structure(
                territory,
                structural,
                client=client,
                topage_client=topage_client,
                persistence=persistence,
            )
        except (HydrometryDataError, HydrometryServiceUnavailableError) as exc:
            expansion_failure = type(exc).__name__
            break
    if expansion_failure:
        payload["pending_review"].append({
            "selection_state": "pending_review",
            "reason": "extraction TOPAGE incomplète après extension technique",
            "diagnostic": expansion_failure,
            "attempt": structural["source_extract"].get("expansion_attempt", 0),
            "spatial_padding_degrees": structural["source_extract"][
                "spatial_padding_degrees"
            ],
        })
        payload["counts"]["pending_review"] = len(payload["pending_review"])
    return payload


def fetch_hydrometric_discovery(
    connection: Any,
    *,
    client: HydrometryClient | None = None,
    topage_client: TopageClient | None = None,
    day_provider: Any = None,
    persistent_connection: Any | None = None,
) -> dict[str, Any]:
    territory = fetch_territoire_administratif(connection)
    reference_date = _cache_day(day_provider)
    key = _discovery_cache_key(territory, reference_date)
    identity = _discovery_identity(territory, reference_date)
    with _discovery_condition:
        while (entry := _discovery_cache.get(key)) is not None:
            if entry.status == "ready" and entry.payload is not None:
                LOGGER.info("discovery_cache=memory_hit cache_key=%s", key[:12])
                return entry.payload
            _discovery_condition.wait()
        _discovery_cache[key] = CacheEntry()

    persistence = persistent_connection or connection
    persistence_available = True
    lock_acquired = False
    try:
        try:
            payload = _read_persistent_discovery(persistence, key, identity)
        except PersistentDiscoveryPayloadError as exc:
            LOGGER.warning(
                "discovery_cache=persistence_error cache_key=%s reason=%s",
                key[:12],
                type(exc).__name__,
            )
            payload = None
        except Exception as exc:
            LOGGER.warning(
                "discovery_cache=persistence_error cache_key=%s operation=read "
                "error=%s",
                key[:12],
                type(exc).__name__,
            )
            payload = None
            persistence_available = False

        if payload is not None:
            LOGGER.info(
                "discovery_cache=persistent_hit cache_key=%s completeness=%s",
                key[:12],
                discovery_completeness(payload),
            )
        else:
            if persistence_available:
                try:
                    _acquire_discovery_lock(persistence, key)
                    lock_acquired = True
                    # Un autre processus peut avoir construit pendant l'attente.
                    payload = _read_persistent_discovery(persistence, key, identity)
                except DiscoveryCacheLockTimeout:
                    # Une dernière lecture couvre une écriture concomitante à
                    # l'expiration. Sans entrée, ne jamais lancer un second
                    # rebuild coûteux hors verrou.
                    try:
                        payload = _read_persistent_discovery(
                            persistence, key, identity
                        )
                    except Exception as exc:
                        LOGGER.warning(
                            "discovery_cache=persistence_error cache_key=%s "
                            "operation=read_after_lock_timeout error=%s",
                            key[:12],
                            type(exc).__name__,
                        )
                        payload = None
                    if payload is None:
                        LOGGER.warning(
                            "discovery_cache=build_in_progress cache_key=%s",
                            key[:12],
                        )
                        raise HydrometryServiceUnavailableError(
                            "Le discovery hydrométrique est encore en construction."
                        )
                except PersistentDiscoveryPayloadError as exc:
                    LOGGER.warning(
                        "discovery_cache=persistence_error cache_key=%s reason=%s",
                        key[:12],
                        type(exc).__name__,
                    )
                    payload = None
                except Exception as exc:
                    LOGGER.warning(
                        "discovery_cache=persistence_error cache_key=%s operation=lock "
                        "error=%s",
                        key[:12],
                        type(exc).__name__,
                    )
                    payload = None
                    persistence_available = False

            if payload is not None:
                LOGGER.info(
                    "discovery_cache=persistent_hit cache_key=%s completeness=%s",
                    key[:12],
                    discovery_completeness(payload),
                )
            else:
                LOGGER.info("discovery_cache=miss_rebuild cache_key=%s", key[:12])
                shared_client = client or HydrometryClient()
                shared_topage_client = topage_client or TopageClient()
                payload = build_daily_discovery_selection(
                    territory,
                    reference_date,
                    client=shared_client,
                    topage_client=shared_topage_client,
                    persistence=persistence,
                )
                completeness = discovery_completeness(payload)
                if persistence_available:
                    try:
                        persisted = _write_persistent_discovery(
                            persistence, key, payload
                        )
                        if completeness != "complete":
                            LOGGER.warning(
                                "discovery_cache=miss_rebuild cache_key=%s "
                                "completeness=%s persisted=%s",
                                key[:12],
                                completeness,
                                str(persisted).lower(),
                            )
                    except Exception as exc:
                        LOGGER.warning(
                            "discovery_cache=persistence_error cache_key=%s "
                            "operation=write error=%s",
                            key[:12],
                            type(exc).__name__,
                        )
                elif completeness != "complete":
                    LOGGER.warning(
                        "discovery_cache=miss_rebuild cache_key=%s completeness=%s "
                        "persisted=false",
                        key[:12],
                        completeness,
                    )
    except Exception:
        with _discovery_condition:
            _discovery_cache.pop(key, None)
            _discovery_condition.notify_all()
        raise
    finally:
        if lock_acquired:
            try:
                _release_discovery_lock(persistence, key)
            except Exception as exc:
                LOGGER.warning(
                    "discovery_cache=persistence_error cache_key=%s operation=unlock "
                    "error=%s",
                    key[:12],
                    type(exc).__name__,
                )
    with _discovery_condition:
        _discovery_cache[key] = CacheEntry("ready", payload)
        _discovery_condition.notify_all()
    return payload


def _nearest_measurement(
    measurements: list[dict[str, Any]], target: datetime
) -> dict[str, Any] | None:
    candidates = []
    for measurement in measurements:
        source_time = _parse_datetime(measurement.get("source_time"))
        if source_time is None:
            continue
        distance = abs(source_time - target)
        if distance <= OBSERVATION_TOLERANCE:
            candidates.append((distance, source_time, measurement))
    return min(candidates, key=lambda item: (item[0], item[1]))[2] if candidates else None


def resample_series(
    measurements: list[dict[str, Any]], cache_date: date
) -> list[dict[str, Any]]:
    return [
        {
            "target_time": _format_utc(target),
            "measurement": _nearest_measurement(measurements, target),
        }
        for target in observation_targets(cache_date)
    ]


def resolve_site_series(
    series: list[dict[str, Any]],
    *,
    official_station_code: str | None = None,
) -> dict[str, Any]:
    station_codes = sorted({
        item["station_code"] for item in series
        if item.get("state") == "active" or item.get("measurements")
    })
    if official_station_code and official_station_code in station_codes:
        return {
            "status": "selected_official",
            "selected_station_code": official_station_code,
            "policy_id": SERIES_POLICY_ID,
        }
    if len(station_codes) == 1:
        return {
            "status": "selected_single_active",
            "selected_station_code": station_codes[0],
            "policy_id": SERIES_POLICY_ID,
        }
    if len(station_codes) > 1:
        return {
            "status": "multiple_unresolved",
            "selected_station_code": None,
            "policy_id": SERIES_POLICY_ID,
        }
    return {
        "status": "unavailable",
        "selected_station_code": None,
        "policy_id": SERIES_POLICY_ID,
    }


def _load_station_series(
    client: HydrometryClient,
    station_code: str,
    *,
    start_time: datetime,
    end_time: datetime,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]]]:
    data = {"height": [], "discharge": []}
    errors: list[dict[str, str]] = []
    for api_quantity, quantity in (("H", "height"), ("Q", "discharge")):
        try:
            data[quantity] = client.observation_series(
                station_code,
                api_quantity,
                start_time=start_time,
                end_time=end_time,
            )
        except (VigicruesDataError, VigicruesServiceUnavailableError):
            errors.append({
                "kind": "observation",
                "quantity": quantity,
                "station_code": station_code,
                "error": "unavailable",
            })
    return data, errors


def build_observation_dataset(
    discovery: dict[str, Any],
    client: HydrometryClient,
    cache_date: date,
    *,
    now: datetime | None = None,
    workers: int = NETWORK_WORKERS,
) -> dict[str, Any]:
    generated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start_time = observation_targets(cache_date)[0] - OBSERVATION_TOLERANCE
    station_codes = sorted({
        station["code_station"]
        for site in discovery.get("sites", [])
        for station in site.get("stations", [])
    })
    loaded: dict[str, tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]]]] = {}
    if station_codes:
        with ThreadPoolExecutor(
            max_workers=max(1, min(workers, len(station_codes))),
            thread_name_prefix="hubeau-observations",
        ) as executor:
            futures = {
                executor.submit(
                    _load_station_series,
                    client,
                    code,
                    start_time=start_time,
                    end_time=generated_at,
                ): code for code in station_codes
            }
            for future in as_completed(futures):
                loaded[futures[future]] = future.result()

    by_site: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    for site in discovery.get("sites", []):
        series = {"height": [], "discharge": []}
        site_errors = []
        for station in site.get("stations", []):
            station_code = station["code_station"]
            station_data, station_errors = loaded.get(
                station_code, ({"height": [], "discharge": []}, [])
            )
            site_errors.extend(station_errors)
            for quantity, api_quantity in (("height", "H"), ("discharge", "Q")):
                measurements = station_data[quantity]
                recent_quantities = station.get("recent_quantities")
                known_active = (
                    api_quantity in recent_quantities
                    if isinstance(recent_quantities, list)
                    else bool(measurements)
                )
                if known_active:
                    series[quantity].append({
                        "station_code": station_code,
                        "state": "active",
                        "measurements": measurements,
                        "latest": measurements[-1] if measurements else None,
                        "samples": resample_series(measurements, cache_date),
                    })
        resolutions = {
            quantity: resolve_site_series(series[quantity])
            for quantity in ("height", "discharge")
        }
        latest: dict[str, Any] = {"height": None, "discharge": None}
        for quantity in ("height", "discharge"):
            selected_code = resolutions[quantity]["selected_station_code"]
            selected = next((
                item for item in series[quantity]
                if item["station_code"] == selected_code
            ), None)
            if selected is not None:
                latest[quantity] = selected["latest"]
        samples = []
        for index, target in enumerate(observation_targets(cache_date)):
            sample = {"target_time": _format_utc(target)}
            for quantity, value_key, time_key, station_key in (
                ("height", "height_m", "height_source_time", "height_station_code"),
                ("discharge", "discharge_m3_s", "discharge_source_time", "discharge_station_code"),
            ):
                selected_code = resolutions[quantity]["selected_station_code"]
                selected = next((
                    item for item in series[quantity]
                    if item["station_code"] == selected_code
                ), None)
                measurement = (
                    selected["samples"][index]["measurement"] if selected else None
                )
                sample[value_key] = measurement["value"] if measurement else None
                sample[time_key] = measurement["source_time"] if measurement else None
                sample[station_key] = measurement["station_code"] if measurement else None
            samples.append(sample)
        by_site[site["code_site"]] = {
            "kind": "observation",
            "source": {
                "provider": "Hub'Eau Hydrométrie",
                "api_version": "v2",
                "endpoint": "observations_tr",
                "fetched_at": _format_utc(generated_at),
            },
            "series_resolution": resolutions,
            "series": series,
            "latest": latest,
            "samples_7d": {
                "window_days": OBSERVATION_DAYS,
                "target_hours_utc": list(OBSERVATION_TARGET_HOURS),
                "tolerance_minutes": int(OBSERVATION_TOLERANCE.total_seconds() / 60),
                "interpolation": False,
                "samples": samples,
            },
            "errors": site_errors,
        }
        errors.extend(site_errors)
    return {
        "kind": "hydrometric_observations",
        "cache_date": cache_date.isoformat(),
        "generated_at": _format_utc(generated_at),
        "discovery_identity": discovery["identity"],
        "sites": by_site,
        "errors": errors,
        "provenance": {
            "provider": "Hub'Eau Hydrométrie",
            "endpoint": "observations_tr",
            "native_units": {"height": "mm", "discharge": "l/s"},
            "units": {"height": "m", "discharge": "m3/s"},
        },
    }


def _observation_cache_key(
    discovery: dict[str, Any], cache_date: date
) -> tuple[str, str]:
    identity = json.dumps(
        discovery["identity"], sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(identity).hexdigest(), cache_date.isoformat()


def fetch_hydrometric_observations(
    discovery: dict[str, Any],
    *,
    client: HydrometryClient | None = None,
    day_provider: Any = None,
) -> dict[str, Any]:
    cache_date = _cache_day(day_provider)
    key = _observation_cache_key(discovery, cache_date)
    with _observation_condition:
        while (entry := _observation_cache.get(key)) is not None:
            if entry.status == "ready" and entry.payload is not None:
                return entry.payload
            _observation_condition.wait()
        _observation_cache[key] = CacheEntry()
    try:
        payload = build_observation_dataset(
            discovery, client or HydrometryClient(), cache_date
        )
    except Exception:
        with _observation_condition:
            _observation_cache.pop(key, None)
            _observation_condition.notify_all()
        raise
    with _observation_condition:
        _observation_cache[key] = CacheEntry("ready", payload)
        _observation_condition.notify_all()
    return payload


def build_forecast_dataset(
    discovery: dict[str, Any], client: HydrometryClient
) -> dict[str, Any]:
    catalogs: dict[str, set[str]] = {"height": set(), "discharge": set()}
    errors: list[dict[str, str]] = []
    for api_quantity, quantity in (("H", "height"), ("Q", "discharge")):
        try:
            catalogs[quantity] = client.forecast_catalog(api_quantity)
        except (VigicruesDataError, VigicruesServiceUnavailableError):
            errors.append({
                "kind": "forecast_catalog", "quantity": quantity,
                "error": "unavailable",
            })
    sites: dict[str, dict[str, Any] | None] = {}
    for site in discovery.get("sites", []):
        forecasts: dict[str, list[dict[str, Any]]] = {
            "height": [], "discharge": [],
        }
        for station in site.get("stations", []):
            station_code = station["code_station"]
            for api_quantity, quantity in (("H", "height"), ("Q", "discharge")):
                if station_code not in catalogs[quantity]:
                    continue
                try:
                    forecast = client.forecast(station_code, api_quantity)
                    forecasts[quantity].append({
                        **forecast,
                        "station_code": station_code,
                    })
                except (VigicruesDataError, VigicruesServiceUnavailableError):
                    errors.append({
                        "kind": "forecast", "quantity": quantity,
                        "station_code": station_code, "error": "unavailable",
                    })
        sites[site["code_site"]] = (
            forecasts if forecasts["height"] or forecasts["discharge"] else None
        )
    return {
        "kind": "hydrometric_forecasts",
        "generated_at": _format_utc(datetime.now(timezone.utc)),
        "sites": sites,
        "errors": errors,
        "provenance": {
            "provider": "Vigicrues",
            "endpoint": "prevision.json",
            "matching": "exact official hydrometric station identifier",
        },
    }


def fetch_hydrometric_forecasts(
    discovery: dict[str, Any], *, client: HydrometryClient | None = None
) -> dict[str, Any]:
    key = (_observation_cache_key(discovery, _cache_day())[0],)
    with _forecast_condition:
        while (entry := _forecast_cache.get(key)) is not None:
            if entry.status == "ready" and entry.payload is not None:
                return entry.payload
            _forecast_condition.wait()
        _forecast_cache[key] = CacheEntry()
    try:
        payload = build_forecast_dataset(discovery, client or HydrometryClient())
    except Exception:
        with _forecast_condition:
            _forecast_cache.pop(key, None)
            _forecast_condition.notify_all()
        raise
    with _forecast_condition:
        _forecast_cache[key] = CacheEntry("ready", payload)
        _forecast_condition.notify_all()
    return payload


def assemble_hydrometric_dataset(
    discovery: dict[str, Any],
    observations: dict[str, Any],
    forecasts: dict[str, Any],
) -> dict[str, Any]:
    evaluated_at = datetime.now(timezone.utc)

    def with_freshness(measurement: Any) -> Any:
        if not isinstance(measurement, dict):
            return measurement
        result = dict(measurement)
        source_time = _parse_datetime(result.get("source_time"))
        result["freshness"] = {
            "evaluated_at": _format_utc(evaluated_at),
            "age_seconds": max(0, int((evaluated_at - source_time).total_seconds()))
            if source_time else None,
        }
        return result

    sites = []
    for discovered in discovery.get("sites", []):
        site = dict(discovered)
        code_site = site["code_site"]
        raw_observations = observations.get("sites", {}).get(code_site)
        if isinstance(raw_observations, dict):
            site_observations = dict(raw_observations)
            site_observations["latest"] = {
                quantity: with_freshness(measurement)
                for quantity, measurement in raw_observations.get("latest", {}).items()
            }
            site_observations["series"] = {
                quantity: [
                    {**series, "latest": with_freshness(series.get("latest"))}
                    for series in values
                ]
                for quantity, values in raw_observations.get("series", {}).items()
            }
            site["observations"] = site_observations
        else:
            site["observations"] = raw_observations
        site["forecasts"] = forecasts.get("sites", {}).get(code_site)
        sites.append(site)
    return {
        "kind": "hydrometric_sites",
        "cache_date": observations["cache_date"],
        "generated_at": observations["generated_at"],
        "territoire": discovery["territory"],
        "sites": sites,
        "pending_review": discovery.get("pending_review", []),
        "errors": [
            *observations.get("errors", []), *forecasts.get("errors", []),
        ],
        "provenance": {
            "reference": "Hub'Eau Hydrométrie",
            "discovery": "BD TOPAGE 2026",
            "observations": "Hub'Eau Hydrométrie",
            "observation_endpoint": "observations_tr",
            "forecasts": "Vigicrues",
            "forecast_endpoint": "prevision.json",
        },
        "datasets": {
            "discovery_identity": discovery["identity"],
            "observation_generated_at": observations["generated_at"],
            "forecast_generated_at": forecasts["generated_at"],
        },
    }


def fetch_hydrometric_territorial_dataset(
    connection: Any,
    *,
    client: HydrometryClient | None = None,
    topage_client: TopageClient | None = None,
    day_provider: Any = None,
    persistent_connection: Any | None = None,
) -> dict[str, Any]:
    shared_client = client or HydrometryClient()
    discovery = fetch_hydrometric_discovery(
        connection,
        client=shared_client,
        topage_client=topage_client,
        day_provider=day_provider,
        persistent_connection=persistent_connection,
    )
    observations = fetch_hydrometric_observations(
        discovery, client=shared_client, day_provider=day_provider
    )
    forecasts = fetch_hydrometric_forecasts(discovery, client=shared_client)
    return assemble_hydrometric_dataset(discovery, observations, forecasts)


def summarize_territory_relation(relation: dict[str, Any]) -> dict[str, Any]:
    """Vue compacte destinée à l'API, au frontend et à l'assistant IA."""

    summary = relation.get("proof_summary", {})
    return {
        "territory": relation.get("territory"),
        "role": relation.get("role"),
        "selection_method": relation.get("selection", {}).get("method"),
        "selection_state": relation.get("selection", {}).get("state"),
        "topology_status": relation.get("topology_status"),
        "hydraulic_continuity": relation.get("hydraulic_continuity"),
        "proof_count": summary.get("count", 0),
        "pending_review_proof_count": summary.get("pending_review_count", 0),
        "has_conditional_connections": bool(
            summary.get("conditional_connection_ids")
        ),
    }


def dataset_sites_geojson(dataset: dict[str, Any]) -> dict[str, Any]:
    features = []
    for site in dataset.get("sites", []):
        roles = sorted({
            relation["role"] for relation in site.get("territory_relations", [])
            if relation.get("selection", {}).get("state") == "selected"
        })
        observations = site.get("observations") or {}
        resolution = observations.get("series_resolution", {})
        properties = {
            "code_site": site["code_site"],
            "name": site["name"],
            "watercourse": site["watercourse"],
            "roles": roles,
            "role": roles[0] if len(roles) == 1 else roles,
            "territory_relations": [
                summarize_territory_relation(relation)
                for relation in site.get("territory_relations", [])
            ],
            "station_codes": [
                station["code_station"] for station in site.get("stations", [])
            ],
            "observations": {
                "latest": observations.get("latest", {}),
                "series_resolution": resolution,
            },
            "forecast_availability": {
                "height": bool(site.get("forecasts") and site["forecasts"].get("height")),
                "discharge": bool(site.get("forecasts") and site["forecasts"].get("discharge")),
            },
        }
        features.append({
            "type": "Feature",
            "id": site["code_site"],
            "geometry": {
                "type": "Point",
                "coordinates": [site["position"]["longitude"], site["position"]["latitude"]],
            },
            "properties": properties,
        })
    return {
        "type": "FeatureCollection",
        "cache_date": dataset.get("cache_date"),
        "features": features,
    }


def fetch_hydrometric_sites(connection: Any, **kwargs: Any) -> dict[str, Any]:
    return dataset_sites_geojson(
        fetch_hydrometric_territorial_dataset(connection, **kwargs)
    )


def clear_hydrometry_caches() -> None:
    for condition, cache in (
        (_discovery_condition, _discovery_cache),
        (_structural_condition, _structural_cache),
        (_observation_condition, _observation_cache),
        (_forecast_condition, _forecast_cache),
    ):
        with condition:
            cache.clear()
            condition.notify_all()
