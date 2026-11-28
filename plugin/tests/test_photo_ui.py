"""Photo controls in the shared creation/edit dialog (QGIS offscreen)."""

import sys
from pathlib import Path
from unittest.mock import patch

from qgis.PyQt.QtWidgets import QMainWindow
from qgis.core import QgsApplication, QgsProject
from qgis.gui import QgsMapCanvas

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin"))
from gemapi_app.creation import CreationDialog  # noqa: E402


class Interface:
    def __init__(self):
        self.window = QMainWindow()
        self.canvas = QgsMapCanvas()

    def mainWindow(self):
        return self.window

    def mapCanvas(self):
        return self.canvas


def check_preview(dialog, index, path):
    dialog.photo_references[index] = str(path)
    dialog._update_photo(index)
    QgsApplication.processEvents()
    label = getattr(dialog, f"photo{index + 1}Preview")
    pixmap = label.pixmap()
    assert pixmap and not pixmap.isNull()
    assert abs(pixmap.width() / pixmap.height() -
               dialog._photo_pixmaps[index].width() / dialog._photo_pixmaps[index].height()) < 0.03
    assert pixmap.height() > 90 or pixmap.width() > 300
    return pixmap


def main():
    app = QgsApplication([], True)
    app.initQgis()
    iface = Interface()
    output = ROOT / "build/qgis-tests"
    output.mkdir(exist_ok=True)
    photos = ROOT / "demo/photos"
    try:
        for mode, filename in (("create", "ux_photos_01_creation.png"),
                               ("edit", "ux_photos_01_edition.png")):
            dialog = CreationDialog(iface, mode=mode)
            dialog.resize(900, 800)
            dialog.show()
            app.processEvents()
            dialog.formScroll.ensureWidgetVisible(dialog.photosGroup)
            app.processEvents()
            assert dialog.photo1Preview.height() >= 220
            assert dialog.photo2Preview.height() >= 220
            assert dialog.photo1Preview.height() <= 260
            assert not dialog.photo1Remove.isEnabled()
            assert not dialog.photo2Remove.isEnabled()
            assert dialog.photo1Preview.text() == "Aucune photographie"
            with patch("gemapi_app.creation.QFileDialog.getOpenFileName",
                       return_value=(str(photos / "photo_3x4.jpg"), "")):
                dialog.photo1Button.click()
            portrait = check_preview(dialog, 0, photos / "photo_3x4.jpg")
            assert portrait.height() > portrait.width()
            landscape = check_preview(dialog, 1, photos / "photo_16x9.jpg")
            assert landscape.width() > landscape.height()
            assert dialog.photo1Remove.isEnabled() and dialog.photo2Remove.isEnabled()
            assert dialog.photo1Button.geometry().y() == dialog.photo1Remove.geometry().y()
            assert dialog.photo2Button.geometry().y() == dialog.photo2Remove.geometry().y()
            assert dialog.photo1Button.width() > dialog.photo1Remove.width()
            assert dialog.photo1Preview.geometry().x() < dialog.photo2Preview.geometry().x()
            dialog.photo1Comment.setText("Vue avant travaux")
            dialog.photo2Comment.setText("Vue après travaux")
            assert dialog._values()["photo_1_commentaire"] == "Vue avant travaux"
            assert dialog._values()["photo_2_commentaire"] == "Vue après travaux"
            assert dialog.grab().save(str(output / filename))
            check_preview(dialog, 0, photos / "photo_1x1.jpg")
            for path in ("photo_4x3.jpg", "photo_9x16.jpg"):
                check_preview(dialog, 0, photos / path)
            dialog.photo_references[0] = r"J:\photos\photo inaccessible.jpg"
            dialog._update_photo(0)
            assert dialog.photo1Preview.text() == "Photographie indisponible"
            assert dialog.photo_references[0].startswith("J:")
            assert dialog.photo1Remove.isEnabled()
            dialog.photo1Remove.click()
            assert dialog.photo1Preview.text() == "Aucune photographie"
            assert not dialog.photo1Remove.isEnabled()
            dialog.photo2Remove.click()
            assert not dialog.photo2Remove.isEnabled()
            assert dialog.photo1Comment.text() == "Vue avant travaux"
            assert dialog.photo2Comment.text() == "Vue après travaux"
            dialog.shutdown()
        print("Aperçus photo création/édition : proportions, états et commandes validés")
    finally:
        QgsProject.instance().clear()
        app.exitQgis()


if __name__ == "__main__":
    main()
