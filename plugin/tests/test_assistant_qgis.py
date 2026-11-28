"""Actual Qt conversation/form, simulated Mistral transport and database reads."""

import copy
import json
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from qgis.PyQt import sip
from qgis.PyQt.QtCore import QByteArray, QObject, QPoint, QTimer, Qt, pyqtSignal
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QComboBox, QMainWindow, QMessageBox, QToolButton
from qgis.core import QgsApplication
from qgis.gui import QgsMapCanvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gemapi_app import classFactory
from gemapi_app.assistant import Draft
from gemapi_app.assistant_dialog import AssistantDialog
from gemapi_app.creation import CreationDialog
from test_assistant import CATALOG, COMPLETE_PHRASE, FULL, PHRASE, proposal, response, uid


class Reply(QObject):
    readyRead = pyqtSignal()
    finished = pyqtSignal()

    def __init__(self, raw=b"", status=200):
        super().__init__()
        self.raw = raw
        self.status = status
        self.aborted = False

    def readAll(self):
        raw, self.raw = self.raw, b""
        return QByteArray(raw)

    def error(self):
        return QNetworkReply.OperationCanceledError if self.aborted else QNetworkReply.NoError

    def attribute(self, key):
        assert key == QNetworkRequest.HttpStatusCodeAttribute
        return self.status

    def abort(self):
        self.aborted = True
        self.finished.emit()

    def complete(self):
        self.readyRead.emit()
        self.finished.emit()


class Session:
    scope_generation = 0

    def __init__(self):
        self.db = Mock()

    def connection(self):
        return self.db


class Interface:
    def __init__(self):
        self.window = QMainWindow()
        self.canvas = QgsMapCanvas(self.window)
        self.window.setCentralWidget(self.canvas)
        self.window.resize(650, 450)
        self.window.show()

    def mainWindow(self):
        return self.window

    def mapCanvas(self):
        return self.canvas

    def addPluginToMenu(self, *_): pass
    def removePluginMenu(self, *_): pass


class QtAssistantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication([], True)
        cls.app.initQgis()

    @classmethod
    def tearDownClass(cls):
        cls.app.exitQgis()

    def setUp(self):
        self.parent = QMainWindow()
        self.session = Session()
        self.open_draft = Mock()
        self.dialog = AssistantDialog(self.session, self.open_draft, self.parent)
        self.dialog.show()
        self.stack = ExitStack()
        self.stack.enter_context(patch('gemapi_app.assistant_dialog.load_catalog', return_value=copy.deepcopy(CATALOG)))
        self.stack.enter_context(patch('gemapi_app.assistant_dialog.read_api_key', return_value='fake-test-key'))
        self.manager = Mock()
        self.dialog.network = self.manager

    def tearDown(self):
        self.dialog.cancel_request()
        self.dialog.close()
        self.dialog.deleteLater()
        self.parent.deleteLater()
        self.stack.close()
        self.app.processEvents()

    def send(self, text, raw, status=200):
        reply = Reply(raw, status)
        self.manager.post.return_value = reply
        self.dialog.submit_user_message(text)
        return reply

    def test_conversation_missing_date_correction_and_return_history(self):
        first = {**FULL, 'date_fin': None, 'message': 'Quelle est la date de fin ?'}
        self.send(PHRASE, response(**first)).complete()
        self.assertFalse(self.dialog.open_button.isEnabled())
        self.assertIn('date de fin', self.dialog.history.toPlainText())
        self.send('Le même jour.', response(date_fin='MEME_JOUR', message='Je complète la date de fin.')).complete()
        self.assertTrue(self.dialog.open_button.isEnabled())
        self.send('Non, le technicien était Pierre Martin.', response(technicien='Pierre Martin')).complete()
        self.dialog.open_button.click()
        self.open_draft.assert_called_once()
        values, links = self.open_draft.call_args.args
        self.assertEqual(values['technicien_id'], uid(4))
        self.assertEqual(links, [('ah_ouvrage', uid(6))])
        self.assertFalse(self.dialog.isVisible())
        transcript = self.dialog.history.toPlainText()
        self.dialog.show()
        self.assertEqual(self.dialog.history.toPlainText(), transcript)
        self.assertIn('Mistral — proposition', transcript)
        self.assertIn(PHRASE, transcript)
        self.assertIn('Le même jour.', transcript)
        self.session.db.executeSql.assert_not_called()
        # Plain text, not model-injected HTML.
        self.dialog._append('Mistral', '<b>texte non exécutable</b>')
        self.assertIn('<b>texte non exécutable</b>', self.dialog.history.toPlainText())
        self.app.processEvents()
        self.assertEqual(self.dialog.history.verticalScrollBar().value(), self.dialog.history.verticalScrollBar().maximum())

    def test_description_in_same_call_original_phrase_preserved_and_correction_retained(self):
        original = COMPLETE_PHRASE + " Euh, accès difficile près de la buse. Accès difficile, voilà."
        description = "Accès difficile près de la buse."
        self.send(original, response(**FULL, description=description)).complete()
        self.manager.post.assert_called_once()
        self.assertIn(original, self.dialog.history.toPlainText())
        self.assertEqual(self.dialog.draft.names['description'], description)
        self.assertEqual(self.dialog.resolution['values']['description'], description)
        self.send('Non, le technicien était Pierre Martin.', response(technicien='Pierre Martin')).complete()
        self.assertEqual(self.dialog.resolution['values']['description'], description)
        self.session.db.executeSql.assert_not_called()

    def test_new_empty_draft_is_discreet_immediate_and_reuses_session_components(self):
        identities = (self.dialog.history, self.dialog.network, self.dialog.recorder, self.dialog.session)
        previous = self.dialog.draft
        history = self.dialog.history.toPlainText()
        self.assertIsInstance(self.dialog.new_button, QToolButton)
        self.assertTrue(self.dialog.new_button.autoRaise())
        self.assertEqual(len([button for button in self.dialog.findChildren(QToolButton)
                              if button.text() == 'Nouvelle prestation']), 1)
        with patch.object(QMessageBox, 'question') as question:
            self.dialog.new_button.click()
            question.assert_not_called()
        self.assertIsNot(self.dialog.draft, previous)
        self.assertEqual((self.dialog.history, self.dialog.network, self.dialog.recorder, self.dialog.session), identities)
        self.assertTrue(self.dialog.history.toPlainText().startswith(history))
        self.assertIn('Prestation abandonnée — nouvelle prestation', self.dialog.history.toPlainText())
        self.assertFalse(self.dialog.has_draft_data())
        self.assertEqual(self.dialog.draft.names, {})
        self.assertEqual(self.dialog.draft.choices, {})
        self.assertEqual(self.dialog.draft.matches, {})
        self.assertIsNone(self.dialog.resolution)
        self.assertFalse(self.dialog.progress.isVisible())
        self.assertFalse(self.dialog.open_button.isEnabled())
        self.assertFalse(self.dialog.apply_button.isVisible())
        self.manager.post.assert_not_called()
        self.session.db.executeSql.assert_not_called()

    def test_new_prestation_confirmation_preserves_or_clears_all_draft_and_staged_state(self):
        self.send(PHRASE, response(**{**FULL, 'date_fin': None, 'prestataire': 'Entreprise inconnue',
                                    'description': 'Observation à abandonner.'})).complete()
        draft = self.dialog.draft
        self.dialog.date_edits['date_fin'].setText('12/11/2026')
        self.dialog.reference_boxes['prestataire'].setCurrentIndex(1)
        self.dialog.input.setPlainText('Correction en cours')
        history = self.dialog.history.toPlainText()
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.No) as question:
            self.assertFalse(self.dialog.new_prestation())
            question.assert_called_once()
        self.assertIs(self.dialog.draft, draft)
        self.assertEqual(self.dialog.history.toPlainText(), history)
        self.assertEqual(self.dialog.date_edits['date_fin'].text(), '12/11/2026')
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes) as question:
            self.assertTrue(self.dialog.new_prestation())
            question.assert_called_once()
        self.assertIsNot(self.dialog.draft, draft)
        self.assertEqual(self.dialog.reference_boxes, {})
        self.assertEqual(self.dialog.date_edits, {})
        self.assertIsNone(self.dialog.family_box)
        self.assertEqual(self.dialog.input.toPlainText(), '')
        self.assertEqual(self.dialog._raw, bytearray())
        self.assertEqual(self.dialog._voice_raw, bytearray())
        self.assertEqual(self.dialog._request_references, {})
        self.assertEqual(self.dialog._request_message, '')
        self.assertFalse(self.dialog._review_required)
        self.assertFalse(self.dialog._recording)
        self.assertFalse(self.dialog.timer.isActive())
        self.assertFalse(self.dialog.voice_timer.isActive())
        self.assertEqual(self.dialog._capture_slots, [])
        self.assertTrue(self.dialog.new_button.isEnabled())
        self.assertFalse(self.dialog.has_draft_data())

    def test_new_prestation_aborts_mistral_and_ignores_old_reply_and_timeout_after_new_request(self):
        old = self.send(COMPLETE_PHRASE, response(**FULL, description='Ancienne observation unique'))
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.No):
            self.assertFalse(self.dialog.new_prestation())
        self.assertFalse(old.aborted)
        self.assertTrue(self.dialog.new_button.isEnabled())
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            self.assertTrue(self.dialog.new_prestation())
        self.assertTrue(old.aborted)
        self.assertIsNone(self.dialog._reply)
        new = self.send('Nouvelle prestation sur une zone terrestre.', response(famille='vegetation'))
        body = json.loads(self.manager.post.call_args.args[1])
        context = json.loads(body['messages'][1]['content'])
        self.assertEqual(context['brouillon'], {})
        self.assertEqual(context['demande'], 'Nouvelle prestation sur une zone terrestre.')
        self.assertNotIn('Ancienne observation unique', json.dumps(body))
        self.dialog._read_response(old)
        self.dialog._finished(old)
        self.dialog._timeout(old)
        old.complete()  # Real signal connections must carry the original reply identity too.
        self.assertIs(self.dialog._reply, new)
        self.assertTrue(self.dialog.timer.isActive())
        self.assertEqual(self.dialog.draft.names, {})
        self.assertEqual(self.dialog._raw, bytearray())
        new.complete()
        self.assertEqual(self.dialog.draft.names, {'famille': 'vegetation'})
        self.assertNotIn('date_fin', self.dialog.draft.names)
        self.assertNotIn('Ancienne observation unique', self.dialog.history.toPlainText())
        self.session.db.executeSql.assert_not_called()

    def test_uncommitted_manual_inputs_require_confirmation_even_without_extracted_fields(self):
        for field in ('input', 'date', 'reference'):
            with self.subTest(field=field):
                self.dialog._refresh()
                self.assertFalse(self.dialog.has_draft_data())
                if field == 'input':
                    self.dialog.input.setPlainText('Texte encore non envoyé')
                elif field == 'date':
                    self.dialog.date_edits['date_fin'].setText('12/11/2026')
                else:
                    self.dialog.reference_boxes['technicien'].setCurrentIndex(1)
                with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes) as question:
                    self.assertTrue(self.dialog.new_prestation())
                    question.assert_called_once()

    def test_several_new_prestations_keep_history_but_never_send_old_draft(self):
        identities = (self.dialog.history, self.dialog.network, self.dialog.recorder)
        for index in range(3):
            with self.subTest(index=index):
                self.send(COMPLETE_PHRASE, response(**FULL, description='Observation ' + str(index))).complete()
                body = json.loads(self.manager.post.call_args.args[1])
                self.assertEqual(json.loads(body['messages'][1]['content'])['brouillon'], {})
                with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
                    self.dialog.new_button.click()
                self.assertEqual((self.dialog.history, self.dialog.network, self.dialog.recorder), identities)
                self.assertEqual(self.dialog.draft.names, {})
        self.assertEqual(self.dialog.history.toPlainText().count('Prestation abandonnée — nouvelle prestation'), 3)
        self.assertEqual(self.manager.post.call_count, 3)
        self.session.db.executeSql.assert_not_called()

    def test_save_resets_immediately_without_confirmation_and_does_not_reuse_old_context(self):
        self.send(COMPLETE_PHRASE, response(**FULL, description='Note déjà enregistrée')).complete()
        with patch.object(QMessageBox, 'question') as question:
            self.dialog.prestation_saved()
            question.assert_not_called()
        self.assertFalse(self.dialog.has_draft_data())
        self.assertIn('Prestation enregistrée — nouvelle prestation', self.dialog.history.toPlainText())
        self.dialog.hide()
        self.dialog.show()
        self.send('Une autre intervention.', response(description='Autre note')).complete()
        context = json.loads(json.loads(self.manager.post.call_args.args[1])['messages'][1]['content'])
        self.assertEqual(context['brouillon'], {})
        self.assertEqual(self.dialog.draft.names, {'description': 'Autre note'})
        self.session.db.executeSql.assert_not_called()

    def test_reactive_timer_and_timeout_disables_stale_opening(self):
        self.send(COMPLETE_PHRASE, response(**FULL)).complete()
        self.assertTrue(self.dialog.open_button.isEnabled())
        reply = self.send('Corriger le technicien.', b'')
        ticks = []
        QTimer.singleShot(5, lambda: ticks.append(True))
        QTest.qWait(30)
        self.assertTrue(ticks)
        self.assertTrue(self.dialog.progress.isVisible())
        self.assertFalse(self.dialog.open_button.isEnabled())
        self.dialog._timeout()
        self.assertTrue(reply.aborted)
        self.assertIn('Délai Mistral dépassé', self.dialog.history.toPlainText())
        self.assertFalse(self.dialog.open_button.isEnabled())
        self.assertEqual(self.dialog.draft.names['technicien'], 'Jean Dubois')

    def test_long_type_false_save_claim_and_uncertain_end_in_actual_ui(self):
        self.send(PHRASE, response(**{**FULL, 'type_prestation': 'intervention de débroussaillage',
                                    'message': 'Votre prestation a été enregistrée dans la base.'})).complete()
        transcript = self.dialog.history.toPlainText()
        self.assertNotIn('Votre prestation a été enregistrée', transcript)
        self.assertIn('Proposition de prestation préparée', transcript)
        self.assertIn('Débroussaillage', self.dialog.summary.text())
        self.assertIn('date de fin à confirmer', transcript.lower())
        self.assertIn('date_fin', self.dialog.date_edits)
        self.assertFalse(self.dialog.open_button.isEnabled())
        self.dialog._open_form()
        self.open_draft.assert_not_called()
        self.dialog.apply_button.click()
        self.assertNotIn('date_fin', self.dialog.date_edits)
        self.assertTrue(self.dialog.open_button.isEnabled())
        self.assertIn('Vous — corrections validées', self.dialog.history.toPlainText())
        self.dialog.open_button.click()
        values, _ = self.open_draft.call_args.args
        self.assertEqual(values['type_prestation_id'], uid(1))
        self.assertEqual(values['date_fin'], '2026-11-11')
        self.assertEqual(self.manager.post.call_count, 1)  # Confirmation stays local.
        self.session.db.executeSql.assert_not_called()

    def test_request_uses_current_connection_references_each_turn(self):
        with patch('gemapi_app.assistant_dialog.load_catalog', return_value=copy.deepcopy(CATALOG)) as loader:
            first = self.send(PHRASE, response(**{**FULL, 'date_fin': None}))
            loader.assert_called_with(self.session.db, None, for_interpretation=True)
            request, body = self.manager.post.call_args.args
            refs = json.loads(json.loads(body)['messages'][1]['content'])['referentiels']
            self.assertEqual(refs['types_prestations'][0]['libelle'], 'Débroussaillage')
            self.assertIn('Pierre Martin', refs['techniciens'])
            first.complete()
            current = copy.deepcopy(CATALOG)
            current['type_prestation'] = [(uid(9), 'Fauche', 'Autre catégorie')]
            loader.return_value = current
            self.send('Le type est Fauche.', response(type_prestation='Fauche')).complete()
            body = self.manager.post.call_args.args[1]
            refs = json.loads(json.loads(body)['messages'][1]['content'])['referentiels']
            self.assertEqual(refs['types_prestations'], [{'libelle': 'Fauche', 'categorie': 'Autre catégorie'}])
        self.session.db.executeSql.assert_not_called()

    def test_unavailable_references_do_not_send_request_or_drop_draft(self):
        self.send(PHRASE, response(**{**FULL, 'date_fin': None})).complete()
        previous = dict(self.dialog.draft.names)
        self.manager.reset_mock()
        with patch('gemapi_app.assistant_dialog.load_catalog', side_effect=RuntimeError('private database failure')):
            self.dialog.submit_user_message('Corriger le type.')
        self.manager.post.assert_not_called()
        self.assertEqual(self.dialog.draft.names, previous)
        self.assertNotIn('private database failure', self.dialog.history.toPlainText())
        self.assertIn('Aucun message envoyé à Mistral', self.dialog.history.toPlainText())

    def test_end_can_be_corrected_in_conversation_instead_of_confirming(self):
        self.send(PHRASE, response(**FULL)).complete()
        self.assertIn('date_fin', self.dialog.date_edits)
        self.send('Non, la date de fin est le 12 novembre 2026.', response(date_fin='2026-11-12')).complete()
        self.assertNotIn('date_fin', self.dialog.date_edits)
        self.assertTrue(self.dialog.open_button.isEnabled())
        self.assertEqual(self.dialog.draft.names['date_fin'], '2026-11-12')
        self.session.db.executeSql.assert_not_called()

    def test_api_error_invalid_json_and_missing_key(self):
        for raw, status in ((b'fake secret response body', 401), (b'broken-json', 200)):
            self.send('Débroussaillage', raw, status).complete()
            self.assertFalse(self.dialog.open_button.isEnabled())
            self.assertNotIn('fake secret response body', self.dialog.history.toPlainText())
        self.manager.reset_mock()
        with patch('gemapi_app.assistant_dialog.read_api_key', side_effect=ValueError('Clé Mistral absente.')):
            self.dialog.submit_user_message('Test')
        self.manager.post.assert_not_called()
        self.assertIn('Clé Mistral absente', self.dialog.history.toPlainText())

    def test_homonym_choice_ui_and_cancel_request(self):
        catalog = copy.deepcopy(CATALOG)
        catalog['patrimoine'].append((uid(7), 'Rivière du Buissons', 'AH du Sud'))
        with patch('gemapi_app.assistant_dialog.load_catalog', return_value=catalog):
            self.send(COMPLETE_PHRASE, response(**FULL)).complete()
            self.assertFalse(self.dialog.open_button.isEnabled())
            combo = self.dialog.choices_widget.findChild(QComboBox)
            self.assertEqual(combo.count(), 3)
            combo.setCurrentIndex(2)
            combo.activated.emit(2)
            self.assertFalse(self.dialog.open_button.isEnabled())
            self.assertNotIn('patrimoine', self.dialog.draft.choices)
            self.dialog.apply_button.click()
            self.assertTrue(self.dialog.open_button.isEnabled())
            self.assertEqual(self.dialog.resolution['associations'], [('ah_ouvrage', uid(7))])
        reply = self.send('Test', b'')
        self.dialog.reject()
        self.assertTrue(reply.aborted)
        self.assertIsNone(self.dialog._reply)
        self.assertGreater(len(self.dialog.messages), 1)

    def test_grouped_uncertain_choices_and_missing_end_date_no_automatic_acceptance(self):
        catalog = copy.deepcopy(CATALOG)
        catalog['technicien'] = [(uid(30), 'Benoît Quentin', ''), (uid(32), 'Benoît Cantine', '')]
        catalog['patrimoine'] = [(uid(31), 'Talus ZEC Ourton', 'AH de test')]
        extracted = {**FULL, 'technicien': 'Benoît Cantin', 'patrimoine': 'Talus ZEC Hurton', 'date_fin': None}
        extracted['correspondances'] = {
            'technicien': {'source': 'Benoît Cantin', 'nature': 'approximative', 'candidats': ['Benoît Quentin']},
            'patrimoine': {'source': 'Talus ZEC Hurton', 'nature': 'approximative', 'candidats': ['Talus ZEC Ourton — AH de test']},
            'prestataire': None,
        }
        with patch('gemapi_app.assistant_dialog.load_catalog', return_value=catalog):
            self.send(PHRASE.replace('Jean Dubois', 'Benoît Cantin').replace('Rivière du Buissons', 'Talus ZEC Hurton'), response(**extracted)).complete()
            self.app.processEvents()
            self.assertEqual(set(self.dialog.reference_boxes), {'technicien', 'patrimoine'})
            self.assertEqual(set(self.dialog.date_edits), {'date_fin'})
            end = self.dialog.date_edits['date_fin']
            self.assertEqual(end.text(), '')
            self.assertTrue(end.isVisible())
            self.assertTrue(self.dialog.apply_button.isVisible())
            viewport = self.dialog.corrections_area.viewport()
            for widget in (end, self.dialog.apply_button, *self.dialog.reference_boxes.values()):
                self.assertTrue(viewport.rect().contains(widget.rect().translated(widget.mapTo(viewport, QPoint()))))
            before = dict(self.dialog.draft.names)
            for key, identifier in (('technicien', uid(30)), ('patrimoine', uid(31))):
                combo = self.dialog.reference_boxes[key]
                self.assertEqual(combo.itemData(1), identifier)
                self.assertIsNone(combo.currentData())
                combo.setCurrentIndex(1)
                combo.activated.emit(1)
                self.assertEqual(self.dialog.draft.names, before)
                self.assertFalse(self.dialog.open_button.isEnabled())
            end.setText('12/11/2026')
            self.dialog.apply_button.click()
            self.assertTrue(self.dialog.open_button.isEnabled())
            self.assertFalse(self.dialog.apply_button.isVisible())
            self.assertEqual(self.dialog.draft.names['technicien'], 'Benoît Quentin')
            self.assertEqual(self.dialog.draft.names['patrimoine'], 'Talus ZEC Ourton — AH de test')
            self.assertEqual(self.dialog.resolution['values']['date_fin'], '2026-11-12')
            for key in ('type_prestation', 'prestataire', 'statut', 'date_debut'):
                self.assertEqual(self.dialog.draft.names[key], before[key])
            self.assertIn('Vous — corrections validées', self.dialog.history.toPlainText())
            self.dialog.open_button.click()
            self.open_draft.assert_called_once()
        self.manager.post.assert_called_once()
        self.session.db.executeSql.assert_not_called()

    def test_eight_demo_trials_present_partial_results_and_explicit_dates(self):
        from test_assistant_validation import TRIALS, trial_proposal
        self.dialog.before_new = lambda _: True
        self.dialog.speech_enabled.setChecked(False)
        for index, trial in enumerate(TRIALS):
            with self.subTest(trial=index + 1):
                self.dialog.new_prestation()
                text, catalog, fields = trial_proposal(index)
                with patch('gemapi_app.assistant_dialog.load_catalog', return_value=catalog):
                    self.send(text, response(**fields)).complete()
                    self.assertEqual(self.dialog.draft.names['date_fin'], trial[8])
                    self.assertNotIn('date_fin', self.dialog.date_edits)
                    if index < 6:
                        self.assertTrue(self.dialog.open_button.isEnabled(), self.dialog.summary.text())
                    else:
                        self.assertFalse(self.dialog.open_button.isEnabled())
                        self.assertIn('Nom source à vérifier', self.dialog.summary.text())
                        box = self.dialog.reference_boxes['patrimoine']
                        self.assertIsNone(box.currentData())
                        target = next(row for row in catalog['patrimoine'] if row[1] == trial[2])
                        box.setCurrentIndex(box.findData(target[0]))
                        self.dialog.apply_button.click()
                        self.assertTrue(self.dialog.open_button.isEnabled(), self.dialog.summary.text())
                    if trial[10] == 'previsionnelle':
                        self.assertIn('(prévue)', self.dialog.summary.text())
                self.session.db.executeSql.assert_not_called()

    def test_unique_mistral_phonetic_names_auto_resolve_all_families_in_one_request(self):
        for family in ('ah_ouvrage', 'ah_stockage', 'mobilier', 'cheminements', 'vegetation'):
            catalog = copy.deepcopy(CATALOG)
            catalog['technicien'] = [(uid(30), 'Benoît Quentin', '')]
            catalog['patrimoine'] = [(uid(31), 'Talus ZEC Ourton', 'Parent de test')]
            catalog['patrimoines'] = {family: catalog['patrimoine']}
            fields = {**FULL, 'famille': family, 'technicien': 'Benoît Quentin',
                      'patrimoine': 'Talus ZEC Ourton — Parent de test', 'date_fin': None,
                      'correspondances': {
                          'technicien': {'source': 'Benoît Cantin', 'nature': 'phonetique_complete', 'candidats': ['Benoît Quentin']},
                          'prestataire': {'source': 'Véollia', 'nature': 'phonetique_complete', 'candidats': ['Veolia']},
                          'patrimoine': {'source': 'Talus ZEC Hurton', 'nature': 'phonetique_complete', 'candidats': ['Talus ZEC Ourton — Parent de test']}}}
            self.dialog.draft = Draft()
            self.manager.reset_mock()
            with patch('gemapi_app.assistant_dialog.load_catalog', return_value=catalog):
                self.send('Intervention sur Talus ZEC Hurton, par Véollia, suivi Benoît Cantin.', response(**fields)).complete()
                self.assertEqual(self.dialog.reference_boxes, {})
                self.assertEqual(set(self.dialog.date_edits), {'date_fin'})
                self.assertFalse(self.dialog.open_button.isEnabled())
                self.dialog.date_edits['date_fin'].setText('12/11/2026')
                self.dialog.apply_button.click()
                self.assertTrue(self.dialog.open_button.isEnabled())
                self.assertEqual(self.dialog.resolution['values']['technicien_id'], uid(30))
                self.assertEqual(self.dialog.resolution['associations'], [(family, uid(31))])
                self.assertEqual(self.dialog.choices_layout.rowCount(), 0)
            self.manager.post.assert_called_once()
            self.session.db.executeSql.assert_not_called()

    def test_all_missing_fields_shown_together_and_invalid_date_keeps_staged_choices(self):
        self.send('Préparer une prestation sur un ouvrage hydraulique.', response(famille='ah_ouvrage')).complete()
        self.assertEqual(set(self.dialog.reference_boxes), {'type_prestation', 'prestataire', 'technicien', 'statut', 'patrimoine'})
        self.assertEqual(set(self.dialog.date_edits), {'date_debut', 'date_fin'})
        before = copy.deepcopy(vars(self.dialog.draft))
        for combo in self.dialog.reference_boxes.values():
            self.assertIsNone(combo.currentData())
            self.assertGreater(combo.count(), 1)
            combo.setCurrentIndex(1)
        self.dialog.date_edits['date_debut'].setText('11/11/2026')
        self.dialog.date_edits['date_fin'].setText('10/11/2026')
        self.dialog.apply_button.click()
        self.assertEqual(vars(self.dialog.draft), before)
        self.assertFalse(self.dialog.open_button.isEnabled())
        self.assertTrue(all(combo.currentIndex() == 1 for combo in self.dialog.reference_boxes.values()))
        self.assertEqual(self.dialog.date_edits['date_fin'].text(), '10/11/2026')
        self.dialog.date_edits['date_fin'].setText('12 novembre 2026')
        self.dialog.apply_button.click()
        self.assertTrue(self.dialog.open_button.isEnabled())
        self.assertEqual(self.dialog.resolution['values']['date_fin'], '2026-11-12')
        self.manager.post.assert_called_once()
        self.session.db.executeSql.assert_not_called()

    def test_unknown_name_offers_active_list_but_free_text_and_stale_id_are_rejected(self):
        self.send(COMPLETE_PHRASE, response(**{**FULL, 'prestataire': 'Entreprise introuvable'})).complete()
        combo = self.dialog.reference_boxes['prestataire']
        self.assertEqual(combo.itemData(1), uid(2))
        before = copy.deepcopy(vars(self.dialog.draft))
        combo.setCurrentIndex(1)
        combo.setEditText('Une entreprise inventée')
        self.dialog.apply_button.click()
        self.assertEqual(vars(self.dialog.draft), before)
        combo.setCurrentIndex(1)
        current = copy.deepcopy(CATALOG)
        current['prestataire'] = []
        with patch('gemapi_app.assistant_dialog.load_catalog', return_value=current):
            self.dialog.apply_button.click()
        self.assertEqual(vars(self.dialog.draft), before)
        self.assertFalse(self.dialog.open_button.isEnabled())
        self.session.db.executeSql.assert_not_called()

    def test_family_selection_loads_objects_without_losing_other_staged_inputs(self):
        unscoped = copy.deepcopy(CATALOG)
        unscoped.pop('patrimoine')
        with patch('gemapi_app.assistant_dialog.load_catalog', return_value=unscoped):
            self.send('Préparer une prestation.', response()).complete()
        self.assertIsNotNone(self.dialog.family_box)
        self.assertEqual(self.dialog.reference_boxes['patrimoine'].count(), 1)
        self.dialog.reference_boxes['technicien'].setCurrentIndex(1)
        self.dialog.date_edits['date_debut'].setText('11/11/2026')
        self.dialog.date_edits['date_fin'].setText('12/11/2026')
        with patch('gemapi_app.assistant_dialog.load_catalog', return_value=copy.deepcopy(CATALOG)) as loader:
            self.dialog.family_box.setCurrentIndex(2)
            loader.assert_called_with(self.session.db, 'ah_stockage')
            self.assertEqual(self.dialog.reference_boxes['technicien'].currentData(), uid(3))
            self.assertEqual(self.dialog.date_edits['date_fin'].text(), '12/11/2026')
            for combo in self.dialog.reference_boxes.values():
                if not combo.currentData():
                    combo.setCurrentIndex(1)
            self.dialog.apply_button.click()
        self.assertEqual(self.dialog.resolution['associations'], [('ah_stockage', uid(6))])
        self.assertTrue(self.dialog.open_button.isEnabled())
        self.manager.post.assert_called_once()

    def test_grouped_review_is_disabled_during_requests_and_after_project_change(self):
        self.send(PHRASE, response(**{**FULL, 'date_fin': None})).complete()
        self.dialog.date_edits['date_fin'].setText('12/11/2026')
        before = copy.deepcopy(vars(self.dialog.draft))
        reply = self.send('Corriger le technicien.', b'')
        self.assertFalse(self.dialog.apply_button.isEnabled())
        self.assertFalse(self.dialog.choices_widget.isEnabled())
        self.dialog._apply_corrections()
        self.assertEqual(vars(self.dialog.draft), before)
        reply.raw = response(technicien='Pierre Martin')
        reply.complete()
        self.dialog.date_edits['date_fin'].setText('12/11/2026')
        self.session.scope_generation += 1
        self.dialog.apply_button.click()
        self.assertFalse(self.dialog.open_button.isEnabled())
        self.assertNotIn('date_fin', self.dialog.draft.names)
        self.assertIn('connexion a changé', self.dialog.history.toPlainText())
        self.assertEqual(self.dialog.date_edits['date_fin'].text(), '')

    def test_changed_catalogue_exposes_new_uncertainty_and_retains_staged_date(self):
        catalog = copy.deepcopy(CATALOG)
        catalog['technicien'] = [(uid(30), 'Benoît Quentin', '')]
        metadata = {'technicien': {'source': 'Benoît Cantin', 'candidats': ['Benoît Quentin'],
                                  'nature': 'phonetique_complete'}, 'prestataire': None, 'patrimoine': None}
        fields = {**FULL, 'technicien': 'Benoît Quentin', 'date_fin': None, 'correspondances': metadata}
        with patch('gemapi_app.assistant_dialog.load_catalog', return_value=catalog):
            self.send(PHRASE.replace('Jean Dubois', 'Benoît Cantin'), response(**fields)).complete()
            self.assertEqual(self.dialog.reference_boxes, {})
            self.dialog.date_edits['date_fin'].setText('12/11/2026')
            catalog['technicien'].append((uid(32), 'Benoît Quantin', ''))
            self.dialog.apply_button.click()
            self.assertIn('technicien', self.dialog.reference_boxes)
            self.assertEqual(self.dialog.date_edits['date_fin'].text(), '12/11/2026')
            self.assertNotIn('date_fin', self.dialog.draft.names)
            box = self.dialog.reference_boxes['technicien']
            self.assertGreaterEqual(box.findData(uid(32)), 1)
            box.setCurrentIndex(box.findData(uid(30)))
            self.dialog.apply_button.click()
            self.assertTrue(self.dialog.open_button.isEnabled())
            self.assertEqual(self.dialog.resolution['values']['date_fin'], '2026-11-12')
        self.manager.post.assert_called_once()
        self.session.db.executeSql.assert_not_called()

    def make_form(self):
        iface = Interface()
        self.addCleanup(iface.canvas.deleteLater)
        self.addCleanup(iface.window.deleteLater)
        stack = self.stack
        def types(form):
            form.typeCombo.set_reference([(uid(99), 'Entretien')], [(uid(1), 'Débroussaillage', uid(99), True)])
        def providers(form, *_):
            form.prestataireCombo.addItem('Veolia', uid(2))
        def statuses(form):
            form.statutCombo.addItem('Terminé', uid(5))
        def technicians(form):
            form.technicienCombo.addItem('Jean Dubois', uid(3))
        def objects(form):
            form.objects = [('ah_ouvrage', (uid(6),), 'AH du Nord — Rivière du Buissons', (1,), 'riviere du buissons', None)]
        for name, method in (('_load_types', types), ('_load_reference', providers), ('_load_statuses', statuses),
                             ('_load_technicians', technicians), ('_load_objects', objects)):
            stack.enter_context(patch.object(CreationDialog, name, method))
        stack.enter_context(patch('gemapi_app.assistant.load_catalog', return_value=copy.deepcopy(CATALOG)))
        form = CreationDialog(iface, iface.window, session=self.session)
        self.addCleanup(lambda: None if sip.isdeleted(form) else form.deleteLater())
        return form

    def test_actual_standard_form_prefill_only_reads_and_null_geometry(self):
        draft = Draft()
        draft.apply(proposal(**FULL, description="Accès difficile près de la buse."), user_message=COMPLETE_PHRASE)
        resolved = draft.resolve(CATALOG)
        form = self.make_form()
        with patch.object(form, '_save') as save:
            form.load_draft(resolved['values'], resolved['associations'])
            self.assertEqual(form._values()['date_fin'], '2026-11-11')
            self.assertEqual(form._values()['technicien_id'], uid(3))
            self.assertEqual(form.descriptionEdit.toPlainText(), 'Accès difficile près de la buse.')
            self.assertFalse(form.descriptionEdit.isReadOnly())
            form.descriptionEdit.setPlainText('Description corrigée par l’utilisateur.')
            self.assertEqual(form._values()['description'], 'Description corrigée par l’utilisateur.')
            self.assertEqual(form._associations(), [('ah_ouvrage', uid(6))])
            self.assertIsNone(form._geometry_wkt_for_save())
            self.assertTrue(form.saveButton.isEnabled())
            self.assertFalse(form.pointButton.isEnabled())
            save.assert_not_called()
            self.session.db.executeSql.assert_not_called()
        form.show()
        self.app.processEvents()
        form.shutdown()

    def test_plugin_navigation_keeps_conversation_dirty_draft_and_focus_after_human_save(self):
        form = self.make_form()
        plugin = classFactory(form.iface)
        plugin.initGui()
        self.addCleanup(plugin.unload)
        self.stack.enter_context(patch.object(plugin.session, 'connection', return_value=self.session.db))
        self.stack.enter_context(patch('gemapi_app.plugin.prestation_permissions', return_value={'insert': True}))
        self.stack.enter_context(patch('gemapi_app.plugin.CreationDialog', return_value=form))
        focus = self.stack.enter_context(patch('gemapi_app.creation.focus_prestation', return_value=True))
        self.stack.enter_context(patch.object(form, '_refresh_layers', return_value=True))
        # The form uses the actual plugin session; all DB methods remain mocked.
        form.session = plugin.session
        plugin.session._values['reference.keep'] = marker = object()
        plugin.show_dialog()
        home = plugin.home
        plugin.show_assistant()
        assistant = plugin.assistant
        assistant._append('Vous', COMPLETE_PHRASE)
        assistant.draft.apply(proposal(**FULL), user_message=COMPLETE_PHRASE)
        history = assistant.history.toPlainText()
        self.assertFalse(home.isVisible())
        self.assertTrue(plugin.action.isChecked())
        assistant.close()
        self.app.processEvents()
        self.assertTrue(home.isVisible())
        self.assertIs(plugin.assistant, assistant)
        plugin.show_assistant()
        result = assistant.draft.resolve(CATALOG)
        plugin.open_creation_draft(result['values'], result['associations'])
        self.assertTrue(form.isVisible())
        self.assertFalse(assistant.isVisible())
        self.assertFalse(home.isVisible())
        before = form._snapshot()
        plugin.action.trigger()
        self.app.processEvents()
        self.assertFalse(form.isVisible())
        self.assertFalse(plugin.action.isChecked())
        plugin.action.trigger()
        self.app.processEvents()
        self.assertIs(plugin.creation, form)
        self.assertEqual(form._snapshot(), before)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.No):
            form.reject()
        self.assertTrue(form.isVisible())
        self.assertFalse(assistant.isVisible())
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            form.reject()
        self.assertTrue(assistant.isVisible())
        self.assertEqual(assistant.history.toPlainText(), history)
        self.session.db.executeSql.assert_not_called()
        focus.assert_not_called()
        # A fresh fixture starts the next flow; no SQL transaction is run.
        form = self.make_form()
        form.session = plugin.session
        with patch('gemapi_app.plugin.CreationDialog', return_value=form):
            plugin.open_creation_draft(result['values'], result['associations'])
        with patch.object(form, '_refresh_layers', return_value=True):
            form._complete_save(uid(800), self.session.db, form._associations(), 'Sauvegarde humaine simulée.')
        self.app.processEvents()
        focus.assert_called_once_with(form.iface, self.session.db, uid(800))
        self.assertTrue(home.isVisible())
        self.assertFalse(form.isVisible())
        self.assertFalse(assistant.isVisible())
        self.assertTrue(plugin.action.isChecked())
        self.assertIs(plugin.session._values['reference.keep'], marker)
        self.assertFalse(plugin._unloading)
        # A new AI draft can reuse the completed form without a second instance or save.
        same = plugin.open_creation_draft(result['values'], result['associations'])
        self.assertIs(same, form)
        self.assertFalse(form._save_completed)
        self.assertIsNone(form.saved_prestation_id)
        self.assertEqual(form._associations(), result['associations'])
        self.assertTrue(form.saveButton.isEnabled())
        self.assertTrue(assistant.history.toPlainText().startswith(history))
        self.assertIn('Prestation enregistrée — nouvelle prestation', assistant.history.toPlainText())
        self.assertFalse(assistant.has_draft_data())
        self.session.db.executeSql.assert_not_called()

    def test_new_assistant_prestation_uses_form_confirmation_once_before_discarding(self):
        form = self.make_form()
        plugin = classFactory(form.iface)
        plugin.initGui()
        self.addCleanup(plugin.unload)
        self.stack.enter_context(patch.object(plugin.session, 'connection', return_value=self.session.db))
        self.stack.enter_context(patch('gemapi_app.plugin.prestation_permissions', return_value={'insert': True}))
        self.stack.enter_context(patch('gemapi_app.plugin.CreationDialog', return_value=form))
        form.session = plugin.session
        plugin.session._values['reference.keep'] = marker = object()
        plugin.show_assistant()
        assistant = plugin.assistant
        assistant.draft.apply(proposal(**FULL), user_message=COMPLETE_PHRASE)
        resolved = assistant.draft.resolve(CATALOG)
        plugin.open_creation_draft(resolved['values'], resolved['associations'])
        self.app.processEvents()
        self.assertIn(assistant.new_action, form.actions())
        self.assertEqual(assistant.new_action.shortcut().toString(), 'Alt+N')
        before = form._snapshot()
        draft = assistant.draft
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.No) as question:
            self.assertFalse(assistant.new_prestation())
            question.assert_called_once()
            self.assertIs(question.call_args.args[0], form)
        self.assertIs(plugin.creation, form)
        self.assertTrue(form.isVisible())
        self.assertIs(assistant.draft, draft)
        self.assertEqual(form._snapshot(), before)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes) as question:
            QTest.keyPress(form, Qt.Key_N, Qt.AltModifier)
            QTest.keyRelease(assistant, Qt.Key_N, Qt.AltModifier)
            question.assert_called_once()
            self.assertIs(question.call_args.args[0], form)
        self.assertIsNone(plugin.creation)
        self.assertIsNone(plugin._creation_origin)
        self.assertIs(plugin.assistant, assistant)
        self.assertTrue(assistant.isVisible())
        self.assertTrue(sip.isdeleted(form) or not form.isVisible())
        self.assertFalse(assistant.has_draft_data())
        self.assertIs(plugin.session._values['reference.keep'], marker)
        self.assertFalse(plugin._unloading)
        self.assertTrue(plugin.action.isChecked())
        self.session.db.executeSql.assert_not_called()

    def test_successful_ai_form_save_then_new_command_never_confirms_or_changes_saved_record(self):
        form = self.make_form()
        plugin = classFactory(form.iface)
        plugin.initGui()
        self.addCleanup(plugin.unload)
        self.stack.enter_context(patch.object(plugin.session, 'connection', return_value=self.session.db))
        self.stack.enter_context(patch('gemapi_app.plugin.prestation_permissions', return_value={'insert': True}))
        self.stack.enter_context(patch('gemapi_app.plugin.CreationDialog', return_value=form))
        self.stack.enter_context(patch('gemapi_app.creation.focus_prestation', return_value=True))
        form.session = plugin.session
        plugin.show_assistant()
        assistant = plugin.assistant
        assistant.draft.apply(proposal(**FULL), user_message=COMPLETE_PHRASE)
        result = assistant.draft.resolve(CATALOG)
        plugin.open_creation_draft(result['values'], result['associations'])
        with patch.object(form, '_refresh_layers', return_value=True), patch.object(QMessageBox, 'question') as question:
            form._complete_save(uid(800), self.session.db, form._associations(), 'Sauvegarde humaine simulée.')
            self.assertEqual(form.saved_prestation_id, uid(800))
            self.assertFalse(assistant.has_draft_data())
            plugin.show_assistant()
            self.assertIs(plugin.assistant, assistant)
            self.assertEqual(assistant.draft.names, {})
            self.assertTrue(assistant.new_prestation())
            self.app.processEvents()  # The queued home-after-save must not cover the new assistant session.
            self.assertTrue(assistant.isVisible())
            question.assert_not_called()
        self.assertIsNone(plugin.creation)
        self.assertEqual(form.saved_prestation_id, uid(800))
        self.session.db.executeSql.assert_not_called()

    def test_failed_ai_form_save_preserves_assistant_draft_and_never_marks_it_saved(self):
        form = self.make_form()
        plugin = classFactory(form.iface)
        plugin.initGui()
        self.addCleanup(plugin.unload)
        self.stack.enter_context(patch.object(plugin.session, 'connection', return_value=self.session.db))
        self.stack.enter_context(patch('gemapi_app.plugin.prestation_permissions', return_value={'insert': True}))
        self.stack.enter_context(patch('gemapi_app.plugin.CreationDialog', return_value=form))
        form.session = plugin.session
        plugin.show_assistant()
        assistant = plugin.assistant
        assistant.draft.apply(proposal(**FULL), user_message=COMPLETE_PHRASE)
        draft = assistant.draft
        result = draft.resolve(CATALOG)
        plugin.open_creation_draft(result['values'], result['associations'])
        self.session.db.executeSql.side_effect = RuntimeError('Échec simulé de sauvegarde')
        with patch.object(assistant, 'prestation_saved') as saved:
            form._save()
            saved.assert_not_called()
        self.assertFalse(form._save_completed)
        self.assertIs(assistant.draft, draft)
        self.assertTrue(assistant.has_draft_data())
        self.assertNotIn('Prestation enregistrée — nouvelle prestation', assistant.history.toPlainText())

    def test_form_rejects_invented_id_and_missing_object_without_mutation(self):
        draft = Draft()
        draft.apply(proposal(**FULL), user_message=COMPLETE_PHRASE)
        resolved = draft.resolve(CATALOG)
        form = self.make_form()
        before = form._snapshot()
        for values, links in (({**resolved['values'], 'technicien_id': uid(999)}, resolved['associations']),
                              (resolved['values'], [('ah_ouvrage', uid(999))])):
            with self.assertRaises(ValueError):
                form.load_draft(values, links)
            self.assertEqual(form._snapshot(), before)
        self.session.db.executeSql.assert_not_called()


if __name__ == '__main__':
    unittest.main()
