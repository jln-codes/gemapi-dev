"""Service backend de précipitations AROME/ARPEGE pour digues-app."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
import logging
import math
import os
from pathlib import Path
import re
import struct
import tempfile
from threading import Condition
import time
from typing import Any, Iterable
from xml.etree import ElementTree

import requests

from .database import WebDatabaseError
from .service_observations import observe


LOGGER = logging.getLogger(__name__)


ARPEGE_API_KEY_ENV = "ARPEGE_API_KEY"
AROME_API_KEY_ENV = "AROME_API_KEY"
WCS_ENDPOINT = (
    "https://public-api.meteofrance.fr/public/arpege/1.0/wcs/"
    "MF-NWP-GLOBAL-ARPEGE-01-EUROPE-WCS"
)
AROME_WCS_ENDPOINT = (
    "https://public-api.meteofrance.fr/public/arome/1.0/wcs/"
    "MF-NWP-HIGHRES-AROME-001-FRANCE-WCS"
)
MODEL = "ARPEGE"
AROME_MODEL = "AROME"
PRODUCT = "TOTAL_WATER_PRECIPITATION__GROUND_OR_WATER_SURFACE"
PERIOD = "P1D"
ACCUMULATION_HOURS = 24
UNIT = "mm"
DAILY_TARGET_LEAD_HOURS = (24, 48, 72, 96)
DAILY_STEP_MAX_OFFSET_HOURS = 6
PRELOAD_MAX_WORKERS = 1
HTTP_TIMEOUT = (15, 120)
HTTP_RETRY_STATUSES = {502, 503, 504}
HTTP_REFUSAL_REASONS = {
    400: "requête fournisseur invalide",
    401: "authentification rejetée",
    403: "ressource non autorisée",
    404: "ressource absente",
    429: "limitation ou quota fournisseur",
}
HTTP_NO_RETRY_STATUSES = set(HTTP_REFUSAL_REASONS)
HTTP_RETRY_DELAYS = (1, 2)
MAX_XML_BYTES = 25 * 1024 * 1024
MAX_TIFF_BYTES = 50 * 1024 * 1024


class WeatherConfigurationError(RuntimeError):
    """Configuration météo absente côté backend."""


class WeatherServiceUnavailableError(RuntimeError):
    """Service WCS Météo-France indisponible ou réponse inexploitable."""

    def __init__(
        self,
        message: str,
        *,
        upstream_status: int | None = None,
        operation: str | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(message)
        self.upstream_status = upstream_status
        self.operation = operation
        self.model = model


class WeatherDataUnavailableError(RuntimeError):
    """Produit météo attendu absent ou échéance inexploitable."""


class TerritoryRequiredError(RuntimeError):
    """Aucun territoire administratif n'est disponible pour borner la météo."""


class InvalidWeatherStepError(RuntimeError):
    """Échéance absente de la session météo quotidienne."""


@dataclass(frozen=True)
class Coverage:
    coverage_id: str
    run: datetime
    period: str


@dataclass(frozen=True)
class CoverageDescription:
    coverage_id: str
    times: tuple[datetime, ...]


@dataclass(frozen=True)
class ForecastStep:
    index: int
    label: str
    valid_time: datetime
    lead_hours: float
    model: str = MODEL
    run: datetime | None = None
    coverage_id: str | None = None


@dataclass(frozen=True)
class DailyWeatherSession:
    cache_date: date
    coverage: Coverage
    steps: tuple[ForecastStep, ...]


@dataclass(frozen=True)
class TerritoryBbox:
    west: float
    south: float
    east: float
    north: float

    def as_tuple(self) -> tuple[float, float, float, float]:
        return self.west, self.south, self.east, self.north


@dataclass
class GridCacheEntry:
    status: str = "pending"
    payload: dict[str, Any] | None = None
    error: Exception | None = None


@dataclass(frozen=True)
class WeatherClients:
    arpege: MeteoFranceWcsClient
    arome: MeteoFranceWcsClient | None = None


_weather_condition = Condition()
_weather_session: DailyWeatherSession | None = None
_weather_session_loading = False
_weather_cache: dict[tuple[Any, ...], GridCacheEntry] = {}
_scheduled_preloads: set[tuple[Any, ...]] = set()
_weather_generation = 0
_preload_executor = ThreadPoolExecutor(
    max_workers=PRELOAD_MAX_WORKERS,
    thread_name_prefix="weather-preload",
)


TERRITORY_BBOX_SQL = """
    SELECT
        ST_XMin(box.extent),
        ST_YMin(box.extent),
        ST_XMax(box.extent),
        ST_YMax(box.extent)
    FROM (
        SELECT ST_Extent(ST_Transform(geometry, 4326)) AS extent
        FROM technique.territoires_administratifs
    ) AS box
"""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_text(root: ElementTree.Element, name: str) -> str | None:
    for element in root.iter():
        if _local_name(element.tag) == name and element.text and element.text.strip():
            return element.text.strip()
    return None


def _parse_utc(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_meteofrance_api_key() -> str:
    """Charge la clé ARPEGE côté serveur sans l'exposer aux logs."""

    key = _load_api_key(ARPEGE_API_KEY_ENV)
    if key:
        return key
    raise WeatherConfigurationError("Clé API ARPEGE absente côté serveur.")


def load_arome_api_key() -> str | None:
    """Charge la clé AROME facultative ; son absence laisse ARPEGE autonome."""

    return _load_api_key(AROME_API_KEY_ENV)


def _load_api_key(name: str) -> str | None:
    """Read the key supplied to this backend process; never inspect files."""

    return os.getenv(name, "").strip() or None


def parse_coverage_id(coverage_id: str) -> Coverage | None:
    prefix = f"{PRODUCT}___"
    if not coverage_id.startswith(prefix):
        return None
    remainder = coverage_id[len(prefix) :]
    try:
        run_text, period = remainder.rsplit("_", 1)
    except ValueError:
        return None
    run_match = re.fullmatch(
        r"(\d{4}-\d{2}-\d{2})T(\d{2})[.:](\d{2})[.:](\d{2})Z",
        run_text,
    )
    if not run_match:
        return None
    run = datetime.strptime("".join(run_match.groups()), "%Y-%m-%d%H%M%S")
    return Coverage(
        coverage_id=coverage_id,
        run=run.replace(tzinfo=timezone.utc),
        period=period,
    )


def parse_capabilities(payload: bytes) -> list[Coverage]:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise WeatherServiceUnavailableError(
            "Réponse GetCapabilities Météo-France invalide."
        ) from exc
    coverages: list[Coverage] = []
    for element in root.iter():
        if _local_name(element.tag) != "CoverageId" or not element.text:
            continue
        parsed = parse_coverage_id(element.text.strip())
        if parsed and parsed.period == PERIOD:
            coverages.append(parsed)
    if not coverages:
        raise WeatherDataUnavailableError("Produit Météo-France P1D indisponible.")
    return coverages


def select_latest_p1d_coverage(coverages: Iterable[Coverage]) -> Coverage:
    p1d = [coverage for coverage in coverages if coverage.period == PERIOD]
    if not p1d:
        raise WeatherDataUnavailableError("Aucun run ARPEGE P1D disponible.")
    return max(p1d, key=lambda coverage: coverage.run)


def select_latest_complete_daily_session(
    client: MeteoFranceWcsClient,
    coverages: Iterable[Coverage],
    cache_date: date,
) -> DailyWeatherSession:
    p1d = sorted(
        (coverage for coverage in coverages if coverage.period == PERIOD),
        key=lambda coverage: coverage.run,
        reverse=True,
    )
    if not p1d:
        raise WeatherDataUnavailableError("Aucun run ARPEGE P1D disponible.")

    incomplete: list[str] = []
    for coverage in p1d:
        try:
            description = parse_description(
                client.describe_coverage(coverage.coverage_id),
                coverage.run,
            )
            steps = select_daily_forecast_steps(description, coverage.run)
        except WeatherDataUnavailableError as exc:
            incomplete.append(
                f"{_format_utc(coverage.run)} ({coverage.coverage_id}) : {exc}"
            )
            continue
        if len(steps) == len(DAILY_TARGET_LEAD_HOURS):
            return DailyWeatherSession(
                cache_date=cache_date,
                coverage=coverage,
                steps=steps,
            )
        incomplete.append(
            f"{_format_utc(coverage.run)} ({coverage.coverage_id}) : "
            f"{len(steps)}/{len(DAILY_TARGET_LEAD_HOURS)} échéances quotidiennes"
        )

    details = "; ".join(incomplete)
    raise WeatherDataUnavailableError(
        "Aucun run ARPEGE P1D ne fournit les 4 échéances quotidiennes "
        f"attendues autour de H+24, H+48, H+72 et H+96. {details}"
    )


def parse_description(payload: bytes, run: datetime) -> CoverageDescription:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise WeatherServiceUnavailableError(
            "Réponse DescribeCoverage Météo-France invalide."
        ) from exc
    coverage_id = _first_text(root, "CoverageId")
    if not coverage_id:
        raise WeatherDataUnavailableError("DescribeCoverage sans CoverageId.")
    coefficients: list[int] = []
    for axis in root.iter():
        if _local_name(axis.tag) != "GeneralGridAxis":
            continue
        if _first_text(axis, "gridAxesSpanned") != "time":
            continue
        values = _first_text(axis, "coefficients") or ""
        try:
            coefficients = [int(float(value)) for value in values.split()]
        except ValueError as exc:
            raise WeatherDataUnavailableError(
                "Échéances temporelles Météo-France invalides."
            ) from exc
        break
    if not coefficients:
        raise WeatherDataUnavailableError("Aucune échéance P1D disponible.")
    return CoverageDescription(
        coverage_id=coverage_id,
        times=tuple(run + timedelta(seconds=value) for value in coefficients),
    )


def select_p1d_valid_time(description: CoverageDescription, run: datetime) -> datetime:
    target = run + timedelta(hours=ACCUMULATION_HOURS)
    candidates = [value for value in description.times if value >= target]
    if not candidates:
        raise WeatherDataUnavailableError(
            "Aucune échéance P1D disponible à partir de H+24."
        )
    return min(candidates)


def select_daily_forecast_steps(
    description: CoverageDescription,
    run: datetime,
) -> tuple[ForecastStep, ...]:
    """Retient au plus une échéance réelle près de H+24, H+48, H+72 et H+96."""

    available = sorted(set(description.times))
    selected: list[ForecastStep] = []
    used: set[datetime] = set()
    for day_index, target_hours in enumerate(DAILY_TARGET_LEAD_HOURS, start=1):
        candidates = [value for value in available if value not in used]
        if not candidates:
            break
        candidate = min(
            candidates,
            key=lambda value: (
                abs((value - run).total_seconds() / 3600 - target_hours),
                value,
            ),
        )
        lead_hours = (candidate - run).total_seconds() / 3600
        if abs(lead_hours - target_hours) > DAILY_STEP_MAX_OFFSET_HOURS:
            continue
        used.add(candidate)
        selected.append(
            ForecastStep(
                index=len(selected),
                label=f"J+{day_index}",
                valid_time=candidate,
                lead_hours=lead_hours,
                model=MODEL,
                run=run,
                coverage_id=description.coverage_id,
            )
        )
    if not selected:
        raise WeatherDataUnavailableError(
            "Aucune échéance quotidienne P1D disponible entre H+24 et H+96."
        )
    return tuple(selected)


def fetch_territory_bbox(connection: Any) -> TerritoryBbox:
    try:
        with connection.cursor() as cursor:
            cursor.execute(TERRITORY_BBOX_SQL)
            row = cursor.fetchone()
    except Exception as exc:
        raise WebDatabaseError(
            "Lecture de l'emprise du territoire administratif impossible."
        ) from exc
    if not row or any(value is None for value in row):
        raise TerritoryRequiredError(
            "Importez d'abord un territoire administratif pour afficher la météo."
        )
    bbox = TerritoryBbox(*(float(value) for value in row))
    if not (bbox.west < bbox.east and bbox.south < bbox.north):
        raise WeatherDataUnavailableError("Emprise du territoire invalide.")
    return bbox


class MeteoFranceWcsClient:
    def __init__(
        self,
        api_key: str,
        session: requests.Session | None = None,
        *,
        sleep=time.sleep,
        endpoint: str = WCS_ENDPOINT,
        model: str = MODEL,
    ) -> None:
        self._session = session or requests.Session()
        self._headers = {"apikey": api_key}
        self._sleep = sleep
        self._endpoint = endpoint.rstrip("/")
        self.model = model

    def _request(
        self,
        operation: str,
        params: list[tuple[str, str]],
        *,
        stream: bool,
        context: str | None = None,
    ):
        label = operation if context is None else f"{operation} {context}"
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                response = self._session.get(
                    f"{self._endpoint}/{operation}",
                    params=params,
                    headers=self._headers,
                    timeout=HTTP_TIMEOUT,
                    stream=stream,
                )
            except requests.Timeout as exc:
                LOGGER.warning(
                    "Délai Météo-France dépassé: modèle=%s opération=%s",
                    self.model,
                    label,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
                raise WeatherServiceUnavailableError(
                    "Météo-France n’a pas répondu à temps.",
                    operation=operation,
                    model=self.model,
                ) from exc
            except requests.RequestException as exc:
                LOGGER.warning(
                    "Requête Météo-France impossible: modèle=%s opération=%s: %s",
                    self.model,
                    label,
                    exc,
                )
                raise WeatherServiceUnavailableError(
                    "Météo-France est temporairement indisponible.",
                    operation=operation,
                    model=self.model,
                ) from exc
            status = response.status_code
            if status < 400:
                return response
            if status in HTTP_NO_RETRY_STATUSES:
                reason = HTTP_REFUSAL_REASONS[status]
                LOGGER.warning(
                    "Requête Météo-France refusée: modèle=%s opération=%s "
                    "HTTP %s (%s)",
                    self.model,
                    label,
                    status,
                    reason,
                )
                raise WeatherServiceUnavailableError(
                    (
                        "Météo-France limite temporairement les requêtes."
                        if status == 429
                        else "Météo-France a refusé la demande."
                    ),
                    upstream_status=status,
                    operation=operation,
                    model=self.model,
                )
            if status not in HTTP_RETRY_STATUSES:
                reason = (
                    "indisponibilité fournisseur"
                    if 500 <= status <= 599
                    else "statut fournisseur inattendu"
                )
                LOGGER.warning(
                    "Réponse Météo-France inattendue: modèle=%s opération=%s "
                    "HTTP %s (%s)",
                    self.model,
                    label,
                    status,
                    reason,
                )
                raise WeatherServiceUnavailableError(
                    "Météo-France est temporairement indisponible.",
                    upstream_status=status,
                    operation=operation,
                    model=self.model,
                )
            if attempt == max_attempts:
                LOGGER.warning(
                    "Météo-France indisponible après %s tentatives: "
                    "modèle=%s opération=%s HTTP %s",
                    max_attempts,
                    self.model,
                    label,
                    status,
                )
                raise WeatherServiceUnavailableError(
                    "Météo-France est temporairement indisponible.",
                    upstream_status=status,
                    operation=operation,
                    model=self.model,
                )
            self._sleep(HTTP_RETRY_DELAYS[attempt - 1])
        raise WeatherServiceUnavailableError(
            "Météo-France est temporairement indisponible."
        )

    @observe("meteofrance")
    def _xml(
        self,
        operation: str,
        params: list[tuple[str, str]],
        *,
        context: str | None = None,
    ) -> bytes:
        response = self._request(
            operation,
            params,
            stream=False,
            context=context,
        )
        payload = response.content
        if len(payload) > MAX_XML_BYTES:
            LOGGER.warning("Réponse Météo-France trop volumineuse pour %s", operation)
            raise WeatherServiceUnavailableError(
                "La réponse de Météo-France est invalide."
            )
        return payload

    def get_capabilities(self) -> bytes:
        return self._xml(
            "GetCapabilities",
            [("service", "WCS"), ("version", "2.0.1"), ("language", "fre")],
        )

    def describe_coverage(self, coverage_id: str) -> bytes:
        return self._xml(
            "DescribeCoverage",
            [
                ("service", "WCS"),
                ("version", "2.0.1"),
                ("coverageID", coverage_id),
            ],
            context=coverage_id,
        )

    @observe("meteofrance")
    def download_coverage(
        self,
        coverage_id: str,
        valid_time: datetime,
        bbox: TerritoryBbox,
        destination: Path,
    ) -> None:
        params = [
            ("service", "WCS"),
            ("version", "2.0.1"),
            ("coverageid", coverage_id),
            ("subset", f"long({bbox.west},{bbox.east})"),
            ("subset", f"lat({bbox.south},{bbox.north})"),
            ("subset", f"time({_format_utc(valid_time)})"),
            ("format", "image/tiff"),
        ]
        response = self._request(
            "GetCoverage",
            params,
            stream=True,
            context=coverage_id,
        )
        size = 0
        with destination.open("wb") as output:
            for chunk in response.iter_content(64 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_TIFF_BYTES:
                    raise WeatherServiceUnavailableError(
                        "GeoTIFF Météo-France trop volumineux."
                    )
                output.write(chunk)
        if size == 0:
            raise WeatherServiceUnavailableError(
                "Météo-France a retourné un GeoTIFF vide."
            )


def _require_gdal():
    try:
        from osgeo import gdal
    except Exception as exc:
        raise WeatherConfigurationError(
            "GDAL est requis côté serveur pour lire les rasters météo."
        ) from exc
    gdal.UseExceptions()
    return gdal


def _read_pixel_value(gdal: Any, band: Any, x: int, y: int) -> float:
    formats = {
        gdal.GDT_Byte: "B",
        gdal.GDT_UInt16: "H",
        gdal.GDT_Int16: "h",
        gdal.GDT_UInt32: "I",
        gdal.GDT_Int32: "i",
        gdal.GDT_Float32: "f",
        gdal.GDT_Float64: "d",
    }
    if hasattr(gdal, "GDT_UInt64"):
        formats[gdal.GDT_UInt64] = "Q"
        formats[gdal.GDT_Int64] = "q"
    value_format = formats.get(band.DataType)
    if not value_format:
        raise WeatherServiceUnavailableError("Type raster météo non pris en charge.")
    payload = band.ReadRaster(x, y, 1, 1, buf_type=band.DataType)
    if not payload:
        raise WeatherServiceUnavailableError("Lecture d'une cellule météo impossible.")
    return float(struct.unpack(f"={value_format}", payload)[0])


def raster_to_cells(
    path: Path,
) -> tuple[list[dict[str, Any]], tuple[float, float, float, float], tuple[float, float]]:
    gdal = _require_gdal()
    dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    if dataset is None or dataset.RasterCount != 1:
        raise WeatherServiceUnavailableError("GeoTIFF météo invalide.")
    band = dataset.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    transform = dataset.GetGeoTransform()
    width = dataset.RasterXSize
    height = dataset.RasterYSize
    cells: list[dict[str, Any]] = []
    bounds_points: list[tuple[float, float]] = []

    for y in range(height):
        for x in range(width):
            west, north = gdal.ApplyGeoTransform(transform, x, y)
            east, south = gdal.ApplyGeoTransform(transform, x + 1, y + 1)
            cell_west, cell_east = sorted((float(west), float(east)))
            cell_south, cell_north = sorted((float(south), float(north)))
            bounds_points.extend(
                [
                    (cell_west, cell_south),
                    (cell_east, cell_north),
                ]
            )
            value = _read_pixel_value(gdal, band, x, y)
            if nodata is not None and (
                (math.isnan(nodata) and math.isnan(value)) or value == nodata
            ):
                cells.append(
                    {
                        "bounds": [[cell_south, cell_west], [cell_north, cell_east]],
                        "value_mm": None,
                    }
                )
                continue
            cells.append(
                {
                    "bounds": [[cell_south, cell_west], [cell_north, cell_east]],
                    "value_mm": value,
                }
            )

    xs = [point[0] for point in bounds_points]
    ys = [point[1] for point in bounds_points]
    return (
        cells,
        (min(xs), min(ys), max(xs), max(ys)),
        (abs(float(transform[1])), abs(float(transform[5]))),
    )


def clear_weather_cache() -> None:
    global _weather_generation, _weather_session, _weather_session_loading
    with _weather_condition:
        _weather_generation += 1
        _weather_session = None
        _weather_session_loading = False
        _weather_cache.clear()
        _scheduled_preloads.clear()
        _weather_condition.notify_all()


def _cache_date(day_provider: Any = None) -> date:
    value = day_provider() if day_provider else datetime.now().astimezone()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise TypeError("Le fournisseur de date météo doit retourner date ou datetime.")


def _make_clients(
    client: MeteoFranceWcsClient | None,
    arome_client: MeteoFranceWcsClient | None,
) -> WeatherClients:
    arpege = client or MeteoFranceWcsClient(load_meteofrance_api_key())
    if arome_client is not None:
        return WeatherClients(arpege=arpege, arome=arome_client)
    # An injected ARPEGE client means a deterministic ARPEGE-only call, as used
    # by existing tests and deployments embedding this service.
    if client is not None:
        return WeatherClients(arpege=arpege)
    arome_key = load_arome_api_key()
    if not arome_key:
        LOGGER.info("AROME désactivé : %s absente ; fallback ARPEGE intégral.", AROME_API_KEY_ENV)
        return WeatherClients(arpege=arpege)
    return WeatherClients(
        arpege=arpege,
        arome=MeteoFranceWcsClient(
            arome_key,
            endpoint=AROME_WCS_ENDPOINT,
            model=AROME_MODEL,
        ),
    )


def select_exact_arome_steps(
    client: MeteoFranceWcsClient,
    arpege_steps: Iterable[ForecastStep],
) -> dict[datetime, ForecastStep]:
    """Retient, par cible canonique, le run AROME exact le plus récent."""

    targets = {step.valid_time for step in arpege_steps}
    selected: dict[datetime, ForecastStep] = {}
    coverages = sorted(
        parse_capabilities(client.get_capabilities()),
        key=lambda coverage: coverage.run,
        reverse=True,
    )
    for coverage in coverages:
        if len(selected) == len(targets):
            break
        try:
            description = parse_description(
                client.describe_coverage(coverage.coverage_id),
                coverage.run,
            )
        except (WeatherDataUnavailableError, WeatherServiceUnavailableError) as exc:
            LOGGER.warning(
                "Coverage AROME P1D inexploitable pour le run %s : %s",
                _format_utc(coverage.run),
                exc,
            )
            continue
        available = set(description.times)
        for target in sorted(targets - selected.keys()):
            if target not in available:
                continue
            selected[target] = ForecastStep(
                index=0,
                label="",
                valid_time=target,
                model=AROME_MODEL,
                run=coverage.run,
                lead_hours=(target - coverage.run).total_seconds() / 3600,
                coverage_id=coverage.coverage_id,
            )
    return selected


def _prefer_arome_steps(
    session: DailyWeatherSession,
    arome_client: MeteoFranceWcsClient | None,
) -> DailyWeatherSession:
    if arome_client is None:
        return session
    try:
        selections = select_exact_arome_steps(arome_client, session.steps)
    except (WeatherDataUnavailableError, WeatherServiceUnavailableError) as exc:
        LOGGER.warning("Découverte AROME impossible ; fallback ARPEGE intégral : %s", exc)
        return session
    return replace(
        session,
        steps=tuple(
            replace(
                selections[step.valid_time],
                index=step.index,
                label=step.label,
            )
            if step.valid_time in selections
            else step
            for step in session.steps
        ),
    )


def _discover_daily_session(
    client: MeteoFranceWcsClient,
    cache_date: date,
    arome_client: MeteoFranceWcsClient | None = None,
) -> DailyWeatherSession:
    canonical = select_latest_complete_daily_session(
        client,
        parse_capabilities(client.get_capabilities()),
        cache_date,
    )
    return _prefer_arome_steps(canonical, arome_client)


def _ensure_daily_session(
    clients: WeatherClients,
    cache_date: date,
) -> DailyWeatherSession:
    global _weather_generation, _weather_session, _weather_session_loading

    while True:
        with _weather_condition:
            if _weather_session and _weather_session.cache_date == cache_date:
                return _weather_session
            if _weather_session_loading:
                _weather_condition.wait()
                continue
            if _weather_session and _weather_session.cache_date != cache_date:
                _weather_generation += 1
                _weather_session = None
                _weather_cache.clear()
                _scheduled_preloads.clear()
            _weather_session_loading = True
            discovery_generation = _weather_generation

        try:
            discovered = _discover_daily_session(
                clients.arpege,
                cache_date,
                clients.arome,
            )
        except Exception:
            with _weather_condition:
                if discovery_generation == _weather_generation:
                    _weather_session_loading = False
                _weather_condition.notify_all()
            raise

        with _weather_condition:
            if discovery_generation != _weather_generation:
                _weather_session_loading = False
                _weather_condition.notify_all()
                continue
            _weather_session = discovered
            _weather_session_loading = False
            _weather_condition.notify_all()
            return discovered


def _cache_key(
    session: DailyWeatherSession,
    step: ForecastStep,
    bbox: TerritoryBbox,
) -> tuple[Any, ...]:
    return (
        session.cache_date.isoformat(),
        round(bbox.west, 8),
        round(bbox.south, 8),
        round(bbox.east, 8),
        round(bbox.north, 8),
        step.model,
        _format_utc(step.run or session.coverage.run),
        _format_utc(step.valid_time),
    )


def _preload_key(
    session: DailyWeatherSession,
    bbox: TerritoryBbox,
) -> tuple[Any, ...]:
    return (
        session.cache_date.isoformat(),
        round(bbox.west, 8),
        round(bbox.south, 8),
        round(bbox.east, 8),
        round(bbox.north, 8),
        tuple(
            (
                step.model,
                _format_utc(step.run or session.coverage.run),
                _format_utc(step.valid_time),
            )
            for step in session.steps
        ),
    )


def _build_grid_payload(
    client: MeteoFranceWcsClient,
    session: DailyWeatherSession,
    step: ForecastStep,
    bbox: TerritoryBbox,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="digues-app-weather-") as directory:
        path = Path(directory) / "precipitations.tif"
        client.download_coverage(
            step.coverage_id or session.coverage.coverage_id,
            step.valid_time,
            bbox,
            path,
        )
        cells, raster_bbox, resolution = raster_to_cells(path)

    return {
        "model": step.model,
        "product": PERIOD,
        "run": _format_utc(step.run or session.coverage.run),
        "valid_time": _format_utc(step.valid_time),
        "lead_hours": step.lead_hours,
        "accumulation_hours": ACCUMULATION_HOURS,
        "unit": UNIT,
        "cache_date": session.cache_date.isoformat(),
        "bbox": list(raster_bbox),
        "requested_bbox": list(bbox.as_tuple()),
        "resolution": list(resolution),
        "cells": cells,
    }


def _load_preload_step(
    clients: WeatherClients,
    session: DailyWeatherSession,
    step: ForecastStep,
    bbox: TerritoryBbox,
    generation: int,
) -> None:
    key = _cache_key(session, step, bbox)
    with _weather_condition:
        if generation != _weather_generation:
            return
        entry = _weather_cache.setdefault(key, GridCacheEntry())
        if entry.status in {"ready", "error", "loading"}:
            return
        entry.status = "loading"
        _weather_condition.notify_all()

    try:
        selected_client = clients.arome if step.model == AROME_MODEL else clients.arpege
        if selected_client is None:
            raise WeatherConfigurationError("Client AROME indisponible.")
        payload = _build_grid_payload(selected_client, session, step, bbox)
    except Exception as exc:
        if step.model == AROME_MODEL:
            LOGGER.warning(
                "Chargement AROME impossible pour %s ; fallback ARPEGE : %s",
                _format_utc(step.valid_time),
                exc,
            )
            fallback = ForecastStep(
                index=step.index,
                label=step.label,
                valid_time=step.valid_time,
                model=MODEL,
                run=session.coverage.run,
                lead_hours=(step.valid_time - session.coverage.run).total_seconds() / 3600,
                coverage_id=session.coverage.coverage_id,
            )
            fallback_key = _cache_key(session, fallback, bbox)
            try:
                payload = _build_grid_payload(
                    clients.arpege,
                    session,
                    fallback,
                    bbox,
                )
            except Exception as fallback_exc:
                with _weather_condition:
                    if generation == _weather_generation:
                        entry.status = "error"
                        entry.error = fallback_exc
                        _weather_condition.notify_all()
                return
            with _weather_condition:
                if generation != _weather_generation:
                    return
                fallback_entry = _weather_cache.setdefault(
                    fallback_key,
                    GridCacheEntry(),
                )
                fallback_entry.status = "ready"
                fallback_entry.payload = payload
                fallback_entry.error = None
                entry.status = "ready"
                entry.payload = payload
                entry.error = None
                _weather_condition.notify_all()
            return
        with _weather_condition:
            if generation == _weather_generation:
                entry.status = "error"
                entry.error = exc
                _weather_condition.notify_all()
        return

    with _weather_condition:
        if generation != _weather_generation:
            return
        entry.status = "ready"
        entry.payload = payload
        entry.error = None
        _weather_condition.notify_all()


def _preload_daily_grids(
    clients: WeatherClients,
    session: DailyWeatherSession,
    bbox: TerritoryBbox,
    generation: int,
) -> None:
    for step in session.steps:
        _load_preload_step(clients, session, step, bbox, generation)


def _ensure_preload_started(
    clients: WeatherClients,
    session: DailyWeatherSession,
    bbox: TerritoryBbox,
) -> None:
    preload_key = _preload_key(session, bbox)
    with _weather_condition:
        if preload_key in _scheduled_preloads:
            return
        _scheduled_preloads.add(preload_key)
        generation = _weather_generation
        for step in session.steps:
            _weather_cache.setdefault(
                _cache_key(session, step, bbox),
                GridCacheEntry(),
            )
    try:
        _preload_executor.submit(
            _preload_daily_grids,
            clients,
            session,
            bbox,
            generation,
        )
    except Exception as exc:
        error = WeatherServiceUnavailableError(
            "Le préchargement des données météo n'a pas pu démarrer."
        )
        with _weather_condition:
            for step in session.steps:
                entry = _weather_cache[_cache_key(session, step, bbox)]
                entry.status = "error"
                entry.error = error
            _weather_condition.notify_all()
        raise error from exc


def _forecast_step_payload(
    session: DailyWeatherSession,
    step: ForecastStep,
    bbox: TerritoryBbox,
) -> dict[str, Any]:
    with _weather_condition:
        entry = _weather_cache.get(_cache_key(session, step, bbox))
        status = entry.status if entry else "pending"
        if status == "pending":
            status = "loading"
        payload = entry.payload if entry and entry.payload else None
    return {
        "index": step.index,
        "label": step.label,
        "valid_time": _format_utc(step.valid_time),
        "model": payload.get("model") if payload else step.model,
        "run": payload.get("run") if payload else _format_utc(step.run or session.coverage.run),
        "lead_hours": payload.get("lead_hours") if payload else step.lead_hours,
        "accumulation_hours": ACCUMULATION_HOURS,
        "unit": UNIT,
        "status": status,
    }


def fetch_precipitation_forecast_steps(
    connection: Any,
    *,
    client: MeteoFranceWcsClient | None = None,
    arome_client: MeteoFranceWcsClient | None = None,
    day_provider: Any = None,
) -> dict[str, Any]:
    """Retourne la session du jour et lance son préchargement séquentiel."""

    bbox = fetch_territory_bbox(connection)
    clients = _make_clients(client, arome_client)
    session = _ensure_daily_session(clients, _cache_date(day_provider))
    _ensure_preload_started(clients, session, bbox)
    steps = [_forecast_step_payload(session, step, bbox) for step in session.steps]
    models = {step["model"] for step in steps}
    runs = {step["run"] for step in steps}
    session_model = next(iter(models)) if len(models) == 1 else "MIXED"
    return {
        "model": session_model,
        "product": PERIOD,
        "run": next(iter(runs)) if len(models) == 1 and len(runs) == 1 else None,
        "unit": UNIT,
        "accumulation_hours": ACCUMULATION_HOURS,
        "cache_date": session.cache_date.isoformat(),
        "forecast_steps": steps,
    }


def _requested_step(
    session: DailyWeatherSession,
    valid_time: str | datetime | None,
) -> ForecastStep:
    if valid_time is None:
        return session.steps[0]
    try:
        requested = (
            valid_time.astimezone(timezone.utc)
            if isinstance(valid_time, datetime) and valid_time.tzinfo
            else _parse_utc(str(valid_time))
        )
    except (TypeError, ValueError) as exc:
        raise InvalidWeatherStepError("Échéance météo invalide.") from exc
    for step in session.steps:
        if step.valid_time == requested:
            return step
    raise InvalidWeatherStepError(
        "Cette échéance n'appartient pas à la session météo du jour."
    )


def fetch_precipitations(
    connection: Any,
    *,
    valid_time: str | datetime | None = None,
    client: MeteoFranceWcsClient | None = None,
    arome_client: MeteoFranceWcsClient | None = None,
    day_provider: Any = None,
) -> dict[str, Any]:
    bbox = fetch_territory_bbox(connection)
    clients = _make_clients(client, arome_client)
    session = _ensure_daily_session(clients, _cache_date(day_provider))
    step = _requested_step(session, valid_time)
    _ensure_preload_started(clients, session, bbox)
    key = _cache_key(session, step, bbox)

    with _weather_condition:
        while True:
            entry = _weather_cache.get(key)
            if entry and entry.status == "ready" and entry.payload is not None:
                return entry.payload
            if entry and entry.status == "error":
                if entry.error is not None:
                    raise entry.error
                raise WeatherServiceUnavailableError(
                    "Chargement de l'échéance météo impossible."
                )
            _weather_condition.wait()
