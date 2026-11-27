"""Public static resources in an installed wheel or a development checkout."""
from pathlib import Path


def frontend_directory() -> Path:
    installed = Path(__file__).with_name("frontend_assets")
    if installed.is_dir():
        return installed
    # The editable/source layout deliberately keeps its single asset source.
    webapp = Path(__file__).resolve().parents[2]
    source = webapp / "frontend"
    if (webapp / "pyproject.toml").is_file() and source.is_dir():
        return source
    raise FileNotFoundError("Les ressources frontend de digues-webapp sont absentes.")
