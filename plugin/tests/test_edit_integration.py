"""UX-07: real PyQGIS and disposable PostGIS editing integration checks.

Run with QT_QPA_PLATFORM=offscreen /usr/bin/python3 and Docker available.
"""

import sys
import time
from statistics import median
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from qgis.PyQt.QtCore import QDate, Qt, QTimer
from qgis.PyQt.QtWidgets import QMainWindow, QMessageBox
from qgis.core import (QgsApplication, QgsDataSourceUri, QgsGeometry, QgsProject,
                       QgsVectorLayer, QgsWkbTypes)
from qgis.gui import QgsMapCanvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gemapi_app import classFactory  # noqa: E402
from gemapi_app.creation import CreationDialog  # noqa: E402
from gemapi_app.management import load_prestation  # noqa: E402
from gemapi_app.record import FIELDS, build_insert, build_update  # noqa: E402
from gemapi_app.perf import ENABLED as PERF_ENABLED  # noqa: E402
from gemapi_app.session_data import CACHE_ENABLED  # noqa: E402
from test_postgres_integration import ROOT, command, sql  # noqa: E402


def main():
    container = "sig-prestations-edit-" + uuid4().hex[:10]
    command("docker", "run", "--rm", "-d", "--name", container,
            "-p", "127.0.0.1::5432", "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
            "postgis/postgis:16-3.4")
    try:
        for _ in range(60):
            if command("docker", "exec", container, "pg_isready", "-h", "127.0.0.1",
                       "-U", "postgres", check=False).returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("PostGIS de test indisponible")
        sql(container, "CREATE EXTENSION IF NOT EXISTS postgis;")
        sql(container, (ROOT / "model/schema_v0.6.1.sql").read_text())
        ids = {name: str(uuid4()) for name in (
            "provider_1", "provider_2", "type_1", "type_2", "zec_1", "zec_2",
            "fosse", "riviere", "site", "ah_1", "ah_2", "site_parent", "technician")}
        sql(container, f"""
            BEGIN;
            INSERT INTO gemapi.techniciens(id,nom,prenom) VALUES
              ('{ids['technician']}', 'Responsable', 'Test');
            INSERT INTO gemapi.prestataires(id, nom) VALUES
              ('{ids['provider_1']}', 'Entreprise A'), ('{ids['provider_2']}', 'Entreprise B');
            INSERT INTO gemapi.types_prestations(id, code, libelle, categorie_id) VALUES
              ('{ids['type_1']}', 'ENTRETIEN', 'Entretien', 'cf2572fb-9365-5ac5-bdf4-0d66581d9aaa'),
              ('{ids['type_2']}', 'EDIT_RESTAURATION', 'Restauration', 'cf2572fb-9365-5ac5-bdf4-0d66581d9aaa');
            INSERT INTO gemapi.ah(id,nom) VALUES
              ('{ids['ah_1']}', 'AH A'), ('{ids['ah_2']}', 'AH B');
            INSERT INTO gemapi.ah_stockage(id, ah_id, nom, geom) VALUES
              ('{ids['zec_1']}', '{ids['ah_1']}', 'ZEC A', ST_GeomFromText(
                  'POLYGON((0 0,10 0,10 10,0 10,0 0))', 3950)),
              ('{ids['zec_2']}', '{ids['ah_2']}', 'ZEC B', ST_GeomFromText(
                  'POLYGON((20 20,30 20,30 30,20 30,20 20))', 3950));
            INSERT INTO gemapi.ah_ouvrage(ah_id,geom) VALUES
              ('{ids['ah_1']}',ST_GeomFromText('LINESTRING(0 0,10 0)',3950)),
              ('{ids['ah_2']}',ST_GeomFromText('LINESTRING(20 20,30 20)',3950));
            INSERT INTO gemapi.cours_eau(id, nom, type_ecoulement, source, geom) VALUES
              ('{ids['fosse']}', 'Fossé A', 'fossé', 'TEST', ST_Multi(ST_GeomFromText('LINESTRING(0 0,10 10)',3950))),
              ('{ids['riviere']}', 'Rivière A', 'rivière', 'TEST', ST_Multi(ST_GeomFromText('LINESTRING(0 0,10 10)',3950)));
            INSERT INTO gemapi.sites_terrestres(id,nom,geom) VALUES
              ('{ids['site_parent']}','Site parent',ST_GeomFromText('MULTIPOLYGON(((0 0,10 0,10 10,0 0)))',3950));
            INSERT INTO gemapi.vegetation(famille_vegetation,id,site_terrestre_id,designation,geometry) VALUES
              ('ARBRES','{ids['site']}', '{ids['site_parent']}', 'Site A', ST_GeomFromText('MULTIPOINT((0 0))',3950));
            COMMIT;
        """)
        status = dict(line.split("|", 1) for line in sql(container,
            "SELECT code, id::text FROM gemapi.statuts_prestations").stdout.strip().splitlines())
        port = command("docker", "port", container, "5432").stdout.strip().rsplit(":", 1)[-1]

        app = QgsApplication([], True)
        app.initQgis()
        canvas = QgsMapCanvas()
        canvas.resize(650, 420)
        window = QMainWindow()
        window.setCentralWidget(canvas)
        window.resize(800, 600)
        window.show()
        app.processEvents()

        def make_layer(table, geometry_column=""):
            uri = QgsDataSourceUri()
            uri.setConnection("127.0.0.1", port, "postgres", "postgres", "")
            uri.setDataSource("gemapi", table, geometry_column, "", "id")
            if geometry_column:
                kind = {"ah_stockage": QgsWkbTypes.Polygon,
                        "ah_ouvrage": QgsWkbTypes.LineString,
                        "cours_eau": QgsWkbTypes.MultiLineString}.get(table, QgsWkbTypes.Point)
                uri.setWkbType(kind)
                uri.setSrid("3950")
            layer = QgsVectorLayer(uri.uri(False), "Couche " + table, "postgres")
            assert layer.isValid(), (table, layer.source(), layer.error().summary())
            QgsProject.instance().addMapLayer(layer)
            return layer

        prestations = make_layer("prestations", "geom")
        make_layer("prestataires")
        make_layer("types_prestations")
        for table in ("ah_stockage", "ah_ouvrage", "cours_eau"):
            make_layer(table, "geom")
        canvas.setDestinationCrs(prestations.crs())
        canvas.setLayers([prestations])

        class Interface:
            def mainWindow(self):
                return window

            def mapCanvas(self):
                return canvas

            def addPluginToMenu(self, *_):
                pass

            def addToolBarIcon(self, *_):
                pass

            def removePluginMenu(self, *_):
                pass

            def removeToolBarIcon(self, *_):
                pass

        plugin = classFactory(Interface())
        plugin.initGui()
        perf_messages = []
        def capture_perf(message, tag, _level):
            if tag == "SIG-PERF":
                perf_messages.append(message)
        app.messageLog().messageReceived.connect(capture_perf)

        def measured(label, action, repetitions=1):
            samples = []
            for _ in range(repetitions):
                before = len(perf_messages)
                start = perf_counter()
                action()
                elapsed = (perf_counter() - start) * 1000
                queries = sum(message.startswith("sql operation=")
                              for message in perf_messages[before:])
                samples.append((elapsed, queries))
            print(f"PERF02 {label}: first_ms={samples[0][0]:.2f} "
                  f"warm_median_ms={median(item[0] for item in samples[1:]):.2f} "
                  f"first_queries={samples[0][1]} "
                  f"warm_queries={samples[-1][1]}", flush=True)
            return samples
        values = dict(type_prestation_id=ids["type_1"],
                      prestataire_id=ids["provider_1"], statut_id=status["PLANIFIE"],
                      technicien_id=ids["technician"],
                      date_debut="2027-01-01", date_fin="2027-01-15",
                      photo_1="photos/initiale.jpg", photo_1_commentaire="Avant")
        first = sql(container, build_insert(
            values, [("ah_stockage", ids["zec_1"])],
            "POINT(0 0)")).stdout.strip().split("|")[0]
        second = sql(container, build_insert(
            {**values, "intitule": "Seconde fiche", "photo_1": ""},
            [("vegetation", ids["site"])], None)).stdout.strip().split("|")[0]

        from gemapi_app.management import connection_from_project
        connection = connection_from_project()

        def loaded(uid):
            row, links = load_prestation(connection, uid)
            return (str(row[18]), dict(zip(FIELDS, [*row[1:5], row[19],
                                                  *row[5:10], row[21], row[22], *row[10:14]])),
                    [(str(item[0]), str(item[1])) for item in links], row[14])

        def edit(uid, new_values=None, new_links=None, geometry="unchanged", check=True):
            version, before, links, wkt = loaded(uid)
            after = {**before, **(new_values or {})}
            target_links = links if new_links is None else new_links
            target_geometry = wkt if geometry == "unchanged" else geometry
            query = build_update(uid, version, before, after, links, target_links,
                                 wkt, target_geometry)
            if query is not None:
                result = sql(container, query, check=check)
                return result, query
            return None, None

        # Simple fields, mandatory references, optional title and photos.
        _, query = edit(first, dict(intitule="Travaux modifiés", description="Texte révisé",
                                    type_prestation_id=ids["type_2"],
                                    prestataire_id=ids["provider_2"],
                                    statut_id=status["TERMINE"],
                                    date_debut="2027-02-01", date_fin="2027-02-28",
                                    photo_1="photos/remplacement.jpg",
                                    photo_1_commentaire="Après"))
        assert "geom =" not in query and "DELETE FROM" not in query
        assert loaded(first)[1]["intitule"] == "Travaux modifiés"
        assert loaded(first)[1]["statut_id"] == status["TERMINE"]
        assert loaded(first)[1]["photo_1"] == "photos/remplacement.jpg"
        _, query = edit(first, dict(intitule="", photo_1="", photo_1_commentaire=""))
        assert loaded(first)[1]["intitule"] is None
        assert loaded(first)[1]["photo_1"] is None
        try:
            edit(first, dict(date_debut="2027-03-01", date_fin="2027-02-28"))
        except ValueError as error:
            assert "date de fin" in str(error)
        else:
            raise AssertionError("Ordre des dates invalide accepté")

        # Changes to geometry must not be written when the WKT is unchanged.
        for source, target, wkt in (
            ("ST_Point", "ST_LineString", "LINESTRING(0 0,10 10)"),
            ("ST_LineString", "ST_Polygon", "POLYGON((0 0,10 0,10 10,0 0))"),
            ("ST_Polygon", "ST_Point", "POINT(5 5)"),
        ):
            assert sql(container, f"SELECT ST_GeometryType(geom) FROM gemapi.prestations "
                       f"WHERE id='{first}'").stdout.strip() == source
            edit(first, geometry=wkt)
            assert sql(container, f"SELECT ST_GeometryType(geom),ST_SRID(geom) "
                       f"FROM gemapi.prestations WHERE id='{first}'").stdout.strip() == f"{target}|3950"

        # A storage can be replaced atomically without changing the prestation.
        links = [("ah_stockage", ids["zec_2"])]
        edit(first, new_links=links)
        assert loaded(first)[3] is not None
        assert set(loaded(first)[2]) == set(links)
        assert sql(container, f"SELECT count(*) FROM gemapi.prestations "
                   f"WHERE id='{first}' AND ah_stockage_id='{ids['zec_1']}'").stdout.strip() == "0"
        assert sql(container, "SELECT count(*) FROM gemapi.ah_stockage").stdout.strip() == "2"
        try:
            edit(first, new_links=[], geometry=None)
        except ValueError as error:
            assert "rattachement" in str(error)
        else:
            raise AssertionError("Localisation vide acceptée")
        assert set(loaded(first)[2]) == set(links)

        # A failed FK must roll back the changed title and every link mutation.
        missing = str(uuid4())
        failed, _ = edit(first, dict(intitule="Ne doit pas persister"),
                         [("ah_stockage", missing)], check=False)
        assert failed.returncode != 0
        assert loaded(first)[1]["intitule"] is None
        assert set(loaded(first)[2]) == set(links)

        # Changes to the row and link-only changes by another writer conflict.
        version, before, old_links, wkt = loaded(first)
        sql(container, f"UPDATE gemapi.prestations SET description='Concurrent' WHERE id='{first}'")
        conflict = sql(container, build_update(first, version, before,
            {**before, "description": "Écrasement"}, old_links, old_links, wkt, wkt),
            check=False)
        assert conflict.returncode != 0 and "SIG_PRESTATIONS_CONFLICT" in conflict.stderr
        assert loaded(first)[1]["description"] == "Concurrent"
        version, before, old_links, wkt = loaded(first)
        assert build_update(first, version, before, before, old_links, old_links,
                            wkt, wkt) is None
        sql(container, "CREATE ROLE edit_readonly; GRANT USAGE ON SCHEMA gemapi "
                       "TO edit_readonly; GRANT SELECT ON ALL TABLES IN SCHEMA "
                       "gemapi TO edit_readonly;")
        forbidden = sql(container, "SET ROLE edit_readonly; " + build_update(
            first, version, before, {**before, "description": "Interdit"},
            old_links, old_links, wkt, wkt), check=False)
        assert forbidden.returncode != 0 and "permission denied" in forbidden.stderr.lower()
        assert loaded(first)[1]["description"] == "Concurrent"
        sql(container, "CREATE ROLE edit_limited; GRANT USAGE ON SCHEMA gemapi "
                       "TO edit_limited; GRANT SELECT ON ALL TABLES IN SCHEMA "
                       "gemapi TO edit_limited; GRANT UPDATE ON gemapi.prestations "
                       "TO edit_limited;")
        version, before, old_links, wkt = loaded(first)
        sql(container, "SET ROLE edit_limited; " + build_update(
            first, version, before, {**before, "description": "Accès limité"},
            old_links, old_links, wkt, wkt))
        assert loaded(first)[1]["description"] == "Accès limité"
        edit(first, geometry="POINT(6 6)")
        version, before, old_links, wkt = loaded(first)
        sql(container, f"UPDATE gemapi.prestations SET ah_stockage_id='{ids['zec_1']}' WHERE id='{first}';")
        conflict = sql(container, build_update(first, version, before,
            {**before, "description": "Écrasement"}, old_links, old_links, wkt, wkt),
            check=False)
        assert conflict.returncode != 0 and "SIG_PRESTATIONS_CONFLICT" in conflict.stderr
        assert loaded(first)[1]["description"] == "Accès limité"
        sql(container, f"UPDATE gemapi.prestations SET ah_stockage_id='{ids['zec_2']}' WHERE id='{first}';")
        edit(first, geometry="MULTILINESTRING((0 0,1 1),(10 10,11 11))")

        # The QGIS form loads two UUIDs in sequence without carrying over fields.
        manager_samples = measured("manager", plugin.show_management, 6)
        if PERF_ENABLED and CACHE_ENABLED:
            assert all(queries == 0 for _, queries in manager_samples[1:])
        manager = plugin.manage
        def construct_form():
            form = CreationDialog(plugin.iface, window, session=plugin.session)
            form.close()
            form.deleteLater()
        form_samples = measured("form", construct_form, 6)
        if PERF_ENABLED and CACHE_ENABLED:
            assert all(queries == 0 for _, queries in form_samples[1:])
        assert manager.connection is not None, manager.statusLabel.text()
        external_provider = str(uuid4())
        sql(container, "INSERT INTO gemapi.prestataires(id,nom) VALUES "
                       f"('{external_provider}','Prestataire externe')")
        def provider_ids():
            picker = manager.pickers["providers"]
            return {str(picker.list.item(i).data(Qt.UserRole))
                    for i in range(picker.list.count())}
        assert external_provider not in provider_ids()
        manager.refreshButton.click()
        assert external_provider in provider_ids()
        assert not any(picker.values() for picker in manager.pickers.values())
        statuses = manager.pickers["statuses"]
        for index in range(statuses.list.count()):
            item = statuses.list.item(index)
            if str(item.data(Qt.UserRole)) == status["PLANIFIE"]:
                item.setCheckState(Qt.Checked)
                break
        assert statuses.values() == (status["PLANIFIE"],)
        manager.searchEdit.setText("Seconde")
        manager._fetch_results()
        assert manager.prestationList.count() == 1
        assert manager.selected_id() is None
        manager.prestationList.item(0).setSelected(True)
        plugin.show_existing()
        dialog = plugin.creation
        assert dialog.mode == "edit" and dialog.current_prestation_id == second
        assert dialog.intituleEdit.text() == "Seconde fiche"
        assert dialog._associations() == [("vegetation", ids["site"])]
        assert dialog.positionCombo.currentData() == "Indéfini"
        assert not dialog.positionCombo.isEnabled()
        assert dialog.riveCombo.currentData() == "Indéfinie"
        assert not dialog.riveCombo.isEnabled()
        dialog.selectedList.clear()
        dialog._add_selection("cours_eau", (ids["riviere"],), "Rivière A")
        dialog._update_position_state()
        assert dialog.positionCombo.isEnabled()
        assert dialog.riveCombo.isEnabled()
        dialog.positionCombo.setCurrentIndex(dialog.positionCombo.findData("Crête"))
        dialog.riveCombo.setCurrentIndex(dialog.riveCombo.findData("Gauche"))
        assert dialog._values()["position"] == "Crête"
        assert dialog._values()["rive"] == "Gauche"
        dialog.resize(900, 650)
        dialog.formScroll.ensureWidgetVisible(dialog.detailsGroup)
        app.processEvents()
        assert dialog.grab().save(str(ROOT / "build/qgis-tests/prestations_position_rive_01_formulaire.png"))
        for family in ("digues", "ah_ouvrage"):
            dialog.selectedList.clear()
            dialog._update_position_state()
            assert dialog._values()["position"] == "Indéfini"
            assert dialog._values()["rive"] == "Indéfinie"
            dialog._add_selection(family, (str(uuid4()),), family)
            dialog._update_position_state()
            assert dialog.positionCombo.isEnabled() and dialog.riveCombo.isEnabled()
            assert dialog._values()["position"] == "Crête"
            assert dialog._values()["rive"] == "Gauche"
        for family in ("ah_stockage", "vegetation"):
            dialog.selectedList.clear()
            dialog._add_selection(family, (str(uuid4()),), family)
            dialog._update_position_state()
            assert not dialog.positionCombo.isEnabled() and not dialog.riveCombo.isEnabled()
            assert dialog._values()["position"] == "Indéfini"
            assert dialog._values()["rive"] == "Indéfinie"
        dialog.selectedList.clear()
        dialog._update_position_state()
        assert dialog.positionCombo.currentData() == "Indéfini"
        assert dialog.riveCombo.currentData() == "Indéfinie"
        assert dialog.photo_references[0] == ""
        dialog.load_existing(manager.connection, first)
        assert dialog.current_prestation_id == first and not dialog.intituleEdit.text()
        assert dialog._associations() == [("ah_stockage", ids["zec_2"])]
        old_geometry = dialog.geometry.asWkt()
        dialog.pointButton.click()
        assert dialog._capture is not None
        dialog.cancel_capture()
        assert dialog.geometry.asWkt() == old_geometry
        dialog.load_existing(manager.connection, second)
        assert dialog.current_prestation_id == second
        assert dialog._associations() == [("vegetation", ids["site"])]
        assert dialog.typeCombo.currentData() == ids["type_1"]
        picker = dialog.typeCombo
        picker.showPopup()
        picker._choose(picker.entries.item(0))
        target = next(picker.entries.item(i) for i in range(picker.entries.count())
                      if picker.entries.item(i).data(Qt.UserRole) == ("type", ids["type_2"]))
        picker._choose(target)
        assert dialog._values()["type_prestation_id"] == ids["type_2"]
        dialog.photo_references[0] = "photos/nouvelle.jpg"
        dialog._update_photo(0)
        dialog.debutDate.setDate(QDate(2027, 4, 1))
        dialog.finDate.setDate(QDate(2027, 4, 30))
        dialog.saveButton.click()
        assert loaded(second)[1]["type_prestation_id"] == ids["type_2"]
        app.processEvents()
        assert plugin.creation is dialog and dialog._save_completed and not dialog.isVisible()
        assert plugin.home.isVisible() and not manager.isVisible()
        assert plugin.manage is manager
        plugin.show_management()
        assert manager.isVisible()
        assert manager.searchEdit.text() == "Seconde"
        assert manager.pickers["statuses"].values() == (status["PLANIFIE"],)
        assert manager.prestationList.count() == 1
        assert loaded(second)[1]["photo_1"] == "photos/nouvelle.jpg"
        assert "Modifications enregistrées" in dialog.statusLabel.text()
        manager.clear_filters()
        for index in range(manager.prestationList.count()):
            item = manager.prestationList.item(index)
            if item.data(Qt.UserRole) == first:
                item.setSelected(True)
                break
        assert manager.selected_id() == first
        plugin.show_existing()
        dialog = plugin.creation
        dialog.descriptionEdit.setPlainText("Modification locale à refuser")
        sql(container, f"UPDATE gemapi.prestations SET description='Version externe' "
                       f"WHERE id='{first}'")
        dialog.saveButton.click()
        assert plugin.creation is dialog and "Recharger la fiche" in dialog.statusLabel.text()
        assert loaded(first)[1]["description"] == "Version externe"
        QTimer.singleShot(0, lambda: app.activeModalWidget().button(QMessageBox.Yes).click())
        dialog.reloadButton.click()
        assert dialog.descriptionEdit.toPlainText() == "Version externe"
        assert dialog.geometry.isMultipart() and len(dialog.geometry.asMultiPolyline()) == 2
        before_links = loaded(first)[2]
        dialog.addPartButton.click()
        assert dialog._capture is not None
        dialog._capture._completed(QgsGeometry.fromWkt("LINESTRING(20 20,21 21)"))
        assert len(dialog.geometry.asMultiPolyline()) == 3
        assert not dialog.saveButton.isEnabled()
        dialog.finishGeometryButton.click()
        assert dialog.saveButton.isEnabled()
        dialog.saveButton.click()
        app.processEvents()
        assert plugin.creation is dialog and dialog._save_completed and not dialog.isVisible()
        assert plugin.home.isVisible()
        assert sql(container, f"SELECT ST_NumGeometries(geom) FROM gemapi.prestations "
                   f"WHERE id='{first}'").stdout.strip() == "3"
        assert loaded(first)[2] == before_links
        assert plugin.creation is dialog and plugin.manage is manager
        # Create through the same hierarchical picker and persist via the real form.
        form = CreationDialog(plugin.iface, window, session=plugin.session)
        form.intituleEdit.setText("Création hiérarchique")
        form.typeCombo.showPopup()
        form.typeCombo._choose(form.typeCombo.entries.item(0))
        target = next(form.typeCombo.entries.item(i)
                      for i in range(form.typeCombo.entries.count())
                      if form.typeCombo.entries.item(i).data(Qt.UserRole) == ("type", ids["type_1"]))
        form.typeCombo._choose(target)
        for combo, uid in ((form.prestataireCombo, ids["provider_1"]),
                           (form.statutCombo, status["PLANIFIE"]),
                           (form.technicienCombo, ids["technician"])):
            combo.setCurrentIndex(combo.findData(uid))
        form.debutDate.setDate(QDate(2027, 5, 1))
        form.finDate.setDate(QDate(2027, 5, 2))
        form._add_selection("vegetation", (ids["site"],), "Site A")
        form._update_save_state()
        assert form.saveButton.isEnabled(), form.statusLabel.text()
        form.saveButton.click()
        assert sql(container, "SELECT type_prestation_id FROM gemapi.prestations "
                   "WHERE intitule='Création hiérarchique'").stdout.strip() == ids["type_1"], form.statusLabel.text()
        assert form._save_completed and not form.isVisible()
        form._reset()  # A new input starts explicitly; a successful form is retained hidden.
        form.intituleEdit.setText("Création sur site")
        for combo, uid in ((form.typeCombo, ids["type_1"]), (form.prestataireCombo, ids["provider_1"]),
                           (form.statutCombo, status["PLANIFIE"]), (form.technicienCombo, ids["technician"])):
            combo.setCurrentIndex(combo.findData(uid))
        form.debutDate.setDate(QDate(2027, 5, 1))
        form.finDate.setDate(QDate(2027, 5, 2))
        form.geometry = QgsGeometry.fromWkt("POINT(1 1)")
        form.geometry_kind = "point"
        form._add_selection("sites_terrestres", (ids["site_parent"],), "Site parent")
        form._update_geometry_status()
        assert form.saveButton.isEnabled()
        form.saveButton.click()
        site_prestation = sql(container, "SELECT id::text,ST_AsText(geom),site_terrestre_id "
                              "FROM gemapi.prestations WHERE intitule='Création sur site'").stdout.strip().split("|")
        assert site_prestation[1:] == ["MULTIPOINT((1 1))", ids["site_parent"]], form.statusLabel.text()
        site_form = CreationDialog(plugin.iface, window, mode="edit", session=plugin.session)
        site_form.load_existing(site_form._connection(), site_prestation[0])
        site_form.geometry = QgsGeometry.fromWkt("LINESTRING(0 0,3 4)")
        site_form.geometry_kind = "line"
        site_form._update_geometry_status()
        site_form.saveButton.click()
        assert site_form._edit_saved, site_form.statusLabel.text()
        assert sql(container, "SELECT ST_AsText(geom),site_terrestre_id "
                   f"FROM gemapi.prestations WHERE id='{site_prestation[0]}'").stdout.strip() == (
                       "MULTILINESTRING((0 0,3 4))|" + ids["site_parent"])
        site_form.shutdown()
        form.close()
        plugin.unload()
        canvas.close()
        window.close()
        QgsProject.instance().clear()
        app.exitQgis()
        print("UX-07 : modifications, géométries, liens, rollback, concurrence et parcours Qt/PostGIS OK")
    finally:
        command("docker", "stop", container, check=False)


if __name__ == "__main__":
    main()
