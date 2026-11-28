"""Topology contracts of the current fictitious patrimony GeoPackages."""

from pathlib import Path
from hashlib import md5, sha256
import sys
import json

from osgeo import ogr

ROOT = Path(__file__).resolve().parents[2]
GPKG = ROOT / "demo/gpkg"
def read(file, table, optional_geometry=False):
    source = ogr.Open(str(GPKG / file))
    layer = source.GetLayerByName(table)
    assert layer is not None
    assert layer.GetSpatialRef().GetAuthorityCode(None) == "3950"
    rows = {}
    for feature in layer:
        geometry = feature.GetGeometryRef()
        if geometry is None:
            assert optional_geometry and feature["vegetation_id"]
        else:
            assert geometry is not None and not geometry.IsEmpty() and geometry.IsValid()
        rows[str(feature["id"])] = (geometry.Clone() if geometry is not None else None,
                                    {name: feature[name] for name in feature.keys()})
    return rows


def no_surface_overlap(left, right, same=False):
    for first, (a, _) in left.items():
        for second, (b, _) in right.items():
            if same and first >= second:
                continue
            assert a.Intersection(b).Area() <= 0.001, (first, second)


def main():
    storage = read("ah.gpkg", "ah_stockage")
    works = read("ah.gpkg", "ah_ouvrage")
    sites = read("sites_terrestres.gpkg", "sites_terrestres")
    zones = {}
    for family, expected in (("mobilier", 4), ("cheminements", 2), ("vegetation", 19)):
        children = read("sites_terrestres.gpkg", family)
        assert len(children) == expected and not zones.keys() & children.keys()
        zones.update(children)
    assert (len(storage), len(works), len(sites), len(zones)) == (10, 13, 12, 25)
    assert all(geometry.GetGeometryName() == "MULTIPOLYGON" and geometry.GetGeometryCount() == 1
               for geometry, _ in sites.values())
    parent_digest = md5("|".join(sorted(uid + ":" + geometry.GetGeometryRef(0).ExportToWkb().hex()
                                     for uid, (geometry, _) in sites.items())).encode()).hexdigest()
    assert parent_digest == "f107cd9adc6494ba70061e221b994021"
    no_surface_overlap(storage, storage, True)
    no_surface_overlap(sites, sites, True)
    no_surface_overlap(storage, sites)
    assert all(geometry.GetGeometryName() == "POLYGON" for geometry, _ in storage.values())
    assert {kind: sum(geometry.GetGeometryName() == kind for geometry, _ in zones.values())
            for kind in ("MULTIPOINT", "MULTILINESTRING", "MULTIPOLYGON")} == {
                "MULTIPOINT": 4, "MULTILINESTRING": 4, "MULTIPOLYGON": 17}
    assert all(geometry.GetGeometryName() == "LINESTRING" for geometry, _ in works.values())
    polygon_sites = {}
    for geometry, attributes in zones.values():
        parent = sites[str(attributes["site_terrestre_id"])][0]
        assert geometry.Difference(parent).IsEmpty()
        if geometry.GetGeometryName() == "MULTIPOLYGON":
            assert not geometry.Equals(parent)
            assert geometry.Area() < parent.Area() * 0.5
            polygon_sites.setdefault(str(attributes["site_terrestre_id"]), []).append(geometry)
    assert sum(len(parts) >= 2 for parts in polygon_sites.values()) == 5
    for parts in polygon_sites.values():
        for index, first in enumerate(parts):
            for second in parts[index + 1:]:
                assert first.Intersection(second).Area() < 0.001
    for geometry, attributes in works.values():
        parent = next(g for g, a in storage.values()
                      if str(a["ah_id"]) == str(attributes["ah_id"]))
        assert geometry.Distance(parent.Boundary()) <= 0.001
        assert geometry.Difference(parent.Boundary()).Length() <= 0.001
    prestations = {}
    for table in ("prestations_points", "prestations_lignes", "prestations_polygones"):
        prestations.update(read("prestations.gpkg", table, optional_geometry=True))
    # Empreinte des UUID, géométries et attributs hors nouveaux rattachements au commit 8e8363b.
    witnesses = []
    for uid, (geom, attrs) in sorted(prestations.items()):
        attrs = dict(attrs)
        for col in ("mobilier_id", "cheminement_id", "vegetation_id", "troncon_id"):
            attrs.pop(col)
        attrs["wkb"] = geom.ExportToWkb().hex() if geom is not None else None
        witnesses.append(attrs)
    assert sha256(json.dumps(witnesses, sort_keys=True).encode()).hexdigest() == (
        "3341bf8d0ce2d854ddc622336602e309337cb4d98d7214509a97e5fc4f676e9f")
    assert len(prestations) == 100
    digest = sha256("|".join(sorted(
        uid + ":" + (geometry.ExportToWkb().hex() if geometry is not None else 'NULL')
        for uid, (geometry, _) in prestations.items())).encode()).hexdigest()
    assert digest == "9e992f40a6028127d2ac37c8eb025ea3432690154942fed8da6013f10589baf5"
    assert sum(geometry is None for geometry,_ in prestations.values()) == 12
    assert {geometry.GetGeometryName() for geometry, _ in prestations.values() if geometry is not None} == {
        "POINT", "MULTIPOINT", "LINESTRING", "MULTILINESTRING", "POLYGON", "MULTIPOLYGON"}
    print("Topologie fictive : 10 stockages, 13 ouvrages, 12 sites, 25 enfants, "
          "100 prestations ; aucun chevauchement surfacique")


if __name__ == "__main__":
    main()
    if "--postgis" in sys.argv:
        from test_demo_import import main as check_import
        check_import()
