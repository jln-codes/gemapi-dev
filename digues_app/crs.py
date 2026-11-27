"""Contrat minimal des CRS métier qualifiés par digues-app."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


class BusinessCRSError(ValueError):
    """Le CRS demandé n'appartient pas au catalogue métier qualifié."""


@dataclass(frozen=True, slots=True)
class BusinessCRS:
    authority: str
    srid: int
    name: str
    projected: bool = True
    linear_unit: str = "metre"

    @property
    def code(self) -> str:
        return f"{self.authority}:{self.srid}"


# EPSG 2154 et 3942–3950 désignent RGF93 v1, pas la réalisation v2b.
EPSG_2154 = BusinessCRS("EPSG", 2154, "RGF93 v1 / Lambert-93")
EPSG_3942 = BusinessCRS("EPSG", 3942, "RGF93 v1 / CC42")
EPSG_3943 = BusinessCRS("EPSG", 3943, "RGF93 v1 / CC43")
EPSG_3944 = BusinessCRS("EPSG", 3944, "RGF93 v1 / CC44")
EPSG_3945 = BusinessCRS("EPSG", 3945, "RGF93 v1 / CC45")
EPSG_3946 = BusinessCRS("EPSG", 3946, "RGF93 v1 / CC46")
EPSG_3947 = BusinessCRS("EPSG", 3947, "RGF93 v1 / CC47")
EPSG_3948 = BusinessCRS("EPSG", 3948, "RGF93 v1 / CC48")
EPSG_3949 = BusinessCRS("EPSG", 3949, "RGF93 v1 / CC49")
EPSG_3950 = BusinessCRS("EPSG", 3950, "RGF93 v1 / CC50")

QUALIFIED_BUSINESS_CRS = MappingProxyType({
    crs.srid: crs for crs in (
        EPSG_2154, EPSG_3942, EPSG_3943, EPSG_3944, EPSG_3945,
        EPSG_3946, EPSG_3947, EPSG_3948, EPSG_3949, EPSG_3950,
    )
})


def qualified_business_crs(value: BusinessCRS | int | str) -> BusinessCRS:
    """Résout un code explicite vers un CRS projeté métrique qualifié."""

    if isinstance(value, BusinessCRS):
        srid = value.srid
    elif isinstance(value, bool):
        raise BusinessCRSError("Un booléen n'est pas un code EPSG métier.")
    elif isinstance(value, int):
        srid = value
    elif isinstance(value, str):
        normalized = value.strip().upper()
        if normalized.startswith("EPSG:"):
            normalized = normalized[5:]
        if not normalized.isascii() or not normalized.isdecimal():
            raise BusinessCRSError(f"CRS métier invalide : {value!r}")
        srid = int(normalized)
    else:
        raise BusinessCRSError(f"CRS métier invalide : {value!r}")

    try:
        result = QUALIFIED_BUSINESS_CRS[srid]
    except KeyError:
        allowed = ", ".join(crs.code for crs in QUALIFIED_BUSINESS_CRS.values())
        raise BusinessCRSError(
            f"CRS métier non qualifié : EPSG:{srid}. Valeurs admises : {allowed}."
        ) from None
    if not result.projected or result.linear_unit != "metre":
        raise BusinessCRSError(f"CRS métier non projeté métrique : {result.code}")
    return result


def argparse_business_crs(value: str) -> BusinessCRS:
    """Adaptateur argparse conservant le message du contrat partagé."""

    try:
        return qualified_business_crs(value)
    except BusinessCRSError as exc:
        import argparse

        raise argparse.ArgumentTypeError(str(exc)) from exc
