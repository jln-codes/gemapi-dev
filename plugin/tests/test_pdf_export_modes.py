"""Experimental PDF settings preserve the normal export and text semantics."""

import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from qgis.core import QgsApplication, QgsLayoutExporter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gemapi_app.report_pdf import export_report  # noqa: E402


def main():
    settings = QgsLayoutExporter.PdfExportSettings()
    assert all(hasattr(settings, field) for field in
               ("dpi", "rasterizeWholeImage", "textRenderFormat"))
    app = QgsApplication([], True)
    app.initQgis()
    record = {
        "id": "00000000-0000-4000-8000-000000000001",
        "intitule": "Essai", "date_debut": "2027-01-01", "date_fin": "2027-01-15",
        "type": "Étude", "statut": "Planifié", "technician": "Technicien",
        "provider": "Prestataire", "communes": "Béthune", "objects": "—",
        "own_length": None, "own_area": None, "has_geometry": True,
        "reference_measures": None, "quantite": None, "unite": None,
        "photo_1": "", "photo_2": "", "own_wkt": "POINT(700000 6600000)",
        "reference_geometries": [],
    }
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for mode, dpi in (("standard", 100), ("standard", 80),
                              ("rasterized", 100), ("hybrid", 100)):
                path = root / f"{mode}-{dpi}.pdf"
                settings = {} if (mode, dpi) == ("standard", 100) else {
                    "export_dpi": dpi, "export_mode": mode}
                assert export_report([record], {"groups": ()}, "Essai", [], str(path),
                                     include_maps=True, map_background="none",
                                     **settings) == 1
                assert path.stat().st_size > 1000
                if shutil.which("pdftotext"):
                    extracted = subprocess.check_output(["pdftotext", str(path), "-"])
                    assert ("Béthune".encode() in extracted) == (mode != "rasterized")
            missing = root / "missing.pdf"
            try:
                export_report([record], {"groups": ()}, "Essai", [], str(missing),
                              include_maps=True, map_background="none",
                              require_basemap=True)
            except RuntimeError as error:
                assert "fond IGN" in str(error)
            else:
                raise AssertionError("Le fond manquant a été accepté")
            assert not missing.exists()
    finally:
        app.exitQgis()
    print("PDF expérimental : 80/100 dpi, raster complet, hybride et fond requis OK")


if __name__ == "__main__":
    main()
