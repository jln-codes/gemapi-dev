"""Connected Observation Photos: prepared media, generic receipts and OCC."""
from __future__ import annotations

import datetime
import logging
import re
import time
from typing import Literal
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from digues_app.media_contract import ABSOLUTE_MEDIA_LIMIT_BYTES

from .database import get_connection, get_write_connection, WebDatabaseError
from .media_storage import get_media_storage, validate_image, lock_photo, fail, MediaScopeError
from .resource_journal import mutation_origin
from .resource_mutations import execute_mutation
from .photo_localisation import LOCALISATION_FIELDS_SQL, read_localisation

LOGGER = logging.getLogger(__name__)
SHA_PATTERN = r"^[0-9a-f]{64}$"


class PhotoMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: datetime.date | None = None
    libelle: str | None = Field(default=None, max_length=2000)
    designation: str | None = Field(default=None, max_length=2000)
    orientation_photo_id: str | None = Field(default=None, max_length=200)
    cote_id: str | None = Field(default=None, max_length=200)
    photographe_id: UUID | None = None

    @field_validator("libelle", "designation", "orientation_photo_id", "cote_id")
    @classmethod
    def text_or_none(cls, value):
        return value.strip() or None if value is not None else None


class PhotoCreate(PhotoMetadata):
    id: UUID
    observation_id: UUID
    upload_ref: UUID
    file_sha256: str = Field(pattern=SHA_PATTERN)
    file_size: int = Field(gt=0, le=ABSOLUTE_MEDIA_LIMIT_BYTES, strict=True)


PHOTO_SQL = f"""
SELECT jsonb_build_object(
    'id',p.id, 'observation_id',p.observation_id, 'version',p.version,
    'date',p.date_photo, 'designation',p.designation, 'libelle',p.libelle,
    'commentaire',p.commentaire, 'valid',p.valid,
    'photographe_id',p.photographe_id,
    'cote_id',p.cote_id, 'cote_libelle',c.libelle,
    'orientation_photo_id',p.orientation_photo_id, 'orientation_photo_libelle',r.libelle,
    '_storage_key',p.storage_key, '_raw_localisation',{LOCALISATION_FIELDS_SQL},
    'nom_fichier',NULL
)
FROM public.photos p
LEFT JOIN referentiel.cotes c ON c.id=p.cote_id
LEFT JOIN referentiel.orientations_photo r ON r.id=p.orientation_photo_id
WHERE p.id=%s
"""


def enrich_photo(photo, *, context=None):
    key = photo.pop("_storage_key", None)
    photo.update(content_available=False, content_url=None, variants={},
                 content_state="media_unavailable" if key else "unassociated")
    if not key:
        return photo
    try:
        storage = get_media_storage(required=False)
        manifest = storage.inspect(key) if storage else None
        if not manifest:
            return photo
        if manifest["owner"]["photo_id"] != str(photo["id"]) or manifest["owner"]["observation_id"] != str(photo["observation_id"]):
            raise MediaScopeError("Photo owner mismatch")
        photo["nom_fichier"] = manifest["source_name"]
        prefix = "/api"
        photo["variants"] = {name: {**item, "content_url": f"{prefix}/photos/{photo['id']}/content?variant={name}"}
                             for name, item in manifest["variants"].items()
                             if name in {"original", "preview", "thumbnail"}}
        photo["content_available"] = "original" in photo["variants"]
        if photo["content_available"]:
            photo["content_state"] = "available"
        photo["content_url"] = f"{prefix}/photos/{photo['id']}/content" if photo["content_available"] else None
    except MediaScopeError as exc:
        raise HTTPException(503, "Identité du stockage média incohérente.") from exc
    except (OSError, ValueError, KeyError, HTTPException):
        LOGGER.warning("Contenu Photo indisponible : %s", photo.get("id"))
    return photo


def read_photo(connection, identifier, *, context=None):
    row = connection.execute(PHOTO_SQL, (identifier,)).fetchone()
    if not row:
        fail(404, "photo-not-found", "Photo introuvable.")
    photo = enrich_photo(row[0], context=context)
    photo["localisation"] = read_localisation(connection, identifier, photo.pop("_raw_localisation"))
    return photo


def validate_references(connection, values):
    for field, table in (("cote_id", "referentiel.cotes"), ("orientation_photo_id", "referentiel.orientations_photo")):
        value = values.get(field)
        if value is not None and not connection.execute(f"SELECT 1 FROM {table} WHERE id=%s AND valid", (value,)).fetchone():
            fail(422, "photo-reference-invalid", "Le côté ou l’orientation est absent ou invalide.")
    contact = values.get("photographe_id")
    if contact is not None and not connection.execute(
        "SELECT 1 FROM public.contacts WHERE id=%s AND valid FOR KEY SHARE", (contact,)
    ).fetchone():
        fail(422, "photo-photographer-invalid", "Le contact photographe est absent ou invalide.")


def prepared_descriptor(manifest):
    original = manifest["variants"]["original"]
    return {"upload_ref": manifest["storage_key"], "id": manifest["owner"]["photo_id"],
            "observation_id": manifest["owner"]["observation_id"], "file_sha256": original["sha256"],
            "file_size": original["size"], "mime_type": original["mime_type"],
            "expires_at": manifest["expires_at"], "nom_fichier": manifest["source_name"]}


def check_prepared(connection, manifest, photo_id, observation_id, digest, *, allow_expired=False):
    if not manifest:
        fail(404, "upload-not-found", "Fichier préparé introuvable. Sélectionnez de nouveau le fichier.")
    if manifest["owner"] != {"kind": "observation-photo", "photo_id": str(photo_id), "observation_id": str(observation_id)} or manifest["variants"]["original"]["sha256"] != digest:
        fail(409, "upload-owner-conflict", "Ce média est réservé à une autre Photo, Observation ou image.")
    # Committed objects never expire. SQL, not a sidecar 'consumed' flag, is authoritative.
    consumed = connection.execute("SELECT id FROM public.photos WHERE storage_key=%s", (manifest["storage_key"],)).fetchone()
    if not consumed and manifest["expires_at"] <= time.time() and not allow_expired:
        fail(410, "upload-expired", "La préparation a expiré. Sélectionnez de nouveau le fichier.")
    return consumed


def create_photo(connection, creation, operation_id, origin=None, *, context=None):
    # A default day is resolved only inside write(), AFTER lookup of the receipt.
    changes = creation.model_dump(mode="json", exclude={"upload_ref"})
    changes["date_default"] = "date" not in creation.model_fields_set
    storage = None
    installed = False

    def write():
        nonlocal storage, installed
        lock_photo(connection, creation.id)
        if connection.execute("SELECT 1 FROM public.photos WHERE id=%s", (creation.id,)).fetchone():
            fail(409, "photo-id-collision", "Cet UUID Photo est déjà utilisé.")
        if not connection.execute("SELECT 1 FROM public.observations WHERE id=%s FOR KEY SHARE", (creation.observation_id,)).fetchone():
            fail(422, "observation-not-found", "L’Observation parente n’existe pas.")
        values = creation.model_dump(include=set(PhotoMetadata.model_fields))
        validate_references(connection, values)
        storage = get_media_storage()
        manifest = storage.inspect(creation.upload_ref)
        consumed = check_prepared(connection, manifest, creation.id, creation.observation_id, creation.file_sha256)
        if consumed:
            fail(409, "upload-consumed", "Ce média a déjà été utilisé pour une Photo.")
        if manifest["variants"]["original"]["size"] != creation.file_size:
            fail(422, "file-size-mismatch", "La taille du média ne correspond pas à l’intention.")
        if changes["date_default"]:
            values["date"] = datetime.date.today()
        installed = True  # complete, fsynced original already exists; SQL makes it visible.
        connection.execute(
            "INSERT INTO public.photos (id,observation_id,storage_key,date_photo,libelle,designation,"
            "orientation_photo_id,cote_id,photographe_id,valid) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,true)",
            (creation.id, creation.observation_id, creation.upload_ref, values["date"], values["libelle"],
             values["designation"], values["orientation_photo_id"], values["cote_id"], values["photographe_id"]),
        )
        return read_photo(connection, creation.id, context=context)

    try:
        return execute_mutation(connection, "photos", "create", write, identifier=creation.id,
                                operation_id=operation_id, changes=changes, origin=origin)
    except Exception:
        # Includes unknown COMMIT results. Never assume that a raised exception
        # implies rollback: recheck on a usable connection and under the shared lock.
        if installed and storage:
            try:
                with connection.transaction():
                    lock_photo(connection, creation.id)
                    if not connection.execute("SELECT 1 FROM public.photos WHERE storage_key=%s", (str(creation.upload_ref),)).fetchone():
                        storage.discard(creation.upload_ref)
            except Exception:
                LOGGER.warning("Nettoyage Photo différé à la réconciliation : %s", creation.id)
        raise


def update_photo(connection, identifier, update, *, context=None):
    values = update.model_dump(exclude_unset=True)
    with connection.transaction():
        row = connection.execute(
            "SELECT orientation_photo_id,cote_id,photographe_id "
            "FROM public.photos WHERE id=%s FOR UPDATE", (identifier,),
        ).fetchone()
        if row is None:
            fail(404, "photo-not-found", "Photo introuvable.")
        previous = dict(zip(("orientation_photo_id", "cote_id", "photographe_id"), row))
        validate_references(connection, {field: value for field, value in values.items()
                                        if field not in previous or value != previous[field]})
        if values:
            sql_columns = {"date": "date_photo"}
            connection.execute("UPDATE public.photos SET " + ",".join(
                f"{sql_columns.get(name, name)}=%s"
                for name in values
            ) + " WHERE id=%s",
                               (*values.values(), identifier))
        return read_photo(connection, identifier, context=context)


def register_routes(application):
    from .database import get_connection, get_write_connection
    read_connection = get_connection
    create_connection = get_write_connection
    update_connection = get_write_connection
    from .photo_localisation import register_routes as register_localisation
    register_localisation(application)
    @application.put("/api/photo-uploads/{photo_id}/{sha256}")
    async def upload_photo(photo_id: UUID, sha256: str, request: Request, observation_id: UUID,
                           filename: str = Query(max_length=200), connection=Depends(create_connection)):
        if not re.fullmatch(SHA_PATTERN, sha256):
            fail(422, "file-hash-invalid", "Empreinte SHA-256 invalide.")
        try:
            if not connection.execute("SELECT 1 FROM public.observations WHERE id=%s", (observation_id,)).fetchone():
                fail(422, "observation-not-found", "L’Observation parente n’existe pas.")
            storage = get_media_storage()
            declared = request.headers.get("content-length")
            if declared and (not declared.isdigit() or int(declared) > ABSOLUTE_MEDIA_LIMIT_BYTES):
                fail(413, "file-too-large", "Le fichier dépasse la limite de 20 Mio.")
            # Raw binary body: bounded streaming, no base64 or multipart buffering.
            with storage.incoming() as stream:
                size = 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > ABSOLUTE_MEDIA_LIMIT_BYTES:
                        fail(413, "file-too-large", "Le fichier dépasse la limite de 20 Mio.")
                    stream.write(chunk)
                variant = await run_in_threadpool(validate_image, stream, filename, request.headers.get("content-type", "").split(";")[0], sha256)
                def prepare():
                    with connection.transaction():
                        lock_photo(connection, photo_id)
                        existing_photo = connection.execute("SELECT storage_key FROM public.photos WHERE id=%s", (photo_id,)).fetchone()
                        existing = storage.prepared_for(photo_id)
                        if existing_photo and (not existing or str(existing_photo[0]) != existing["storage_key"]):
                            fail(409, "photo-id-collision", "Cet UUID Photo est déjà utilisé.")
                        if existing:
                            consumed = check_prepared(connection, existing, photo_id, observation_id, sha256,
                                                      allow_expired=True)
                            if existing["variants"]["original"] != variant:
                                fail(409, "upload-collision", "Cette préparation correspond à un autre contenu.")
                            if not consumed and existing["expires_at"] <= time.time():
                                # The shared Photo lock excludes finalization/GC. The
                                # SQL check above is authoritative, including lost replies.
                                # discard remains recoverable if the process stops here.
                                storage.discard(existing["storage_key"])
                        return prepared_descriptor(storage.prepare(stream, photo_id, observation_id, filename, variant))
                return await run_in_threadpool(prepare)
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(503, {"code": "media-unavailable", "message": "Le stockage du fichier est indisponible."}) from exc

    @application.get("/api/photo-uploads/{photo_id}/{sha256}")
    def upload_status(photo_id: UUID, sha256: str, observation_id: UUID, connection=Depends(create_connection)):
        try:
            with connection.transaction():
                lock_photo(connection, photo_id)
                if not connection.execute("SELECT 1 FROM public.observations WHERE id=%s", (observation_id,)).fetchone():
                    fail(422, "observation-not-found", "L’Observation parente n’existe pas.")
                storage = get_media_storage()
                manifest = storage.prepared_for(photo_id)
                check_prepared(connection, manifest, photo_id, observation_id, sha256)
                return prepared_descriptor(manifest)
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(503, "Stockage des photos indisponible.") from exc

    @application.post("/api/photos", status_code=201)
    def post_photo(creation: PhotoCreate, operation_id: str = Header(alias="Operation-Id"),
                   origin=Depends(mutation_origin), connection=Depends(create_connection)):
        try:
            return create_photo(connection, creation, operation_id, origin)
        except HTTPException:
            raise
        except Exception as exc:
            raise WebDatabaseError("Création Photo non confirmée.") from exc

    @application.get("/api/photos/{photo_id}")
    def get_photo(photo_id: UUID, connection=Depends(read_connection)):
        return read_photo(connection, photo_id)

    @application.put("/api/photos/{photo_id}")
    def put_photo(photo_id: UUID, update: PhotoMetadata, if_match: str | None = Header(default=None),
                  operation_id: str = Header(alias="Operation-Id"), origin=Depends(mutation_origin),
                  connection=Depends(update_connection)):
        return execute_mutation(connection, "photos", "update", lambda: update_photo(connection, photo_id, update),
                                identifier=photo_id, if_match=if_match, operation_id=operation_id, origin=origin,
                                changes=update.model_dump(mode="json", exclude_unset=True))

    @application.get("/api/photos/{photo_id}/content")
    def photo_content(photo_id: UUID, variant: Literal["original", "preview", "thumbnail"] = "original",
                      connection=Depends(read_connection)):
        row = connection.execute("SELECT storage_key,observation_id FROM public.photos WHERE id=%s", (photo_id,)).fetchone()
        if not row or not row[0]:
            fail(404, "photo-content-unavailable", "Fichier Photo indisponible.")
        try:
            storage = get_media_storage()
            manifest = storage.inspect(row[0])
            if manifest and (manifest["owner"]["photo_id"] != str(photo_id)
                    or manifest["owner"]["observation_id"] != str(row[1])):
                raise MediaScopeError("Photo owner mismatch")
            if not manifest or manifest["owner"]["photo_id"] != str(photo_id) or manifest["owner"]["observation_id"] != str(row[1]) or variant not in manifest["variants"]:
                fail(404, "photo-content-unavailable", "Cette variante Photo n’est pas disponible.")
            item = manifest["variants"][variant]
            stream = storage.open_variant(row[0], variant)
            def chunks():
                try:
                    while chunk := stream.read(65536):
                        yield chunk
                finally:
                    stream.close()
            return StreamingResponse(chunks(), media_type=item["mime_type"], headers={
                "Content-Length": str(item["size"]), "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store", "ETag": '"' + item["sha256"] + '"',
                "Content-Disposition": f'inline; filename="{photo_id}-{variant}"',
            })
        except FileNotFoundError:
            fail(404, "photo-content-unavailable", "Fichier Photo indisponible.")
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(503, "Stockage des photos indisponible.") from exc
