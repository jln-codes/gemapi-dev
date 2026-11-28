"""Business field layout in the shared creation/edit dialog (QGIS offscreen)."""

import sys
from pathlib import Path

from qgis.core import QgsApplication, QgsProject

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin"))
from gemapi_app.creation import CreationDialog  # noqa: E402
from test_photo_ui import Interface  # noqa: E402


def main():
    app = QgsApplication([], True)
    app.initQgis()
    iface = Interface()
    output = ROOT / "build/qgis-tests"
    output.mkdir(exist_ok=True)
    try:
        for mode, filename in (("create", "ux_form_metier_01_creation.png"),
                               ("edit", "ux_form_metier_01_edition.png")):
            dialog = CreationDialog(iface, mode=mode)
            dialog.resize(900, 800)
            dialog.show()
            app.processEvents()
            grid = dialog.detailsLayout
            for column, widget in enumerate((dialog.typeCombo, dialog.prestataireCombo,
                                             dialog.statutCombo, dialog.technicienCombo)):
                assert grid.itemAtPosition(2, column).widget() is widget
            for column, widget in enumerate((dialog.debutDate, dialog.finDate,
                                             dialog.positionCombo, dialog.riveCombo)):
                assert grid.itemAtPosition(4, column).widget() is widget
            assert grid.itemAtPosition(6, 0).layout() is dialog.amountLayout
            assert grid.itemAtPosition(8, 0).widget() is dialog.descriptionEdit
            assert [grid.columnStretch(i) for i in range(4)] == [1] * 4
            assert [dialog.amountLayout.stretch(i) for i in range(3)] == [2, 1, 1]
            assert all(label.text().endswith("*") for label in (
                dialog.typeLabel, dialog.prestataireLabel, dialog.statutLabel,
                dialog.technicienLabel, dialog.debutLabel, dialog.finLabel))
            assert not dialog.positionCombo.isEnabled() and not dialog.riveCombo.isEnabled()
            dialog._add_selection("cours_eau", ("11111111-1111-1111-1111-111111111111",), "Rivière test")
            dialog._update_position_state()
            assert dialog.positionCombo.isEnabled() and dialog.riveCombo.isEnabled()
            dialog.selectedList.clear()
            dialog._add_selection("ah_stockage", ("22222222-2222-2222-2222-222222222222",), "Stockage test")
            dialog._update_position_state()
            assert not dialog.positionCombo.isEnabled() and not dialog.riveCombo.isEnabled()
            assert dialog.positionCombo.currentData() == "Indéfini"
            assert dialog.riveCombo.currentData() == "Indéfinie"
            dialog.intituleEdit.setText("Intitulé facultatif")
            dialog.quantiteEdit.setText("12,5")
            dialog.uniteEdit.setText("m²")
            dialog.descriptionEdit.setPlainText("Description courte de l'intervention")
            dialog.formScroll.ensureWidgetVisible(dialog.detailsGroup)
            app.processEvents()
            widths = [widget.width() for widget in (
                dialog.typeCombo, dialog.prestataireCombo, dialog.statutCombo,
                dialog.technicienCombo)]
            assert min(widths) > 150, widths
            assert dialog.intituleEdit.width() > dialog.quantiteEdit.width() * 1.6
            assert dialog.descriptionEdit.width() > dialog.intituleEdit.width()
            assert dialog.grab().save(str(output / filename))
            dialog.resize(780, 650)
            app.processEvents()
            assert dialog.photo1Button.geometry().x() < dialog.photo2Button.geometry().x()
            dialog.shutdown()
        print("Informations métier création/édition : grille, états et tailles validés")
    finally:
        QgsProject.instance().clear()
        app.exitQgis()


if __name__ == "__main__":
    main()
