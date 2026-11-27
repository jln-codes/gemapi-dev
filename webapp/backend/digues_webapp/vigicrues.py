"""Stations Vigicrues présentes dans le territoire administratif courant."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
from threading import Condition
from typing import Any
from urllib.parse import urlsplit

import requests

from .territoire import fetch_territoire_administratif
from .service_observations import observe


VIGICRUES_STATIONS_URL = (
    "https://www.vigicrues.gouv.fr/services/v1.1/StaEntVigiCru.json"
)
VIGICRUES_STATIONS_REDIRECT_URL = (
    "https://www.vigicrues.gouv.fr/services/StaEntVigiCru.json"
)
HUBEAU_STATIONS_URL = (
    "https://hubeau.eaufrance.fr/api/v2/hydrometrie/referentiel/stations"
)
HUBEAU_OBSERVATIONS_URL = (
    "https://hubeau.eaufrance.fr/api/v2/hydrometrie/observations_tr"
)
VIGICRUES_FORECASTS_URL = (
    "https://www.vigicrues.gouv.fr/services/v1.1/prevision.json"
)
VIGICRUES_TIMEOUT_SECONDS = 12
VIGICRUES_MAX_RESPONSE_BYTES = 25_000_000
HUBEAU_PAGE_SIZE = 10_000
HUBEAU_OBSERVATION_PAGE_SIZE = 20_000
OBSERVATION_DAYS = 7
OBSERVATION_TARGET_HOURS = (0, 6, 12, 18)
OBSERVATION_TOLERANCE = timedelta(minutes=90)
OBSERVATION_WORKERS = 6


@dataclass
class DatasetCacheEntry:
    status: str = "loading"
    payload: dict[str, Any] | None = None


_dataset_condition = Condition()
_dataset_cache: dict[tuple[Any, ...], DatasetCacheEntry] = {}


class VigicruesError(RuntimeError):
    """Erreur Vigicrues présentable sans détail de transport."""


class VigicruesTerritoryRequiredError(VigicruesError):
    """Aucun territoire administratif exploitable n'est configuré."""


class VigicruesServiceUnavailableError(VigicruesError):
    """Une source distante nécessaire n'a pas répondu."""


class VigicruesDataError(VigicruesError):
    """Une source distante a retourné un format inexploitable."""


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _coordinate(value: Any, minimum: float, maximum: float) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < minimum or number > maximum:
        return None
    return number


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hydrometry_coordinates(item: dict[str, Any]) -> tuple[float, float] | None:
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


def parse_vigicrues_stations(
    vigicrues_payload: dict[str, Any],
    hydrometry_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Croise la liste Vigicrues avec les coordonnées du référentiel hydrométrique."""

    raw_vigicrues = vigicrues_payload.get("ListEntVigiCru")
    raw_hydrometry = hydrometry_payload.get("data")
    if not isinstance(raw_vigicrues, list) or not isinstance(raw_hydrometry, list):
        raise VigicruesDataError("Réponse des référentiels hydrométriques invalide.")

    vigilance_by_code: dict[str, dict[str, Any]] = {}
    for item in raw_vigicrues:
        if not isinstance(item, dict):
            continue
        code = _clean_string(item.get("CdEntVigiCru"))
        if code:
            vigilance_by_code[code] = item

    stations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_hydrometry:
        if not isinstance(item, dict):
            continue
        code_hydro = _clean_string(item.get("code_station"))
        if not code_hydro or code_hydro in seen:
            continue
        vigilance = vigilance_by_code.get(code_hydro)
        coordinates = _hydrometry_coordinates(item)
        if vigilance is None or coordinates is None:
            continue
        seen.add(code_hydro)
        longitude, latitude = coordinates
        stations.append({
            "id": code_hydro,
            "code_vigicrues": _clean_string(vigilance.get("CdEntVigiCru")),
            "code_hydro": code_hydro,
            "nom": _clean_string(vigilance.get("LbEntVigiCru"))
            or _clean_string(item.get("libelle_station"))
            or code_hydro,
            "cours_eau": _clean_string(vigilance.get("LbCoursEau"))
            or _clean_string(item.get("libelle_cours_eau")),
            "troncon": None,
            "territoire": None,
            "longitude": longitude,
            "latitude": latitude,
        })
    stations.sort(key=lambda station: station["id"])
    return stations


def _territory_geometry(collection: dict[str, Any]) -> dict[str, Any]:
    features = collection.get("features")
    if not isinstance(features, list) or len(features) != 1:
        raise VigicruesTerritoryRequiredError(
            "Un territoire administratif est requis pour afficher Vigicrues."
        )
    feature = features[0]
    geometry = feature.get("geometry") if isinstance(feature, dict) else None
    if not isinstance(geometry, dict) or geometry.get("type") not in {
        "Polygon",
        "MultiPolygon",
    }:
        raise VigicruesTerritoryRequiredError(
            "Le territoire administratif n'est pas exploitable pour Vigicrues."
        )
    return geometry


def _geometry_points(value: Any) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    if isinstance(value, list):
        if len(value) >= 2:
            longitude = _coordinate(value[0], -180, 180)
            latitude = _coordinate(value[1], -90, 90)
            if longitude is not None and latitude is not None:
                return [(longitude, latitude)]
        for child in value:
            points.extend(_geometry_points(child))
    return points


def territory_bbox(collection: dict[str, Any]) -> tuple[float, float, float, float]:
    geometry = _territory_geometry(collection)
    points = _geometry_points(geometry.get("coordinates"))
    if not points:
        raise VigicruesTerritoryRequiredError(
            "Le territoire administratif n'a pas de coordonnées exploitables."
        )
    longitudes = [point[0] for point in points]
    latitudes = [point[1] for point in points]
    return min(longitudes), min(latitudes), max(longitudes), max(latitudes)


def _point_on_segment(
    longitude: float,
    latitude: float,
    start: list[Any],
    end: list[Any],
) -> bool:
    if len(start) < 2 or len(end) < 2:
        return False
    x1 = _coordinate(start[0], -180, 180)
    y1 = _coordinate(start[1], -90, 90)
    x2 = _coordinate(end[0], -180, 180)
    y2 = _coordinate(end[1], -90, 90)
    if None in (x1, y1, x2, y2):
        return False
    cross = (longitude - x1) * (y2 - y1) - (latitude - y1) * (x2 - x1)
    if abs(cross) > 1e-10:
        return False
    return (
        min(x1, x2) - 1e-10 <= longitude <= max(x1, x2) + 1e-10
        and min(y1, y2) - 1e-10 <= latitude <= max(y1, y2) + 1e-10
    )


def _ring_location(longitude: float, latitude: float, ring: Any) -> int:
    """Retourne 0 dehors, 1 dedans ou 2 sur la limite d'un anneau."""

    if not isinstance(ring, list) or len(ring) < 4:
        return 0
    inside = False
    previous = ring[-1]
    for current in ring:
        if not isinstance(previous, list) or not isinstance(current, list):
            previous = current
            continue
        if _point_on_segment(longitude, latitude, previous, current):
            return 2
        x1 = _coordinate(previous[0], -180, 180) if len(previous) >= 2 else None
        y1 = _coordinate(previous[1], -90, 90) if len(previous) >= 2 else None
        x2 = _coordinate(current[0], -180, 180) if len(current) >= 2 else None
        y2 = _coordinate(current[1], -90, 90) if len(current) >= 2 else None
        if None not in (x1, y1, x2, y2) and (y1 > latitude) != (y2 > latitude):
            intersection = (x2 - x1) * (latitude - y1) / (y2 - y1) + x1
            if longitude < intersection:
                inside = not inside
        previous = current
    return 1 if inside else 0


def _point_in_polygon(longitude: float, latitude: float, polygon: Any) -> bool:
    if not isinstance(polygon, list) or not polygon:
        return False
    outer = _ring_location(longitude, latitude, polygon[0])
    if outer == 0:
        return False
    if outer == 2:
        return True
    for hole in polygon[1:]:
        location = _ring_location(longitude, latitude, hole)
        if location == 1:
            return False
        if location == 2:
            return True
    return True


def point_in_territory(
    longitude: float,
    latitude: float,
    geometry: dict[str, Any],
) -> bool:
    coordinates = geometry.get("coordinates")
    polygons = [coordinates] if geometry.get("type") == "Polygon" else coordinates
    if not isinstance(polygons, list):
        return False
    return any(
        _point_in_polygon(longitude, latitude, polygon)
        for polygon in polygons
    )


def stations_geojson(
    stations: list[dict[str, Any]],
    territory: dict[str, Any],
) -> dict[str, Any]:
    geometry = _territory_geometry(territory)
    features = []
    for station in stations:
        longitude = station["longitude"]
        latitude = station["latitude"]
        if not point_in_territory(longitude, latitude, geometry):
            continue
        properties = {
            key: station.get(key)
            for key in (
                "id",
                "code_vigicrues",
                "code_hydro",
                "nom",
                "cours_eau",
                "troncon",
                "territoire",
                "longitude",
                "latitude",
            )
        }
        features.append({
            "type": "Feature",
            "id": station["id"],
            "geometry": {
                "type": "Point",
                "coordinates": [longitude, latitude],
            },
            "properties": properties,
        })
    return {"type": "FeatureCollection", "features": features}


def parse_hubeau_observation_series(
    payload: dict[str, Any],
    quantity: str,
) -> list[dict[str, Any]]:
    """Normalise H/Q Hub'Eau et convertit une seule fois vers les unités métier."""

    if quantity not in {"H", "Q"}:
        raise ValueError("quantity doit valoir H ou Q")
    raw_observations = payload.get("data")
    if not isinstance(raw_observations, list):
        raise VigicruesDataError("Série d'observations Hub'Eau invalide.")
    native_unit = "mm" if quantity == "H" else "l/s"
    observations: list[dict[str, Any]] = []
    for item in raw_observations:
        if (
            not isinstance(item, dict)
            or item.get("grandeur_hydro") != quantity
        ):
            continue
        observed_at = _parse_datetime(item.get("date_obs"))
        native_value = _number(item.get("resultat_obs"))
        if observed_at is None or native_value is None:
            continue
        observations.append({
            "source_time": _format_utc(observed_at),
            "value": native_value / 1000,
            "native_value": native_value,
            "native_unit": native_unit,
        })
    observations.sort(key=lambda item: item["source_time"])
    return observations


def observation_targets(cache_date: date) -> list[datetime]:
    first_day = cache_date - timedelta(days=OBSERVATION_DAYS - 1)
    return [
        datetime.combine(
            first_day + timedelta(days=day_offset),
            time(hour=hour, tzinfo=timezone.utc),
        )
        for day_offset in range(OBSERVATION_DAYS)
        for hour in OBSERVATION_TARGET_HOURS
    ]


def _nearest_observation(
    observations: list[dict[str, Any]],
    target: datetime,
    tolerance: timedelta,
) -> dict[str, Any] | None:
    candidates = []
    for observation in observations:
        source_time = _parse_datetime(observation.get("source_time"))
        value = _number(observation.get("value"))
        if source_time is None or value is None:
            continue
        distance = abs(source_time - target)
        if distance <= tolerance:
            candidates.append((distance, source_time, value))
    if not candidates:
        return None
    _, source_time, value = min(candidates, key=lambda item: (item[0], item[1]))
    return {"source_time": _format_utc(source_time), "value": value}


def resample_observations(
    height_observations: list[dict[str, Any]],
    discharge_observations: list[dict[str, Any]],
    cache_date: date,
    *,
    tolerance: timedelta = OBSERVATION_TOLERANCE,
) -> list[dict[str, Any]]:
    """Produit 28 cibles UTC sans interpolation et avec sources H/Q séparées."""

    samples = []
    for target in observation_targets(cache_date):
        height = _nearest_observation(height_observations, target, tolerance)
        discharge = _nearest_observation(discharge_observations, target, tolerance)
        samples.append({
            "target_time": _format_utc(target),
            "height_m": height["value"] if height else None,
            "height_source_time": height["source_time"] if height else None,
            "discharge_m3_s": discharge["value"] if discharge else None,
            "discharge_source_time": discharge["source_time"] if discharge else None,
        })
    return samples


def _latest_observation(
    observations: list[dict[str, Any]],
    *,
    not_after: datetime,
    not_before: datetime,
) -> dict[str, Any] | None:
    candidates = []
    for observation in observations:
        source_time = _parse_datetime(observation.get("source_time"))
        value = _number(observation.get("value"))
        if (
            source_time is not None
            and value is not None
            and not_before <= source_time <= not_after
        ):
            candidates.append((source_time, value))
    if not candidates:
        return None
    source_time, value = max(candidates, key=lambda item: item[0])
    return {"source_time": _format_utc(source_time), "value": value}


def parse_forecast(payload: dict[str, Any]) -> dict[str, Any]:
    simulation = payload.get("Simul")
    if not isinstance(simulation, dict):
        raise VigicruesDataError("Prévision Vigicrues invalide.")
    quantity = _clean_string(simulation.get("GrdSimul"))
    if quantity not in {"H", "Q"}:
        raise VigicruesDataError("Prévision Vigicrues invalide.")
    points = []
    raw_points = simulation.get("Prevs")
    if not isinstance(raw_points, list):
        raise VigicruesDataError("Prévision Vigicrues invalide.")
    for item in raw_points:
        if not isinstance(item, dict):
            continue
        forecast_time = _parse_datetime(item.get("DtPrev"))
        if forecast_time is None:
            continue
        minimum = _number(item.get("ResMinPrev"))
        mean = _number(item.get("ResMoyPrev"))
        maximum = _number(item.get("ResMaxPrev"))
        if minimum is None and mean is None and maximum is None:
            continue
        points.append({
            "forecast_time": _format_utc(forecast_time),
            "minimum": minimum,
            "mean": mean,
            "maximum": maximum,
        })
    produced_at = _parse_datetime(simulation.get("DtProdSimul"))
    return {
        "kind": "forecast",
        "source": "Vigicrues",
        "quantity": "height" if quantity == "H" else "discharge",
        "unit": "m" if quantity == "H" else "m3/s",
        "produced_at": _format_utc(produced_at) if produced_at else None,
        "points": points,
    }


def parse_forecast_catalog(payload: dict[str, Any]) -> set[str]:
    raw_entries = payload.get("ListEntVigiCru")
    if not isinstance(raw_entries, list):
        raise VigicruesDataError("Catalogue de prévisions Vigicrues invalide.")
    return {
        code
        for item in raw_entries
        if isinstance(item, dict)
        if (code := _clean_string(item.get("CdEntVigiCru")))
        and item.get("TypEntVigiCru") in {7, "7"}
    }


class VigicruesClient:
    """Client borné Hub'Eau/Vigicrues du dataset hydrométrique territorial."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout: float = VIGICRUES_TIMEOUT_SECONDS,
    ):
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get_json(
        self, url, *, params=None, allowed_urls,
    ):
        source = "hubeau" if urlsplit(url).hostname == "hubeau.eaufrance.fr" else "vigicrues"
        with observe(source):
            return self._observed_get_json(url, params=params, allowed_urls=allowed_urls)

    def _observed_get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        allowed_urls: set[str],
    ) -> dict[str, Any]:
        try:
            response = self.session.get(
                url,
                params=params,
                headers={"Accept": "application/json"},
                timeout=self.timeout,
                stream=True,
            )
        except requests.Timeout as exc:
            raise VigicruesServiceUnavailableError(
                "Vigicrues n’a pas répondu à temps."
            ) from exc
        except requests.RequestException as exc:
            raise VigicruesServiceUnavailableError(
                "Vigicrues est temporairement indisponible."
            ) from exc
        try:
            final_url = response.url.split("?", 1)[0]
            parsed = urlsplit(final_url)
            if (
                final_url not in allowed_urls
                or parsed.scheme != "https"
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port not in (None, 443)
            ):
                raise VigicruesServiceUnavailableError(
                    "Destination du service hydrométrique refusée."
                )
            if response.status_code not in {200, 206}:
                raise VigicruesServiceUnavailableError(
                    "Vigicrues est temporairement indisponible."
                )
            chunks: list[bytes] = []
            size = 0
            try:
                for chunk in response.iter_content(chunk_size=65_536):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > VIGICRUES_MAX_RESPONSE_BYTES:
                        raise VigicruesDataError(
                            "La réponse du référentiel hydrométrique est trop "
                            "volumineuse."
                        )
                    chunks.append(chunk)
            except requests.Timeout as exc:
                raise VigicruesServiceUnavailableError(
                    "Vigicrues n’a pas répondu à temps."
                ) from exc
            except requests.RequestException as exc:
                raise VigicruesServiceUnavailableError(
                    "Vigicrues est temporairement indisponible."
                ) from exc
            try:
                payload = json.loads(b"".join(chunks).decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise VigicruesDataError(
                    "Réponse du référentiel hydrométrique invalide."
                ) from exc
            if not isinstance(payload, dict):
                raise VigicruesDataError(
                    "Réponse du référentiel hydrométrique invalide."
                )
            return payload
        finally:
            response.close()

    def stations(self, bbox: tuple[float, float, float, float]) -> list[dict[str, Any]]:
        vigicrues = self._get_json(
            VIGICRUES_STATIONS_URL,
            allowed_urls={VIGICRUES_STATIONS_URL, VIGICRUES_STATIONS_REDIRECT_URL},
        )
        hydrometry = self._get_json(
            HUBEAU_STATIONS_URL,
            params={
                "bbox": ",".join(f"{coordinate:.7f}" for coordinate in bbox),
                "size": HUBEAU_PAGE_SIZE,
            },
            allowed_urls={HUBEAU_STATIONS_URL},
        )
        returned = hydrometry.get("data")
        count = hydrometry.get("count")
        if (
            isinstance(returned, list)
            and isinstance(count, int)
            and count > len(returned)
        ):
            raise VigicruesDataError(
                "Le territoire contient trop de stations hydrométriques."
            )
        return parse_vigicrues_stations(vigicrues, hydrometry)

    def observations(
        self,
        code_hydro: str,
        quantity: str,
        *,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        payload = self._get_json(
            HUBEAU_OBSERVATIONS_URL,
            params={
                "code_entite": code_hydro,
                "grandeur_hydro": quantity,
                "date_debut_obs": _format_utc(start_time),
                "date_fin_obs": _format_utc(end_time),
                "sort": "asc",
                "size": HUBEAU_OBSERVATION_PAGE_SIZE,
                "format": "json",
            },
            allowed_urls={HUBEAU_OBSERVATIONS_URL},
        )
        returned = payload.get("data")
        count = payload.get("count")
        if (
            isinstance(returned, list)
            and isinstance(count, int)
            and count > len(returned)
        ):
            raise VigicruesDataError(
                "La série d'observations Hub'Eau est trop volumineuse."
            )
        return parse_hubeau_observation_series(payload, quantity)

    def forecast_catalog(self, quantity: str) -> set[str]:
        payload = self._get_json(
            VIGICRUES_FORECASTS_URL,
            params={"GrdSimul": quantity, "FormatDate": "iso"},
            allowed_urls={VIGICRUES_FORECASTS_URL},
        )
        return parse_forecast_catalog(payload)

    def forecast(self, code_vigicrues: str, quantity: str) -> dict[str, Any]:
        payload = self._get_json(
            VIGICRUES_FORECASTS_URL,
            params={
                "CdEntVigiCru": code_vigicrues,
                "TypEntVigiCru": "7",
                "GrdSimul": quantity,
                "FormatDate": "iso",
            },
            allowed_urls={VIGICRUES_FORECASTS_URL},
        )
        return parse_forecast(payload)


def _partial_error(kind: str, quantity: str) -> dict[str, str]:
    return {
        "kind": kind,
        "quantity": quantity,
        "error": "unavailable",
    }


def _load_station_observations(
    client: VigicruesClient,
    station: dict[str, Any],
    *,
    start_time: datetime,
    end_time: datetime,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]]]:
    observations: dict[str, list[dict[str, Any]]] = {
        "height": [],
        "discharge": [],
    }
    errors = []
    for api_quantity, quantity in (("H", "height"), ("Q", "discharge")):
        try:
            observations[quantity] = client.observations(
                station["code_hydro"],
                api_quantity,
                start_time=start_time,
                end_time=end_time,
            )
        except VigicruesError:
            errors.append(_partial_error("observation", quantity))
    return observations, errors


def _territory_identity(
    territory: dict[str, Any],
    bbox: tuple[float, float, float, float],
) -> tuple[str | None, str]:
    feature = territory["features"][0]
    properties = feature.get("properties")
    label = _clean_string(properties.get("libelle")) if isinstance(
        properties, dict
    ) else None
    geometry_payload = json.dumps(
        feature["geometry"], sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    fingerprint = hashlib.sha256(geometry_payload).hexdigest()
    return label, fingerprint


def build_territorial_dataset(
    territory: dict[str, Any],
    client: VigicruesClient,
    cache_date: date,
    *,
    now: datetime | None = None,
    observation_workers: int = OBSERVATION_WORKERS,
) -> dict[str, Any]:
    bbox = territory_bbox(territory)
    geometry = _territory_geometry(territory)
    stations = [
        station
        for station in client.stations(bbox)
        if point_in_territory(
            station["longitude"], station["latitude"], geometry
        )
    ]
    label, fingerprint = _territory_identity(territory, bbox)
    generated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    first_target = observation_targets(cache_date)[0]
    observation_start = first_target - OBSERVATION_TOLERANCE
    observation_results: dict[
        str,
        tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]]],
    ] = {}
    if stations:
        with ThreadPoolExecutor(
            max_workers=max(1, min(observation_workers, len(stations))),
            thread_name_prefix="hydrometry-observations",
        ) as executor:
            futures = {
                station["id"]: executor.submit(
                    _load_station_observations,
                    client,
                    station,
                    start_time=observation_start,
                    end_time=generated_at,
                )
                for station in stations
            }
            for station_id, future in futures.items():
                observation_results[station_id] = future.result()

    dataset_errors: list[dict[str, str]] = []
    forecast_catalogs: dict[str, set[str]] = {"height": set(), "discharge": set()}
    if stations:
        for api_quantity, quantity in (("H", "height"), ("Q", "discharge")):
            try:
                forecast_catalogs[quantity] = client.forecast_catalog(api_quantity)
            except VigicruesError:
                dataset_errors.append(
                    _partial_error("forecast_catalog", quantity)
                )

    normalized_stations = []
    for station in stations:
        observations, errors = observation_results[station["id"]]
        latest_height = _latest_observation(
            observations["height"],
            not_after=generated_at,
            not_before=first_target,
        )
        latest_discharge = _latest_observation(
            observations["discharge"],
            not_after=generated_at,
            not_before=first_target,
        )
        forecasts: dict[str, dict[str, Any] | None] = {
            "height": None,
            "discharge": None,
        }
        for api_quantity, quantity in (("H", "height"), ("Q", "discharge")):
            if station["code_vigicrues"] not in forecast_catalogs[quantity]:
                continue
            try:
                forecasts[quantity] = client.forecast(
                    station["code_vigicrues"], api_quantity
                )
            except VigicruesError:
                errors.append(_partial_error("forecast", quantity))
        normalized_stations.append({
            **station,
            "observations": {
                "kind": "observation",
                "source": "Hub'Eau Hydrométrie",
                "source_endpoint": "observations_tr",
                "window_days": OBSERVATION_DAYS,
                "target_hours_utc": list(OBSERVATION_TARGET_HOURS),
                "tolerance_minutes": int(
                    OBSERVATION_TOLERANCE.total_seconds() / 60
                ),
                "height_unit": "m",
                "discharge_unit": "m3/s",
                "native_height_unit": "mm",
                "native_discharge_unit": "l/s",
                "samples": resample_observations(
                    observations["height"], observations["discharge"], cache_date
                ),
                "latest": {
                    "height_m": latest_height["value"] if latest_height else None,
                    "height_source_time": (
                        latest_height["source_time"] if latest_height else None
                    ),
                    "discharge_m3_s": (
                        latest_discharge["value"] if latest_discharge else None
                    ),
                    "discharge_source_time": (
                        latest_discharge["source_time"]
                        if latest_discharge else None
                    ),
                },
            },
            "forecasts": forecasts,
            "errors": errors,
        })

    return {
        "cache_date": cache_date.isoformat(),
        "generated_at": _format_utc(generated_at),
        "territoire": {
            "label": label,
            "bbox": list(bbox),
            "fingerprint": fingerprint,
        },
        "provenance": {
            "station_membership": "Vigicrues API services v1.1",
            "station_coordinates": "Hub'Eau Hydrométrie API v2",
            "observations": "Hub'Eau Hydrométrie",
            "observation_endpoint": "observations_tr",
            "observation_native_units": {
                "height": "mm",
                "discharge": "l/s",
            },
            "forecasts": "Vigicrues",
            "forecast_endpoint": "prevision.json",
        },
        "stations": normalized_stations,
        "errors": dataset_errors,
    }


def _cache_day(day_provider: Any = None) -> date:
    value = day_provider() if day_provider else datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).date()
    if isinstance(value, date):
        return value
    raise TypeError("Le fournisseur de date Vigicrues doit retourner date ou datetime.")


def clear_vigicrues_cache() -> None:
    with _dataset_condition:
        _dataset_cache.clear()
        _dataset_condition.notify_all()


def fetch_vigicrues_territorial_dataset(
    connection: Any,
    *,
    client: VigicruesClient | None = None,
    day_provider: Any = None,
) -> dict[str, Any]:
    territory = fetch_territoire_administratif(connection)
    bbox = territory_bbox(territory)
    _, fingerprint = _territory_identity(territory, bbox)
    cache_date = _cache_day(day_provider)
    key = (
        cache_date.isoformat(),
        *(round(coordinate, 8) for coordinate in bbox),
        fingerprint,
    )
    while True:
        with _dataset_condition:
            stale_keys = [
                cache_key
                for cache_key in _dataset_cache
                if cache_key[0] != key[0]
            ]
            for stale_key in stale_keys:
                del _dataset_cache[stale_key]
            entry = _dataset_cache.get(key)
            if entry is None:
                entry = DatasetCacheEntry()
                _dataset_cache[key] = entry
                break
            if entry.status == "loading":
                _dataset_condition.wait()
                continue
            if entry.payload is not None:
                return entry.payload
            del _dataset_cache[key]

    try:
        payload = build_territorial_dataset(
            territory,
            client or VigicruesClient(),
            cache_date,
        )
    except Exception:
        with _dataset_condition:
            entry.status = "failed"
            if _dataset_cache.get(key) is entry:
                del _dataset_cache[key]
            _dataset_condition.notify_all()
        raise
    with _dataset_condition:
        entry.status = "ready"
        entry.payload = payload
        _dataset_condition.notify_all()
    return payload


def dataset_stations_geojson(dataset: dict[str, Any]) -> dict[str, Any]:
    features = []
    for station in dataset.get("stations", []):
        latest = station["observations"]["latest"]
        properties = {
            key: station.get(key)
            for key in (
                "id",
                "code_vigicrues",
                "code_hydro",
                "nom",
                "cours_eau",
                "troncon",
                "territoire",
                "longitude",
                "latitude",
            )
        }
        properties.update({
            "latest_height_m": latest["height_m"],
            "latest_height_source_time": latest["height_source_time"],
            "latest_discharge_m3_s": latest["discharge_m3_s"],
            "latest_discharge_source_time": latest["discharge_source_time"],
        })
        features.append({
            "type": "Feature",
            "id": station["id"],
            "geometry": {
                "type": "Point",
                "coordinates": [station["longitude"], station["latitude"]],
            },
            "properties": properties,
        })
    return {
        "type": "FeatureCollection",
        "cache_date": dataset["cache_date"],
        "features": features,
    }


def fetch_vigicrues_stations(
    connection: Any,
    *,
    client: VigicruesClient | None = None,
    day_provider: Any = None,
) -> dict[str, Any]:
    dataset = fetch_vigicrues_territorial_dataset(
        connection,
        client=client,
        day_provider=day_provider,
    )
    return dataset_stations_geojson(dataset)
