"""Resolve photo references relative to the active QGIS project."""

import os
from pathlib import Path, PurePosixPath, PureWindowsPath


def project_file():
    """Return the opened QGIS file, also in the separate PDF process."""
    try:
        from qgis.core import QgsProject

        value = QgsProject.instance().fileName()
    except ImportError:
        value = ""
    if not value:
        value = os.environ.get("SIG_PRESTATIONS_PROJECT_FILE", "").strip()
    if not value:
        return None
    try:
        source = Path(value).expanduser()
        if not source.is_absolute():
            return None
        source = source.resolve()
        return source if source.is_file() else None
    except (OSError, ValueError):
        return None


def project_root():
    source = project_file()
    return source.parent if source else None


def photo_root():
    """Compatibility root for references saved before the photos/ convention."""
    value = os.environ.get("SIG_PRESTATIONS_PHOTOS_DIR", "").strip()
    if value:
        root = Path(value).expanduser()
        return root.resolve() if root.is_absolute() else None
    root = project_root()
    return (root / "photos").resolve() if root and (root / "photos").is_dir() else None


def reference_for(path):
    source = Path(path).expanduser().resolve()
    root = project_root()
    if root and source.is_relative_to((root / "photos").resolve()):
        return source.relative_to(root).as_posix()
    return str(source)


def resolve_photo(reference):
    if not reference:
        return None
    try:
        value = str(reference).strip()
        if not value:
            return None
        if PureWindowsPath(value).drive:
            if os.name != "nt":
                return None
            source = Path(value).expanduser()
            return source if source.is_absolute() and source.is_file() else None

        normalized = value.replace("\\", "/")
        relative = PurePosixPath(normalized)
        if relative.is_absolute():
            source = Path(normalized)
            return source if source.is_absolute() and source.is_file() else None

        parts = relative.parts
        if not parts:
            return None
        if parts[0] == "photos":
            root = project_root()
            if root is None:
                return None
            allowed = (root / "photos").resolve()
            parts = parts[1:]
        else:
            allowed = photo_root()
            if allowed is None:
                return None
        source = allowed.joinpath(*parts).resolve()
        return source if source.is_relative_to(allowed) and source.is_file() else None
    except (OSError, ValueError):
        return None
