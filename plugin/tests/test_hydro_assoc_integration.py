"""HYDRO-ASSOC-01: real PostGIS and QGIS, invented data, disposable database.

QT_QPA_PLATFORM=offscreen /usr/bin/python3 plugin/tests/test_hydro_assoc_integration.py
"""

import os
import faulthandler
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

import psycopg2
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsDataSourceUri,
                       QgsGeometry, QgsPointXY, QgsProject)
from qgis.PyQt.QtCore import QCoreApplication, QDate, QEvent, Qt
from qgis.PyQt.QtWidgets import QWidget

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gemapi_app.creation import CreationDialog
from gemapi_app.management import load_prestation
from gemapi_app.patrimony_spatial import candidate_query, resolve_candidates
from gemapi_app.record import build_delete, build_insert, build_update
from gemapi_app.report_data import build_management_query, build_report_query, prepare_rows
from test_postgres_integration import ROOT, command
from test_patrimony_spatial_integration import _project_copy
from test_qgis_runtime import Interface, assert_capture


def uid(name):
    return str(uuid5(NAMESPACE_URL, "HYDRO-ASSOC-01/" + name))


class HydroAssociationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.container = "sig-hydro-assoc-" + uuid4().hex[:10]
        command("docker", "run", "--rm", "-d", "--name", cls.container,
                "-p", "127.0.0.1::5432", "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
                "-e", "POSTGRES_DB=seed", "postgis/postgis:16-3.4")
        cls.addClassCleanup(command, "docker", "stop", cls.container, check=False)
        for _ in range(60):
            if command("docker", "exec", cls.container, "pg_isready", "-h", "127.0.0.1",
                       "-U", "postgres", check=False).returncode == 0:
                break
            time.sleep(.5)
        cls.port = command("docker", "port", cls.container, "5432").stdout.strip().rsplit(":", 1)[-1]
        cls.db = psycopg2.connect(host="127.0.0.1", port=cls.port, dbname="postgres", user="postgres")
        cls.db.autocommit = True
        cls.addClassCleanup(cls.db.close)
        cls.q("CREATE SCHEMA extensions; CREATE EXTENSION postgis WITH SCHEMA extensions;")
        cls.q((ROOT / "model/schema_v0.6.1.sql").read_text())
        cls.q((ROOT / "model/roles.sql").read_text())
        # Match deployed QGIS logins: the provider resolves PostGIS functions
        # through its login search_path, independently of our SQL test cursor.
        cls.q("ALTER DATABASE postgres SET search_path=pg_catalog,extensions,public,gemapi")
        cls.q("SET search_path=pg_catalog,extensions,public")
        cls.q("INSERT INTO gemapi.prestataires(id,nom) VALUES (%s,'Entreprise fictive')", (uid("provider"),))
        cls.q("INSERT INTO gemapi.techniciens(id,nom) VALUES (%s,'Responsable fictif')", (uid("tech"),))
        cls.q("INSERT INTO gemapi.types_prestations(id,code,libelle,categorie_id) "
              "SELECT %s,'HYDRO_TEST','Intervention fictive',id FROM gemapi.prestations_categorie "
              "ORDER BY ordre LIMIT 1", (uid("type"),))
        cls.q("INSERT INTO gemapi.ah(id,nom) VALUES (%s,'ZEC Gosnay1')", (uid("AH"),))
        for name, y in (("Clarence", 0), ("La Lawe", 1000), ("la Blanche", 2000)):
            cls.q("INSERT INTO gemapi.cours_eau(id,nom,type_ecoulement,source,direction,geom) "
                  "VALUES (%s,%s,'rivière','FICTION HYDRO-ASSOC-01',true,"
                  "ST_Translate(ST_GeomFromText('MULTILINESTRING((1700000 9300000,1701000 9300000))',3950),0,%s))",
                  (uid(name), name, y))
        cls.q("BEGIN; INSERT INTO gemapi.digues(id,nom) VALUES (%s,'CL5')", (uid("CL5"),))
        cls.q("INSERT INTO gemapi.digues_reperes(digue_id,riviere_id,rive,debut_fraction,fin_fraction) "
              "VALUES (%s,%s,'G',.2,.4); COMMIT;", (uid("CL5"), uid("Clarence")))
        for name in ("La Lawe", "la Blanche"):
            cls.q("INSERT INTO gemapi.ah_sections(id,ah_id,cours_eau_id,debut_fraction,fin_fraction) "
                  "VALUES (%s,%s,%s,.2,.4)", (uid("section " + name), uid("AH"), uid(name)))
        cls.values = dict(type_prestation_id=uid("type"), prestataire_id=uid("provider"),
                          technicien_id=uid("tech"), date_debut="2026-11-01", date_fin="2026-11-02",
                          statut_id=str(cls.q("SELECT id FROM gemapi.statuts_prestations WHERE code='PLANIFIE'")[0][0]),
                          intitule="Intervention HYDRO fictive", quantite="3", unite="ouvrages")

    @classmethod
    def q(cls, statement, parameters=None):
        with cls.db.cursor() as cursor:
            cursor.execute(statement, parameters)
            return cursor.fetchall() if cursor.description else []

    def tearDown(self):
        self.q("DELETE FROM gemapi.prestations")

    def insert(self, family="ah_sections", name="section La Lawe", geometry="POINT(1700300 9301080)"):
        return str(self.q(build_insert(self.values, [(family, uid(name))], geometry))[0][0])

    def resolution(self, wkt, course):
        rows = self.q(candidate_query(wkt, 1e9, uid(course)))
        return resolve_candidates(QgsGeometry.fromWkt(wkt), rows)

    def test_01_seven_modes_geometry_fk_and_exclusivity(self):
        row_id = self.insert()
        self.assertEqual(self.q("SELECT ah_section_id::text,origine_mesures,nombre_points,quantite,unite "
                                "FROM gemapi.prestations WHERE id=%s", (row_id,))[0],
                         (uid("section La Lawe"), "Prestation", 1, 3, "ouvrages"))
        for statement in (
                "UPDATE gemapi.prestations SET geom=NULL",
                f"UPDATE gemapi.prestations SET cours_eau_id='{uid('La Lawe')}'",
                f"UPDATE gemapi.prestations SET digue_id='{uid('CL5')}'",
                f"UPDATE gemapi.prestations SET ah_section_id='{uid('absent')}'",
                "UPDATE gemapi.prestations SET ah_section_id=NULL",
                f"DELETE FROM gemapi.ah_sections WHERE id='{uid('section La Lawe')}'"):
            with self.subTest(statement=statement), self.assertRaises(psycopg2.IntegrityError):
                self.q(statement)
        self.q("UPDATE gemapi.prestations SET position='Berge',rive='Gauche'")
        self.q("UPDATE gemapi.prestations SET ah_section_id=%s", (uid("section la Blanche"),))
        self.q("UPDATE gemapi.prestations SET ah_section_id=NULL,cours_eau_id=%s", (uid("Clarence"),))
        self.q("UPDATE gemapi.prestations SET cours_eau_id=NULL,ah_section_id=%s", (uid("section La Lawe"),))
        version = str(self.q("SELECT xmin::text FROM gemapi.prestations")[0][0])
        self.q(build_delete(row_id, version))
        self.assertEqual(self.q("SELECT count(*) FROM gemapi.ah_sections")[0][0], 2)

    def test_02_measures_views_and_single_counted_reports(self):
        for geometry, measure, expected in (
                ("POINT(1700300 9301080)", "nombre_points", 1),
                ("LINESTRING(1700260 9301080,1700340 9301080)", "longueur_m", 80),
                ("POLYGON((1700260 9301060,1700340 9301060,1700340 9301120,1700260 9301120,1700260 9301060))", "surface_m2", 4800)):
            self.insert(geometry=geometry)
            self.assertEqual(self.q(f"SELECT {measure} FROM gemapi.prestations ORDER BY date_modification DESC LIMIT 1")[0][0], expected)
        fields = "nombre_points longueur_m surface_m2 surface_ha type_geometrie type_geometrie_postgis possede_geometrie origine_mesures".split()
        for view in ("prestations_carte", "prestations_fiches"):
            differences = " OR ".join(f"p.{field} IS DISTINCT FROM v.{field}" for field in fields)
            self.assertEqual(self.q(f"SELECT count(*) FROM gemapi.prestations p JOIN gemapi.{view} v USING(id) WHERE {differences}")[0][0], 0)
            self.assertEqual(self.q(f"SELECT count(*)-count(DISTINCT id) FROM gemapi.{view}")[0][0], 0)
        self.assertEqual(self.q("SELECT count(*) FROM gemapi.prestations__ah WHERE ah_id=%s", (uid("AH"),))[0][0], 3)
        self.assertEqual(self.q("SELECT count(*) FROM gemapi.prestations p JOIN gemapi.prestations_carte v USING(id) "
                               "WHERE ST_AsEWKB(p.geom) IS DISTINCT FROM ST_AsEWKB(v.geom)")[0][0], 0)
        for filters in ({}, {"ah": [uid("AH")]}, {"cours_eau": [uid("La Lawe")]},
                        {"ah_sections": [uid("section La Lawe")]}):
            records = prepare_rows(self.q(build_report_query(filters, include_geometry=True)))
            self.assertEqual(len(records), 3)
            self.assertEqual(len({record["id"] for record in records}), 3)
            self.assertTrue(all(record["reference_count"] == 1 for record in records))
            self.assertTrue(all("La Lawe (ZEC Gosnay1)" in record["objects"] for record in records))
            self.assertEqual(len(self.q(build_management_query(filters))), 3)
        self.assertEqual(len(self.q(build_report_query({"cours_eau": [uid("Clarence")]}))), 0)

    def test_02b_multiple_sections_of_one_parent_remain_distinct(self):
        from gemapi_app.report_data import filter_option_queries
        self.q("INSERT INTO gemapi.ah_sections(id,ah_id,cours_eau_id,debut_fraction,fin_fraction) "
               "VALUES (%s,%s,%s,.6,.8)", (uid("second section"), uid("AH"), uid("La Lawe")))
        try:
            options = dict((str(identity), label) for identity, label in self.q(filter_option_queries()["ah_sections"]))
            self.assertNotEqual(options[uid("second section")], options[uid("section La Lawe")])
            allowed, resolutions = self.resolution("POINT(1700700 9301080)", "La Lawe")
            self.assertEqual(allowed["ah_sections"], {uid("second section")})
            self.assertFalse(resolutions[uid("La Lawe")][0])
        finally:
            self.q("DELETE FROM gemapi.ah_sections WHERE id=%s", (uid("second section"),))

    def test_03_spatial_substitution_real_postgis_and_qgis(self):
        cases = (
            ("Clarence", 0, "POINT(1700100 9300080)", "cours_eau", "Clarence"),
            ("Clarence", 0, "POINT(1700300 9300080)", "digues", "CL5"),
            ("La Lawe", 1000, "POINT(1700100 9301080)", "cours_eau", "La Lawe"),
            ("La Lawe", 1000, "POINT(1700300 9301080)", "ah_sections", "section La Lawe"),
            ("la Blanche", 2000, "POINT(1700100 9302080)", "cours_eau", "la Blanche"),
            ("la Blanche", 2000, "POINT(1700300 9302080)", "ah_sections", "section la Blanche"),
            ("La Lawe", 1000, "LINESTRING(1700260 9301080,1700340 9301080)", "ah_sections", "section La Lawe"),
        )
        for course, _, geometry, family, name in cases:
            allowed, _ = self.resolution(geometry, course)
            self.assertEqual({(key, identity) for key, ids in allowed.items() for identity in ids}, {(family, uid(name))})
        for geometry in ("POINT(1700200 9301080)", "LINESTRING(1700150 9301080,1700250 9301080)",
                         "POLYGON((1700170 9301060,1700230 9301060,1700230 9301120,1700170 9301120,1700170 9301060))"):
            _, resolutions = self.resolution(geometry, "La Lawe")
            visible, priorities, ambiguous = resolutions[uid("La Lawe")]
            self.assertTrue(visible and priorities and ambiguous)
        self.q("BEGIN; INSERT INTO gemapi.digues(id,nom) VALUES (%s,'Concurrent fictif')", (uid("overlap"),))
        try:
            self.q("INSERT INTO gemapi.digues_reperes(digue_id,riviere_id,rive,debut_fraction,fin_fraction) "
                   "VALUES (%s,%s,'G',.2,.4); COMMIT;", (uid("overlap"), uid("La Lawe")))
            _, resolutions = self.resolution("POINT(1700300 9301080)", "La Lawe")
            self.assertEqual(resolutions[uid("La Lawe")],
                             (False, {("digues", uid("overlap")), ("ah_sections", uid("section La Lawe"))}, True))
        finally:
            self.q("BEGIN; DELETE FROM gemapi.digues_reperes WHERE digue_id=%s", (uid("overlap"),))
            self.q("DELETE FROM gemapi.digues WHERE id=%s; COMMIT;", (uid("overlap"),))

    def test_04_stale_reference_historical_links_and_atomic_edit(self):
        row_id = self.insert()
        self.q("UPDATE gemapi.cours_eau SET direction=false WHERE id=%s", (uid("La Lawe"),))
        try:
            with self.assertRaises(psycopg2.IntegrityError):
                self.insert()
            self.q("UPDATE gemapi.prestations SET description='Historique conservé' WHERE id=%s", (row_id,))
            self.assertEqual(self.q("SELECT ah_section_id::text FROM gemapi.prestations")[0][0], uid("section La Lawe"))
            allowed, _ = self.resolution("POINT(1700300 9301080)", "La Lawe")
            self.assertFalse(allowed["ah_sections"])
            self.assertTrue(allowed["cours_eau"])
        finally:
            self.q("UPDATE gemapi.cours_eau SET direction=true WHERE id=%s", (uid("La Lawe"),))
        self.q("UPDATE gemapi.cours_eau SET geom=ST_Translate(geom,0,1) WHERE id=%s", (uid("La Lawe"),))
        self.q("UPDATE gemapi.cours_eau SET direction=true WHERE id=%s", (uid("La Lawe"),))
        try:
            with self.assertRaises(psycopg2.IntegrityError):
                self.insert()
            self.q("UPDATE gemapi.prestations SET description='Signature obsolète conservée'")
            allowed, _ = self.resolution("POINT(1700300 9301080)", "La Lawe")
            self.assertFalse(allowed["ah_sections"])
        finally:
            self.q("UPDATE gemapi.cours_eau SET geom=ST_Translate(geom,0,-1) WHERE id=%s", (uid("La Lawe"),))
            self.q("UPDATE gemapi.cours_eau SET direction=true WHERE id=%s", (uid("La Lawe"),))
        version = str(self.q("SELECT xmin::text FROM gemapi.prestations WHERE id=%s", (row_id,))[0][0])
        geometry = "POINT(1700300 9301080)"
        old_links = [("ah_sections", uid("section La Lawe"))]
        new_values = {**self.values, "intitule": "Modification explicite"}
        query = build_update(row_id, version, self.values, new_values, old_links,
                             [("ah_sections", uid("section la Blanche"))], geometry, geometry)
        self.q(query)
        with self.assertRaisesRegex(psycopg2.Error, "SIG_PRESTATIONS_CONFLICT"):
            self.q(query)

    def test_05_roles_need_no_patrimony_edit_privilege(self):
        for role, permitted in (("milieux_consultation", False), ("milieux_prestations", True), ("milieux_patrimoine", True)):
            self.q(f"SET ROLE {role}")
            try:
                self.assertEqual(self.q("SELECT count(*) FROM gemapi.ah_sections")[0][0], 2)
                if permitted:
                    row_id = self.insert()
                    self.q("DELETE FROM gemapi.prestations WHERE id=%s", (row_id,))
                else:
                    with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                        self.insert()
            finally:
                self.q("RESET ROLE")

    def test_06_real_qgis_form_roundtrip_and_layout(self):
        app = QgsApplication.instance() or QgsApplication([], True)
        app.initQgis()
        from qgis.gui import QgsGui
        QgsGui.editorWidgetRegistry().initEditors()
        app.messageLog().messageReceived.connect(
            lambda message, tag, level: print(f"{tag}: {message}", flush=True)
            if tag in ("GEMAPI-App", "PostGIS", "PostgreSQL") else None)
        self.__class__.app = app
        project = QgsProject.instance()
        with TemporaryDirectory() as folder:
            path = Path(folder) / "hydro.qgz"
            copy_folder = os.environ.get("HYDRO_PROJECT_COPY_DIR")
            source = Path(copy_folder) / "prestations_demo.qgz" if copy_folder else None
            _project_copy(path, self.port, source)
            self.assertTrue(project.read(str(path)))
            iface = Interface()
            iface.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3950"))
            from gemapi_app.session_data import SessionData
            course_layer = next(layer for layer in project.mapLayers().values()
                                if layer.providerType() == "postgres" and QgsDataSourceUri(layer.source()).table() == "cours_eau")
            self.assertTrue(course_layer.setSubsetString("\"nom\"='Clarence'"))
            session = SessionData(project)
            session.get("family.hydro_options", lambda: (), 0)  # filtered manager cache
            form = CreationDialog(iface, session=session)
            print("QGIS: formulaire construit", flush=True)
            try:
                self.assertEqual(len(form._hydro_options()), 3)
                sections = [obj for obj in form.objects if obj[0] == "ah_sections"]
                self.assertEqual(len(sections), 2, (form._object_load_failures, form._section_labels))
                self.assertEqual({obj[2] for obj in sections}, {"La Lawe (ZEC Gosnay1)", "la Blanche (ZEC Gosnay1)"})
                self.assertFalse(any("candidats spatiaux" in form.objectList.item(i).text() for i in range(form.objectList.count())))
                for width in (780, 1000):
                    form.resize(width, 650); form.show(); app.processEvents()
                    self.assertEqual(form.geometryGroup.y(), form.associationGroup.y())
                    self.assertEqual(form.photo1Preview.y(), form.photo2Preview.y())
                capture = ROOT / "build/qgis-tests/HYDRO-ASSOC-01/formulaire-creation.png"
                capture.parent.mkdir(parents=True, exist_ok=True)
                self.assertTrue(form.grab().save(str(capture)))
                for combo, key in ((form.typeCombo, "type_prestation_id"), (form.prestataireCombo, "prestataire_id"),
                                   (form.statutCombo, "statut_id"), (form.technicienCombo, "technicien_id")):
                    combo.setCurrentIndex(combo.findData(self.values[key]))
                form.debutDate.setDate(QDate(2026, 11, 1)); form.finDate.setDate(QDate(2026, 11, 2))
                form.intituleEdit.setText(self.values["intitule"])
                form.quantiteEdit.setText("3"); form.uniteEdit.setText("ouvrages")
                with self.assertRaisesRegex(ValueError, "portion hydrographique"):
                    form._check_spatial_attachment([("cours_eau", uid("La Lawe"))], "POINT(1700300 9301080)")
                with self.assertRaisesRegex(ValueError, "ambigu"):
                    form._check_spatial_attachment([("ah_sections", uid("section La Lawe"))],
                                                   "LINESTRING(1700150 9301080,1700250 9301080)")
                form.mapSelectButton.click()
                iface.canvas.mapTool().canvasReleaseEvent(SimpleNamespace(
                    button=lambda: Qt.LeftButton, mapPoint=lambda: QgsPointXY(1700300, 9301080)))
                self.assertIsNone(form.geometry)  # A reference click is never saved as an intervention.
                visible = {form.objectList.item(i).data(Qt.UserRole)[0] for i in range(form.objectList.count())}
                self.assertEqual(visible, {"ah_sections"})
                form._add_selection("ah_sections", (uid("section La Lawe"),), "La Lawe (ZEC Gosnay1)")
                form._update_geometry_status()
                for button, vertices, kind in (
                        (form.pointButton, [(1700300, 9301080)], "point"),
                        (form.lineButton, [(1700260, 9301080), (1700340, 9301080)], "line"),
                        (form.polygonButton, [(1700260, 9301060), (1700340, 9301060), (1700340, 9301120)], "polygon"),
                        (form.pointButton, [(1700300, 9301080)], "point")):
                    assert_capture(form, iface, button, vertices, kind)
                self.assertIsNone(form._geometry_error())
                self.assertTrue(form.saveButton.isEnabled())
                form._save()
                print("QGIS: enregistrement demandé", flush=True)
                self.assertTrue(form.saved_prestation_id, form.statusLabel.text())
                row_id = str(self.q("SELECT id FROM gemapi.prestations")[0][0])
                row, links = load_prestation(form._connection(), row_id)
                self.assertEqual([(str(family), str(identity)) for family, identity, _ in links],
                                 [("ah_sections", uid("section La Lawe"))])
                form.mode = "edit"
                form.load_existing(form._connection(), row_id)
                print("QGIS: fiche rechargée", flush=True)
                self.assertEqual(form._associations(), [("ah_sections", uid("section La Lawe"))])
                form.intituleEdit.setText("Modification depuis le formulaire")
                form._save_edit()
                self.assertTrue(form._edit_saved)
                self.assertEqual(self.q("SELECT intitule FROM gemapi.prestations WHERE id=%s", (row_id,))[0][0],
                                 "Modification depuis le formulaire")
                for layer in project.mapLayers().values():
                    if layer.providerType() == "postgres" and QgsDataSourceUri(layer.source()).table() == "prestations_carte":
                        self.assertIn("ah_section_id", layer.fields().names())
                form.mode = "view"
                form._set_editable(False)
                self.assertFalse(form.saveButton.isEnabled())
                self.assertTrue(form.selectedList.count())
            finally:
                form.shutdown(); form.close()
                form.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                app.processEvents()
                session.close()
                project.clear()
            self.insert(geometry="LINESTRING(1700260 9301080,1700340 9301080)")
            self.insert(geometry="POLYGON((1700260 9301060,1700340 9301060,1700340 9301120,1700260 9301120,1700260 9301060))")
            from qgis.gui import QgsAttributeForm
            base = "demo"
            source = (Path(copy_folder) / f"prestations_{base}.qgz" if copy_folder else
                      ROOT / base / f"prestations_{base}.qgz")
            _project_copy(path, self.port, source)
            self.assertTrue(project.read(str(path)))
            print(f"QGIS: copie {base} chargée", flush=True)
            self.assertTrue(all(layer.isValid() for layer in project.mapLayers().values()))
            section_relations = [relation for identity, relation in project.relationManager().relations().items()
                                 if identity.startswith("hydro_assoc_section_")]
            self.assertEqual(len(section_relations), 4)
            self.assertTrue(all(relation.isValid() for relation in section_relations))
            for layer in project.mapLayers().values():
                if layer.providerType() != "postgres":
                    continue
                table = QgsDataSourceUri(layer.source()).table()
                if table not in ("prestations_carte", "prestations_fiches"):
                    continue
                self.assertEqual(layer.fields().indexFromName("ah_section_id"), 40 if table == "prestations_fiches" else 36)
                features = list(layer.getFeatures())
                self.assertEqual(len(features), 3 if table == "prestations_fiches" else 1)
                for index, feature in enumerate(features):
                    self.assertEqual(str(feature["ah_section_id"]).strip("{}"), uid("section La Lawe"))
                    if table == "prestations_carte":
                        layer.selectByIds([feature.id()])
                        self.assertEqual(layer.selectedFeatureCount(), 1)
                    if index:
                        continue  # All rows checked, one native form per representation suffices.
                    native = QgsAttributeForm(layer, feature)
                    native.show(); app.processEvents()
                    self.assertIsNotNone(native.findChild(QWidget, "ah_section_id"))
                    native.close(); native.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                    app.processEvents()
                    print(f"QGIS: {base} formulaire natif {table} contrôlé", flush=True)
            project.clear()
        # Keep QApplication alive through all SIP-owned cleanup.
        self.__class__.app = app

    def test_07_detailed_and_synthetic_pdf_with_local_basemap(self):
        from qgis.core import QgsVectorLayer
        from gemapi_app.report_pdf import export_detailed_report
        from gemapi_app.report_synthetic_portrait import export_synthetic_portrait_report
        self.insert(geometry="LINESTRING(1700260 9301080,1700340 9301080)")
        records = prepare_rows(self.q(build_report_query({}, include_geometry=True)))
        app = QgsApplication.instance()
        if app is None:
            app = QgsApplication([], True)
            app.initQgis()
        self.__class__.app = app
        basemap = QgsVectorLayer("Polygon?crs=EPSG:3950", "Fond local fictif", "memory")
        output = ROOT / "build/qgis-tests/HYDRO-ASSOC-01"
        output.mkdir(parents=True, exist_ok=True)
        detailed = output / "section-ah-detaille.pdf"
        synthetic = output / "section-ah-synthetique.pdf"
        self.assertEqual(export_detailed_report(records, str(detailed), export_dpi=90,
                                                _shared_basemap=basemap), 1)
        export_synthetic_portrait_report(records, {"groups": ["ah"]}, str(synthetic), basemap_layer=basemap)
        for path in (detailed, synthetic):
            text = " ".join(command("pdftotext", "-layout", str(path), "-").stdout.split())
            self.assertIn("ZEC Gosnay1", text)
            self.assertIn("La Lawe", text)


if __name__ == "__main__":
    faulthandler.enable()
    unittest.main(verbosity=2)
