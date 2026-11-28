#!/usr/bin/env python3
"""Validate and build the installable GEMAPI-App QGIS plugin archive."""

from __future__ import annotations

import configparser
import ast
import py_compile
import re
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


PROJECT_DIR = Path(__file__).resolve().parents[1]
PLUGIN_DIR = PROJECT_DIR / "gemapi_app"
DIST_DIR = PROJECT_DIR / "dist"
PACKAGE_NAME = "gemapi_app"
ARCHIVE_NAME = "gemapi-app"
REQUIRED_FILES = {"__init__.py", "plugin.py", "creation.py", "management.py", "filter_picker.py", "type_picker.py", "digitize.py", "record.py", "hydro_groups.py", "watercourse_labels.py", "patrimony_spatial.py", "report_data.py", "report_dialog.py", "report_worker.py", "report_pdf.py", "report_synthetic_portrait.py", "pdf_runtime.py", "photo_paths.py", "report_maps.py", "window_geometry.py", "perf.py", "session_data.py", "ah_sections.py", "form.ui", "home.ui", "create.ui", "report.ui", "icons/icon.png", "icons/icon.svg", "assets/logo_institutionnel.webp", "metadata.txt", "help.md", "LICENSE", "COPYING"}
REQUIRED_FILES.update(('digues.py', 'permissions.py', 'prestation_focus.py', 'assistant.py', 'assistant_dialog.py', 'voice.py'))
ARCHIVE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
ALLOWED_IMPORTS = {"pathlib", "plugin", "creation", "management", "filter_picker", "type_picker", "digitize", "record", "hydro_groups", "watercourse_labels", "patrimony_spatial", "report_data", "report_dialog", "report_worker", "report_pdf", "pdf_runtime", "gemapi_app", "qgis", "osgeo", "datetime", "decimal", "uuid", "math", "textwrap", "os", "photo_paths", "report_maps", "window_geometry", "json", "urllib", "shutil", "sys", "tempfile", "collections", "dataclasses", "unicodedata", "traceback", "perf", "hashlib", "contextlib", "contextvars", "functools", "time", "session_data", "ah_sections"}
REPORT_EXPORTS = {"filter_option_queries", "build_management_query", "build_report_query", "prepare_rows", "grouped_entries", "measure_lines", "report_warnings"}
ALLOWED_IMPORTS.update(('digues', 'permissions', 'prestation_focus', 'assistant', 'assistant_dialog', 'voice', 're', 'difflib', 'array', 'io', 'wave'))
ALLOWED_IMPORTS.add('copy')
ALLOWED_IMPORTS.update(('base64', 'binascii'))


def validate_python(source: Path) -> None:
    py_compile.compile(str(source), doraise=True)
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    imports = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        (node.module or "").split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    unexpected = sorted(name for name in imports if name and name not in ALLOWED_IMPORTS)
    if unexpected:
        raise SystemExit(f"Imports non autorisés dans {source.name} : {', '.join(unexpected)}")


def validate_plugin() -> str:
    missing = sorted(name for name in REQUIRED_FILES if not (PLUGIN_DIR / name).is_file())
    if missing:
        raise SystemExit(f"Fichiers requis absents : {', '.join(missing)}")

    metadata = configparser.ConfigParser(interpolation=None)
    metadata.read(PLUGIN_DIR / "metadata.txt", encoding="utf-8")
    if not metadata.has_section("general"):
        raise SystemExit("metadata.txt ne contient pas la section [general]")

    required_keys = {"name", "qgisMinimumVersion", "version", "author", "description", "about"}
    missing_keys = sorted(key for key in required_keys if not metadata.has_option("general", key))
    if missing_keys:
        raise SystemExit(f"Métadonnées requises absentes : {', '.join(missing_keys)}")

    version = metadata.get("general", "version").strip()
    if metadata.get("general", "name") != "GEMAPI-App":
        raise SystemExit("Nom QGIS attendu : GEMAPI-App")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise SystemExit(f"Numéro de version invalide : {version}")
    if metadata.get("general", "icon", fallback="") != "icons/icon.png":
        raise SystemExit("Icône QGIS absente des métadonnées")

    for source in sorted(PLUGIN_DIR.glob("*.py")):
        validate_python(source)

    report_tree = ast.parse((PLUGIN_DIR / "report_data.py").read_text(encoding="utf-8"))
    exports = {node.name for node in report_tree.body if isinstance(node, ast.FunctionDef)}
    missing_exports = sorted(REPORT_EXPORTS - exports)
    if missing_exports:
        raise SystemExit(f"Fonctions de rapport absentes : {', '.join(missing_exports)}")

    for form in ("form.ui", "home.ui", "create.ui", "report.ui"):
        ElementTree.parse(PLUGIN_DIR / form)

    return version


def archive_files() -> list[Path]:
    # Explicit runtime manifest: development files and generated reports cannot
    # enter the archive even if they are accidentally placed inside the package.
    return sorted(PLUGIN_DIR / name for name in REQUIRED_FILES)


def archive_content(source: Path) -> bytes:
    return source.read_bytes()


def write_archive(version: str) -> Path:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    output = DIST_DIR / f"{ARCHIVE_NAME}-{version}.zip"
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for source in archive_files():
            relative = source.relative_to(PLUGIN_DIR)
            member = PurePosixPath(PACKAGE_NAME, *relative.parts).as_posix()
            info = ZipInfo(member, date_time=ARCHIVE_TIMESTAMP)
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, archive_content(source), compress_type=ZIP_DEFLATED, compresslevel=9)
    return output


def validate_archive(output: Path) -> None:
    """Check the ZIP that will actually be installed, not just the source tree."""
    with ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise SystemExit("Le ZIP du plugin est corrompu")
        expected = {f"{PACKAGE_NAME}/{source.relative_to(PLUGIN_DIR).as_posix()}": source
                    for source in archive_files()}
        if set(archive.namelist()) != set(expected):
            raise SystemExit("Le ZIP ne correspond pas au manifeste du plugin")
        for member, source in expected.items():
            if archive.read(member) != archive_content(source):
                raise SystemExit(f"Contenu du ZIP différent des sources : {member}")


def main() -> None:
    version = validate_plugin()
    output = write_archive(version)
    validate_archive(output)
    print(f"Plugin QGIS valide : {PLUGIN_DIR.name} {version}")
    print(f"Archive créée : {output.relative_to(PROJECT_DIR.parent)}")


if __name__ == "__main__":
    main()
