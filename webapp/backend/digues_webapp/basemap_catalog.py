"""Validated, declarative catalogue of browser basemaps."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


DEFAULT_CATALOG_PATH = Path(__file__).with_name("basemaps.toml")
BASEMAP_ID_PATTERN = r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"
CACHE_ID_PATTERN = r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"


class BasemapCatalogError(RuntimeError):
    """The server-side basemap catalogue cannot be loaded safely."""


@dataclass(frozen=True)
class BasemapAdapter:
    public_delivery: Literal["tilejson"]
    tile_formats: frozenset[str]
    discovery_ready: bool


# Closed registry: a provider cannot make the browser execute a new adapter.
ADAPTER_REGISTRY = {
    "plan_ign_raster": BasemapAdapter("tilejson", frozenset({"webp"}), True),
    "wmts_raster": BasemapAdapter("tilejson", frozenset({"jpeg", "png"}), True),
}

OFFLINE_STORAGE_CONTRACT = {
    "contract_version": 1,
    "required_before_cartography_completion": True,
    "payload_store": "cache-storage",
    "metadata_store": "indexeddb",
    "package_unit": "basemap-territory-generation",
    "local_delivery": "service-worker-xyz",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BasemapCapabilities(StrictModel):
    browser_print: bool
    server_pdf: bool


class BasemapOffline(StrictModel):
    required: Literal[True]
    status: Literal["planned", "available"]
    retention: Literal["review-required", "authorized", "prohibited"]
    min_zoom: int = Field(ge=0, le=30)
    max_zoom: int = Field(ge=0, le=30)

    @model_validator(mode="after")
    def validate_offline_contract(self) -> "BasemapOffline":
        if self.min_zoom > self.max_zoom:
            raise ValueError("offline min_zoom must not exceed max_zoom")
        if self.status == "available" and self.retention != "authorized":
            raise ValueError("offline availability requires authorized retention")
        return self


class BasemapCache(StrictModel):
    id: str = Field(pattern=CACHE_ID_PATTERN)
    policy: Literal["persistent-reconstructible", "planned-reconstructible"]
    http_max_age_seconds: int = Field(ge=0, le=31_536_000)
    directory: str = Field(pattern=r"^[a-z][a-z0-9-]*(?:/[a-z][a-z0-9-]*)*$")
    resources: bool
    resource_max_age_seconds: int = Field(ge=0, le=31_536_000)
    tile_max_age_seconds: int = Field(default=0, ge=0, le=31_536_000)
    eviction: Literal["lru"]
    retain_on_error: bool

    @model_validator(mode="after")
    def validate_capacity(self) -> "BasemapCache":
        return self


class BasemapDefinition(StrictModel):
    id: str = Field(pattern=BASEMAP_ID_PATTERN)
    display_name: str = Field(min_length=1, max_length=100)
    order: int = Field(ge=0, le=10_000)
    enabled: bool
    default: bool
    adapter: Literal["plan_ign_raster", "wmts_raster"]
    public_tilejson_url: str | None = None
    provider_endpoint: str | None = None
    provider_layer: str | None = None
    provider_style: str | None = None
    tile_matrix_set: str | None = None
    wmts_matrix_limits: tuple[tuple[int, int, int, int, int], ...] | None = None
    wmts_publication: Literal["review-required", "authorized"] | None = None
    cache_generation: str | None = None
    native_min_zoom: int = Field(ge=0, le=30)
    native_max_zoom: int = Field(ge=0, le=30)
    display_max_zoom: int = Field(ge=0, le=30)
    attribution: str = Field(min_length=1, max_length=500)
    tile_format: Literal["webp", "png", "jpeg"]
    capabilities: BasemapCapabilities
    offline: BasemapOffline
    cache: BasemapCache

    @model_validator(mode="after")
    def validate_contract(self) -> "BasemapDefinition":
        if self.native_min_zoom > self.native_max_zoom:
            raise ValueError("native_min_zoom must not exceed native_max_zoom")
        if self.display_max_zoom < self.native_max_zoom:
            raise ValueError("display_max_zoom must not be below native_max_zoom")
        if self.display_max_zoom > self.native_max_zoom + 1:
            raise ValueError("at most one display overzoom level is supported")
        if self.tile_format not in ADAPTER_REGISTRY[self.adapter].tile_formats:
            raise ValueError(f"{self.adapter} does not support {self.tile_format} tiles")
        if self.offline.min_zoom < self.native_min_zoom:
            raise ValueError("offline min_zoom must not be below native_min_zoom")
        if self.offline.max_zoom > self.native_max_zoom:
            raise ValueError("offline max_zoom must not exceed native_max_zoom")

        if self.public_tilejson_url is not None:
            _validate_public_url(self.public_tilejson_url, "public_tilejson_url")

        provider_fields = (
            self.provider_endpoint,
            self.provider_layer,
            self.provider_style,
            self.tile_matrix_set,
            self.wmts_matrix_limits,
            self.wmts_publication,
            self.cache_generation,
        )
        if self.adapter == "plan_ign_raster":
            if not self.public_tilejson_url:
                raise ValueError("plan_ign_raster requires public_tilejson_url")
            if any(value is not None for value in provider_fields):
                raise ValueError("plan_ign_raster does not accept WMTS provider fields")
            if self.tile_format != "webp":
                raise ValueError("plan_ign_raster publishes WebP tiles")
        else:
            if not all(provider_fields):
                raise ValueError("wmts_raster requires provider, matrix, publication and generation fields")
            _validate_provider_url(self.provider_endpoint or "")
            if self.public_tilejson_url and self.public_tilejson_url != f"/basemap/{self.id}.json":
                raise ValueError("WMTS public TileJSON URL must match its catalogue id")
            if not self.cache_generation or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", self.cache_generation):
                raise ValueError("invalid WMTS cache generation")
            limits = self.wmts_matrix_limits or ()
            if [row[0] for row in limits] != list(range(self.native_min_zoom, self.native_max_zoom + 1)):
                raise ValueError("WMTS matrix limits must cover each native zoom exactly once")
            for z, min_row, max_row, min_col, max_col in limits:
                if not (0 <= min_row <= max_row < 2**z and 0 <= min_col <= max_col < 2**z):
                    raise ValueError("WMTS matrix limits exceed the XYZ matrix")
            if self.cache.tile_max_age_seconds == 0:
                raise ValueError("WMTS tile freshness must be positive")
            if self.cache.http_max_age_seconds > self.cache.tile_max_age_seconds:
                raise ValueError("browser freshness cannot exceed WMTS tile freshness")
            if self.enabled and not self.public_tilejson_url:
                raise ValueError("an enabled WMTS basemap requires a public TileJSON URL")
            if self.enabled and self.wmts_publication != "authorized":
                raise ValueError("WMTS publication requires an explicit rights authorization")
        return self

    def public_payload(self) -> dict[str, Any]:
        """Return the provider-neutral contract safe to send to a browser."""

        if not self.public_tilejson_url:
            raise BasemapCatalogError(f"Basemap {self.id!r} has no public TileJSON URL")
        return {
            "id": self.id,
            "name": self.display_name,
            "order": self.order,
            "delivery": {
                "adapter": ADAPTER_REGISTRY[self.adapter].public_delivery,
                "url": self.public_tilejson_url,
            },
            "zoom": {
                "native_min": self.native_min_zoom,
                "native_max": self.native_max_zoom,
                "display_max": self.display_max_zoom,
            },
            "attribution": self.attribution,
            "tile_format": self.tile_format,
            "capabilities": self.capabilities.model_dump(),
            "offline": self.offline.model_dump(),
        }


class BasemapCatalog(StrictModel):
    contract_version: Literal[1]
    min_free_bytes: int = Field(gt=0, le=9_007_199_254_740_991)
    basemaps: tuple[BasemapDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_catalog(self) -> "BasemapCatalog":
        _require_unique((item.id for item in self.basemaps), "basemap id")
        _require_unique((item.order for item in self.basemaps), "presentation order")
        _require_unique((item.cache.id for item in self.basemaps), "cache id")
        directories = [item.cache.directory for item in self.basemaps]
        _require_unique(directories, "cache directory")
        if any(a.startswith(f"{b}/") for a in directories for b in directories if a != b):
            raise ValueError("cache directories must not overlap")
        if sum(item.default for item in self.basemaps) > 1:
            raise ValueError("at most one basemap may be marked as default")
        for item in self.basemaps:
            adapter = ADAPTER_REGISTRY[item.adapter]
            if item.enabled and not adapter.discovery_ready:
                raise ValueError(f"adapter {item.adapter!r} is not ready for discovery")
            if item.enabled and item.cache.policy == "planned-reconstructible":
                raise ValueError(f"cache {item.cache.id!r} is not operational")
        return self

    def available(self) -> tuple[BasemapDefinition, ...]:
        return tuple(sorted(
            (
                item for item in self.basemaps
                if item.enabled and item.public_tilejson_url
            ),
            key=lambda item: (item.order, item.id),
        ))

    def resolved_default(self) -> BasemapDefinition | None:
        available = self.available()
        return next((item for item in available if item.default), available[0] if available else None)

    def public_payload(self) -> dict[str, Any]:
        available = self.available()
        default = self.resolved_default()
        return {
            "contract_version": self.contract_version,
            "default_basemap_id": default.id if default else None,
            "offline": dict(OFFLINE_STORAGE_CONTRACT),
            "basemaps": [item.public_payload() for item in available],
        }


def _require_unique(values: Any, label: str) -> None:
    seen: set[Any] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate {label}: {value!r}")
        seen.add(value)


def _validate_public_url(value: str, field: str) -> None:
    if value != value.strip() or not value:
        raise ValueError(f"{field} must be non-empty and trimmed")
    parsed = urlsplit(value)
    if value.startswith("/") and not value.startswith("//"):
        if parsed.query or parsed.fragment:
            raise ValueError(f"{field} must not contain a query or fragment")
        return
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field} must be root-relative or HTTP(S)")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{field} must not contain credentials, a query or a fragment")


def _validate_provider_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("provider_endpoint must be an absolute HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("provider_endpoint must not contain credentials, a query or a fragment")


def parse_basemap_catalog(data: dict[str, Any]) -> BasemapCatalog:
    try:
        return BasemapCatalog.model_validate(data)
    except ValidationError as exc:
        raise BasemapCatalogError(f"Invalid basemap catalogue: {exc}") from exc


def load_basemap_catalog(path: Path | str | None = None) -> BasemapCatalog:
    configured_path = path or os.getenv("BASEMAP_CATALOG_FILE") or DEFAULT_CATALOG_PATH
    catalog_path = Path(configured_path)
    try:
        with catalog_path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise BasemapCatalogError(
            f"Cannot load basemap catalogue {catalog_path}: {exc}"
        ) from exc
    return parse_basemap_catalog(data)


def basemap_catalog_payload(path: Path | str | None = None) -> dict[str, Any]:
    return load_basemap_catalog(path).public_payload()
