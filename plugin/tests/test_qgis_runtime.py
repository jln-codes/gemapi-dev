"""Offscreen tests with the installed QGIS Python, no database connection.

Run with QT_QPA_PLATFORM=offscreen and the QGIS launcher/environment.
PostgreSQL writes are covered separately by test_postgres_integration.py.
"""

import gc
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from tempfile import TemporaryDirectory

from qgis.PyQt import uic
from qgis.PyQt.QtCore import QDate, QPoint, Qt, QTimer
from qgis.PyQt.QtGui import QIcon, QKeyEvent, QImage, QColor
from qgis.PyQt.QtWidgets import QDockWidget, QHBoxLayout, QMainWindow, QMessageBox
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry, QgsPointXY
from qgis.gui import QgsMapCanvas, QgsMapToolPan

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gemapi_app import classFactory
from gemapi_app.digitize import (append_geometry_part, geometry_from_vertices,
                                     geometry_part_count, remove_last_geometry_part)
from gemapi_app.report_pdf import _columns


class Interface:
    def __init__(self):
        self.window = QMainWindow()
        self.canvas = QgsMapCanvas()
        self.window.setCentralWidget(self.canvas)
        self.window.show()
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        self.previous_tool = QgsMapToolPan(self.canvas)
        self.canvas.setMapTool(self.previous_tool)

    def mainWindow(self):
        return self.window

    def mapCanvas(self):
        return self.canvas

    def addPluginToMenu(self, *_):
        pass

    def addToolBarIcon(self, action):
        self.toolbar_action = action

    def removePluginMenu(self, *_):
        pass

    def removeToolBarIcon(self, *_):
        pass


def click(canvas, button, x=2.3, y=48.8):
    canvas.mapTool().canvasReleaseEvent(SimpleNamespace(
        button=lambda: button, mapPoint=lambda: QgsPointXY(x, y)))


def assert_capture(dialog, iface, button, vertices, expected_kind):
    button.click()
    assert dialog._capture is not None and not dialog.isVisible()
    assert iface.canvas.mapTool() is dialog._capture.tool
    for x, y in vertices:
        click(iface.canvas, Qt.LeftButton, x, y)
    if expected_kind != "point":
        click(iface.canvas, Qt.RightButton)
    assert dialog._capture is None and dialog.isVisible()
    assert iface.canvas.mapTool() is iface.previous_tool
    assert dialog.geometry_kind == expected_kind
    assert dialog.geometry is not None and not dialog.geometry.isEmpty()


def expect_invalid(kind, vertices, crs):
    try:
        geometry_from_vertices(kind, vertices, crs)
    except ValueError:
        pass
    else:
        raise AssertionError(f"Géométrie invalide acceptée : {kind}")


def main():
    app = QgsApplication([], True)
    app.initQgis()
    diagnostics = []
    app.messageLog().messageReceived.connect(
        lambda message, tag, level: diagnostics.append(f"{tag}: {message}"))
    crs = QgsCoordinateReferenceSystem("EPSG:4326")
    expect_invalid("point", [], crs)
    expect_invalid("line", [QgsPointXY(0, 0), QgsPointXY(0, 0)], crs)
    expect_invalid("polygon", [QgsPointXY(0, 0), QgsPointXY(1, 1),
                               QgsPointXY(0, 1), QgsPointXY(1, 0)], crs)
    expect_invalid("point", [QgsPointXY(0, 0)], QgsCoordinateReferenceSystem())
    transformed = geometry_from_vertices("point", [QgsPointXY(2.3, 48.8)], crs)
    assert transformed.asPoint().x() > 1000000
    for kind, vertices in (
        ("line", [QgsPointXY(2.3, 48.8), QgsPointXY(2.31, 48.81)]),
        ("polygon", [QgsPointXY(2.3, 48.8), QgsPointXY(2.31, 48.8),
                     QgsPointXY(2.31, 48.81)]),
    ):
        assert not geometry_from_vertices(kind, vertices, crs).isEmpty()

    plugin_dir = Path(__file__).resolve().parents[1] / "gemapi_app"
    for form in ("home.ui", "create.ui", "form.ui", "report.ui"):
        widget = uic.loadUi(str(plugin_dir / form))
        widget.close()

    iface = Interface()
    plugin = classFactory(iface)
    plugin.initGui()
    plugin.session.close()
    class StandInConnection:
        def executeSql(self, query):
            if query.startswith("SELECT has_table_privilege"):
                return [[True, True, True, True]]
            return []

    class StandInSession:
        scope_generation = 0
        prestations_generation = 0
        def __init__(self):
            self.conn = StandInConnection()
        def connection(self):
            return self.conn
        def get(self, key, loader, avoided_queries=1):
            return loader()
        def invalidate_prestations(self):
            self.prestations_generation += 1
        def invalidate_family(self, table):
            pass
        def clear(self, reason="manual"):
            self.scope_generation += 1
        def close(self):
            pass

    plugin.session = StandInSession()  # Stand-in rows, no PostgreSQL layer.
    assert plugin.action.text() == "" and not plugin.action.icon().isNull()
    plugin.show_dialog()
    assert plugin.home.isVisible()
    assert plugin.home.windowTitle() == "GEMAPI-App"
    assert plugin.menu_action.text() == "GEMAPI-App"
    assert plugin.action.toolTip() == "GEMAPI-App"
    buttons = (
        (plugin.home.createButton, "Créer une prestation"),
        (plugin.home.manageButton, "Gérer les prestations"),
        (plugin.home.reportButton, "Éditer un rapport"),
        (plugin.home.assistantButton, "Assistant IA"),
        (plugin.home.helpButton, "Aide"),
    )
    assert [plugin.home.actionsLayout.itemAtPosition(row, 0).widget().text()
            for row in range(5)] == [text for _, text in buttons]
    assert len(plugin.home.findChildren(type(plugin.home.manageButton))) == 5
    assert plugin.home.width() <= max(button.sizeHint().width() for button, _ in buttons) + 24
    assert plugin.home.height() <= sum(button.height() for button, _ in buttons) + 32
    assert all(button.minimumHeight() == 31 for button, _ in buttons)
    font_scales = (1.0,)
    for scale in font_scales:
        font = plugin.home.font()
        font.setPointSizeF(10 * scale)
        plugin.home.setFont(font)
        app.processEvents()
        widths = {button.width() for button, _ in buttons}
        heights = {button.height() for button, _ in buttons}
        assert max(widths) - min(widths) <= 1
        assert max(heights) - min(heights) <= 1
        rectangles = []
        for button, label in buttons:
            rect = button.rect().translated(button.mapTo(plugin.home, QPoint(0, 0)))
            assert plugin.home.rect().contains(rect)
            assert button.width() > 0 and label
            assert all(not rect.intersects(previous) for previous in rectangles)
            rectangles.append(rect)
    plugin.home.setFont(app.font())
    app.processEvents()
    plugin.home.helpButton.click()
    dock = plugin.help_dock
    assert isinstance(dock, QDockWidget) and not dock.isHidden()
    assert dock.windowTitle() == "Aide — GEMAPI-App"
    assert iface.window.dockWidgetArea(dock) == Qt.RightDockWidgetArea
    assert dock.widget().toPlainText().startswith("Assistant IA")
    assert iface.canvas.isEnabled() and iface.canvas.mapTool() is iface.previous_tool
    dock.close()
    assert dock.isHidden()
    plugin.show_help()
    assert plugin.help_dock is dock and not dock.isHidden()
    assert len(iface.window.findChildren(QDockWidget, "gemapi_app_help")) == 1
    plugin.home.reportButton.click()
    assert plugin.report.isVisible() and not plugin.report.exportButton.isEnabled()
    assert plugin.report.windowTitle() == "GEMAPI-App — Créer un rapport"
    assert plugin.report.exportButton.text() == "Générer le PDF"
    assert not hasattr(plugin.report, "previewButton")
    report_opening_size = plugin.report.size()
    assert plugin.report.reportKindCombo.currentText() == "Fiches de prestations"
    assert not hasattr(plugin.report, "mapsCheck")
    assert all(not combo.isEnabled() and combo.currentData() is None
               for combo in plugin.report.group_combos)
    assert plugin.report.filters()["groups"] == ()
    selectors = (plugin.report.reportKindCombo, *plugin.report.group_combos)
    assert len({combo.mapTo(plugin.report, QPoint(0, 0)).y() for combo in selectors}) == 1
    plugin.report.reportKindCombo.setCurrentIndex(1)
    assert plugin.report.reportKindCombo.currentText() == "Rapport synthétique"
    assert plugin.report.group1Combo.isEnabled()
    assert not plugin.report.group2Combo.isEnabled()
    assert not plugin.report.group3Combo.isEnabled()
    for combo, criterion in zip(plugin.report.group_combos, ("year", "type", "status")):
        combo.setCurrentIndex(combo.findData(criterion))
    assert plugin.report.filters()["groups"] == ("year", "type", "status")
    assert all(combo.isEnabled() for combo in plugin.report.group_combos)
    plugin.report.reportKindCombo.setCurrentIndex(0)
    assert plugin.report.filters()["groups"] == ()
    assert all(not combo.isEnabled() and combo.currentData() is None
               for combo in plugin.report.group_combos)
    plugin.report.reportKindCombo.setCurrentIndex(1)
    assert plugin.report.filters()["groups"] == ("year", "type", "status")
    plugin.report.group1Combo.setCurrentIndex(0)
    assert plugin.report.filters()["groups"] == ()
    assert not plugin.report.group2Combo.isEnabled()
    assert not plugin.report.group3Combo.isEnabled()
    plugin.report.reportKindCombo.setCurrentIndex(0)
    assert len(_columns(())) == 8
    assert "Type" not in [column[0] for column in _columns(("type",))]
    assert "Technicien" not in [column[0] for column in _columns(("technician",))]
    assert "Prestataire" not in [column[0] for column in _columns(("provider",))]
    assert "Statut" not in [column[0] for column in _columns(("status",))]
    assert "Communes" in [column[0] for column in _columns(("commune",))]
    class IncompleteDatabase:
        def executeSql(self, query):
            if "information_schema.columns" in query:
                return [[1]]
            if "FROM gemapi.prestations WHERE statut_id IS NULL" in query:
                return [[12]]
            raise AssertionError(query)

    plugin.report._connection = lambda: IncompleteDatabase()
    plugin.report._load_options()
    assert plugin.report.connection is None
    assert "12 prestation(s) incomplète(s)" in plugin.report.statusLabel.text()
    assert not plugin.report.exportButton.isEnabled()
    assert plugin.report.mapBackgroundCombo.isEnabled()
    assert plugin.report.mapBackgroundCombo.count() == 2
    assert plugin.report.mapBackgroundCombo.currentData() == "epure"
    plugin.report.backButton.click()
    assert plugin.report is None and plugin.home.isVisible()
    plugin.home.createButton.click()
    dialog = plugin.creation
    assert dialog.isVisible() and not plugin.home.isVisible()
    assert dialog.windowTitle() == "Créer une prestation — GEMAPI-App"
    assert plugin.help_dock is dock and dock.isHidden()
    assert dialog.formLayout.count() == 3
    assert [dialog.formLayout.itemAt(index).widget() for index in range(3)] == [
        dialog.locationGroup, dialog.detailsGroup, dialog.photosGroup]
    location_columns = dialog.locationGroup.layout().itemAt(0).layout()
    assert isinstance(location_columns, QHBoxLayout)
    assert location_columns.count() == 2
    assert [location_columns.itemAt(index).widget() for index in range(2)] == [
        dialog.geometryGroup, dialog.associationGroup]
    assert dialog.locationGroup.isAncestorOf(dialog.selectedList)
    assert dialog.locationGroup.isAncestorOf(dialog.removeButton)
    assert not dialog.saveButton.isEnabled()
    initial_creation_size = dialog.size()
    app.processEvents()
    assert initial_creation_size.width() <= iface.window.screen().availableGeometry().width()
    app.processEvents()
    assert dialog.backButton.isVisible()
    assert dialog.formScroll.widgetResizable()
    assert not hasattr(dialog, "statutEdit")
    assert [dialog.geometryActions.itemAt(index).widget() for index in range(4)] == [
        dialog.pointButton, dialog.lineButton, dialog.polygonButton, dialog.removeGeometryButton]
    assert [dialog.multipartActions.itemAt(index).widget() for index in range(2)] == [
        dialog.addPartButton, dialog.removePartButton]
    for column, (label, field) in enumerate((
            (dialog.typeLabel, dialog.typeCombo),
            (dialog.prestataireLabel, dialog.prestataireCombo),
            (dialog.statutLabel, dialog.statutCombo),
            (dialog.technicienLabel, dialog.technicienCombo))):
        assert dialog.detailsLayout.itemAtPosition(1, column).widget() is label
        assert dialog.detailsLayout.itemAtPosition(2, column).widget() is field
    for column, field in enumerate((dialog.debutDate, dialog.finDate,
                                    dialog.positionCombo, dialog.riveCombo)):
        assert dialog.detailsLayout.itemAtPosition(4, column).widget() is field
    assert dialog.detailsLayout.itemAtPosition(6, 0).layout() is dialog.amountLayout
    assert [dialog.amountLayout.stretch(i) for i in range(3)] == [2, 1, 1]
    search_row = dialog.associationLayout.itemAt(1).layout()
    assert search_row.count() == 2
    assert [search_row.itemAt(index).widget() for index in range(2)] == [
        dialog.searchEdit, dialog.mapSelectButton]
    assert dialog.associationControls.count() == 1
    assert dialog.associationControls.itemAt(0).layout() is dialog.distanceLayout
    assert dialog.mapSelectButton.isCheckable() and dialog.mapSelectButton.isVisible()
    assert dialog.nearbyOnly.parent() is dialog.associationGroup
    assert dialog.distanceSpin.parent() is dialog.associationGroup
    assert dialog.distanceUnitCombo.parent() is dialog.associationGroup
    assert dialog.formScroll.verticalScrollBar().maximum() > 0
    dialog.resize(1000, 800)
    app.processEvents()
    dialog.resize(780, 650)
    app.processEvents()
    assert dialog.width() == 780 and dialog.backButton.isVisible()
    assert abs(dialog.nearbyOnly.geometry().center().y() - dialog.distanceSpin.geometry().center().y()) <= 6
    assert abs(dialog.distanceSpin.geometry().center().y() - dialog.distanceUnitCombo.geometry().center().y()) <= 6
    assert dialog.photo1Button.geometry().x() < dialog.photo2Button.geometry().x()

    patrimony_ids = (str(uuid4()), str(uuid4()))

    class FakeFeature:
        def __init__(self, fid, name, x):
            self._fid = fid
            self._name = name
            self._geometry = QgsGeometry.fromWkt(f"POINT({x} 0)")

        def id(self):
            return self._fid

        def __getitem__(self, key):
            return self._name if key == "nom" else patrimony_ids[self._fid]

        def fields(self):
            return SimpleNamespace(names=lambda: ["id", "nom"])

        def hasGeometry(self):
            return True

        def geometry(self):
            return self._geometry

    class FakeLayer:
        features = (FakeFeature(0, "ZEC proche", 10), FakeFeature(1, "ZEC distante", 5000))

        def getFeatures(self):
            return iter(self.features)

        def getFeature(self, fid):
            return self.features[fid]

        def crs(self):
            return QgsCoordinateReferenceSystem("EPSG:3950")

    class SpatialReadConnection:
        def executeSql(self, query):
            if query.startswith("SELECT id::text,nom FROM gemapi."):
                if "gemapi.ah" in query:
                    return [(patrimony_ids[0], "ZEC proche"),
                            (patrimony_ids[1], "ZEC distante")]
                return []
            assert query.count("ST_DWithin(") == 9, query
            reference = dialog._reference_geometry()
            radius = dialog.distanceSpin.value() * dialog.distanceUnitCombo.currentData()
            return [("ah_stockage", patrimony_ids[f.id()], None, None, None, None, None)
                    for f in FakeLayer.features
                    if reference.distance(f.geometry()) <= radius]

    spatial_connection = SpatialReadConnection()
    dialog._connection = lambda: spatial_connection
    dialog._object_layer = lambda table: FakeLayer() if table == "ah_stockage" else None
    dialog._load_objects()
    assert set(dialog._object_load_failures) == {
        "cours_eau", "digues", "ah_sections", "ah_ouvrage", "sites_terrestres", "vegetation", "mobilier", "cheminements"}
    # The stand-in deliberately provides one family. Test filtering separately
    # from the incomplete-loading warning asserted above.
    dialog._object_load_failures = ()
    assert dialog.objectList.count() == 2
    dialog.searchEdit.setText("proche")
    assert dialog.objectList.count() == 1
    dialog.searchEdit.clear()
    dialog.geometry = QgsGeometry.fromWkt("POINT(0 0)")
    dialog.geometry_kind = "point"
    dialog.nearbyOnly.setChecked(True)
    assert dialog.objectList.count() == 1
    dialog.distanceSpin.setValue(10)
    dialog.distanceUnitCombo.setCurrentIndex(1)
    assert dialog.objectList.count() == 2
    dialog.objectList.selectAll()
    assert not dialog.addButton.isEnabled()
    dialog._add_objects()
    assert not dialog._associations() and "un seul" in dialog.statusLabel.text()
    dialog.objectList.clearSelection()
    dialog.objectList.item(0).setSelected(True)
    dialog.addButton.click()
    assert dialog._associations() == [("ah_stockage", patrimony_ids[0])]
    assert all(uid not in dialog.selectedList.item(i).text() for i in range(1)
               for uid in patrimony_ids)
    dialog.selectedList.setCurrentRow(0)
    dialog.removeButton.click()
    assert not dialog._associations()
    dialog._reset()
    assert dialog._associations() == [] and not dialog.nearbyOnly.isChecked()
    dialog.nearbyOnly.setChecked(True)
    assert dialog.statusLabel.text() == (
        "Aucune référence spatiale valide : le filtre de distance ne peut pas être appliqué")
    dialog.nearbyOnly.setChecked(False)
    with TemporaryDirectory() as photo_directory:
        photo = Path(photo_directory) / "essai.png"
        image = QImage(640, 320, QImage.Format_RGB32)
        image.fill(QColor("#416f91"))
        assert image.save(str(photo))
        dialog.photo_references[0] = str(photo)
        dialog._update_photo(0)
        dialog.photo1Comment.setText("Vue avant travaux")
        assert not dialog.photo1Preview.pixmap().isNull()
        assert dialog.photo1Button.text() == "Remplacer photo"
        assert dialog._values()["photo_1_commentaire"] == "Vue avant travaux"
        dialog._remove_photo(0)
        assert dialog._values()["photo_1"] == ""
        assert dialog.photo1Preview.text() == "Aucune photographie"
        assert dialog.photo1Button.text() == "Choisir photo"
    dialog.intituleEdit.setText("Essai de capture")
    dialog.descriptionEdit.setPlainText("Texte conservé")
    assert_capture(dialog, iface, dialog.pointButton, [(2.3, 48.8)], "point")
    assert dialog.intituleEdit.text() == "Essai de capture"
    assert dialog.descriptionEdit.toPlainText() == "Texte conservé"
    assert dialog.nearbyOnly.isChecked() and dialog.distanceSpin.value() == 500
    assert not dialog.saveButton.isEnabled()  # A geometry cannot replace the required link.
    dialog._add_selection("ah_stockage", (patrimony_ids[0],), "ZEC proche")
    dialog._update_geometry_status()
    assert dialog.saveButton.isEnabled()
    original = dialog.geometry.asWkt()
    dialog.addPartButton.click()
    assert dialog._capture is not None and not dialog.saveButton.isEnabled()
    click(iface.canvas, Qt.LeftButton, 2.32, 48.82)
    assert dialog.geometry.wkbType() == QgsGeometry.fromWkt("MULTIPOINT((0 0),(1 1))").wkbType()
    assert geometry_part_count(dialog.geometry) == 2
    assert not dialog.saveButton.isEnabled() and dialog.finishGeometryButton.isEnabled()
    dialog.finishGeometryButton.click()
    assert dialog.saveButton.isEnabled()
    dialog.removePartButton.click()
    assert geometry_part_count(dialog.geometry) == 1 and not dialog.geometry.isMultipart()
    assert dialog.geometry.asWkt() == original
    dialog.addPartButton.click()
    iface.canvas.mapTool().keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert dialog.geometry.asWkt() == original and dialog.saveButton.isEnabled()
    dialog.lineButton.click()
    click(iface.canvas, Qt.LeftButton)
    iface.canvas.mapTool().keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert dialog.isVisible() and dialog.geometry.asWkt() == original
    assert iface.canvas.mapTool() is iface.previous_tool
    assert_capture(dialog, iface, dialog.lineButton, [(2.3, 48.8), (2.31, 48.81)], "line")
    dialog.addPartButton.click()
    click(iface.canvas, Qt.LeftButton, 2.33, 48.83)
    click(iface.canvas, Qt.LeftButton, 2.34, 48.84)
    click(iface.canvas, Qt.RightButton)
    assert dialog.geometry.isMultipart() and geometry_part_count(dialog.geometry) == 2
    assert dialog.geometry.length() > 0 and not dialog.saveButton.isEnabled()
    dialog.finishGeometryButton.click()
    assert dialog.saveButton.isEnabled()
    assert_capture(dialog, iface, dialog.polygonButton,
                   [(2.3, 48.8), (2.31, 48.8), (2.31, 48.81)], "polygon")
    dialog.addPartButton.click()
    for x, y in ((2.33, 48.83), (2.34, 48.83), (2.34, 48.84)):
        click(iface.canvas, Qt.LeftButton, x, y)
    click(iface.canvas, Qt.RightButton)
    assert dialog.geometry.isMultipart() and geometry_part_count(dialog.geometry) == 2
    assert dialog.geometry.area() > 0 and not dialog.saveButton.isEnabled()
    dialog.finishGeometryButton.click()
    assert dialog.saveButton.isEnabled()
    dialog.removePartButton.click()
    assert not dialog.geometry.isMultipart()
    polygon = dialog.geometry.asWkt()
    dialog.lineButton.click()
    click(iface.canvas, Qt.LeftButton)
    click(iface.canvas, Qt.RightButton)  # Invalid redraw must preserve polygon.
    assert dialog.geometry.asWkt() == polygon and dialog.geometry_kind == "polygon"
    dialog.pointButton.click()
    iface.canvas.setMapTool(iface.previous_tool)  # Another QGIS tool interrupted capture.
    assert dialog.isVisible() and dialog.geometry.asWkt() == polygon
    dialog.lineButton.click()
    iface.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3950"))
    assert dialog.isVisible() and dialog.geometry.asWkt() == polygon
    assert iface.canvas.mapTool() is iface.previous_tool
    iface.canvas.setDestinationCrs(crs)
    assert_capture(dialog, iface, dialog.pointButton, [(2.32, 48.82)], "point")
    dialog.removeGeometryButton.click()
    assert dialog.geometry is None and dialog.geometryStatusLabel.text() == (
        "Aucune géométrie dessinée — facultative selon le patrimoine.")
    assert dialog.saveButton.isEnabled()  # AH may use only its patrimonial reference.
    dialog.geometry = QgsGeometry.fromWkt("POINT Z(1 2 3)")
    dialog.geometry_kind = "point"
    dialog._update_geometry_status()
    assert "invalide" in dialog.geometryStatusLabel.text() and not dialog.saveButton.isEnabled()
    dialog._remove_geometry()

    for first, second, expected in (
        ("POINT(0 0)", "POINT(10 10)", "MultiPoint"),
        ("LINESTRING(0 0,1 1)", "LINESTRING(10 10,11 11)", "MultiLineString"),
        ("POLYGON((0 0,1 0,1 1,0 1,0 0))",
         "POLYGON((10 10,11 10,11 11,10 11,10 10))", "MultiPolygon"),
    ):
        combined = append_geometry_part(QgsGeometry.fromWkt(first), QgsGeometry.fromWkt(second))
        assert combined.isGeosValid() and combined.isMultipart() and expected in combined.asWkt()
        assert geometry_part_count(combined) == 2
        assert remove_last_geometry_part(combined).asWkt() == QgsGeometry.fromWkt(first).asWkt()

    class FakeConnection(SpatialReadConnection):
        calls = 0

        def executeSql(self, query):
            if "INSERT INTO gemapi.prestations" not in query:
                return super().executeSql(query)
            self.calls += 1
            assert "SRID=3950;" in query
            return [[str(uuid4()), 0]]

    fake_connection = FakeConnection()
    dialog._connection = lambda: fake_connection
    dialog.prestataireCombo.addItem("Prestataire de test", str(uuid4()))
    dialog.typeCombo.addItem("Type de test", str(uuid4()))
    dialog.statutCombo.addItem("Planifié", str(uuid4()))
    dialog.technicienCombo.addItem("Responsable de test", str(uuid4()))
    for number in (1, 2):
        dialog.prestataireCombo.setCurrentIndex(1)
        dialog.typeCombo.setCurrentIndex(1)
        dialog.statutCombo.setCurrentIndex(1)
        dialog.technicienCombo.setCurrentIndex(1)
        dialog.debutDate.setDate(QDate(2026, 11, 1))
        dialog.finDate.setDate(QDate(2027, 3, 1))  # Prévisionnelle, pas achevée.
        dialog.intituleEdit.setText(f"Essai {number}")
        if not dialog._associations():
            dialog._add_selection("ah_stockage", (patrimony_ids[0],), "ZEC proche")
        assert_capture(dialog, iface, dialog.pointButton, [(2.3, 48.8)], "point")
        dialog.saveButton.click()
        app.processEvents()
        assert not dialog.isVisible() and dialog._save_completed, dialog.statusLabel.text()
        assert plugin.home.isVisible()
        plugin.home.createButton.click()
        assert plugin.creation is dialog and dialog.isVisible() and dialog.geometry is None
    assert fake_connection.calls == 2
    dialog.intituleEdit.setText("Saisie non enregistrée")
    QTimer.singleShot(0, lambda: app.activeModalWidget().button(QMessageBox.No).click())
    dialog.close()
    assert dialog.isVisible()  # Refusing the warning preserves the form.
    QTimer.singleShot(0, lambda: app.activeModalWidget().button(QMessageBox.Yes).click())
    dialog.backButton.click()
    assert plugin.creation is None and plugin.home.isVisible()
    plugin.home.manageButton.click()
    assert plugin.manage is not None and plugin.manage.isVisible()
    assert plugin.manage.windowTitle() == "Gérer les prestations — GEMAPI-App"

    first_id, second_id = str(uuid4()), str(uuid4())
    first_object, second_object = str(uuid4()), str(uuid4())
    status_id = str(uuid4())
    provider_id = str(uuid4())

    class FakeReadConnection:
        def executeSql(self, query):
            if query.startswith("SELECT has_table_privilege"):
                return [[True, True, True, True]]
            if query.startswith("SELECT DISTINCT annee"):
                return [[2026], [2027]]
            if query.startswith("SELECT id::text,libelle FROM gemapi.statuts_prestations"):
                return [[status_id, "Planifié"]]
            if query.startswith("SELECT id::text,nom FROM gemapi.prestataires"):
                return [[provider_id, "Prestataire de test"]]
            if query.startswith("SELECT id::text,nom,actif FROM gemapi.prestataires"):
                return [[provider_id, "Prestataire de test", True]]
            if query.startswith("SELECT o.id::text"):
                if "FROM gemapi.ah_stockage" in query:
                    return [[first_object, "AH — ZEC Alpha"]]
                if "FROM gemapi.vegetation" in query:
                    return [[first_object, "Site — Zone Alpha"]]
                return []
            uid = first_id if first_id in query else second_id
            if query.startswith("SELECT id::text, intitule, type_prestation_id"):
                title = "Première prestation" if uid == first_id else "Seconde prestation"
                return [[uid, title, str(uuid4()), str(uuid4()), str(uuid4()),
                         "2026-11-01", "2027-03-01", None, None, None, None,
                         None, None, None, None if uid == first_id else "POINT(20 20)", "Type de test",
                         "Prestataire de test", "Planifié", "123",
                         dialog.technicienCombo.itemData(1), "Responsable de test",
                         "Indéfini", "Indéfinie"]]
            if query.startswith("SELECT id::text"):
                if "FROM gemapi.ah_stockage" in query:
                    return [[first_object, "ZEC Alpha"]]
                if "FROM gemapi.cours_eau" in query:
                    return [[second_object, "Fossé Bêta"]]
                return []
            if query.startswith("SELECT id,titre,version FROM"):
                if "E'absent'" in query:
                    return []
                return [[first_id, "Première prestation", "123"],
                        [second_id, "Seconde prestation", "456"]]
            if query.startswith("SELECT 'ah_sections'"):
                if uid == first_id:
                    return [["ah_stockage", first_object, "ZEC Alpha"]]
                return [["cours_eau", second_object, "Fossé Bêta"]]
            raise AssertionError(query)

    plugin.session.conn = FakeReadConnection()
    plugin.session.scope_generation += 1
    plugin.manage.connection = plugin.session.conn
    plugin.manage.refresh(force=True)
    assert plugin.manage.prestationList.count() == 2
    assert first_id not in plugin.manage.prestationList.item(0).text()
    assert plugin.manage.filterScroll.isVisible()
    assert len(plugin.manage.pickers) == 5
    assert plugin.manage.pickers["providers"].list.item(0).text() == "Prestataire de test"
    plugin.manage.familyCombo.setCurrentIndex(
        plugin.manage.familyCombo.findData("ah"))
    assert plugin.manage.objectPicker.list.count() == 1
    plugin.manage.objectPicker.list.item(0).setCheckState(Qt.Checked)
    assert plugin.manage.filters()["ah_stockage"] == (first_object,)
    plugin.manage.familyCombo.setCurrentIndex(
        plugin.manage.familyCombo.findData("site_terrestre"))
    assert "ah_stockage" not in plugin.manage.filters()
    assert not plugin.manage.objectPicker.values()
    plugin.manage.familyCombo.setCurrentIndex(0)
    assert not hasattr(plugin.manage, "filtersToggle")
    assert not hasattr(plugin.manage, "clearButton")
    assert plugin.manage.resetButton.text() == "Réinitialiser"
    plugin.manage.resize(1000, 700)
    app.processEvents()
    assert plugin.manage.pickers["years"].geometry().x() < plugin.manage.pickers["statuses"].geometry().x()
    assert plugin.manage.pickers["statuses"].geometry().x() < plugin.manage.pickers["technicians"].geometry().x()
    assert plugin.manage.pickers["technicians"].geometry().x() < plugin.manage.pickers["providers"].geometry().x()
    assert max(button.width() for button in (plugin.manage.newButton,
            plugin.manage.openButton, plugin.manage.deleteButton, plugin.manage.backButton)) - min(
            button.width() for button in (plugin.manage.newButton, plugin.manage.openButton,
                                           plugin.manage.deleteButton, plugin.manage.backButton)) <= 1
    assert len({button.geometry().y() for button in (plugin.manage.newButton,
                                                       plugin.manage.openButton,
                                                       plugin.manage.backButton)}) == 1
    plugin.manage.resize(780, 700)
    app.processEvents()
    assert plugin.manage.pickers["years"].geometry().x() < plugin.manage.pickers["statuses"].geometry().x()
    assert plugin.manage.pickers["years"].geometry().x() < plugin.manage.pickers["technicians"].geometry().x()
    assert plugin.manage.pickers["years"].geometry().x() == plugin.manage.pickers["providers"].geometry().x()
    plugin.manage.resize(560, 700)
    app.processEvents()
    assert plugin.manage.pickers["years"].geometry().x() < plugin.manage.pickers["statuses"].geometry().x()
    assert plugin.manage.pickers["years"].geometry().x() == plugin.manage.pickers["technicians"].geometry().x()
    plugin.manage.pickers["years"].list.item(0).setCheckState(Qt.Checked)
    assert plugin.manage.pickers["years"].values() == (2026,)
    plugin.manage.pickers["years"].list.item(1).setCheckState(Qt.Checked)
    assert plugin.manage.pickers["years"].values() == (2026, 2027)
    plugin.manage.pickers["providers"].list.item(0).setCheckState(Qt.Checked)
    assert plugin.manage.pickers["providers"].values() == (provider_id,)
    plugin.manage.searchEdit.setText("absent")
    plugin.manage._fetch_results()
    assert plugin.manage.prestationList.count() == 0
    assert "Aucune prestation" in plugin.manage.statusLabel.text()
    plugin.manage.resetButton.click()
    assert plugin.manage.prestationList.count() == 2
    assert not any(picker.values() for picker in plugin.manage.pickers.values())
    assert not plugin.manage.searchEdit.text()
    plugin.manage.pickers["years"].list.item(0).setCheckState(Qt.Checked)
    plugin.manage.pickers["providers"].list.item(0).setCheckState(Qt.Checked)
    plugin.manage.prestationList.setCurrentRow(0)
    plugin.show_existing()
    viewed = plugin.creation
    assert viewed is not None, (plugin.manage.statusLabel.text(), diagnostics[-3:])
    assert viewed.current_prestation_id == first_id
    assert viewed.intituleEdit.text() == "Première prestation"
    assert viewed._associations() == [("ah_stockage", first_object)]
    assert viewed.mode == "edit" and viewed.saveButton.isVisible()
    assert viewed.size() == initial_creation_size
    app.processEvents()
    assert viewed.associationGroup.isEnabled()
    assert viewed.reloadButton.isVisible()
    buttons = (viewed.saveButton, viewed.reloadButton, viewed.backButton)
    assert [viewed.bottomActions.itemAt(index).widget() for index in range(3)] == list(buttons)
    assert len({button.geometry().center().y() for button in buttons}) == 1
    assert buttons[0].x() < buttons[1].x() < buttons[2].x()
    viewed.resize(780, 650)
    app.processEvents()
    assert len({button.geometry().center().y() for button in buttons}) == 1
    assert all(button.isVisible() and button.width() > 0 for button in buttons)
    assert all(button.width() > 0 for button in buttons)
    viewed.backButton.click()
    assert plugin.manage.isVisible()
    assert plugin.manage.pickers["years"].values() == (2026,)
    assert plugin.manage.pickers["providers"].values() == (provider_id,)
    plugin.manage.clear_filters()
    assert plugin.manage.prestationList.count() == 2
    plugin.manage.prestationList.setCurrentRow(1)
    assert plugin.manage.selected_id() == second_id
    plugin.show_existing()
    viewed = plugin.creation
    assert viewed is not None, plugin.manage.statusLabel.text()
    assert viewed.current_prestation_id == second_id
    assert viewed.intituleEdit.text() == "Seconde prestation"
    assert viewed._associations() == [("cours_eau", second_object)]
    viewed.backButton.click()
    plugin.manage.backButton.click()
    assert plugin.manage is None and plugin.home.isVisible()
    plugin.show_creation()
    dialog = plugin.creation
    dialog.lineButton.click()
    assert not dialog.isVisible() and iface.canvas.mapTool() is dialog._capture.tool
    plugin.unload()
    assert plugin.help_dock is None and dock.isHidden()
    assert iface.canvas.mapTool() is iface.previous_tool
    assert plugin.action is None
    plugin.menu_action.deleteLater()
    plugin.report_action.deleteLater()
    iface.toolbar_action = None
    iface.canvas.close()
    iface.window.close()
    iface.canvas.deleteLater()
    iface.window.deleteLater()
    del dialog, plugin, iface, widget
    app.processEvents()
    gc.collect()
    app.exitQgis()
    print("PyQGIS 3.44 : géométries, capture, annulation, redessin, UI et chargement OK")


if __name__ == "__main__":
    main()
