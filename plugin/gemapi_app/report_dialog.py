"""Direct PDF export with read-only count and an isolated QGIS renderer."""

import json
import os
import tempfile
import traceback
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from qgis.PyQt import uic
from qgis.PyQt.QtCore import QProcess, QProcessEnvironment, Qt, QTimer, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QLabel, QMessageBox, QProgressBar, QVBoxLayout,
)
from qgis.core import QgsApplication, QgsMessageLog, QgsProject, Qgis

from .report_data import (
    GROUPS, PATRIMONY_GROUPS, REPORT_FILTERS, PATRIMONY, build_report_query,
    filter_option_queries, normalized_filters, prepare_rows, report_warnings,
    resolve_report_editor,
)
from .perf import ENABLED, measure, profiled
from .filter_picker import FilterPicker
from .pdf_runtime import select_python, worker_environment
from .session_data import connection_from_project, connection_login
from .window_geometry import initialize_window


class ExportProgress(QDialog):
    """Nonmodal status window; export has no unsafe mid-render cancellation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.allow_close = False
        self.setWindowTitle("Génération du rapport")
        layout = QVBoxLayout(self)
        self.message = QLabel("Lecture des prestations…", self)
        self.bar = QProgressBar(self)
        self.bar.setRange(0, 0)
        self._export_base = None
        self._activity_text = ""
        self._activity_ticks = 0
        self._activity = QTimer(self)
        self._activity.setInterval(500)
        self._activity.timeout.connect(self._show_activity)
        layout.addWidget(self.message)
        layout.addWidget(self.bar)
        self.resize(430, 110)

    def update_stage(self, stage, current=0, total=0):
        self._activity.stop()
        preparing = stage in ("Mise en page des prestations", "Mise en page des groupes")
        if preparing and total:
            self.message.setText(f"{stage} — {current}/{total}")
            self.bar.setRange(0, 100)
            self.bar.setValue(min(45, 10 + 35 * current // total))
        elif stage == "Rendu cartographique" and total:
            self._activity_text = ("Rendu cartographique et export PDF\n"
                                   f"{current}/{total} cartes rendues")
            self._activity_ticks = 0
            self.bar.setRange(0, 100)
            self.bar.setValue(min(85, 45 + 40 * current // total))
            self._show_activity()
            self._activity.start()
        elif stage.startswith("Rendu cartographique et export PDF") and total:
            if self._export_base is None:
                self._export_base = max(45, self.bar.value()) if self.bar.maximum() == 100 else 45
            heading, detail = stage.split(" — ", 1)
            self._activity_text = f"{heading}\n{detail[0].upper()}{detail[1:]}"
            self._activity_ticks = 0
            self.bar.setRange(0, 100)
            self.bar.setValue(min(95, self._export_base +
                                  (95 - self._export_base) * current // total))
            self._show_activity()
            self._activity.start()
        elif stage == "Finalisation du document":
            self._activity_text = stage
            self._activity_ticks = 0
            self.bar.setRange(0, 100)
            self.bar.setValue(95)
            self._show_activity()
            self._activity.start()
        elif stage == "Terminé":
            self.message.setText("Rapport généré")
            self.bar.setRange(0, 100)
            self.bar.setValue(100)
        else:
            self.message.setText(stage)
            self.bar.setRange(0, 0)

    def _show_activity(self):
        self._activity_ticks = (self._activity_ticks % 3) + 1
        self.message.setText(self._activity_text + "." * self._activity_ticks)

    def closeEvent(self, event):
        if not self.allow_close:
            event.ignore()
            return
        self._activity.stop()
        super().closeEvent(event)


def _complete_pdf(path):
    """Reject an interrupted worker output before replacing the destination."""
    try:
        size = path.stat().st_size
        if size < 16:
            return False
        with path.open("rb") as pdf:
            if pdf.read(5) != b"%PDF-":
                return False
            pdf.seek(max(0, size - 4096))
            return b"%%EOF" in pdf.read()
    except OSError:
        return False


class ReportDialog(QDialog):
    @profiled("report.construct")
    def __init__(self, iface, parent=None, session=None):
        super().__init__(parent)
        uic.loadUi(str(Path(__file__).with_name("report.ui")), self)
        self.iface = iface
        self.session = session
        self._loaded_scope = None
        initialize_window(self, "report", parent)
        self.connection = None
        self.match_count = None
        self.other_statuses = ()
        self._busy = False
        self._process = None
        self._progress = None
        self._temporary = None
        self._partial = None
        self._destination = None
        self._worker_buffer = ""
        self._worker_stderr = ""
        self._worker_error = None
        self._worker_pages = None
        self._worker_started = None
        self._count_timer = QTimer(self)
        self._count_timer.setSingleShot(True)
        self._count_timer.timeout.connect(self.refresh_count)
        names = {"years": "Années", "technicians": "Techniciens", "communes": "Communes",
                 "types": "Types de prestation", "statuses": "Statuts", "providers": "Prestataires",
                 "ah": "Aménagements hydrauliques", "site_terrestre": "Sites terrestres"}
        names.update({key: definition[0] for key, definition in PATRIMONY.items()})
        names["cours_eau"] = "Cours d'eau"
        self.pickers = {}
        for key in REPORT_FILTERS:
            picker = FilterPicker(names[key], self, compact_title=True)
            self.pickers[key] = picker
            picker.list.setMinimumHeight(70)
            picker.list.setMaximumHeight(90)
            picker.list.itemChanged.connect(self._invalidate)
        self._filter_columns = None
        self._arrange_filters()
        for label, value in (("Toutes", "all"), ("Complétées", "completed"),
                             ("Non complétées", "incomplete")):
            self.completionCombo.addItem(label, value)
        self.group_combos = (self.group1Combo, self.group2Combo, self.group3Combo)
        self._synthetic_groups = ()
        for column in range(4):
            self.groupGrid.setColumnStretch(column, 1)
        self.reportKindCombo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.reportKindCombo.setMinimumContentsLength(14)
        for combo in self.group_combos:
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
            combo.addItem("Aucun regroupement", None)
            for value, label in GROUPS.items():
                combo.addItem(label, value)
            combo.currentIndexChanged.connect(self._group_changed)
        self.completionCombo.currentIndexChanged.connect(self._invalidate)
        self.reportKindCombo.addItem("Fiches de prestations", "detailed")
        self.reportKindCombo.addItem("Rapport synthétique", "synthetic")
        self.reportKindCombo.currentIndexChanged.connect(self._report_kind_changed)
        self.mapBackgroundCombo.addItem("IGN Plan Épuré", "epure")
        self.mapBackgroundCombo.addItem("IGN Orthophotographies", "ortho")
        self.mapBackgroundCombo.currentIndexChanged.connect(self._invalidate)
        self.mapBackgroundCombo.setCurrentIndex(0)
        self.exportButton.clicked.connect(self.export)
        self.backButton.clicked.connect(self.reject)
        self.exportButton.setEnabled(False)
        self._report_kind_changed()
        self._load_options()
        if self.connection is not None:
            self._count_timer.start(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "pickers") and len(self.pickers) == len(REPORT_FILTERS):
            self._arrange_filters()

    def _arrange_filters(self):
        columns = 4 if self.width() >= 950 else 3 if self.width() >= 700 else 2
        picker_width = max(160, (self.width() - 70 - (columns - 1) * 6) // columns)
        for picker in self.pickers.values():
            picker.setFixedWidth(picker_width)
        if self._filter_columns == columns:
            return
        self._filter_columns = columns
        for picker in self.pickers.values():
            self.filterGrid.removeWidget(picker)
        for index, picker in enumerate(self.pickers.values()):
            self.filterGrid.addWidget(picker, index // columns, index % columns)

    def _report_kind_changed(self, *_):
        synthetic = self.reportKindCombo.currentData() == "synthetic"
        if synthetic:
            for combo, value in zip(self.group_combos,
                                    (*self._synthetic_groups, None, None, None)):
                combo.blockSignals(True)
                combo.setCurrentIndex(max(0, combo.findData(value)))
                combo.blockSignals(False)
        else:
            self._synthetic_groups = tuple(combo.currentData() for combo in self.group_combos
                                           if combo.currentData())
            for combo in self.group_combos:
                combo.blockSignals(True)
                combo.setCurrentIndex(0)
                combo.blockSignals(False)
        self._refresh_group_controls()
        self._invalidate()

    def _refresh_group_controls(self):
        synthetic = self.reportKindCombo.currentData() == "synthetic"
        allowed = GROUPS
        selected = {combo.currentData() for combo in self.group_combos if combo.currentData()}
        for level, combo in enumerate(self.group_combos):
            previous = level == 0 or self.group_combos[level - 1].currentData() is not None
            combo.setEnabled(synthetic and not self._busy and previous)
            others = selected - {combo.currentData()}
            for index in range(1, combo.count()):
                value = combo.itemData(index)
                compatible = value not in PATRIMONY_GROUPS or not others.intersection(PATRIMONY_GROUPS)
                combo.model().item(index).setEnabled(value in allowed and value not in others and compatible)

    @profiled("report.connection")
    def _connection(self):
        if self.session is not None:
            return self.session.connection()
        return connection_from_project()

    @profiled("report.filters")
    def _load_options(self):
        try:
            self.connection = self._connection()
            def preflight():
                available = self.connection.executeSql(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema='gemapi' AND table_name='prestations' "
                    "AND column_name='statut_id'")
                if int(available[0][0]) != 1:
                    raise ValueError("La colonne statut_id est absente du schéma courant.")
                pending = self.connection.executeSql(
                    "SELECT count(*) FROM gemapi.prestations WHERE statut_id IS NULL "
                    "OR type_prestation_id IS NULL OR prestataire_id IS NULL "
                    "OR date_debut IS NULL OR date_fin IS NULL OR date_fin < date_debut")
                if int(pending[0][0]):
                    raise ValueError(
                        f"{int(pending[0][0])} prestation(s) incomplète(s) pour le rapport.")
                return True
            preflight()
            for key, query in filter_option_queries(include_statuses=True,
                                                    include_providers=True).items():
                if key not in self.pickers:
                    continue
                rows = (self.session.get(
                    f"filters.{key}", lambda q=query: tuple(self.connection.executeSql(q)))
                    if self.session is not None else self.connection.executeSql(query))
                self.pickers[key].fill(((row[0], row[0]) if key == "years" else row[:2]
                                        for row in rows))
            self.other_statuses = ()
            self._loaded_scope = (self.session.scope_generation
                                  if self.session is not None else None)
            self.statusLabel.setText("Filtres chargés depuis PostgreSQL. Lecture seule.")
        except ValueError as error:
            self.connection = None
            self.statusLabel.setText(str(error))
        except Exception:
            QgsMessageLog.logMessage(f"Chargement des filtres du rapport :\n{traceback.format_exc()}", "GEMAPI-App", Qgis.Critical)
            self.connection = None
            self.statusLabel.setText(
                "Impossible de lire les référentiels PostgreSQL avec la connexion QGIS actuelle.")

    def _invalidate(self, *_):
        self.match_count = None
        self.exportButton.setEnabled(False)
        self.summaryLabel.setText("Calcul des prestations correspondantes…")
        if self.connection is not None and not self._busy:
            self._count_timer.start(250)

    def _group_changed(self, *_):
        if self.reportKindCombo.currentData() != "synthetic":
            return
        selected = []
        empty_predecessor = False
        allowed = GROUPS
        for combo in self.group_combos:
            value = combo.currentData()
            incompatible = value in PATRIMONY_GROUPS and PATRIMONY_GROUPS.intersection(selected)
            if empty_predecessor or value in selected or (value and value not in allowed) or incompatible:
                combo.blockSignals(True)
                combo.setCurrentIndex(0)
                combo.blockSignals(False)
                value = None
            if value is None:
                empty_predecessor = True
            else:
                selected.append(value)
        self._synthetic_groups = tuple(selected)
        self._refresh_group_controls()
        self._invalidate()

    def filters(self):
        values = {key: picker.values() for key, picker in self.pickers.items()}
        values["completion"] = self.completionCombo.currentData()
        values["groups"] = (tuple(combo.currentData() for combo in self.group_combos
                                  if combo.currentData())
                            if self.reportKindCombo.currentData() == "synthetic" else ())
        return values

    def filter_summary(self):
        parts = []
        for key, picker in self.pickers.items():
            if picker.values():
                parts.append(picker.title + " : " + ", ".join(picker.labels()))
        if self.completionCombo.currentData() != "all":
            parts.append("Achèvement : " + self.completionCombo.currentText())
        groups = [GROUPS[value] for value in self.filters()["groups"]]
        parts.append("Regroupements : " + " > ".join(groups) if groups else
                     "Regroupements : aucun")
        parts.append("Cartes : " + self.mapBackgroundCombo.currentText())
        return "; ".join(parts) if parts else "Aucun filtre"

    @profiled("report.count")
    def refresh_count(self):
        if self.connection is None or self._busy:
            return
        try:
            if self.session is not None:
                self.connection = self.session.connection()
                if self._loaded_scope != self.session.scope_generation:
                    self._load_options()
                    if self.connection is None:
                        return
            filters = normalized_filters(self.filters())
            rows = self.connection.executeSql(build_report_query(filters, count_only=True))
            count = int(rows[0][0])
        except ValueError as error:
            self.statusLabel.setText(str(error))
            self.summaryLabel.setText("Effectif indisponible.")
            return
        except Exception:
            QgsMessageLog.logMessage(f"Comptage du rapport :\n{traceback.format_exc()}", "GEMAPI-App", Qgis.Critical)
            self.statusLabel.setText("Le comptage PostgreSQL a échoué ; vérifiez les droits SELECT.")
            self.summaryLabel.setText("Effectif indisponible.")
            return
        self.match_count = count
        self.summaryLabel.setText(f"{count} prestation(s) distincte(s) correspondent à la sélection.")
        self.statusLabel.setText("Aucune prestation correspondante." if count == 0 else
                                 "Choisissez Générer le PDF pour sélectionner un fichier de sortie.")
        self.exportButton.setEnabled(count > 0)

    @profiled("report.prepare_export")
    def export(self, *_):
        if self._busy or self.connection is None:
            return
        if self.match_count is None:
            self.refresh_count()
        if not self.match_count:
            return
        report_kind = self.reportKindCombo.currentData()
        proposed_name = ("fiches_prestations.pdf" if report_kind == "detailed"
                         else "rapport_synthetique.pdf")
        destination, _ = QFileDialog.getSaveFileName(
            self, "Générer le rapport des prestations", proposed_name,
            "Fichiers PDF (*.pdf)", options=QFileDialog.DontConfirmOverwrite)
        if not destination:
            return
        path = Path(destination)
        if path.suffix.lower() != ".pdf":
            path = path.with_suffix(".pdf")
        self._overwrite_confirmed = False
        if path.exists():
            if QMessageBox.question(
                    self, "Remplacer le PDF ?",
                    f"Le fichier « {path.name} » existe déjà. Le remplacer ?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            self._overwrite_confirmed = True
        self._destination = path
        self._filters = normalized_filters(self.filters())
        self._summary = self.filter_summary()
        self._report_kind = report_kind
        self._include_maps = True
        self._map_background = self.mapBackgroundCombo.currentData()
        self._busy = True
        self._set_controls_enabled(False)
        self._progress = ExportProgress(self)
        self._progress.show()
        self._progress.update_stage("Lecture des données")
        self.statusLabel.setText("Génération du rapport en cours…")
        QTimer.singleShot(0, self._load_records_and_start)

    def _set_controls_enabled(self, enabled):
        for widget in (self.filterScroll, self.completionCombo, self.reportKindCombo,
                       self.mapBackgroundCombo,
                       self.exportButton, self.backButton):
            widget.setEnabled(enabled)
        self._refresh_group_controls()

    @profiled("report.load_and_start")
    def _load_records_and_start(self):
        if not self._busy:
            return
        try:
            if self.session is not None:
                self.connection = self.session.connection()
                if self._loaded_scope != self.session.scope_generation:
                    raise ValueError("La connexion PostgreSQL a changé ; rouvrez le rapport.")
            with measure("report.sql.fetch"):
                query = build_report_query(self._filters, include_geometry=self._include_maps)
                raw_rows = self.connection.executeSql(query)
            with measure("report.python.prepare_rows"):
                records = prepare_rows(raw_rows)
            if not records:
                raise ValueError("Aucune prestation ne correspond plus aux filtres choisis.")
            self.match_count = len(records)
            self.summaryLabel.setText(
                f"{len(records)} prestation(s) distincte(s) correspondent à la sélection.")
            warnings = report_warnings(records, self._filters, self.other_statuses)
            self._editor_login, self._editor_name = resolve_report_editor(
                self.connection, connection_login(
                    self.session.project if self.session is not None else QgsProject.instance()))
            self._start_worker(records, warnings)
        except ValueError as error:
            self._finish_failure(str(error))
        except Exception:
            QgsMessageLog.logMessage(f"Lecture des prestations du rapport :\n{traceback.format_exc()}", "GEMAPI-App", Qgis.Critical)
            self._finish_failure("La lecture PostgreSQL du rapport a échoué ; vérifiez les droits SELECT.")

    def _python_executable(self):
        return select_python(QgsApplication.prefixPath())

    @profiled("report.worker.start")
    def _start_worker(self, records, warnings):
        program = self._python_executable()
        self._temporary = tempfile.TemporaryDirectory(prefix="sig-prestations-report-")
        payload_path = Path(self._temporary.name) / "rapport.json"
        payload_path.write_text(json.dumps({
            "records": records, "filters": self._filters, "summary": self._summary,
            "warnings": warnings,
            "include_maps": self._include_maps, "map_background": self._map_background,
            "report_kind": self._report_kind, "editor_login": self._editor_login,
            "editor_name": self._editor_name,
        }, ensure_ascii=False), encoding="utf-8")
        self._partial = self._destination.with_name(
            self._destination.stem + ".part-" + uuid4().hex + ".pdf")
        self._worker_error = None
        self._worker_pages = None
        self._worker_python = None
        self._worker_buffer = ""
        process = QProcess(self)
        environment = QProcessEnvironment()
        for name, value in worker_environment(QgsApplication.prefixPath(), program).items():
            environment.insert(name, value)
        if not environment.value("SIG_PRESTATIONS_PHOTOS_DIR"):
            from .photo_paths import photo_root
            root = photo_root()
            if root:
                environment.insert("SIG_PRESTATIONS_PHOTOS_DIR", str(root))
        from .photo_paths import project_file
        source = project_file()
        if source:
            environment.insert("SIG_PRESTATIONS_PROJECT_FILE", str(source))
        process.setProcessEnvironment(environment)
        process.readyReadStandardOutput.connect(self._read_worker_output)
        process.readyReadStandardError.connect(self._read_worker_stderr)
        process.finished.connect(self._worker_finished)
        process.errorOccurred.connect(self._process_error)
        self._process = process
        self._progress.update_stage("Démarrage du moteur PDF")
        self._worker_started = perf_counter()
        process.start(program, [str(Path(__file__).with_name("report_worker.py")),
                                str(payload_path), str(self._partial)])

    def _read_worker_output(self):
        if self._process is None:
            return
        self._worker_buffer += bytes(self._process.readAllStandardOutput()).decode("utf-8", "replace")
        while "\n" in self._worker_buffer:
            line, self._worker_buffer = self._worker_buffer.split("\n", 1)
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if "stage" in message:
                self._progress.update_stage(message["stage"],
                                            message.get("current", 0), message.get("total", 0))
            elif "error" in message:
                self._worker_error = str(message["error"])
            elif "python" in message:
                self._worker_python = str(message["python"])
            elif "font_diagnostic" in message:
                QgsMessageLog.logMessage(
                    "Polices du moteur PDF : " + json.dumps(
                        message["font_diagnostic"], ensure_ascii=False),
                    "GEMAPI-App", Qgis.Info)
            elif "perf" in message:
                detail = message["perf"]
                if isinstance(detail, str):
                    suffix = (f" total_ms={float(message['ms']):.2f}"
                              if isinstance(message.get("ms"), (int, float)) else "")
                    QgsMessageLog.logMessage(
                        detail + suffix,
                        "SIG-PERF", Qgis.Info)
            elif "done" in message:
                self._worker_pages = int(message["done"])

    def _read_worker_stderr(self):
        if self._process is not None:
            self._worker_stderr += bytes(
                self._process.readAllStandardError()).decode("utf-8", "replace")

    def _process_error(self, error):
        if self._busy and error == QProcess.FailedToStart:
            QgsMessageLog.logMessage(
                "Démarrage du moteur PDF : " + self._process.errorString(),
                "GEMAPI-App", Qgis.Critical)
            self._finish_failure(
                "Impossible de démarrer le moteur PDF. Vérifiez l'installation QGIS."
            )

    @profiled("report.worker.finish")
    def _worker_finished(self, exit_code, exit_status):
        if not self._busy:
            return
        self._read_worker_output()
        self._read_worker_stderr()
        if self._worker_started is not None and ENABLED:
            QgsMessageLog.logMessage(
                f"report.worker.wall total_ms={(perf_counter() - self._worker_started) * 1000:.2f}",
                "SIG-PERF", Qgis.Info)
        if self._worker_stderr:
            QgsMessageLog.logMessage(
                "Messages Qt du moteur PDF :\n" + self._worker_stderr[-12000:],
                "GEMAPI-App", Qgis.Warning if exit_code == 0 else Qgis.Critical)
        if (exit_status != QProcess.NormalExit or exit_code != 0 or
                self._worker_pages is None or self._partial is None or
                not self._partial.is_file() or not _complete_pdf(self._partial)):
            self._finish_failure(self._worker_error or
                                 "Le moteur PDF s’est interrompu avant la fin de l’export.")
            return
        self._progress.update_stage("Finalisation du document")
        try:
            from .report_pdf import _restore_qgis_pdf_metadata
            with measure("report.finalize.metadata"):
                _restore_qgis_pdf_metadata(self._partial)
            if not _complete_pdf(self._partial):
                raise RuntimeError("PDF incomplet après la finalisation des métadonnées.")
        except Exception as error:
            self._finish_failure(f"Impossible de finaliser le PDF : {error}")
            return
        part_bytes = self._partial.stat().st_size
        rename_started = perf_counter()
        if self._destination.exists() and not self._overwrite_confirmed:
            if QMessageBox.question(
                    self, "Remplacer le PDF ?",
                    f"Le fichier « {self._destination.name} » existe désormais. Le remplacer ?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                self._cleanup_export()
                self.statusLabel.setText("Enregistrement annulé : le fichier existe déjà.")
                return
        try:
            os.replace(self._partial, self._destination)
        except OSError:
            self._finish_failure("Impossible d’enregistrer le PDF à l’emplacement choisi.")
            return
        rename_ms = (perf_counter() - rename_started) * 1000
        destination = self._destination
        self._progress.update_stage("Terminé")
        cleanup_started = perf_counter()
        self._cleanup_export()
        if ENABLED:
            QgsMessageLog.logMessage(
                f"report.finalize rename_ms={rename_ms:.2f} "
                f"cleanup_ms={(perf_counter() - cleanup_started) * 1000:.2f} "
                f"bytes={part_bytes} pages={self._worker_pages}",
                "SIG-PERF", Qgis.Info)
        self.statusLabel.setText("Rapport généré")
        message = QMessageBox(self)
        message.setWindowTitle("Rapport généré")
        message.setText("Rapport généré")
        open_button = message.addButton("Ouvrir le PDF", QMessageBox.AcceptRole)
        message.addButton("Fermer", QMessageBox.RejectRole)
        message.exec_()
        if message.clickedButton() is open_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(destination)))

    def _finish_failure(self, message):
        if not self._busy:
            return
        self._cleanup_export()
        self.statusLabel.setText(message)
        QMessageBox.warning(self, "Échec de génération", message)

    def _cleanup_export(self):
        self._busy = False
        if self._progress is not None:
            self._progress.allow_close = True
            self._progress.close()
            self._progress.deleteLater()
            self._progress = None
        if self._partial is not None:
            self._partial.unlink(missing_ok=True)
            self._partial = None
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
        if self._process is not None:
            self._process.deleteLater()
            self._process = None
        self._set_controls_enabled(True)
        self.exportButton.setEnabled(bool(self.match_count))

    def shutdown(self):
        self._busy = False
        if self._process is not None and self._process.state() != QProcess.NotRunning:
            self._process.kill()
            self._process.waitForFinished(5000)
        if self._process is not None or self._progress is not None:
            self._cleanup_export()
        self.close()

    def reject(self):
        if self._busy:
            self.statusLabel.setText("La génération est en cours. Attendez la fin du rapport.")
            return
        super().reject()

    def closeEvent(self, event):
        if self._busy:
            event.ignore()
            return
        super().closeEvent(event)
