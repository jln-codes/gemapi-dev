"""Indexed PostGIS prefilter and QGIS 3.44 form checks on a disposable database."""

import csv
import sys
import time
from collections import Counter
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from uuid import uuid4
from xml.etree import ElementTree
from zipfile import ZipFile

from qgis.PyQt.QtWidgets import QMainWindow
from qgis.core import (Qgis, QgsApplication, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsDataSourceUri, QgsGeometry,
                       QgsProject)
from qgis.gui import QgsMapCanvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gemapi_app.creation import CreationDialog
from gemapi_app.digitize import geometry_from_vertices
from gemapi_app.patrimony_spatial import (ProjectionSettings, candidate_query,
                                              resolve_watercourse, _samples, _sections)
from test_postgres_integration import ROOT, command, load_demo_rows, sql


AUDITS = ROOT / "build/qgis-tests"


def _timed(action, repeats=1):
    measures = []
    result = None
    for _ in range(repeats):
        start = time.perf_counter()
        result = action()
        measures.append((time.perf_counter() - start) * 1000)
    return result, median(measures)


def _project_copy(target, port, source_path=None):
    with ZipFile(source_path or ROOT / "demo/prestations_demo.qgz") as source:
        members = [(name, source.read(name)) for name in source.namelist()]
    from test_mod_geom_project import local_document
    document = local_document(source_path or ROOT / "demo/prestations_demo.qgz", port)
    document.find("./properties/Paths/Absolute").text = "true"
    with ZipFile(target, "w") as destination:
        for name, data in members:
            destination.writestr(name, ElementTree.tostring(document, encoding="utf-8")
                                 if name.endswith(".qgs") else data)


def main():
    container = "sig-prestations-spatial-" + uuid4().hex[:10]
    command("docker", "run", "--rm", "-d", "--name", container,
            "-p", "127.0.0.1::5432", "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
            "postgis/postgis:16-3.4")
    app = None
    try:
        for _ in range(60):
            if command("docker", "exec", container, "pg_isready", "-h", "127.0.0.1",
                       "-U", "postgres", check=False).returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("PostGIS jetable indisponible")
        sql(container, "CREATE EXTENSION IF NOT EXISTS postgis;")
        sql(container, (ROOT / "model/schema_v0.6.1.sql").read_text())
        load_demo_rows(container)

        river_id, digue_id = str(uuid4()), str(uuid4())
        sql(container, f"BEGIN; INSERT INTO gemapi.cours_eau(id,nom,type_ecoulement,source,direction,geom) "
                       f"VALUES ('{river_id}','Rivière des Bancs','rivière','TEST',TRUE,"
                       "ST_GeomFromText('MULTILINESTRING((1700000 9300000,1701000 9300000))',3950)); "
                       f"INSERT INTO gemapi.digues(id,nom) VALUES ('{digue_id}','Digue des Bancs'); "
                       "INSERT INTO gemapi.digues_reperes(digue_id,riviere_id,rive,debut_fraction,fin_fraction) "
                       f"VALUES ('{digue_id}','{river_id}','G',0.2,0.4); COMMIT;")
        port = command("docker", "port", container, "5432").stdout.strip().rsplit(":", 1)[-1]
        app = QgsApplication([], True)
        app.initQgis()
        app.messageLog().messageReceived.connect(
            lambda message, tag, level: print(f"{tag}: {message}")
            if tag == "GEMAPI-App — patrimoine" else None)
        with TemporaryDirectory() as directory:
            local = Path(directory) / "spatial_project.qgz"
            _project_copy(local, port)
            project = QgsProject.instance()
            assert project.read(str(local))
            with ZipFile(local) as archive:
                document=ElementTree.fromstring(next(archive.read(name) for name in archive.namelist() if name.endswith('.qgs')))
            assert set(project.mapLayers()) == {layer.findtext('id') for layer in document.findall('./projectlayers/maplayer')}
            class Interface:
                def __init__(self):
                    self.window = QMainWindow()
                    self.canvas = QgsMapCanvas()
                    self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3950"))
                def mainWindow(self): return self.window
                def mapCanvas(self): return self.canvas
            iface = Interface()
            form = CreationDialog(iface)
            assert form.geometry is None and form._geometry_error() is None
            assert not form.saveButton.isEnabled()
            assert not form.nearbyOnly.isChecked() and not form.nearbyOnly.isEnabled()
            assert form.objectList.count() > 0
            assert {table for table, *_ in form.objects} == {
                "digues", "ah_sections", "cours_eau", "ah_stockage", "ah_ouvrage", "sites_terrestres", "mobilier", "cheminements", "vegetation"}
            # A missing family must not empty the remaining families.
            form._object_layers["digues"] = None
            form._load_objects()
            assert {table for table, *_ in form.objects} == {
                "ah_sections", "cours_eau", "ah_stockage", "ah_ouvrage", "sites_terrestres", "mobilier", "cheminements", "vegetation"}
            assert "Lecture patrimoniale incomplète : digues" in form.statusLabel.text()
            form._object_layers.pop("digues")
            form._load_objects()
            assert {table for table, *_ in form.objects} == {
                "digues", "ah_sections", "cours_eau", "ah_stockage", "ah_ouvrage", "sites_terrestres", "mobilier", "cheminements", "vegetation"}
            connection = form._connection
            def unavailable_connection():
                raise RuntimeError("Connexion SQL séparée indisponible")
            form._connection = unavailable_connection
            form._load_objects()
            assert {table for table, *_ in form.objects} == {
                "digues", "cours_eau", "ah_stockage", "ah_ouvrage", "sites_terrestres", "mobilier", "cheminements", "vegetation"}
            form._connection = connection
            form._load_objects()
            source = QgsGeometry.fromWkt("POINT(1700300 9300080)")
            source.transform(QgsCoordinateTransform(
                QgsCoordinateReferenceSystem("EPSG:3950"),
                QgsCoordinateReferenceSystem("EPSG:2154"), project))
            captured = geometry_from_vertices(
                "point", [source.asPoint()], QgsCoordinateReferenceSystem("EPSG:2154"),
                project)
            assert captured.distance(QgsGeometry.fromWkt("POINT(1700300 9300080)")) < 0.01
            form._capture = type("Capture", (), {"kind": "point"})()
            form._capture_finished(captured, "Géométrie définie en EPSG:3950.")
            assert form.geometry is not None and form._geometry_error() is None
            form.nearbyOnly.setChecked(True)
            radius_counts = []
            for value, unit in ((10, 0), (500, 0), (500, 1)):
                form.distanceSpin.setValue(value)
                form.distanceUnitCombo.setCurrentIndex(unit)
                radius_m = value * (form.distanceUnitCombo.currentData() or 1)
                raw = form._connection().executeSql(candidate_query(
                    form.geometry.asWkt(17), radius_m))
                if radius_m == 500000:
                    assert set(Counter(str(row[0]) for row in raw)) == {
                        "digues", "ah_sections", "cours_eau", "ah_stockage", "ah_ouvrage", "sites_terrestres", "mobilier", "cheminements", "vegetation"}
                visible = {(item.data(0x0100)[0], uid)
                           for item in (form.objectList.item(i)
                                        for i in range(form.objectList.count()))
                           for uid in item.data(0x0100)[1]}
                allowed = form._spatial_candidates(radius_m)
                assert visible == {(table, uid) for table, ids in allowed.items()
                                   for uid in ids}, (radius_m, len(raw), visible)
                radius_counts.append((len(raw), len(visible)))
                print(f"form radius_m={radius_m} sql_candidates={len(raw)} "
                      f"displayed={len(visible)}")
            assert radius_counts[0][0] < radius_counts[1][0] < radius_counts[2][0]
            assert radius_counts[2][1] > radius_counts[1][1]
            for width in (780, 1000):
                form.resize(width, 650)
                form.show(); app.processEvents()
                assert form.geometryGroup.y() == form.associationGroup.y()
                assert form.objectList.height() >= 145
                assert 85 <= form.selectedList.height() <= 115
                assert form.locationGroup.isAncestorOf(form.selectedList)
                assert form.selectedList.mapToGlobal(form.selectedList.rect().topLeft()).y() > form.objectList.mapToGlobal(form.objectList.rect().bottomLeft()).y()
                left, middle = (
                    getattr(form, name).mapToGlobal(getattr(form, name).rect().center()).x()
                    for name in ("objectList", "addButton"))
                assert left < middle
            assert all(form.objectList.item(i).toolTip().startswith(form.objectList.item(i).text())
                       for i in range(form.objectList.count()))
            assert not form.addButton.isEnabled() and not form.locateButton.isEnabled()
            digue_row = next(index for index in range(form.objectList.count())
                             if form.objectList.item(index).data(0x0100)[0] == "digues")
            river_rows = [index for index in range(form.objectList.count())
                          if form.objectList.item(index).data(0x0100)[0] == "cours_eau"]
            form.objectList.item(digue_row).setSelected(True)
            form.objectList.item(river_rows[0]).setSelected(True)
            assert not form.addButton.isEnabled()  # Familles incompatibles.
            form.objectList.clearSelection()
            for index in river_rows[:2]:
                form.objectList.item(index).setSelected(True)
            assert not form.addButton.isEnabled() and form.locateButton.isEnabled()
            form._add_objects()
            assert form.selectedList.count()==0
            form.objectList.clearSelection()
            index=next(i for i in river_rows if not form.objectList.item(i).data(0x0100)[4].getFeature(form.objectList.item(i).data(0x0100)[3][0]).geometry().boundingBox().isEmpty())
            form.objectList.item(index).setSelected(True)
            assert form.addButton.isEnabled() and form.locateButton.isEnabled()
            _, _, _, fids, layer = form.objectList.selectedItems()[0].data(0x0100)
            expected_extent = layer.getFeature(fids[0]).geometry().boundingBox()
            form.locateButton.click()
            assert iface.canvas.extent().intersects(expected_extent)
            form.addButton.click()
            assert form.selectedList.count() == 1
            assert all(form.selectedList.item(i).toolTip() == form.selectedList.item(i).text()
                       for i in range(form.selectedList.count()))
            form.selectedList.setCurrentRow(0)
            assert form.removeButton.isEnabled()
            form.distanceSpin.setValue(400)
            assert form.selectedList.item(0).isSelected()
            form.distanceUnitCombo.setCurrentIndex(1)
            form.distanceSpin.setValue(500)
            form.resize(900, 650); app.processEvents()
            assert form.grab().save(str(AUDITS / "ux_patrimoine_03.png"))
            form.removeButton.click()
            assert form.selectedList.count() == 0 and not form.removeButton.isEnabled()
            form.searchEdit.setText("objet introuvable")
            assert form.objectList.count() == 0
            assert form._spatial_candidates(500000) is not None
            form.searchEdit.clear()
            form.distanceUnitCombo.setCurrentIndex(0)
            form.distanceSpin.setValue(500)
            form.searchEdit.setText("Bancs")
            app.processEvents()
            cases = (
                ("digue_seule", "POINT(1700300 9300080)", {("digues", digue_id)}),
                ("cours_eau_seul", "POINT(1700100 9300080)", {("cours_eau", river_id)}),
                ("chevauchement", "LINESTRING(1700150 9300080,1700250 9300080)",
                 {("cours_eau", river_id), ("digues", digue_id)}),
            )
            rows = []
            for label, wkt, expected in cases:
                form.geometry = QgsGeometry.fromWkt(wkt)
                form.geometry_kind = "line" if label == "chevauchement" else "point"
                form._update_geometry_status()
                form._spatial_cache = None
                _, cold_elapsed = _timed(form._filter_objects)
                _, warm_elapsed = _timed(form._filter_objects, 3)
                shown = {(item.data(0x0100)[0], uid)
                         for item in (form.objectList.item(i) for i in range(form.objectList.count()))
                         for uid in item.data(0x0100)[1]}
                assert shown == expected, (label, shown, expected)
                image = AUDITS / f"patrimoine_spatial_01_{label}.png"
                form.show(); app.processEvents(); form._filter_objects()
                visible_after_show = {(item.data(0x0100)[0], uid)
                                      for item in (form.objectList.item(i) for i in range(form.objectList.count()))
                                      for uid in item.data(0x0100)[1]}
                assert visible_after_show == expected, (label, visible_after_show, expected)
                app.processEvents()
                assert form.grab().save(str(image))
                rows.append(("form_refresh_cold", label, 500, len(wkt), 1, 1,
                             round(cold_elapsed, 3), "QGIS offscreen, SQL included"))
                rows.append(("form_refresh_cached", label, 500, len(wkt), 1, 1,
                             round(warm_elapsed, 3), "QGIS offscreen, median of 3"))
            form.selectedList.clear()
            form._add_selection("cours_eau", (river_id,), "Rivière des Bancs")
            form.geometry = QgsGeometry.fromWkt(cases[0][1])
            form._filter_objects()
            assert "à vérifier" in form.selectedLabel.text()
            assert "nouvelle géométrie" in form.selectedLabel.toolTip()
            form.resize(780, 650); app.processEvents()
            assert form.geometryGroup.y() == form.associationGroup.y()
            assert form._associations() == [("cours_eau", river_id)]
            for family, table in (("ah_stockage", "ah_stockage"),
                                  ("vegetation", "vegetation")):
                uid, wkt = sql(container,
                    f"SELECT id::text,ST_AsText(ST_PointOnSurface({'geometry' if family=='vegetation' else 'geom'})) FROM gemapi.{table} "
                    "ORDER BY id LIMIT 1").stdout.strip().split("|", 1)
                form.geometry = QgsGeometry.fromWkt(wkt)
                form._spatial_cache = None
                assert uid in form._spatial_candidates(100)[family]
            form.shutdown()
            for radius in (100, 500, 1000):
                for label, wkt, _ in cases:
                    query = candidate_query(wkt, radius)
                    result, elapsed = _timed(lambda: sql(container, query).stdout, 3)
                    candidates = [line.split("|") for line in result.splitlines()]
                    n_rivers = sum(row[0] == "cours_eau" for row in candidates)
                    n_levees = sum(row[0] == "digues" for row in candidates)
                    assert n_rivers == 1
                    # Full intervals of nearby rivers precede longitudinal resolution.
                    assert n_levees == 1
                    rows.append(("indexed_prefilter", label, radius, len(wkt), n_rivers,
                                 n_levees, round(elapsed, 3), "PostGIS 16, Docker exec included"))
            settings = ProjectionSettings()
            river_wkt = "MULTILINESTRING((1700000 9300000,1701000 9300000))"
            levees = ((digue_id, 0.2, 0.4, True),)
            _, elapsed = _timed(lambda: _sections(river_wkt, levees), 20)
            rows.append(("interval_sections", "one_levee", 500, len(river_wkt), 1, 1,
                         round(elapsed, 3), "median of 20"))
            for label, wkt, _ in cases + (
                    ("polygon", "POLYGON((1700260 9300060,1700340 9300060,"
                     "1700340 9300120,1700260 9300120,1700260 9300060))", set()),
                    ("large_polygon", "POLYGON((1700000 9300000,1710000 9300000,"
                     "1710000 9310000,1700000 9310000,1700000 9300000))", set())):
                geometry = QgsGeometry.fromWkt(wkt)
                _, sample_elapsed = _timed(lambda: _samples(geometry, settings), 20)
                _, elapsed = _timed(lambda: resolve_watercourse(
                    geometry, river_wkt, levees, settings), 20)
                rows.append(("geometry_sampling", label, 500, len(wkt), 1, 1,
                             round(sample_elapsed, 3), "median of 20"))
                rows.append(("projection_intervals", label, 500, len(wkt), 1, 1,
                             round(elapsed, 3), "median of 20"))
            plan = sql(container, "SET enable_seqscan=off; EXPLAIN " +
                       candidate_query(cases[0][1], 500)).stdout
            for index in ("cours_eau_geom_idx", "sites_terrestres_geom_idx",
                          "ah_stockage_geom_idx", "ah_ouvrage_geom_idx",
                          "vegetation_geometry_idx"):
                assert index in plan, (index, plan)
            default_plan = sql(container, "EXPLAIN " + candidate_query(cases[0][1], 500)).stdout
            default_index_count = sum(index in default_plan for index in
                                      ("cours_eau_geom_idx", "sites_terrestres_geom_idx",
                                       "ah_stockage_geom_idx", "ah_ouvrage_geom_idx",
                                       "vegetation_geometry_idx"))
            project.clear()
        AUDITS.mkdir(exist_ok=True)
        with (AUDITS / "patrimoine_spatial_01_benchmark.csv").open("w", newline="", encoding="utf-8") as target:
            writer = csv.writer(target)
            writer.writerow(("etape", "cas", "rayon_m", "taille_wkt", "cours_eau_candidats",
                             "digues_candidates", "duree_mediane_ms", "note"))
            writer.writerows(rows)
        print(f"PATRIMOINE-SPATIAL-01 : QGIS {Qgis.QGIS_VERSION}, 3 captures, "
              f"{len(rows)} mesures, 5 index GiST disponibles, "
              f"{default_index_count} retenu(s) par le plan de la petite base")
    finally:
        if app is not None:
            QgsProject.instance().clear()
            app.exitQgis()
        command("docker", "stop", container, check=False)


if __name__ == "__main__":
    main()
