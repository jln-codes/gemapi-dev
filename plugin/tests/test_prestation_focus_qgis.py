"""Native Qt/PyQGIS save lifecycle and geometry focus, without any database writes."""
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import Mock, patch

from qgis.PyQt.QtCore import QDate, QEvent
from qgis.PyQt.QtWidgets import QDialog, QMainWindow, QMessageBox
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsDataSourceUri,
                       QgsGeometry, QgsRectangle)
from qgis.gui import QgsMapCanvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gemapi_app import classFactory
from gemapi_app.creation import CreationDialog
from gemapi_app.management import ManageDialog
from gemapi_app.prestation_focus import focus_prestation
REAL_REFRESH = CreationDialog._refresh_layers


def uid(i): return f'00000000-0000-0000-0000-{i:012d}'
TYPE, PROVIDER, STATUS, TECH, OBJECT, SAVED = (uid(i) for i in range(1, 7))
LINE = 'LINESTRING(1700000 9200000,1700100 9200100)'


class Connection:
    def __init__(self):
        self.queries = []
        self.writes = []  # Simulated transactions in Python memory only.
        self.error = None
        self.version = 1
        self.title = 'Prestation test'
        self.form = None
        self.wkt = LINE
        self.srid = 3950
    def executeSql(self, query):
        self.queries.append(query)
        if query.startswith('SELECT has_table_privilege'):
            return [(True, True, True, True)]
        if query.startswith('SELECT ST_AsText(geom),ST_SRID(geom)'):
            return [] if self.wkt is None else [(self.wkt, self.srid)]
        if query.startswith('WITH new_prestation') or query.startswith('DO $sig_prestations$'):
            if self.error:
                raise RuntimeError(self.error)
            self.writes.append(query)
            self.version += 1
            if self.form is not None:
                self.title = self.form.intituleEdit.text()
            return [(SAVED, '0')] if query.startswith('WITH new_prestation') else []
        return []


class Interface:
    def __init__(self):
        self.window = QMainWindow()
        self.canvas = QgsMapCanvas(self.window)
        self.window.setCentralWidget(self.canvas)
        self.window.resize(650, 450)
        self.window.show()
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:3950'))
        self.canvas.setExtent(QgsRectangle(0, 0, 100, 100))
        self.bar = SimpleNamespace(pushMessage=Mock())
    def mainWindow(self): return self.window
    def mapCanvas(self): return self.canvas
    def messageBar(self): return self.bar
    def addPluginToMenu(self, *_): pass
    def removePluginMenu(self, *_): pass


class Manager(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.refresh = Mock()
        self.statusLabel = Mock()


class FocusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication([], True)
        cls.app.initQgis()
    @classmethod
    def tearDownClass(cls): cls.app.exitQgis()

    def setUp(self):
        self.stack = ExitStack()
        self.iface = Interface()
        self.db = Connection()
        self.plugin = classFactory(self.iface)
        self.plugin.initGui()
        self.stack.enter_context(patch.object(self.plugin.session, 'connection', return_value=self.db))
        self.plugin.session._values.update({'reference.keep': object(), 'family.layer.keep': object(),
                                            'filters.years': ('2026',)})
        self.cache = dict(self.plugin.session._values)
        def types(form):
            form.typeCombo.set_reference([(uid(20), 'Catégorie')], [(TYPE, 'Entretien', uid(20), True)])
        def providers(form, *_): form.prestataireCombo.addItem('Entreprise test', PROVIDER)
        def statuses(form): form.statutCombo.addItem('Terminé', STATUS)
        def technicians(form): form.technicienCombo.addItem('Technicien test', TECH)
        def objects(form): form.objects = []
        self.loaders = []
        for name, method in (('_load_types', types), ('_load_reference', providers), ('_load_statuses', statuses),
                             ('_load_technicians', technicians), ('_load_objects', objects)):
            self.loaders.append(self.stack.enter_context(patch.object(CreationDialog, name, autospec=True, side_effect=method)))
        self.refresh = self.stack.enter_context(patch.object(CreationDialog, '_refresh_layers', return_value=True))
        def load(connection, identifier):
            self.assertIs(connection, self.db)
            return ([identifier, self.db.title, TYPE, PROVIDER, STATUS, '2026-11-11', '2026-11-11', '', '', '',
                     '', '', '', '', None, 'Entretien', 'Entreprise test', 'Terminé', str(self.db.version),
                     TECH, 'Technicien test', 'Indéfini', 'Indéfinie'], [('ah_ouvrage', OBJECT, 'Ouvrage test')])
        self.load = self.stack.enter_context(patch('gemapi_app.creation.load_prestation', side_effect=load))
        self.plugin.show_dialog()
        self.app.processEvents()

    def tearDown(self):
        self.plugin.unload()
        self.stack.close()
        self.iface.window.close()
        self.iface.window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def fill(self, form, family='ah_ouvrage'):
        self.db.form = form
        form.intituleEdit.setText('Prestation test')
        for combo, value in ((form.typeCombo, TYPE), (form.prestataireCombo, PROVIDER),
                             (form.statutCombo, STATUS), (form.technicienCombo, TECH)):
            combo.setCurrentIndex(combo.findData(value))
        form.debutDate.setDate(QDate(2026, 11, 11))
        form.finDate.setDate(QDate(2026, 11, 11))
        form._add_selection(family, (OBJECT,), 'Patrimoine test')
        if family in ('cours_eau', 'digues', 'ah_sections', 'sites_terrestres'):
            form.geometry = QgsGeometry.fromWkt(LINE)
            form.geometry_kind = "line"
        form._update_position_state()
        form._update_geometry_status()

    def test_geometry_types_and_effective_view_independent_of_attachment(self):
        geometries = ('POINT(1700000 9200000)', 'MULTIPOINT((1700000 9200000),(1700020 9200030))',
                      LINE, 'MULTILINESTRING((1700000 9200000,1700100 9200100),(1700200 9200200,1700250 9200210))',
                      'POLYGON((1700000 9200000,1700100 9200000,1700100 9200100,1700000 9200000))',
                      'MULTIPOLYGON(((1700000 9200000,1700100 9200000,1700100 9200100,1700000 9200000)))')
        for geometry in geometries:
            with self.subTest(geometry=geometry.split('(')[0]):
                self.db.wkt = geometry
                self.assertTrue(focus_prestation(self.iface, self.db, SAVED))
                bounds = QgsGeometry.fromWkt(geometry).boundingBox()
                self.assertTrue(self.iface.canvas.extent().contains(bounds))
                self.assertGreater(self.iface.canvas.extent().width(), 0)
                self.assertIn('FROM gemapi.prestations_carte', self.db.queries[-1])
                self.assertIn(SAVED, self.db.queries[-1])
        self.assertFalse(self.db.writes)

    def test_transform_crs_and_preserve_extent_on_unavailable_geometry(self):
        self.db.wkt, self.db.srid = 'POINT(2 50)', 4326
        self.iface.canvas.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:3857'))
        self.assertTrue(focus_prestation(self.iface, self.db, SAVED))
        center = self.iface.canvas.extent().center()
        self.assertAlmostEqual(center.x(), 222638.98, delta=1)
        self.assertAlmostEqual(center.y(), 6446275.84, delta=1)
        for value in (None, 'POINT EMPTY', 'invalid'):
            self.db.wkt = value
            before = QgsRectangle(self.iface.canvas.extent())
            self.assertFalse(focus_prestation(self.iface, self.db, SAVED))
            self.assertEqual(self.iface.canvas.extent(), before)
        self.assertTrue(self.iface.bar.pushMessage.called)
        self.assertFalse(self.db.writes)

    def test_creation_keeps_session_cache_and_form_then_reopens_quickly(self):
        self.plugin.show_creation()
        form = self.plugin.creation
        self.fill(form)
        before_scope = self.plugin.session.scope_generation
        with patch.object(self.plugin.session, 'close') as close, patch.object(self.plugin.session, 'clear') as clear:
            form.saveButton.click()
            self.app.processEvents()
            self.assertEqual(form.saved_prestation_id, SAVED)
            self.assertIsNone(form.geometry)  # Effective map geometry came from the view instead.
            self.assertTrue(form._save_completed)
            self.assertFalse(form.isVisible())
            self.assertTrue(self.plugin.home.isVisible())
            self.assertEqual(form.debutDate.date(), QDate(2026, 11, 11))  # Not reset until a new entry is requested.
            self.assertTrue(self.iface.canvas.extent().contains(QgsGeometry.fromWkt(LINE).boundingBox()))
            self.assertIs(self.plugin.session.connection(), self.db)
            self.assertIs(self.plugin.session._values['reference.keep'], self.cache['reference.keep'])
            self.assertIs(self.plugin.session._values['family.layer.keep'], self.cache['family.layer.keep'])
            self.assertEqual(self.plugin.session.scope_generation, before_scope)
            self.assertNotIn('filters.years', self.plugin.session._values)
            close.assert_not_called()
            clear.assert_not_called()
            form._save()
            self.assertEqual(len(self.db.writes), 1)  # Cannot accidentally submit the hidden saved form twice.
            started = perf_counter()
            self.plugin.show_creation()
            elapsed = (perf_counter() - started) * 1000
            print(f'Reopening create after save: {elapsed:.2f} ms (native Qt, simulated SQL)')
            self.assertLess(elapsed, 500)
            self.assertIs(self.plugin.creation, form)
            self.assertFalse(form._save_completed)
            self.assertEqual(form.debutDate.date(), form._empty_date)
            self.assertTrue(form.isVisible())
            self.assertFalse(self.plugin.home.isVisible())
            for loader in self.loaders: self.assertEqual(loader.call_count, 1)
        form._saved_snapshot = form._snapshot()


    def test_form_widgets_are_reused_between_create_and_edit_without_stale_version(self):
        self.plugin.show_creation()
        form = self.plugin.creation
        self.fill(form)
        form.saveButton.click()
        self.app.processEvents()
        edited = self.plugin.open_prestation(SAVED, origin='map')
        self.assertIs(edited, form)
        self.assertEqual(form.mode, 'edit')
        self.assertEqual(form._loaded_version, str(self.db.version))
        form.intituleEdit.setText('Autre titre')
        form.saveButton.click()
        self.app.processEvents()
        self.plugin.show_creation()
        self.assertIs(self.plugin.creation, form)
        self.assertEqual(form.mode, 'create')
        self.assertIsNone(form._loaded_version)
        self.assertIsNone(form.current_prestation_id)
        self.assertFalse(form._edit_saved)
        for loader in self.loaders: self.assertEqual(loader.call_count, 1)
        form._saved_snapshot = form._snapshot()

    def test_edit_reload_is_targeted_and_versions_are_not_reused(self):
        form = self.plugin.open_prestation(SAVED, origin='manager')
        self.db.form = form
        old_version = form._loaded_version
        form.intituleEdit.setText('Titre corrigé')
        form.saveButton.click()
        self.app.processEvents()
        self.assertTrue(form._save_completed)
        self.assertTrue(self.plugin.home.isVisible())
        self.assertFalse(form.isVisible())
        self.assertEqual(form._loaded_version, old_version)
        self.assertEqual(len(self.db.writes), 1)
        started = perf_counter()
        reopened = self.plugin.open_prestation(SAVED, origin='manager')
        elapsed = (perf_counter() - started) * 1000
        print(f'Reopening edit after save: {elapsed:.2f} ms (native Qt, simulated SQL)')
        self.assertLess(elapsed, 500)
        self.assertIs(reopened, form)
        self.assertEqual(form._loaded_version, str(self.db.version))
        self.assertNotEqual(form._loaded_version, old_version)
        self.assertEqual(self.load.call_count, 2)
        for loader in self.loaders: self.assertEqual(loader.call_count, 1)
        self.assertTrue(form.isVisible())
        self.assertFalse(self.plugin.home.isVisible())
        self.assertIs(self.plugin.session._values['reference.keep'], self.cache['reference.keep'])


    def test_refresh_failure_after_committed_save_still_hides_and_cannot_write_twice(self):
        self.plugin.show_creation()
        form = self.plugin.creation
        self.fill(form)
        self.refresh.side_effect = RuntimeError('Refresh unavailable')
        form.saveButton.click()
        self.app.processEvents()
        self.assertTrue(form._save_completed)
        self.assertFalse(form.isVisible())
        self.assertTrue(self.plugin.home.isVisible())
        self.assertTrue(self.iface.bar.pushMessage.called)
        form._save()
        self.assertEqual(len(self.db.writes), 1)

    def test_error_conflict_no_change_and_cancel_never_focus(self):
        self.plugin.show_creation()
        form = self.plugin.creation
        self.fill(form)
        self.db.error = 'permission denied'
        with patch('gemapi_app.creation.focus_prestation') as focus:
            form.saveButton.click()
            focus.assert_not_called()
            self.assertTrue(form.isVisible())
            self.assertFalse(form._save_completed)
            self.assertEqual(self.plugin.session.prestations_generation, 0)
            with patch('gemapi_app.creation.QMessageBox.question', return_value=QMessageBox.No):
                form.reject()
            self.assertIs(self.plugin.creation, form)
            self.assertTrue(form.isVisible())
            with patch('gemapi_app.creation.QMessageBox.question', return_value=QMessageBox.Yes):
                form.reject()
            self.assertTrue(self.plugin.home.isVisible())
            focus.assert_not_called()
        edit = self.plugin.open_prestation(SAVED, origin='map')
        with patch('gemapi_app.creation.focus_prestation') as focus:
            edit._save_edit()  # Unchanged form: no transaction, no focus.
            focus.assert_not_called()
            edit.intituleEdit.setText('Conflit simulé')
            self.db.form = edit
            self.db.error = 'SIG_PRESTATIONS_CONFLICT'
            edit._save_edit()
            self.assertIn('changé', edit.statusLabel.text())
            self.assertTrue(edit.isVisible())
            self.assertFalse(edit._save_completed)
            focus.assert_not_called()
        edit._saved_snapshot = edit._snapshot()
        self.assertFalse(self.db.writes)

    def test_focus_is_shared_by_all_seven_direct_attachment_families(self):
        for family in ('ah_ouvrage', 'ah_stockage', 'mobilier', 'cheminements', 'vegetation', 'cours_eau', 'digues', 'ah_sections', 'sites_terrestres'):
            with self.subTest(family=family):
                self.plugin.show_creation()
                form = self.plugin.creation
                self.fill(form, family)
                with patch.object(form, '_check_spatial_attachment'):
                    form.saveButton.click()
                self.app.processEvents()
                self.assertTrue(form._save_completed)
                self.assertFalse(form.isVisible())
                self.assertTrue(self.plugin.home.isVisible())
                self.assertTrue(self.iface.canvas.extent().contains(QgsGeometry.fromWkt(LINE).boundingBox()))
                self.assertIn('FROM gemapi.prestations_carte', self.db.queries[-1])
        self.assertEqual(len(self.db.writes), 9)

    def test_cancel_returns_to_correct_previous_window(self):
        manager = Manager(self.iface.window)
        self.plugin.manage = manager
        manager.show()
        self.plugin.home.hide()
        self.plugin.show_creation()
        self.assertEqual(self.plugin._creation_origin, 'manager')
        self.plugin.creation.reject()  # Pristine form: no abandon dialog.
        self.assertTrue(manager.isVisible())
        self.assertFalse(self.plugin.home.isVisible())
        manager.hide()
        self.plugin._show_home()
        self.plugin.show_creation()
        self.assertEqual(self.plugin._creation_origin, 'home')
        self.plugin.creation.reject()
        self.assertTrue(self.plugin.home.isVisible())
        self.assertFalse(manager.isVisible())
        self.assertFalse(self.db.writes)


    def test_only_current_connection_and_affected_layers_are_refreshed(self):
        def layer(table, host='connection-a', schema='gemapi'):
            uri = QgsDataSourceUri()
            uri.setConnection(host, '5432', 'test', 'user_test', '')
            uri.setDataSource(schema, table, 'geom', '', 'id')
            return SimpleNamespace(isValid=lambda: True, providerType=lambda: 'postgres',
                                   source=lambda: uri.uri(False), reload=Mock(), triggerRepaint=Mock())
        anchor = layer('prestations')
        selected = [anchor, layer('prestations_fiches'), layer('prestations_carte'), layer('ah_ouvrage')]
        untouched = [layer('techniciens'), layer('ah_stockage'), layer('prestations', 'connection-b'),
                     layer('prestations', schema='other')]
        self.plugin.show_creation()
        form = self.plugin.creation
        with patch.object(form, '_canonical_layer', return_value=anchor), patch('gemapi_app.creation.QgsProject') as project:
            project.instance.return_value.mapLayers.return_value = dict(enumerate(selected + untouched))
            self.assertTrue(REAL_REFRESH(form, [('ah_ouvrage', OBJECT)]))
        for item in selected:
            item.reload.assert_called_once()
        for item in untouched:
            item.reload.assert_not_called()

    def test_manager_refresh_after_save_only_rebuilds_years_and_results(self):
        picker_years, picker_types, picker_statuses = Mock(), Mock(), Mock()
        fake = SimpleNamespace(session=self.plugin.session, _loaded_scope=self.plugin.session.scope_generation,
                               _loaded_generation=0, connection=self.db, pickers={'years': picker_years,
                               'types': picker_types, 'statuses': picker_statuses}, newButton=Mock(), openButton=Mock(),
                               deleteButton=Mock(), _selection_changed=Mock(), _load_family=Mock(), _fetch_results=Mock())
        self.plugin.session.invalidate_prestations()
        queries = {'years': 'SELECT years', 'types': 'SELECT types', 'statuses': 'SELECT statuses'}
        with patch('gemapi_app.management.filter_option_queries', return_value=queries):
            ManageDialog.refresh(fake)
        picker_years.fill.assert_called_once()
        picker_types.fill.assert_not_called()
        picker_statuses.fill.assert_not_called()
        fake._load_family.assert_not_called()
        fake._fetch_results.assert_called_once()
        self.assertIs(self.plugin.session._values['reference.keep'], self.cache['reference.keep'])

    def test_toolbar_hides_and_restores_dirty_form_without_reset_or_confirmation(self):
        self.plugin.show_creation()
        form = self.plugin.creation
        self.fill(form)
        form.intituleEdit.setText('Brouillon à conserver')
        before = form._snapshot()
        self.assertTrue(self.plugin.action.isChecked())
        with patch.object(QMessageBox, 'question') as question:
            self.plugin.action.trigger()
            self.app.processEvents()
            self.assertFalse(form.isVisible())
            self.assertFalse(self.plugin.action.isChecked())
            self.plugin.action.trigger()
            self.app.processEvents()
            self.assertIs(self.plugin.creation, form)
            self.assertTrue(form.isVisible())
            self.assertTrue(self.plugin.action.isChecked())
            self.assertEqual(form._snapshot(), before)
            self.assertEqual(self.db.writes, [])
            question.assert_not_called()


if __name__ == '__main__': unittest.main()
