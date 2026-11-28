"""Open the distributed QGZ and exercise the standard form on a fresh local demo.

Run with QT_QPA_PLATFORM=offscreen /usr/bin/python3 plugin/tests/test_demo_site_children_qgis.py
"""
import argparse
from collections import Counter
from pathlib import Path
import os
from tempfile import TemporaryDirectory
from time import sleep
from uuid import uuid4

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QMainWindow
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsDataSourceUri,
                       QgsGeometry, QgsProject, QgsVectorLayer)
from qgis.gui import QgsMapCanvas

from test_postgres_integration import ROOT, command, install_demo
from gemapi_app.assistant import load_catalog
from gemapi_app.creation import CreationDialog
from gemapi_app.management import connection_from_project, list_prestations, load_prestation
from gemapi_app.patrimony_spatial import candidate_query
from gemapi_app.record import SITE_CHILDREN, TARGETS, build_delete, build_insert
from gemapi_app.report_data import build_report_query, prepare_rows
from gemapi_app.report_pdf import export_detailed_report
from gemapi_app.report_synthetic_portrait import export_synthetic_portrait_report


class Interface:
    def __init__(self):
        self.window = QMainWindow()
        self.canvas = QgsMapCanvas()
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3950"))

    def mainWindow(self):
        return self.window

    def mapCanvas(self):
        return self.canvas


def qualify(directory, project_path):
    app = QgsApplication([], True, str(directory / "profile"))
    app.initQgis()
    project = QgsProject.instance()
    assert project.read(str(project_path))
    assert all(layer.isValid() for layer in project.mapLayers().values())
    assert len(project.mapLayers()) == 33
    relations = project.relationManager().relations()
    assert len(relations) == 70 and all(rel.isValid() for rel in relations.values())
    for layer in project.mapLayers().values():
        if layer.providerType() != "postgres":
            continue
        uri = QgsDataSourceUri(layer.source())
        assert uri.service() == "gemapi_demo" and uri.schema() == "gemapi"
        if uri.table() in ("prestations_fiches", "prestations_carte"):
            assert {target.fk for target in TARGETS.values()} <= set(layer.fields().names())
            assert layer.editFormConfig().readOnly(layer.fields().indexOf("troncon_id"))
        if uri.table() in SITE_CHILDREN:
            assert layer.readOnly() and "site_terrestre_id IS NOT NULL" in uri.sql()
        for i, field in enumerate(layer.fields()):
            widget = layer.editorWidgetSetup(i)
            if widget.type() == "RelationReference" and widget.config().get("Relation"):
                rel = relations[widget.config()["Relation"]]
                assert rel.referencingLayer() == layer and field.name() in rel.fieldPairs()
            if widget.type() == "ValueRelation":
                ref = project.mapLayer(widget.config()["Layer"])
                assert ref and all(ref.fields().indexOf(widget.config()[key]) >= 0 for key in ("Key", "Value"))

    iface = Interface()
    db = connection_from_project()
    # All writes below run only against the freshly installed container database.
    assert db.executeSql("SELECT current_database(), current_user") == [["demo_qualification", "qgis_technicien"]]

    def report(filters):
        raw = db.executeSql(build_report_query(filters, include_geometry=True))
        records = prepare_rows(raw)
        assert len(raw) == len(records)
        return records

    baseline = report({})
    assert len(baseline) == 100
    borrowed = [r for r in baseline if r["own_wkt"] is None]
    assert len(borrowed) == 12 and all(r["objects"].startswith("vegetation : ") for r in borrowed)
    assert all(r["reference_geometries"][0]["wkt"] for r in borrowed)
    known_site = "6fb4c2ae-3be1-5ba1-cc5f-31daadd0c6f4"
    filters = {"site_terrestre": [known_site]}
    selected = report(filters)
    expected = {r[0] for r in db.executeSql("SELECT prestation_id::text FROM gemapi.prestations__sites_terrestres "
                                            f"WHERE site_terrestre_id='{known_site}'")}
    assert len(expected) == 8 and {r["id"] for r in selected} == expected
    assert {r[0] for r in list_prestations(db, filters)} == expected
    background = QgsVectorLayer("Polygon?crs=EPSG:2154", "Fond de contrôle", "memory")
    assert export_detailed_report(borrowed[:1], str(directory / "fiche.pdf"), _shared_basemap=background) == 1
    pages, maps = export_synthetic_portrait_report(selected, filters, str(directory / "site.pdf"), basemap_layer=background)
    assert pages >= 1 and maps >= 1

    edit = CreationDialog(iface, mode="edit")
    edit.load_existing(db, borrowed[0]["id"])
    values, links, shape = edit._snapshot()
    assert links[0][0] == "vegetation" and shape is None
    edit.intituleEdit.setText("Entretien fictif vérifié")
    edit.saveButton.click()
    assert edit._edit_saved, edit.statusLabel.text()
    key = borrowed[0]["id"]
    row, saved_links = load_prestation(db, key)
    assert [(r[0], r[1]) for r in saved_links] == list(links)
    assert [str(row[i] or "") for i in (7, 8, 10, 11, 12, 13)] == [
        values[f] for f in ("quantite", "unite", "photo_1", "photo_1_commentaire", "photo_2", "photo_2_commentaire")]
    assert row[14] is None
    edit.shutdown()

    for family in (*SITE_CHILDREN, "ah_ouvrage", "ah_stockage"):
        form = CreationDialog(iface)
        assert not form._object_load_failures
        counts = Counter(obj[0] for obj in form.objects)
        assert [counts[k] for k in SITE_CHILDREN] == [4, 2, 19]
        assert "troncons" not in counts
        catalog = load_catalog(db, family)
        uid = catalog["patrimoine"][0][0]
        if family in SITE_CHILDREN:
            choice = next(form.objectList.item(i) for i in range(form.objectList.count())
                          if form.objectList.item(i).data(Qt.UserRole)[:2] == (family, (uid,)))
            form.objectList.setCurrentItem(choice)
            _, _, _, fids, layer = choice.data(Qt.UserRole)
            iface.canvas.setExtent(layer.extent())
            form._locate()
            feature = layer.getFeature(fids[0])
            assert iface.canvas.extent().contains(feature.geometry().boundingBox())
        unavailable = [str(uuid4())]
        if family in SITE_CHILDREN:
            unavailable.extend(r[0] for r in db.executeSql(
                f"SELECT id::text FROM gemapi.{family} WHERE site_terrestre_id IS NULL"))
        for absent in unavailable:
            try:
                form.load_draft(values, [(family, absent)])
            except ValueError:
                pass
            else:
                raise AssertionError("Brouillon sur cible inaccessible")
        form.load_draft(values, [(family, uid)])
        assert form.saveButton.isEnabled() and form.geometry is None
        form.saveButton.click()
        assert form.saved_prestation_id, form.statusLabel.text()
        fk = TARGETS[family].fk
        shape, origin, qty = db.executeSql(
            f"SELECT ST_AsText(c.geom),c.origine_mesures,c.quantite::text FROM gemapi.prestations_carte c "
            f"WHERE c.id='{form.saved_prestation_id}' AND c.{fk}='{uid}'")[0]
        assert origin == "Patrimoine de rattachement" and qty == values["quantite"]
        assert iface.canvas.extent().contains(QgsGeometry.fromWkt(shape).boundingBox())
        if family in SITE_CHILDREN:
            assert any(r[:2] == [family, uid] for r in db.executeSql(candidate_query(shape, 0)))
        row, _ = load_prestation(db, form.saved_prestation_id)
        db.executeSql(build_delete(form.saved_prestation_id, row[18]))
        form.shutdown()

    for family in (*SITE_CHILDREN, "troncons"):
        where = " WHERE site_terrestre_id IS NULL" if family in SITE_CHILDREN else ""
        uid = db.executeSql(f"SELECT id::text FROM gemapi.{family}{where} LIMIT 1")[0][0]
        own = "POINT(1700000 9300000)" if family == "troncons" else None
        key = db.executeSql(build_insert(values, [(family, uid)], own))[0][0]
        row, links = load_prestation(db, key)
        assert links[0][:2] == [family, uid]
        rec = next(r for r in report({}) if r["id"] == key)
        assert not rec["parents"] and key not in {r["id"] for r in report(filters)}
        db.executeSql(build_delete(key, row[18]))
    assert len(list_prestations(db)) == 100
    project.clear()
    print("QGZ, relations, droits Prestations, 100 fiches, édition, 5 créations, focus, PDF et cibles hors site vérifiés.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--postgis-extensions", action="store_true")
    parser.add_argument("--project", type=Path, default=ROOT / "demo/prestations_demo.qgz")
    args = parser.parse_args()
    dedicated = args.postgis_extensions
    container = "gemapi-qgis-" + uuid4().hex
    command("docker", "run", "--rm", "-d", "--name", container, "-p", "127.0.0.1::5432",
            "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "postgis/postgis:16-3.4")
    try:
        for _ in range(90):
            if command("docker", "exec", container, "pg_isready", "-h", "127.0.0.1", "-U", "postgres", check=False).returncode == 0:
                break
            sleep(1)
        else:
            raise RuntimeError("PostGIS jetable indisponible")
        install_demo(container, dedicated_postgis=dedicated)
        setup = (ROOT / "model/roles.sql").read_text() + "\nCREATE ROLE qgis_technicien LOGIN IN ROLE milieux_prestations;\n"
        for table, col, code in (("mobilier", "type_id", "PAN"), ("cheminements", "type_cheminement_id", "PAS"),
                               ("vegetation", "famille_vegetation", "ARBRES")):
            setup += (f"INSERT INTO gemapi.{table}(designation,{col},geometry,ah_section_id) "
                      f"SELECT 'Objet hors site','{code}','SRID=3950;POINT(1 1)',id FROM gemapi.ah_sections LIMIT 1;\n")
        command("docker", "exec", "-i", container, "psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "demo_qualification", input_text=setup)
        port = command("docker", "port", container, "5432").stdout.strip().rsplit(":", 1)[1]
        with TemporaryDirectory(dir=ROOT / "build/qgis-tests") as folder:
            directory = Path(folder)
            service = directory / "pg_service.conf"
            service.write_text(f"[gemapi_demo]\nhost=127.0.0.1\nport={port}\ndbname=demo_qualification\nuser=qgis_technicien\n"
                               + ("options=-csearch_path=pg_catalog,extensions,pg_temp\n" if dedicated else ""))
            os.environ["PGSERVICEFILE"] = str(service)
            qualify(directory, args.project)
    finally:
        command("docker", "stop", container)


if __name__ == "__main__":
    main()
