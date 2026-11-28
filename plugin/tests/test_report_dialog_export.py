"""Offscreen direct-export UI: count, process progress, success and cleanup."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtWidgets import QApplication, QMessageBox
from qgis.core import QgsApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gemapi_app.pdf_runtime import select_python
from gemapi_app.report_data import ROW_FIELDS
from gemapi_app.report_dialog import ExportProgress, ReportDialog, _complete_pdf


class Connection:
    def __init__(self, count=1):
        self.count = count

    def executeSql(self, query):
        if "count(DISTINCT p.id)" in query:
            return [[self.count]]
        if "current_user::text" in query:
            return [["test_report"]]
        if "FROM gemapi.techniciens " in query:
            return []
        record = dict.fromkeys(ROW_FIELDS)
        record.update(id=str(uuid4()), intitule="Prestation de test",
                      date_debut="2027-01-01", date_fin="2027-01-15",
                      type="Test", technician="Technicien", provider="Prestataire",
                      statut="Planifié", description="Description", unite="",
                      has_geometry=True, objects="", reference_count=0,
                      communes="Béthune", parents="[]",
                      own_wkt="POINT(700000 6600000)",
                      reference_geometries="[]", position="Indéfini")
        return [[str(uuid4()) if field == "id" else record[field]
                 for field in ROW_FIELDS] for _ in range(self.count)]


def wait_for_export(app, dialog):
    closer = QTimer()
    heartbeat = QTimer()
    ticks = [0]
    heartbeat.timeout.connect(lambda: ticks.__setitem__(0, ticks[0] + 1))
    heartbeat.start(20)

    def dismiss_message():
        active = QApplication.activeModalWidget()
        if isinstance(active, QMessageBox):
            active.close()

    closer.timeout.connect(dismiss_message)
    closer.start(50)
    deadline = time.monotonic() + 180
    while dialog._busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.05)
    app.processEvents()
    closer.stop()
    heartbeat.stop()
    assert not dialog._busy, "Export QGIS bloqué"
    return ticks[0]


def main():
    app = QgsApplication([], True)
    app.initQgis()
    progress = ExportProgress()
    initial_size = progress.size()
    progress.update_stage("Mise en page des prestations", 76, 100)
    assert progress.message.text() == "Mise en page des prestations — 76/100"
    progress.update_stage("Mise en page des prestations", 100, 100)
    assert progress.bar.maximum() == 100 and progress.bar.value() == 45
    progress.update_stage("Rendu cartographique et export PDF — lot 1/3, 10 pages", 0, 3)
    assert progress.bar.value() == 45 and progress._activity.isActive()
    assert progress.message.text().splitlines()[0] == "Rendu cartographique et export PDF"
    progress.show()
    app.processEvents()
    assert progress.size() == initial_size
    activity_label = progress.message.text()
    time.sleep(0.55)
    app.processEvents()
    assert progress.message.text() != activity_label and progress.bar.value() == 45
    progress.update_stage("Rendu cartographique et export PDF — lot 2/3, 10 pages", 1, 3)
    assert 45 < progress.bar.value() < 95 and "Lot 2/3" in progress.message.text()
    progress.update_stage("Rendu cartographique et export PDF — lot 3/3 terminé, 5 pages", 3, 3)
    assert progress.bar.value() == 95
    progress.update_stage("Finalisation du document")
    assert progress.bar.value() == 95 and progress._activity.isActive()
    progress.update_stage("Terminé")
    assert progress.bar.value() == 100 and progress.size() == initial_size
    progress.allow_close = True
    progress.close()
    progress = ExportProgress()
    initial_size = progress.size()
    progress.update_stage("Mise en page des groupes", 7, 12)
    assert progress.message.text() == "Mise en page des groupes — 7/12"
    progress.update_stage("Rendu cartographique", 10, 10)
    assert progress.bar.maximum() == 100 and progress.bar.value() == 85
    assert progress.message.text().splitlines()[0] == "Rendu cartographique et export PDF"
    progress.update_stage("Rendu cartographique et export PDF — lot 1/2, 10 groupes", 0, 2)
    assert progress.bar.value() == 85
    progress.show()
    app.processEvents()
    assert progress.size() == initial_size
    progress.update_stage("Rendu cartographique et export PDF — lot 2/2, 2 groupes", 1, 2)
    assert progress.bar.value() == 90
    progress.update_stage("Finalisation du document")
    assert progress.bar.value() == 95 and progress.size() == initial_size
    progress.allow_close = True
    progress.close()
    dialog = ReportDialog(type("Interface", (), {"mainWindow": lambda self: None})())
    dialog.connection = Connection()
    login_patch = patch("gemapi_app.report_dialog.connection_login",
                        return_value="test_report")
    login_patch.start()
    dialog.refresh_count()
    assert dialog.match_count == 1 and dialog.exportButton.isEnabled()
    assert dialog.connection is not None, dialog.statusLabel.text()
    if os.name == "nt":
        prefix = Path(QgsApplication.prefixPath())
        root = prefix.parent.parent
        selected = select_python(
            str(prefix), environment={"PATH": str(Path(sys.prefix).parent)},
            executable=str(root / "bin/qgis-ltr-bin.exe"), platform="win32",
        )
        assert Path(selected).resolve() == (root / "bin/python3.exe").resolve()
    with tempfile.TemporaryDirectory() as directory, patch(
            "gemapi_app.report_dialog.QMessageBox.question",
            return_value=QMessageBox.Yes) as overwrite_confirmation:
        incomplete = Path(directory) / "incomplete.pdf"
        incomplete.write_bytes(b"%PDF-1.7\nbody without trailer")
        assert not _complete_pdf(incomplete)
        target = Path(directory) / "nom_personnalise.pdf"
        with patch("gemapi_app.report_dialog.QFileDialog.getSaveFileName",
                   return_value=(str(target), "")) as choose_file:
            dialog.export()
        assert choose_file.call_args.args[2] == "fiches_prestations.pdf"
        assert choose_file.call_args.kwargs["options"]
        assert dialog.connection is not None, dialog.statusLabel.text()
        assert dialog._busy and dialog._progress.isVisible()
        assert wait_for_export(app, dialog) > 2
        assert target.is_file() and target.stat().st_size > 1000, dialog.statusLabel.text()
        assert dialog.statusLabel.text() == "Rapport généré"
        assert Path(dialog._worker_python).resolve() == Path(dialog._python_executable()).resolve()
        assert not list(Path(directory).glob("*.part-*.pdf"))

        existing = Path(directory) / "deja_present.pdf"
        existing.write_bytes(b"original")
        with patch("gemapi_app.report_dialog.QMessageBox.question",
                   return_value=QMessageBox.No) as declined:
            with patch("gemapi_app.report_dialog.QFileDialog.getSaveFileName",
                       return_value=(str(existing), "")):
                dialog.export()
        declined.assert_called_once()
        assert existing.read_bytes() == b"original" and not dialog._busy

        appeared_later = Path(directory) / "cree_pendant_export.pdf"
        with patch("gemapi_app.report_dialog.QFileDialog.getSaveFileName",
                   return_value=(str(appeared_later), "")):
            dialog.export()
        app.processEvents()
        appeared_later.write_bytes(b"new file")
        with patch("gemapi_app.report_dialog.QMessageBox.question",
                   return_value=QMessageBox.No) as declined_late:
            assert wait_for_export(app, dialog) > 0
        declined_late.assert_called_once()
        assert appeared_later.read_bytes() == b"new file"
        assert not list(Path(directory).glob("*.part-*.pdf"))

        dialog.reportKindCombo.setCurrentIndex(1)
        dialog.refresh_count()
        assert dialog.reportKindCombo.currentText() == "Rapport synthétique"
        assert not hasattr(dialog, "photosCheck")
        for family in ("cours_eau", "digues", "ah", "site_terrestre"):
            assert dialog.group1Combo.model().item(dialog.group1Combo.findData(family)).isEnabled()
        dialog.group1Combo.setCurrentIndex(dialog.group1Combo.findData("ah"))
        assert dialog.filters()["groups"] == ("ah",)
        synthetic_target = Path(directory) / "synthetic.pdf"
        with patch("gemapi_app.report_dialog.QFileDialog.getSaveFileName",
                   return_value=(str(synthetic_target), "")) as choose_file:
            dialog.export()
        assert choose_file.call_args.args[2] == "rapport_synthetique.pdf"
        assert dialog._include_maps
        assert wait_for_export(app, dialog) > 2
        assert synthetic_target.is_file(), dialog.statusLabel.text()
        if shutil.which("pdfinfo"):
            info = subprocess.check_output(["pdfinfo", str(synthetic_target)], text=True)
            assert "595 x 842 pts (A4)" in info
        dialog.reportKindCombo.setCurrentIndex(0)
        dialog.connection = Connection(count=12)
        dialog.refresh_count()
        assert dialog.match_count == 12
        batch_target = Path(directory) / "fiches_12.pdf"
        with patch("gemapi_app.report_dialog.QFileDialog.getSaveFileName",
                   return_value=(str(batch_target), "")) as choose_file:
            dialog.export()
        assert choose_file.call_args.args[2] == "fiches_prestations.pdf"
        assert wait_for_export(app, dialog) > 2
        assert batch_target.is_file() and dialog.statusLabel.text() == "Rapport généré"
        assert not list(Path(directory).glob("*.part-*.pdf"))
        if shutil.which("pdfinfo"):
            info = subprocess.check_output(["pdfinfo", str(batch_target)], text=True)
            assert "Pages:           12" in info
            assert "Creator:         QGIS " in info

        metadata_target = Path(directory) / "metadata_failure.pdf"
        metadata_target.write_bytes(b"original")
        with patch("gemapi_app.report_pdf._restore_qgis_pdf_metadata",
                   side_effect=RuntimeError("métadonnées indisponibles")):
            with patch("gemapi_app.report_dialog.QFileDialog.getSaveFileName",
                       return_value=(str(metadata_target), "")):
                dialog.export()
            wait_for_export(app, dialog)
        assert metadata_target.read_bytes() == b"original"
        assert not list(Path(directory).glob("*.part-*.pdf"))
        assert "Impossible de finaliser le PDF" in dialog.statusLabel.text()

        failed_target = Path(directory) / "original.pdf"
        failed_target.write_bytes(b"original")
        if os.name == "nt":
            # QProcess cannot execute the POSIX shell fixture on Windows.
            # A missing executable still exercises failed-start cleanup.
            failed_worker = Path(directory) / "missing-python.exe"
            with patch("gemapi_app.report_dialog.ReportDialog._python_executable",
                       return_value=str(failed_worker)):
                with patch("gemapi_app.report_dialog.QFileDialog.getSaveFileName",
                           return_value=(str(failed_target), "")):
                    dialog.export()
                wait_for_export(app, dialog)
        else:
            slow_worker = Path(directory) / "slow-python"
            slow_worker.write_text("#!/bin/sh\nsleep 2\nexit 1\n", encoding="utf-8")
            slow_worker.chmod(0o755)
            with patch.dict(os.environ, {"SIG_PRESTATIONS_PYTHON": str(slow_worker)}):
                with patch("gemapi_app.report_dialog.QFileDialog.getSaveFileName",
                           return_value=(str(failed_target), "")):
                    dialog.export()
                assert wait_for_export(app, dialog) > 20
        assert failed_target.read_bytes() == b"original"
        assert not list(Path(directory).glob("*.part-*.pdf"))
        expected = "Impossible de démarrer" if os.name == "nt" else "interrompu"
        assert expected in dialog.statusLabel.text()

        cancelled_target = Path(directory) / "cancelled.pdf"
        cancelled_target.write_bytes(b"original")
        with patch("gemapi_app.report_dialog.QFileDialog.getSaveFileName",
                   return_value=(str(cancelled_target), "")):
            dialog.export()
        app.processEvents()  # Runs the queued _load_records_and_start call.
        assert dialog._busy and dialog._process is not None
        dialog.shutdown()
        assert cancelled_target.read_bytes() == b"original"
        assert not list(Path(directory).glob("*.part-*.pdf"))
        assert dialog._temporary is None and dialog._process is None
        assert overwrite_confirmation.call_count >= 3
    dialog.close()
    login_patch.stop()
    app.exitQgis()
    print("Export direct : effectif, progression, succès et nettoyage après échec OK")


if __name__ == "__main__":
    main()
