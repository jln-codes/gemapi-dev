"""Private, product-neutral cache policy inventory for administration."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DEFAULT_PRODUCTS_PATH = Path(__file__).with_name("cartographic_caches.json")
DIRECTORY = r"^[a-z][a-z0-9-]*(?:/[a-z][a-z0-9-]*)*$"
PRODUCT_ID = r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"


class CacheArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    format: str = Field(pattern=PRODUCT_ID)
    generation: str = Field(pattern=PRODUCT_ID)
    grid_id: str = Field(pattern=PRODUCT_ID)


class CacheProduct(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(pattern=PRODUCT_ID)
    display_name: str = Field(min_length=1)
    scope: Literal["global"]
    policy: Literal["persistent-reconstructible", "planned-reconstructible"]
    directory: str = Field(pattern=DIRECTORY)
    resources: bool
    resource_max_age_seconds: int = Field(ge=0)
    tile_max_age_seconds: int = Field(ge=0)
    eviction: Literal["lru"]
    retain_on_error: bool
    http_max_age_seconds: int = Field(ge=0, le=31_536_000)
    artifact: CacheArtifact | None = None

    @model_validator(mode="after")
    def validate_policy(self):
        if (self.tile_max_age_seconds > 0
                and self.http_max_age_seconds > self.tile_max_age_seconds):
            raise ValueError("Invalid cartographic cache capacity")
        return self


class CacheProductCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract_version: Literal[1]
    min_free_bytes: int = Field(gt=0, le=9_007_199_254_740_991)
    capacity_bytes: int = Field(gt=0, le=9_007_199_254_740_991)
    products: list[CacheProduct] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_products(self):
        ids = [item.id for item in self.products]
        directories = [item.directory for item in self.products]
        if len(ids) != len(set(ids)) or any(
                a == b or a.startswith(f"{b}/")
                for i, a in enumerate(directories)
                for j, b in enumerate(directories) if i != j):
            raise ValueError("Duplicate or overlapping cartographic cache products")
        return self


def load_cache_products() -> CacheProductCatalog:
    filename = Path(os.environ.get("CARTOGRAPHIC_CACHE_PRODUCTS_FILE") or DEFAULT_PRODUCTS_PATH)
    return CacheProductCatalog.model_validate(json.loads(filename.read_text(encoding="utf-8")))
