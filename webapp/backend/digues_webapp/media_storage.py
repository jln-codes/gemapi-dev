"""FastAPI and PostgreSQL adapters for the reusable media ingestion core."""
from __future__ import annotations

from pathlib import Path
from uuid import UUID
import argparse
import json
import os

from fastapi import HTTPException
from .resources import frontend_directory

from digues_app import media_ingestion as core
from digues_app.media_ingestion import (
    MediaError,
    MediaPublicationError,
    MediaScopeError,
    MediaStorage,
    MediaValidationError,
    PREPARED_TTL,
)

# Kept as module attributes for maintenance diagnostics and existing focused
# tests. The implementation itself lives in the HTTP-independent core.
LOGGER = core.LOGGER
shutil = core.shutil
time = core.time


def fail(status, code, message):
    raise HTTPException(status, {"code": code, "message": message})


_VALIDATION_HTTP_STATUS = {
    "file-name-invalid": 422,
    "file-empty": 422,
    "file-too-large": 413,
    "file-hash-mismatch": 422,
    "file-type-forbidden": 415,
    "file-dimensions": 413,
    "file-type-mismatch": 415,
    "file-invalid": 415,
}


def _raise_http_media_error(error: MediaError) -> None:
    if isinstance(error, MediaValidationError):
        status = _VALIDATION_HTTP_STATUS[error.code]
    elif isinstance(error, MediaPublicationError):
        status = 409
    else:  # Scope errors are handled explicitly by their calling endpoint.
        status = 503
    fail(status, error.code, error.message)


def validate_image(stream, filename, content_type, expected_hash):
    """HTTP-compatible adapter preserving the established error responses."""
    try:
        return core.validate_image(stream, filename, content_type, expected_hash)
    except MediaValidationError as error:
        _raise_http_media_error(error)


class FilesystemMediaStorage(core.FilesystemMediaStorage):
    """Web adapter: forbid public roots and translate upload collisions to HTTP."""

    def __init__(self, root, *, business_database_id=None, media_namespace=None):
        frontend = frontend_directory()
        super().__init__(
            root,
            business_database_id=business_database_id,
            media_namespace=media_namespace,
            forbidden_roots=(frontend,),
        )

    def prepare(self, stream, photo_id, observation_id, filename, variant):
        try:
            return super().prepare(
                stream, photo_id, observation_id, filename, variant
            )
        except MediaPublicationError as error:
            _raise_http_media_error(error)


def lock_photo(connection, photo_id):
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 1013))",
        (str(photo_id),),
    )


def lock_seuil_plan(connection, plan_id):
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 1014))",
        (str(plan_id),),
    )


def get_media_storage(required=True, *, context=None) -> MediaStorage | None:
    """Resolve an authorized web target; there is no implicit legacy fallback."""
    root = os.getenv("MEDIA_ROOT")
    backend = os.getenv("MEDIA_BACKEND", "disabled")
    if backend == "disabled" or not root:
        if required:
            fail(503, "media-unconfigured", "Le stockage des photos n’est pas configuré.")
        return None
    if backend != "filesystem":
        fail(
            503,
            "media-backend-unavailable",
            "Le backend média configuré n’est pas disponible.",
        )
    try:
        return FilesystemMediaStorage(root)
    except (OSError, ValueError):
        fail(
            503,
            "media-configuration-invalid",
            "La configuration du stockage privé est invalide.",
        )


def clean_prepared(connection, storage, now=None, *, context=None):
    # Preflight before even enumerating files, including the zero-candidate case.
    # Both the selected connection and physical namespace must match the target.
    if getattr(storage, "business_database_id", None) is not None:
        if (
            context is None
            or storage.business_database_id != context.business_database_id
            or storage.media_namespace != context.media_namespace
        ):
            raise MediaScopeError("Cleanup target mismatch")
        if connection.execute("SELECT current_database()").fetchone()[0] != context.database_name:
            raise MediaScopeError("Cleanup database mismatch")
    elif context is not None:
        raise MediaScopeError("Legacy storage cannot clean a business namespace")
    connection.execute("SELECT 1 FROM public.photos LIMIT 1").fetchone()
    connection.execute("SELECT 1 FROM public.plans_seuils_lit LIMIT 1").fetchone()
    now = time.time() if now is None else now
    deleted = 0
    for manifest in storage.candidates():
        if manifest["expires_at"] > now:
            continue
        with connection.transaction():
            owner = manifest["owner"]
            if owner["kind"] == "observation-photo":
                lock_photo(connection, owner["photo_id"])
            elif owner["kind"] == "seuil-plan":
                lock_seuil_plan(connection, owner["plan_id"])
            else:
                raise MediaScopeError("Unknown media owner")
            # SQL is authoritative even if a crash occurred immediately after COMMIT.
            table = "photos" if owner["kind"] == "observation-photo" else "plans_seuils_lit"
            exists = connection.execute(
                f"SELECT 1 FROM public.{table} WHERE storage_key=%s",
                (manifest["storage_key"],),
            ).fetchone()
            if not exists:
                try:
                    storage.discard(manifest["storage_key"])
                    deleted += 1
                except FileNotFoundError:
                    pass  # Another collector already reconciled this candidate.
    connection.execute("SELECT 1 FROM public.photos LIMIT 1").fetchone()
    connection.execute("SELECT 1 FROM public.plans_seuils_lit LIMIT 1").fetchone()
    return {
        "prepared_removed": deleted,
        "partials_removed": storage.clean_incoming(now - PREPARED_TTL),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Supprime les préparations expirées non référencées (jamais les Photos)."
    )
    parser.parse_args()
    from .database import open_write_connection

    with open_write_connection() as connection:
        print(json.dumps(clean_prepared(connection, get_media_storage())))
