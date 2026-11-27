"""Vues métier compactes des caches météo et hydrométrique pour l'IA."""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timezone
import logging
import math
from typing import Any
import unicodedata

from .database import open_ai_connection
from .hydrometry import (
    fetch_hydrometric_territorial_dataset,
    summarize_territory_relation,
)
from .weather import fetch_precipitation_forecast_steps, fetch_precipitations


MAX_STATIONS = 20
MAX_TIME_POINTS = 56
LOGGER = logging.getLogger(__name__)

class EnvironmentToolError(ValueError):
    """Paramètre invalide ou donnée métier demandée indisponible."""


def _partial_errors(value: Any) -> list[dict[str, str]]:
    """Expose seulement les indicateurs bornés produits par le cache hydrométrique."""

    if not isinstance(value, list):
        return []
    return [
        {
            "kind": str(item.get("kind")),
            "variable": str(item.get("quantity")),
            "error": "unavailable",
        }
        for item in value
        if isinstance(item, dict)
        and item.get("kind") in {"observation", "forecast", "forecast_catalog"}
        and item.get("quantity") in {"height", "discharge"}
        and item.get("error") == "unavailable"
    ]


def _normalized_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(
        "".join(
            character
            for character in decomposed
            if not unicodedata.combining(character)
        ).split()
    )


def _optional_iso_datetime(value: Any, label: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 40:
        raise EnvironmentToolError(f"{label} doit être une date ISO 8601.")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise EnvironmentToolError(f"{label} doit être une date ISO 8601.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _within_period(value: str, start: datetime | None, end: datetime | None) -> bool:
    parsed = _optional_iso_datetime(value, "Horodatage des données")
    if parsed is None:
        return False
    return (start is None or parsed >= start) and (end is None or parsed <= end)


def _validate_period(
    start_time: Any,
    end_time: Any,
) -> tuple[datetime | None, datetime | None]:
    start = _optional_iso_datetime(start_time, "start_time")
    end = _optional_iso_datetime(end_time, "end_time")
    if start is not None and end is not None and start > end:
        raise EnvironmentToolError("start_time doit précéder end_time.")
    return start, end


def _format_utc(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _vigicrues_dataset(*, connection_factory=None) -> dict[str, Any]:
    factory = connection_factory or open_ai_connection
    with factory() as connection:
        cache_factory = vars(factory).get("hydrometry_cache_factory")
        if cache_factory is not None:
            stack = ExitStack()
            try:
                cache_connection = stack.enter_context(cache_factory())
            except Exception as exc:
                stack.close()
                LOGGER.warning(
                    "discovery_cache=persistence_error operation=open error=%s",
                    type(exc).__name__,
                )
            else:
                with stack:
                    return fetch_hydrometric_territorial_dataset(
                        connection,
                        persistent_connection=cache_connection,
                    )
        return fetch_hydrometric_territorial_dataset(connection)


def get_hydrometric_sites(*, query: Any = None, connection_factory=None) -> dict[str, Any]:
    """Catalogue neutre des sites hydrométriques du territoire."""

    if query is not None and (not isinstance(query, str) or len(query.strip()) > 100):
        raise EnvironmentToolError("query doit être un texte de 100 caractères maximum.")
    dataset = _vigicrues_dataset(**({'connection_factory': connection_factory} if connection_factory else {}))
    normalized_query = _normalized_text(query) if isinstance(query, str) else ""
    matching = []
    for site in dataset.get("sites", []):
        watercourse = site.get("watercourse") or {}
        position = site.get("position") or {}
        searchable = " ".join(
            [
                str(site.get("name") or ""),
                str(site.get("code_site") or ""),
                str(watercourse.get("name") or ""),
                *(
                    str(item.get("code_station") or "")
                    for item in site.get("stations", [])
                ),
            ]
        )
        if normalized_query and normalized_query not in _normalized_text(searchable):
            continue
        latest = site.get("observations", {}).get("latest", {})
        height = latest.get("height") if isinstance(latest.get("height"), dict) else {}
        discharge = latest.get("discharge") if isinstance(latest.get("discharge"), dict) else {}
        territory_relations = [
            summarize_territory_relation(relation)
            for relation in site.get("territory_relations", [])
        ]
        matching.append({
            "code_site": site.get("code_site"),
            "name": site.get("name"),
            "river": watercourse.get("name"),
            "longitude": position.get("longitude"),
            "latitude": position.get("latitude"),
            "roles": sorted({
                relation.get("role") for relation in territory_relations
                if relation.get("selection_state") == "selected"
            }),
            "territory_relations": territory_relations,
            "station_codes": [item.get("code_station") for item in site.get("stations", [])],
            "series_resolution": site.get("observations", {}).get("series_resolution"),
            "latest_height_m": height.get("value"),
            "latest_height_time": height.get("source_time"),
            "latest_discharge_m3_s": discharge.get("value"),
            "latest_discharge_time": discharge.get("source_time"),
            "has_height_forecast": bool(site.get("forecasts") and site["forecasts"].get("height")),
            "has_discharge_forecast": bool(site.get("forecasts") and site["forecasts"].get("discharge")),
        })
    matching.sort(key=lambda item: (
        _normalized_text(item["name"] or ""), item["code_site"] or ""
    ))
    return {
        "ok": True,
        "source": "Hub'Eau Hydrométrie + BD TOPAGE",
        "kind": "hydrometric_site_catalog",
        "cache_date": dataset.get("cache_date"),
        "retrieved_at": dataset.get("generated_at"),
        "territory": dataset.get("territoire"),
        "query": query.strip() if isinstance(query, str) else None,
        "sites": matching[:MAX_STATIONS],
        "count": len(matching),
        "truncated": len(matching) > MAX_STATIONS,
    }


def _find_site(dataset: dict[str, Any], requested: Any) -> dict[str, Any]:
    if not isinstance(requested, str) or not requested.strip() or len(requested.strip()) > 100:
        raise EnvironmentToolError("station doit être un nom ou identifiant valide.")
    needle = _normalized_text(requested)
    sites = dataset.get("sites", [])
    exact_codes = [
        site for site in sites
        if needle in {
            _normalized_text(str(site.get("code_site") or "")),
            *(
                _normalized_text(str(item.get("code_station") or ""))
                for item in site.get("stations", [])
            ),
        }
    ]
    if len(exact_codes) == 1:
        return exact_codes[0]
    exact_names = [
        site for site in sites
        if _normalized_text(str(site.get("name") or "")) == needle
    ]
    if len(exact_names) == 1:
        return exact_names[0]
    partial = [
        site for site in sites
        if needle in _normalized_text(
            f"{site.get('name') or ''} {(site.get('watercourse') or {}).get('name') or ''}"
        )
    ]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise EnvironmentToolError(
            "Station hydrométrique inconnue ou site hydrométrique inconnu "
            "sur ce territoire."
        )
    candidates = ", ".join(str(site.get("name")) for site in partial[:5])
    raise EnvironmentToolError(
        f"Station ambiguë ; précisez parmi : {candidates}."
    )


def _selected_variables(variable: Any) -> tuple[str, ...]:
    if variable == "H":
        return ("height",)
    if variable == "Q":
        return ("discharge",)
    if variable == "both":
        return ("height", "discharge")
    raise EnvironmentToolError("variable doit valoir H, Q ou both.")


def _point_limit(value: Any) -> int:
    if value is None:
        return 28
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_TIME_POINTS:
        raise EnvironmentToolError(
            f"max_points doit être un entier entre 1 et {MAX_TIME_POINTS}."
        )
    return value


def _observation_series(
    station: dict[str, Any],
    variables: tuple[str, ...],
    start: datetime | None,
    end: datetime | None,
    limit: int,
) -> list[dict[str, Any]]:
    observations = station.get("observations", {})
    samples = observations.get("samples_7d", {}).get("samples", [])
    series = []
    mapping = {
        "height": ("m", "height_m", "height_source_time"),
        "discharge": ("m3/s", "discharge_m3_s", "discharge_source_time"),
    }
    for variable in variables:
        unit, value_key, source_key = mapping[variable]
        resolution = observations.get("series_resolution", {}).get(variable, {})
        if resolution.get("status") == "multiple_unresolved":
            for station_series in observations.get("series", {}).get(variable, []):
                points = [
                    {
                        "target_time": item.get("target_time"),
                        "source_time": (
                            item.get("measurement", {}).get("source_time")
                            if isinstance(item.get("measurement"), dict) else None
                        ),
                        "value": (
                            item.get("measurement", {}).get("value")
                            if isinstance(item.get("measurement"), dict) else None
                        ),
                    }
                    for item in station_series.get("samples", [])
                    if isinstance(item, dict)
                    and isinstance(item.get("target_time"), str)
                    and _within_period(item["target_time"], start, end)
                ]
                series.append({
                    "kind": "observation",
                    "variable": variable,
                    "unit": unit,
                    "station_code": station_series.get("station_code"),
                    "resolution": "multiple_unresolved",
                    "points": points[-limit:],
                    "data_available": any(point["value"] is not None for point in points),
                    "truncated": len(points) > limit,
                })
            continue
        points = [
            {
                "target_time": sample.get("target_time"),
                "source_time": sample.get(source_key),
                "value": sample.get(value_key),
            }
            for sample in samples
            if isinstance(sample, dict)
            and isinstance(sample.get("target_time"), str)
            and _within_period(sample["target_time"], start, end)
        ]
        truncated = len(points) > limit
        points = points[-limit:]
        series.append({
            "kind": "observation",
            "variable": variable,
            "unit": unit,
            "station_code": resolution.get("selected_station_code"),
            "resolution": resolution.get("status"),
            "points": points,
            "data_available": any(point["value"] is not None for point in points),
            "truncated": truncated,
        })
    return series


def _forecast_series(
    station: dict[str, Any],
    variables: tuple[str, ...],
    start: datetime | None,
    end: datetime | None,
    limit: int,
) -> list[dict[str, Any]]:
    series = []
    forecasts = station.get("forecasts", {})
    for variable in variables:
        raw_forecasts = forecasts.get(variable) if isinstance(forecasts, dict) else None
        candidates = (
            [item for item in raw_forecasts if isinstance(item, dict)]
            if isinstance(raw_forecasts, list)
            else [raw_forecasts] if isinstance(raw_forecasts, dict)
            else []
        )
        if not candidates:
            series.append({
                "kind": "forecast",
                "variable": variable,
                "unit": "m" if variable == "height" else "m3/s",
                "produced_at": None,
                "points": [],
                "data_available": False,
                "truncated": False,
            })
            continue
        for forecast in candidates:
            points = [
                point for point in forecast.get("points", [])
                if isinstance(point, dict)
                and isinstance(point.get("forecast_time"), str)
                and _within_period(point["forecast_time"], start, end)
            ]
            truncated = len(points) > limit
            series.append({
                "kind": "forecast",
                "variable": variable,
                "station_code": forecast.get("station_code"),
                "unit": forecast.get("unit"),
                "produced_at": forecast.get("produced_at"),
                "points": points[:limit],
                "data_available": bool(points),
                "truncated": truncated,
            })
    return series


def get_hydrometric_data(
    *,
    station: Any,
    kind: Any,
    variable: Any = "both",
    start_time: Any = None,
    end_time: Any = None,
    max_points: Any = None,
    connection_factory=None,
) -> dict[str, Any]:
    if kind not in {"observation", "forecast"}:
        raise EnvironmentToolError("kind doit valoir observation ou forecast.")
    variables = _selected_variables(variable)
    start, end = _validate_period(start_time, end_time)
    limit = _point_limit(max_points)
    dataset = _vigicrues_dataset(**({'connection_factory': connection_factory} if connection_factory else {}))
    selected = _find_site(dataset, station)
    series = (
        _observation_series(selected, variables, start, end, limit)
        if kind == "observation"
        else _forecast_series(selected, variables, start, end, limit)
    )
    provenance_key = "observations" if kind == "observation" else "forecasts"
    provenance = dataset.get("provenance")
    source = provenance.get(provenance_key) if isinstance(provenance, dict) else None
    if not isinstance(source, str) or not source:
        source = "Hub'Eau Hydrométrie" if kind == "observation" else "Vigicrues"
    site_identity = {
        "code_site": selected.get("code_site"),
        "name": selected.get("name"),
        "river": (selected.get("watercourse") or {}).get("name"),
        "station_codes": [
            item.get("code_station") for item in selected.get("stations", [])
        ],
        "territory_roles": sorted({
            relation.get("role")
            for relation in selected.get("territory_relations", [])
            if relation.get("selection", {}).get("state") == "selected"
        }),
        "territory_relations": [
            summarize_territory_relation(relation)
            for relation in selected.get("territory_relations", [])
        ],
    }
    return {
        "ok": True,
        "source": source,
        "cache_date": dataset.get("cache_date"),
        "retrieved_at": dataset.get("generated_at"),
        "site": site_identity,
        "period": {
            "start_time": _format_utc(start),
            "end_time": _format_utc(end),
        },
        "series": series,
        "data_available": any(item["data_available"] for item in series),
        "partial_errors": _partial_errors(selected.get("errors")),
        "dataset_errors": _partial_errors(dataset.get("errors")),
    }


def _coordinate(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EnvironmentToolError(f"{label} est invalide.")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise EnvironmentToolError(f"{label} est invalide.")
    return number


def _point_precipitation(
    cells: list[dict[str, Any]], longitude: float, latitude: float
) -> float | None:
    for cell in cells:
        bounds = cell.get("bounds") if isinstance(cell, dict) else None
        if (
            isinstance(bounds, list)
            and len(bounds) == 2
            and bounds[0][0] <= latitude <= bounds[1][0]
            and bounds[0][1] <= longitude <= bounds[1][1]
        ):
            value = cell.get("value_mm")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            number = float(value)
            return number if math.isfinite(number) else None
    raise EnvironmentToolError("La localisation est hors de la grille territoriale.")


def get_precipitation_forecast(
    *,
    valid_time: Any = None,
    longitude: Any = None,
    latitude: Any = None,
    connection_factory=None,
) -> dict[str, Any]:
    if (longitude is None) != (latitude is None):
        raise EnvironmentToolError(
            "longitude et latitude doivent être fournies ensemble."
        )

    point = None
    if longitude is not None:
        point = {
            "longitude": _coordinate(longitude, "longitude", -180, 180),
            "latitude": _coordinate(latitude, "latitude", -90, 90),
        }

    if valid_time is not None:
        _optional_iso_datetime(valid_time, "valid_time")

    with (connection_factory or open_ai_connection)() as connection:
        available = fetch_precipitation_forecast_steps(connection)
        forecast_steps = available.get("forecast_steps", [])

        if valid_time is None:
            return {
                "ok": True,
                "source": "Météo-France",
                "kind": "forecast_availability",
                "model": available.get("model"),
                "variable": "precipitation",
                "unit": available.get("unit"),
                "accumulation_hours": available.get("accumulation_hours"),
                "run": available.get("run"),
                "cache_date": available.get("cache_date"),
                "location": point or "territory",
                "forecast_steps": forecast_steps,
            }

        available_valid_times = [
            step["valid_time"]
            for step in forecast_steps
            if isinstance(step, dict)
            and step.get("status") == "ready"
            and isinstance(step.get("valid_time"), str)
            and step["valid_time"]
        ]

        if valid_time not in available_valid_times:
            return {
                "ok": False,
                "error": "Échéance indisponible",
                "requested_valid_time": valid_time,
                "available_valid_times": available_valid_times,
            }

        grid = fetch_precipitations(connection, valid_time=valid_time)

    cells = grid.get("cells", [])
    if not isinstance(cells, list):
        raise EnvironmentToolError("Grille de précipitations indisponible.")

    result = {
        "ok": True,
        "source": "Météo-France",
        "kind": "forecast",
        "model": grid.get("model"),
        "variable": "precipitation",
        "unit": grid.get("unit"),
        "accumulation_hours": grid.get("accumulation_hours"),
        "run": grid.get("run"),
        "valid_time": grid.get("valid_time"),
        "cache_date": grid.get("cache_date"),
    }

    if point is not None:
        result["location"] = point
        result["value"] = _point_precipitation(
            cells,
            point["longitude"],
            point["latitude"],
        )
        result["data_available"] = result["value"] is not None
        return result

    values = [
        float(cell["value_mm"])
        for cell in cells
        if isinstance(cell, dict)
        and not isinstance(cell.get("value_mm"), bool)
        and isinstance(cell.get("value_mm"), (int, float))
        and math.isfinite(float(cell["value_mm"]))
    ]

    result.update({
        "location": "territory",
        "cell_count": len(cells),
        "valid_cell_count": len(values),
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
        "mean": sum(values) / len(values) if values else None,
        "data_available": bool(values),
    })

    return result
