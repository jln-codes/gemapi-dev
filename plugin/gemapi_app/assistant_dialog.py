"""Persistent conversation UI; Qt network requests leave QGIS's event loop free."""

import json

from pathlib import Path

from qgis.PyQt import sip
from qgis.PyQt.QtCore import QSettings, QTimer, Qt, QUrl, pyqtSlot
from qgis.PyQt.QtGui import QColor, QIcon, QTextCharFormat, QTextCursor
from qgis.PyQt.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from qgis.PyQt.QtWidgets import (QAction, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                                QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
                                QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from .record import TARGETS
from .assistant import (ALLOWED_FAMILIES, Draft, ENDPOINT, KEY_PATH_SETTING, LABELS, REFERENCES, candidates, configured_key_path, load_catalog, parse_response,
                        read_api_key, request_payload, spoken_result)
from .window_geometry import initialize_window
from .voice import (Recorder, Speaker, SPEECH_SETTING, TRANSCRIPTION_ENDPOINT, context_vocabulary,
                    parse_transcription, transcription_error, transcription_multipart)

VOICE_IDLE = "Dictée ponctuelle : l'audio est transmis à Voxtral après Arrêter ; aucun fichier audio n'est conservé."


class AssistantDialog(QDialog):
    def __init__(self, session, open_draft, parent=None, before_new=None):
        super().__init__(parent)
        self.session = session
        self.open_draft = open_draft
        self.before_new = before_new
        self.draft = Draft()
        # Private Qt manager: bearer headers must not enter QGIS's network logger.
        self.network = QNetworkAccessManager(self)
        self._reply = None
        self._voice_reply = None
        self._voice_raw = bytearray()
        self._voice_failure = ""
        self._recording = False
        self._voice_previous_review = False
        self.recorder = Recorder(self)
        self.speaker = Speaker(self)
        self._capture_generation = 0
        self._capture_slots = []
        self.recorder.finished.connect(self._voice_finished)
        self._raw = bytearray()
        self._network_failure = ""
        self._request_message = ""
        self._request_references = {}
        self._review_required = False
        self._scope = None
        self.resolution = None
        self.messages = []  # Same transcript for typed text and future speech transcription.
        self.setWindowTitle("Assistant IA — GEMAPI-App")
        self.setWindowIcon(QIcon(str(Path(__file__).parent / "icons" / "icon.png")))
        layout = QVBoxLayout(self)
        self.history = QTextBrowser(self)
        self.history.setOpenExternalLinks(False)
        self.history.verticalScrollBar().rangeChanged.connect(self._scroll_to_latest)
        layout.addWidget(self.history, 2)
        self.summary = QLabel("Aucun brouillon pour le moment.", self)
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.PlainText)
        area = QScrollArea(self)
        self.corrections_area = area
        area.setWidgetResizable(True)
        content = QWidget(area)
        self.summary_layout = QVBoxLayout(content)
        self.choices_widget = QWidget(content)
        self.choices_layout = QFormLayout(self.choices_widget)
        self.choices_layout.setContentsMargins(0, 0, 0, 0)
        self.choices_layout.setVerticalSpacing(4)
        self.choices_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.summary_layout.addWidget(self.choices_widget)
        self.reference_boxes = {}
        self.date_edits = {}
        self.family_box = None
        self.apply_button = QPushButton("Valider les corrections ensemble", content)
        self.apply_button.hide()
        self.apply_button.clicked.connect(self._apply_corrections)
        self.summary_layout.addWidget(self.apply_button)
        self.summary_layout.addWidget(self.summary)
        area.setWidget(content)
        layout.addWidget(area, 3)
        self.input = QPlainTextEdit(self)
        self.input.setPlaceholderText("Décrivez la prestation ou corrigez un champ…")
        self.input.setMaximumHeight(100)
        layout.addWidget(self.input)
        controls = QHBoxLayout()
        self.mic_button = QPushButton("🎙 Dicter", self)
        self.mic_button.setToolTip("Enregistrer jusqu'à 60 secondes ; arrêter pour transcrire avec Voxtral.")
        self.mic_button.clicked.connect(self._toggle_microphone)
        controls.addWidget(self.mic_button)
        self.cancel_voice_button = QPushButton("Annuler dictée", self)
        self.cancel_voice_button.setEnabled(False)
        self.cancel_voice_button.clicked.connect(self._cancel_voice)
        controls.addWidget(self.cancel_voice_button)
        self.key_button = QPushButton("Fichier de clé Mistral…", self)
        self.key_button.clicked.connect(self._choose_key)
        controls.addWidget(self.key_button)
        self.send_button = QPushButton("Envoyer", self)
        self.send_button.clicked.connect(self._send)
        controls.addWidget(self.send_button)
        layout.addLayout(controls)
        self.voice_status = QLabel(VOICE_IDLE, self)
        self.voice_status.setWordWrap(True)
        self.voice_status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.voice_status)
        speech_controls = QHBoxLayout()
        self.speech_enabled = QCheckBox("Réponses vocales", self)
        self.speech_enabled.setChecked(QSettings().value(SPEECH_SETTING, False, type=bool))
        self.speech_enabled.setToolTip("Lire une phrase courte vérifiée localement avec Voxtral, sans transmettre les noms du brouillon.")
        speech_controls.addWidget(self.speech_enabled)
        self.replay_button = QToolButton(self)
        self.replay_button.setText("Relire")
        self.replay_button.setAutoRaise(True)
        self.replay_button.setToolTip("Relire la dernière réponse ; réutiliser l'audio en mémoire si disponible.")
        self.replay_button.clicked.connect(self._replay_speech)
        speech_controls.addWidget(self.replay_button)
        self.stop_speech_button = QToolButton(self)
        self.stop_speech_button.setText("Arrêter la lecture")
        self.stop_speech_button.setAutoRaise(True)
        self.stop_speech_button.clicked.connect(self._stop_speech)
        speech_controls.addWidget(self.stop_speech_button)
        speech_controls.addStretch()
        layout.addLayout(speech_controls)
        self.speech_status = QLabel(self)
        self.speech_status.setWordWrap(True)
        self.speech_status.setTextFormat(Qt.PlainText)
        self.speech_status.hide()
        layout.addWidget(self.speech_status)
        self.speaker.status.connect(self._show_speech_status)
        self.speaker.changed.connect(self._sync_speech_controls)
        self.speech_enabled.toggled.connect(self._speech_toggled)
        self.input.textChanged.connect(self._forget_speech)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        self.progress.setFormat("Mistral traite le message…")
        self.progress.hide()
        layout.addWidget(self.progress)
        self.open_button = QPushButton("Ouvrir le formulaire de création prérempli", self)
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open_form)
        bottom = QHBoxLayout()
        self.new_action = QAction("Nouvelle prestation", self)
        self.new_action.setShortcut("Alt+N")
        self.new_action.setShortcutContext(Qt.WindowShortcut)
        self.new_action.setToolTip("Commencer un nouveau brouillon (Alt+N, aussi depuis le formulaire), en conservant la conversation.")
        self.new_action.triggered.connect(self.new_prestation)
        self.addAction(self.new_action)
        self.new_button = QToolButton(self)
        self.new_button.setObjectName("newPrestationButton")
        self.new_button.setDefaultAction(self.new_action)
        self.new_button.setAutoRaise(True)
        bottom.addWidget(self.new_button)
        bottom.addWidget(self.open_button, 1)
        layout.addLayout(bottom)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.voice_timer = QTimer(self)
        self.voice_timer.setSingleShot(True)
        self.corrections_timer = QTimer(self)
        self.corrections_timer.setSingleShot(True)
        self.corrections_timer.timeout.connect(self._scroll_corrections_top)
        self._sync_speech_controls()
        initialize_window(self, "assistant", parent)
        self._append("Assistant IA", "Décrivez une prestation sur un ouvrage hydraulique, un stockage hydraulique "
                     "ou un enfant direct de site (mobilier, cheminement, végétation). Vos messages et les noms du brouillon sont transmis à Mistral. "
                     "Les noms complets des référentiels et des familles patrimoniales autorisées avec leurs parents sont aussi transmis ; les UUID et géométries restent locaux. Vérifiez ensuite le formulaire et cliquez "
                     "vous-même sur Enregistrer. La conversation reste disponible pendant cette session.")

    def _append(self, role, message):
        self.messages.append((role, message))
        # Plain text insertion: neither user input nor model text can inject HTML or links.
        cursor = self.history.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#245d98" if role.startswith("Vous") else "#725400"))
        fmt.setFontWeight(75)
        cursor.insertText(role + "\n", fmt)
        fmt.setFontWeight(50)
        cursor.insertText(message + "\n\n", fmt)
        self.history.setTextCursor(cursor)
        self.history.ensureCursorVisible()
        self._scroll_to_latest()

    def _scroll_to_latest(self, *_):
        bar = self.history.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _choose_key(self):
        path, _ = QFileDialog.getOpenFileName(self, "Fichier local de clé Mistral", "", "Texte (*.txt);;Tous (*)")
        if path:
            # Only a local path is stored in this QGIS profile, never the key itself.
            QSettings().setValue(KEY_PATH_SETTING, path)
            self._append("Assistant IA", "Chemin du fichier de clé enregistré pour ce profil QGIS.")

    def _sync_speech_controls(self):
        available = (self.speech_enabled.isChecked() and bool(self.speaker.text) and
                     self._reply is None and self._voice_reply is None and not self._recording)
        self.replay_button.setEnabled(available and not self.speaker.busy)
        self.stop_speech_button.setEnabled(self.speaker.busy)

    def _show_speech_status(self, text):
        self.speech_status.setText(text)
        self.speech_status.setVisible(bool(text))

    def _speech_toggled(self, enabled):
        QSettings().setValue(SPEECH_SETTING, enabled)
        if not enabled:
            self._stop_speech()
            self.speaker.audio = None
        self._sync_speech_controls()

    def _stop_speech(self):
        self.speaker.stop()
        self._show_speech_status("Lecture arrêtée." if self.speech_enabled.isChecked() else "")

    def _forget_speech(self):
        self.speaker.stop(clear=True)
        self._show_speech_status("")

    def _remember_spoken_result(self):
        self.speaker.set_text(spoken_result(self.draft, self.resolution))
        if self.speech_enabled.isChecked() and self.isVisible():
            self._replay_speech()

    def _replay_speech(self):
        if (not self.speech_enabled.isChecked() or not self.speaker.text or
                self._reply is not None or self._voice_reply is not None or self._recording):
            return
        try:
            key = '' if self.speaker.audio is not None else read_api_key(configured_key_path(QSettings()))
        except ValueError:
            self._show_speech_status("Clé Mistral indisponible pour la réponse vocale. Le texte reste disponible.")
            return
        self.speaker.speak(self.network, key)

    def _send(self):
        self.submit_user_message(self.input.toPlainText())

    def has_draft_data(self):
        return bool(any(value not in (None, "", False) for value in self.draft.names.values()) or
                    self.draft.matches or self.draft.choices or self.input.toPlainText().strip() or
                    self._request_message or any(edit.text().strip() for edit in self.date_edits.values()) or
                    any(box.currentData() or box.currentText() != box.itemText(box.currentIndex())
                        for box in self.reference_boxes.values()) or
                    self.family_box is not None and self.family_box.currentData())

    def confirm_new_prestation(self):
        return QMessageBox.question(
            self, "Nouvelle prestation", "Abandonner le brouillon non enregistré et commencer une nouvelle prestation ?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def new_prestation(self, *_):
        pending = self.has_draft_data()
        if self.before_new is not None:
            if not self.before_new(pending):
                return False
        elif pending and not self.confirm_new_prestation():
            return False
        self._reset_draft("Prestation abandonnée — nouvelle prestation")
        return True

    def prestation_saved(self):
        """Called only by the plugin after the linked standard form actually succeeds."""
        self._reset_draft("Prestation enregistrée — nouvelle prestation")

    def _reset_draft(self, separator):
        self.cancel_request()
        self.draft = Draft()
        self.resolution = None
        self._scope = None
        self._review_required = False
        self._voice_previous_review = False
        self._network_failure = ""
        self._voice_failure = ""
        self._clear_corrections()
        self.input.clear()
        self.summary.setText("Aucun brouillon pour le moment.")
        self.voice_status.setStyleSheet("")
        self.voice_status.setText(VOICE_IDLE)
        self._set_busy(False)
        self._append("Assistant IA", "──────── " + separator + " ────────")
        self.input.setFocus()

    @staticmethod
    def _arm_timer(timer, duration, slot):
        timer.stop()
        try:
            timer.timeout.disconnect()
        except TypeError:
            pass
        timer.timeout.connect(slot)
        timer.start(duration)

    def submit_user_message(self, text):
        """Shared entry point for keyboard input and future speech-to-text messages."""
        if self._reply is not None or self._voice_reply is not None or self._recording or not text.strip():
            return
        self._forget_speech()
        text = text.strip()
        self._review_required = True
        self.open_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self._append("Vous", text)
        try:
            key = read_api_key(configured_key_path(QSettings()))
            # Fresh reference labels from THIS project's connection before every turn.
            catalog = load_catalog(self.session.connection(), None, for_interpretation=True)
            payload = request_payload(self.draft, text, catalog)
            self._request_references = json.loads(payload["messages"][1]["content"])["referentiels"]
        except ValueError as error:
            self._append("Assistant IA", str(error))
            return
        except Exception:
            self._append("Assistant IA", "Référentiels PostgreSQL indisponibles. Aucun message envoyé à Mistral ; le brouillon est conservé.")
            return
        self.input.clear()
        self._request_message = text
        self._set_busy(True)
        self._raw = bytearray()
        self._network_failure = ""
        request = QNetworkRequest(QUrl(ENDPOINT))
        request.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        request.setRawHeader(b"Authorization", ("Bearer " + key).encode("utf-8"))
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.ManualRedirectPolicy)
        request.setTransferTimeout(30000)
        self._reply = self.network.post(
            request, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        reply = self._reply
        reply.readyRead.connect(self._read_response)
        reply.finished.connect(self._finished)
        self._arm_timer(self.timer, 30000, lambda current=reply: self._timeout(current))

    @pyqtSlot()
    def _read_response(self, reply=None):
        reply = reply if reply is not None else self.sender() or self._reply
        if self._reply is None or reply is not None and reply is not self._reply:
            return
        self._raw.extend(bytes(self._reply.readAll()))
        if len(self._raw) > 1024 * 1024 and not self._network_failure:
            self._network_failure = "Réponse Mistral trop volumineuse. Le brouillon est conservé."
            self._reply.abort()

    def _timeout(self, reply=None):
        if self._reply is not None and (reply is None or reply is self._reply):
            self._network_failure = "Délai Mistral dépassé (30 s). Réessayez votre message ; le brouillon est conservé."
            self._reply.abort()

    @pyqtSlot()
    def _finished(self, reply=None):
        reply = reply if reply is not None else self.sender() or self._reply
        if self._reply is None or reply is not None and reply is not self._reply:
            return
        reply = self._reply
        self.timer.stop()
        self._read_response(reply)
        if reply is not self._reply:  # abort() may emit finished synchronously.
            return
        raw, message, references, failure = bytes(self._raw), self._request_message, self._request_references, self._network_failure
        self._reply = None
        self._raw = bytearray()
        self._request_message = ""
        self._request_references = {}
        try:
            status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
            if failure:
                raise ValueError(failure)
            if reply.error() != QNetworkReply.NoError or status != 200:
                # Do not display response bodies, request headers or network error strings.
                raise ValueError("Échec Mistral (réseau, authentification ou service indisponible). "
                                 "Vérifiez la clé et la connexion puis réessayez votre message. Le brouillon est conservé.")
            proposal = parse_response(raw)
            self.draft.apply(proposal, user_message=message, reference_context=references)
            # Free model comments cannot establish that any database write occurred.
            self._append("Mistral — proposition", "Proposition de prestation préparée. Vérifiez le récapitulatif local.")
            self._review_required = False
            self._refresh(log=True)
            self._remember_spoken_result()
        except ValueError as error:
            self._append("Assistant IA", str(error))
        finally:
            reply.deleteLater()
            self._set_busy(self._reply is not None)

    def _set_busy(self, busy):
        voice_busy = self._voice_reply is not None
        self.progress.setVisible(busy or voice_busy)
        self.progress.setFormat("Voxtral transcrit la dictée…" if voice_busy else "Mistral traite le message…")
        for widget in (self.input, self.send_button, self.key_button):
            widget.setEnabled(not busy)
        review_enabled = not busy and not voice_busy and not self._recording and not self._review_required
        self.choices_widget.setEnabled(review_enabled)
        self.apply_button.setEnabled(review_enabled)
        self.send_button.setEnabled(not busy and not voice_busy and not self._recording)
        self.mic_button.setEnabled(not busy and not voice_busy and (self._recording or self.recorder.thread is None))
        self.mic_button.setText("■ Arrêter et transcrire" if self._recording else "🎙 Dicter")
        self.cancel_voice_button.setEnabled(self._recording or voice_busy)
        self.cancel_voice_button.setText("Interrompre Voxtral" if voice_busy else "Annuler dictée")
        self.open_button.setEnabled(not busy and not voice_busy and not self._recording and not self._review_required and
                                    bool(self.resolution and self.resolution["values"]))
        self._sync_speech_controls()

    def _toggle_microphone(self):
        if self._recording:
            self.recorder.stop()
            self.mic_button.setEnabled(False)
            self.voice_status.setText("Arrêt du microphone et préparation du WAV…")
            return
        if self._reply is not None or self._voice_reply is not None or self.recorder.thread is not None:
            return
        self._forget_speech()
        self._voice_previous_review = self._review_required
        self._review_required = True
        self._recording = True
        self.voice_status.setText("Initialisation du microphone… Annuler permet de poursuivre au clavier.")
        self.voice_status.setStyleSheet("color: #a32121; font-weight: bold;")
        self._set_busy(False)
        self._connect_capture_signals()
        self.recorder.start()

    def _invalidate_capture(self):
        self._capture_generation += 1
        for signal, slot in self._capture_slots:
            signal.disconnect(slot)
        self._capture_slots.clear()

    def _connect_capture_signals(self):
        self._invalidate_capture()
        generation = self._capture_generation
        for signal, method in ((self.recorder.ready, self._voice_ready),
                               (self.recorder.failed, self._voice_error),
                               (self.recorder.status, self._voice_status)):
            slot = lambda value, target=method, current=generation: self._capture_event(current, target, value)
            signal.connect(slot)
            self._capture_slots.append((signal, slot))

    def _capture_event(self, generation, method, value):
        if not sip.isdeleted(self) and generation == self._capture_generation:
            method(value)

    def _voice_status(self, message):
        if self._recording:
            self.voice_status.setText(message)

    def _voice_error(self, message):
        if not self._recording:
            return
        self._recording = False
        self.voice_status.setStyleSheet("")
        self.voice_status.setText(message)
        self._append("Assistant IA — dictée", message)
        self._set_busy(self._reply is not None)

    def _voice_finished(self):
        self._set_busy(self._reply is not None)

    def _voice_ready(self, audio):
        if not self._recording:  # A cancelled recording must never send audio.
            return
        self._forget_speech()
        self._recording = False
        self.voice_status.setStyleSheet("")
        try:
            key = read_api_key(configured_key_path(QSettings()))
            vocabulary = context_vocabulary(self.draft.names, self.resolution)
            data, content_type = transcription_multipart(audio, vocabulary)
        except ValueError as error:
            self.voice_status.setText(str(error))
            self._append("Assistant IA — dictée", str(error))
            self._set_busy(self._reply is not None)
            return
        self._voice_raw = bytearray()
        self._voice_failure = ""
        request = QNetworkRequest(QUrl(TRANSCRIPTION_ENDPOINT))
        request.setHeader(QNetworkRequest.ContentTypeHeader, content_type)
        request.setRawHeader(b"Authorization", ("Bearer " + key).encode("utf-8"))
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.ManualRedirectPolicy)
        request.setTransferTimeout(60000)
        self._voice_reply = self.network.post(request, data)
        reply = self._voice_reply
        reply.readyRead.connect(self._read_voice_response)
        reply.finished.connect(self._voice_response_finished)
        self._arm_timer(self.voice_timer, 60000, lambda current=reply: self._voice_timeout(current))
        self.voice_status.setText("Transcription Voxtral en cours. Le vocabulaire est limité au brouillon, à quelques candidats et aux termes métier (24 maximum).")
        self._set_busy(self._reply is not None)

    @pyqtSlot()
    def _read_voice_response(self, reply=None):
        reply = reply if reply is not None else self.sender() or self._voice_reply
        if self._voice_reply is not None and (reply is None or reply is self._voice_reply):
            self._voice_raw.extend(bytes(self._voice_reply.readAll()))
            if len(self._voice_raw) > 1024 * 1024 and not self._voice_failure:
                self._voice_failure = "Réponse Voxtral trop volumineuse. Poursuivez au clavier."
                self._voice_reply.abort()

    def _voice_timeout(self, reply=None):
        if self._voice_reply is not None and (reply is None or reply is self._voice_reply):
            self._voice_failure = "Délai Voxtral dépassé (60 s). La conversation est conservée ; vous pouvez poursuivre au clavier."
            self._voice_reply.abort()

    @pyqtSlot()
    def _voice_response_finished(self, reply=None):
        reply = reply if reply is not None else self.sender() or self._voice_reply
        if self._voice_reply is None or reply is not None and reply is not self._voice_reply:
            return
        reply = self._voice_reply
        self.voice_timer.stop()
        self._read_voice_response(reply)
        if reply is not self._voice_reply:
            return
        raw, failure = bytes(self._voice_raw), self._voice_failure
        self._voice_reply = None
        self._voice_raw = bytearray()
        try:
            status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
            if failure:
                raise ValueError(failure)
            if reply.error() != QNetworkReply.NoError or status != 200:
                raise ValueError(transcription_error(status))
            text = parse_transcription(raw)
            self._append("Vous — dictée à vérifier (Voxtral)", text)
            previous = self.input.toPlainText().rstrip()
            self.input.setPlainText(previous + "\n" + text if previous else text)
            self.input.setFocus()
            self.voice_status.setText("Transcription visible ci-dessus et modifiable dans la saisie. Corrigez-la puis cliquez sur Envoyer pour Mistral Small.")
        except ValueError as error:
            self.voice_status.setText(str(error))
            self._append("Assistant IA — dictée", str(error))
        finally:
            reply.deleteLater()
            self._set_busy(self._reply is not None)

    def _cancel_voice(self):
        was_recording = self._recording
        self._recording = False
        self._invalidate_capture()
        self.recorder.cancel()
        self.voice_timer.stop()
        if self._voice_reply is not None:
            reply, self._voice_reply = self._voice_reply, None
            reply.abort()
            reply.deleteLater()
        self._voice_raw.clear()
        self._review_required = self._voice_previous_review
        self.voice_status.setStyleSheet("")
        message = "Dictée annulée, aucun audio envoyé." if was_recording else "Transcription interrompue. Vous pouvez poursuivre au clavier."
        self.voice_status.setText(message)
        self._set_busy(self._reply is not None)

    def _refresh(self, log=False):
        self.resolution = None
        self.open_button.setEnabled(False)
        self._clear_corrections()
        try:
            connection = self.session.connection()
            if self._scope != self.session.scope_generation:
                self.draft.choices.clear()
                self._scope = self.session.scope_generation
            catalog = load_catalog(connection, self.draft.names.get("famille"))
            self.resolution = self.draft.resolve(catalog)
        except Exception:
            self._append("Assistant IA", "Référentiels PostgreSQL indisponibles. Chargez le projet et vérifiez les droits SELECT.")
            return
        lines = []
        for key, label in LABELS.items():
            if self.draft.names.get(key):
                row = self.resolution["verified"].get(key)
                value = row[1] + (" — " + row[2] if row[2] else "") if row else self.draft.names[key]
                if key == "date_fin" and self.draft.end_date_needs_confirmation:
                    value += " (proposée, à confirmer)"
                elif key == "date_fin" and (self.draft.date_evidence_kind == 'previsionnelle' or
                        self.draft.date_evidence_kind == 'periode' and
                        self.resolution['verified'].get('statut', ('', '', ''))[1] in ('Planifié', 'En cours')):
                    value += " (prévue)"
                lines.append(label.capitalize() + " : " + value)
        lines.extend(self.resolution["issues"])
        if self.resolution["values"]:
            lines.append("Brouillon complet. Géométrie propre : NULL. Ouvrez le formulaire pour vérifier et enregistrer.")
        self.summary.setText("\n".join(lines))
        self._build_corrections(catalog)
        self.corrections_area.verticalScrollBar().setValue(0)
        self.corrections_timer.start(0)
        self._set_busy(self._reply is not None)
        if log:
            self._append("Assistant IA — vérification locale", "\n".join(lines))

    @pyqtSlot()
    def _scroll_corrections_top(self):
        self.corrections_area.verticalScrollBar().setValue(0)

    def _clear_corrections(self):
        self.reference_boxes = {}
        self.date_edits = {}
        self.family_box = None
        self.apply_button.hide()
        while self.choices_layout.rowCount():
            row = self.choices_layout.takeRow(0)
            for item in (row.labelItem, row.fieldItem):
                widget = item.widget() if item is not None else None
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()

    def _correction_label(self, text, field=None):
        label = QLabel(text, self.choices_widget)
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(field is None)
        if field is None:
            self.choices_layout.addRow(label)
        else:
            self.choices_layout.addRow(label, field)

    def _fill_reference_box(self, key, combo, catalog):
        combo.clear()
        combo.addItem("— Choisir : " + LABELS[key] + " —", None)
        rows = self.resolution["pending"].get(key, [])
        other_rows = sorted(catalog.get(key, []), key=lambda row: (row[1].casefold(), row[2].casefold(), row[0]))
        # Empty or unknown names still offer a searchable list of actual active values.
        if not rows:
            rows = other_rows
            other_rows = []
        else:
            suggested_ids = {row[0] for row in rows}
            other_rows = [row for row in other_rows if row[0] not in suggested_ids]
        for uid, label, context in rows:
            combo.addItem(label + (" — " + context if context else ""), uid)
            combo.setItemData(combo.count() - 1, uid, Qt.ToolTipRole)
        if other_rows:
            combo.insertSeparator(combo.count())
            for uid, label, context in other_rows:
                combo.addItem(label + (" — " + context if context else ""), uid)
                combo.setItemData(combo.count() - 1, uid, Qt.ToolTipRole)
        combo.setCurrentIndex(0)  # Ranking is a suggestion, never a selected identity.

    def _build_corrections(self, catalog):
        if self.resolution["values"]:
            return
        self._correction_label("Complétez les champs ci-dessous, puis validez ensemble.")
        family = self.draft.names.get("famille")
        family_labels = {key: TARGETS[key].label for key in ALLOWED_FAMILIES}
        if family not in ALLOWED_FAMILIES:
            self.family_box = QComboBox(self.choices_widget)
            self.family_box.addItem("— Choisir la famille —", None)
            for key, label in family_labels.items():
                self.family_box.addItem(label, key)
            self._correction_label("Famille patrimoniale", self.family_box)
            box = self.family_box
            box.currentIndexChanged.connect(lambda index, current=box: self._correction_family_changed(index, current))
        for key in (*REFERENCES, "patrimoine"):
            if key in self.resolution["verified"]:
                continue
            label = LABELS[key].capitalize()
            if key == "patrimoine" and family in ALLOWED_FAMILIES:
                label = family_labels[family]
            combo = QComboBox(self.choices_widget)
            combo.setObjectName("correction_" + key)
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.NoInsert)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(20)
            combo.setMaxVisibleItems(12)
            combo.completer().setFilterMode(Qt.MatchContains)
            combo.completer().setCaseSensitivity(Qt.CaseInsensitive)
            self._fill_reference_box(key, combo, catalog)
            self.reference_boxes[key] = combo
            self._correction_label(label, combo)
            combo.currentTextChanged.connect(self._forget_speech)
        for key in ("date_debut", "date_fin"):
            if self.draft.names.get(key) and not (key == "date_fin" and self.draft.end_date_needs_confirmation):
                continue
            label = LABELS[key].capitalize()
            if key == "date_fin" and self.draft.end_date_needs_confirmation:
                label += " proposée — à confirmer ou corriger"
            edit = QLineEdit(self.choices_widget)
            edit.setObjectName("correction_" + key)
            edit.setPlaceholderText("JJ/MM/AAAA ou 11 novembre 2026")
            edit.setText(self.draft.names.get(key, ""))  # Missing dates stay empty, never today's/start date.
            self.date_edits[key] = edit
            self._correction_label(label, edit)
            edit.textChanged.connect(self._forget_speech)
        self.apply_button.setVisible(bool(self.reference_boxes or self.date_edits or self.family_box))

    def _correction_family_changed(self, index, box=None):
        if self.family_box is None or box is not None and box is not self.family_box:
            return
        family = self.family_box.itemData(index)
        if not family or "patrimoine" not in self.reference_boxes:
            return
        self._forget_speech()
        try:
            catalog = load_catalog(self.session.connection(), family)
            # Re-rank places only in the explicitly selected family, keeping the other staged inputs.
            box = self.reference_boxes["patrimoine"]
            options, _exact = candidates(self.draft.names.get("patrimoine", ""), catalog.get("patrimoine", []))
            self.resolution["pending"].pop("patrimoine", None)
            if options:
                self.resolution["pending"]["patrimoine"] = options
            self._fill_reference_box("patrimoine", box, catalog)
        except Exception:
            self.reference_boxes["patrimoine"].clear()
            self._append("Assistant IA", "Objets de cette famille indisponibles. Vérifiez la connexion PostgreSQL.")

    def _apply_corrections(self):
        if self.resolution is None or self._reply is not None or self._voice_reply is not None or self._recording or self._review_required:
            return
        self._forget_speech()
        catalog, references, dates = None, {}, {}
        try:
            family = self.family_box.currentData() if self.family_box is not None else self.draft.names.get("famille")
            if family not in ALLOWED_FAMILIES:
                raise ValueError("Choisissez une famille patrimoniale autorisée.")
            references = {}
            for key, combo in self.reference_boxes.items():
                if not combo.currentData() or combo.currentText() != combo.itemText(combo.currentIndex()):
                    raise ValueError("Sélectionnez une valeur de la liste : " + LABELS[key] + ".")
                references[key] = combo.currentData()
            dates = {key: edit.text().strip() for key, edit in self.date_edits.items()}
            connection = self.session.connection()
            if self._scope != self.session.scope_generation:
                self._refresh(log=True)
                raise ValueError("La connexion a changé. Réexaminez les corrections avant validation.")
            catalog = load_catalog(connection, family)
            self.resolution = self.draft.correct(references, dates, catalog, family)
        except ValueError as error:
            self._append("Assistant IA", str(error))
            if catalog is not None:
                # Show every newly uncertain field, retaining uncommitted inputs that still exist.
                self._refresh()
                for key, value in dates.items():
                    if key in self.date_edits:
                        self.date_edits[key].setText(value)
                for key, identifier in references.items():
                    if key in self.reference_boxes:
                        index = self.reference_boxes[key].findData(identifier)
                        if index >= 0:
                            self.reference_boxes[key].setCurrentIndex(index)
            return
        except Exception:
            self._append("Assistant IA", "Référentiels PostgreSQL indisponibles. Aucune correction appliquée ; le brouillon est conservé.")
            return
        lines = [LABELS[key].capitalize() + " : " + self.draft.names[key] for key in (*references, *dates)]
        if self.family_box is not None:
            lines.insert(0, "Famille : " + family)
        self._append("Vous — corrections validées", "\n".join(lines))
        self._refresh(log=True)
        self._remember_spoken_result()

    def _open_form(self):
        if self._reply is not None or self._review_required:
            return
        # Refresh against the CURRENT database and role before opening a form.
        self._refresh()
        if not self.resolution or not self.resolution["values"]:
            return
        try:
            self.open_draft(self.resolution["values"], self.resolution["associations"])
        except ValueError as error:
            self._append("Assistant IA", str(error))
            return
        except Exception:
            self._append("Assistant IA", "Impossible d'ouvrir le formulaire. Vérifiez la connexion PostgreSQL et les droits.")
            return
        self._append("Assistant IA", "Formulaire ouvert sans enregistrement. La conversation reste disponible.")
        self.hide()

    def cancel_request(self):
        self._forget_speech()
        if self._recording or self._voice_reply is not None or self.recorder.thread is not None:
            self._cancel_voice()
        else:
            self._invalidate_capture()
        self.timer.stop()
        if self._reply is not None:
            reply, self._reply = self._reply, None
            reply.abort()
            reply.deleteLater()
        self._raw.clear()
        self._request_message = ""
        self._request_references = {}
        self._set_busy(False)

    def reject(self):
        self.cancel_request()
        super().reject()  # Kept alive by the plugin; transcript is not deleted.

    def closeEvent(self, event):
        self.cancel_request()
        super().closeEvent(event)

    def hideEvent(self, event):
        self._forget_speech()
        super().hideEvent(event)
