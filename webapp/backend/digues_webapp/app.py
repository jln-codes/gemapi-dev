"""Entrée FastAPI du socle gemapi.dev."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .basemap_catalog import basemap_catalog_payload
from .protected_routes import register_closed_routes
from .resources import frontend_directory

FRONTEND_DIRECTORY = frontend_directory()
APP_VERSION = "0.1.0"


def calculate_frontend_build_id(frontend_directory: Path) -> str:
    """Calcule un identifiant stable à partir des chemins et contenus frontend."""

    digest = hashlib.sha256()
    files = sorted(
        path for path in frontend_directory.rglob("*") if path.is_file()
    )
    for path in files:
        relative_path = (
            path.relative_to(frontend_directory).as_posix().encode("utf-8")
        )
        content = path.read_bytes()
        digest.update(len(relative_path).to_bytes(8, "big"))
        digest.update(relative_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()[:12]


# Le répertoire est parcouru une seule fois au chargement de l'application.
BUILD_ID = calculate_frontend_build_id(FRONTEND_DIRECTORY)


def create_app() -> FastAPI:
    application = FastAPI(title="gemapi.dev", version=APP_VERSION)

    @application.get("/", include_in_schema=False)
    def index():
        return RedirectResponse("/carte")

    @application.get("/carte", include_in_schema=False)
    def map_page():
        return FileResponse(FRONTEND_DIRECTORY / "map.html")

    @application.get("/dev/lidar", include_in_schema=False)
    def lidar_prototype_page():
        if (os.getenv("DIGUES_ENVIRONMENT", "").lower() not in {"development", "local", "test"}
                or os.getenv("LIDAR_PROTOTYPE_ENABLED") != "1"):
            raise HTTPException(404, "Page introuvable.")
        return FileResponse(Path(__file__).with_name("lidar_prototype.html"),
                            headers={"Cache-Control": "no-store"})

    @application.get("/base", include_in_schema=False)
    def data_page():
        return FileResponse(FRONTEND_DIRECTORY / "data.html")

    @application.get("/manifest.webmanifest", include_in_schema=False)
    def web_manifest():
        return RedirectResponse("/branding/site.webmanifest")

    @application.get("/branding/site.webmanifest", include_in_schema=False)
    def brand_manifest():
        return FileResponse(FRONTEND_DIRECTORY / "branding" / "site.webmanifest",
                            media_type="application/manifest+json")

    @application.get("/service-worker.js", include_in_schema=False)
    def service_worker(v: str | None = None):
        if v is not None and v != BUILD_ID:
            raise HTTPException(409, "Version du service worker indisponible.",
                                headers={"Cache-Control": "no-cache"})
        return FileResponse(FRONTEND_DIRECTORY / "service-worker.js",
                            media_type="application/javascript",
                            headers={"Cache-Control": "no-cache"})

    @application.get("/api/config")
    def frontend_config():
        return JSONResponse({"build_id": BUILD_ID, "app_version": APP_VERSION},
                            headers={"Cache-Control": "no-store"})

    @application.get("/api/basemaps")
    def basemap_catalog():
        return JSONResponse(basemap_catalog_payload(), headers={"Cache-Control": "no-store"})

    @application.get("/api/health")
    def health():
        return {"status": "ok"}

    register_closed_routes(application)
    application.mount("/branding", StaticFiles(directory=FRONTEND_DIRECTORY / "branding"), name="branding")
    application.mount("/static", StaticFiles(directory=FRONTEND_DIRECTORY), name="static")
    return application


app = create_app()
