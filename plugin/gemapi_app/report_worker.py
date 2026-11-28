"""Isolated PyQGIS PDF renderer launched by the report dialog as a process."""

import json
import os
import sys
from pathlib import Path
from time import perf_counter

def _emit(payload):
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def main(input_path, output_path):
    perf_enabled = os.environ.get("SIG_PRESTATIONS_PERF", "").strip().lower() in ("1", "true", "yes")
    if perf_enabled:
        os.environ["SIG_PRESTATIONS_PERF_WORKER"] = "1"
    started = perf_counter()
    os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform == "win32" else "offscreen")
    try:
        from qgis.core import QgsApplication
    except (ImportError, OSError) as error:
        raise RuntimeError(
            "PyQGIS est indisponible dans le processus PDF "
            f"({Path(sys.executable).name}) : {error}. "
            "Vérifiez ou réparez l'installation QGIS."
        ) from error
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from gemapi_app.report_pdf import (
        configure_report_font, export_detailed_report,
        inspect_report_fonts,
    )
    from gemapi_app.report_synthetic_portrait import export_synthetic_portrait_report

    payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
    prefix = os.environ.get("QGIS_PREFIX_PATH", "")
    if prefix and Path(prefix).is_dir():
        QgsApplication.setPrefixPath(prefix, True)
    app = QgsApplication([], True)
    app.initQgis()
    if perf_enabled:
        _emit({"perf": "worker.init_qgis", "ms": round((perf_counter() - started) * 1000, 2)})
    try:
        _emit({"python": sys.executable})
        font_diagnostic = inspect_report_fonts()
        _emit({"font_diagnostic": font_diagnostic})
        configure_report_font(font_diagnostic)
        render_start = perf_counter()
        progress = lambda stage, current, total: _emit({
            "stage": stage, "current": current, "total": total})
        if payload.get("report_kind") == "detailed":
            pages = export_detailed_report(
                payload["records"], output_path,
                editor_login=payload.get("editor_login"),
                editor_name=payload.get("editor_name"), progress=progress,
                map_background=payload.get("map_background", "epure"))
        elif payload.get("report_kind") == "synthetic":
            pages, _maps = export_synthetic_portrait_report(
                payload["records"], payload["filters"], output_path,
                progress=progress,
                map_background=payload.get("map_background", "epure"))
        else:
            raise ValueError("Type de rapport inconnu.")
        if perf_enabled:
            _emit({"perf": "worker.render_total", "ms": round((perf_counter() - render_start) * 1000, 2)})
    finally:
        cleanup_start = perf_counter()
        app.exitQgis()
        if perf_enabled:
            _emit({"perf": "worker.cleanup", "ms": round((perf_counter() - cleanup_start) * 1000, 2)})
    _emit({"done": pages})


if __name__ == "__main__":
    try:
        main(sys.argv[1], sys.argv[2])
    except Exception as error:
        _emit({"error": str(error) or type(error).__name__})
        raise SystemExit(1)
