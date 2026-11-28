"""Select and prepare the current QGIS installation for the PDF subprocess."""

import os
import sys
from pathlib import Path


def _qgis_prefix(prefix, environment):
    value = prefix or environment.get("QGIS_PREFIX_PATH", "")
    path = Path(value) if value else None
    if path is None or not path.is_dir():
        raise ValueError(
            "Le répertoire de l'installation QGIS est introuvable. "
            "Vérifiez ou réparez l'installation QGIS."
        )
    return path


def _windows_root(prefix):
    if prefix.parent.name.lower() != "apps":
        raise ValueError(
            "L'environnement QGIS Windows est incomplet : préfixe OSGeo4W invalide."
        )
    return prefix.parent.parent


def _inside(path, root):
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _python_name(path):
    return path.name.lower().startswith("python")


def select_python(prefix, environment=None, executable=None, python_prefix=None,
                  platform=None):
    """Return an installed Python tied to QGIS, never a PATH search result."""
    environment = os.environ if environment is None else environment
    executable = sys.executable if executable is None else executable
    python_prefix = sys.prefix if python_prefix is None else python_prefix
    platform = sys.platform if platform is None else platform

    override = environment.get("SIG_PRESTATIONS_PYTHON", "").strip()
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return str(candidate)
        raise ValueError("L'interpréteur de diagnostic SIG_PRESTATIONS_PYTHON est introuvable.")

    qgis_prefix = _qgis_prefix(prefix, environment)
    current = Path(executable)
    if platform == "win32":
        root = _windows_root(qgis_prefix)
        declared = environment.get("PYTHONEXECUTABLE", "")
        candidates = [Path(declared)] if declared else []
        candidates.extend((current, root / "bin/python3.exe", root / "bin/python.exe"))
        for candidate in candidates:
            if candidate.is_file() and _python_name(candidate) and _inside(candidate, root):
                return str(candidate)
        raise ValueError(
            "Python de l'installation QGIS Windows introuvable. "
            "Vérifiez ou réparez QGIS/OSGeo4W."
        )

    candidates = (current, Path(python_prefix) / "bin/python3",
                  Path(python_prefix) / "bin/python")
    for candidate in candidates:
        if candidate.is_file() and _python_name(candidate):
            return str(candidate)
    raise ValueError(
        "Python de l'installation QGIS introuvable. Vérifiez ou réparez QGIS."
    )


def _prepend_paths(paths, existing, separator):
    result = []
    seen = set()
    for path in (*paths, *existing.split(separator)):
        if not path:
            continue
        key = os.path.normcase(str(Path(path)))
        if key not in seen:
            seen.add(key)
            result.append(str(path))
    return separator.join(result)


def worker_environment(prefix, python_executable, environment=None, platform=None):
    """Build the worker environment from the active QGIS installation."""
    result = dict(os.environ if environment is None else environment)
    platform = sys.platform if platform is None else platform
    qgis_prefix = _qgis_prefix(prefix, result)
    result["QGIS_PREFIX_PATH"] = str(qgis_prefix)
    # The native Windows QPA exposes the system font database to the PDF worker.
    # QgsApplication does not open a window here; Linux keeps its headless QPA.
    result["QT_QPA_PLATFORM"] = "windows" if platform == "win32" else "offscreen"

    if platform != "win32":
        return result

    root = _windows_root(qgis_prefix)
    python_home = Path(result.get("PYTHONHOME", "")) if result.get("PYTHONHOME") else None
    if python_home is None or not python_home.is_dir() or not _inside(python_home, root):
        homes = [path for path in (root / "apps").glob("Python*") if path.is_dir()]
        if len(homes) != 1:
            raise ValueError(
                "Bibliothèque Python de QGIS introuvable ou ambiguë dans OSGeo4W."
            )
        python_home = homes[0]
    result["PYTHONHOME"] = str(python_home)
    result["PYTHONEXECUTABLE"] = str(python_executable)
    result["OSGEO4W_ROOT"] = str(root)

    python_paths = [qgis_prefix / "python", qgis_prefix / "python/plugins"]
    inherited_python = [path for path in result.get("PYTHONPATH", "").split(";")
                        if path and _inside(Path(path), root)]
    result["PYTHONPATH"] = _prepend_paths(
        [str(path) for path in python_paths], ";".join(inherited_python), ";"
    )
    result["PATH"] = _prepend_paths(
        [str(qgis_prefix / "bin"), str(root / "apps/qt5/bin"), str(root / "bin")],
        result.get("PATH", ""), ";"
    )
    result["QT_PLUGIN_PATH"] = _prepend_paths(
        [str(qgis_prefix / "qtplugins"), str(root / "apps/qt5/plugins")],
        "", ";"
    )
    for name, path in (("GDAL_DATA", root / "apps/gdal/share/gdal"),
                       ("PROJ_DATA", root / "share/proj")):
        if not result.get(name) and path.is_dir():
            result[name] = str(path)
    return result
