"""Provider-neutral validation and immutable media bundle publication.

This module deliberately has no HTTP or database dependency.  Callers select
an explicit storage namespace, validate bytes, and coordinate SQL separately.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable, Protocol
from uuid import UUID, uuid4
import fcntl
import hashlib
import json
import logging
import math
import os
import shutil
import stat
import tempfile
import threading
import time

from PIL import Image

from .media_contract import ABSOLUTE_MEDIA_LIMIT_BYTES
from .media_lifecycle import namespace_writer, namespace_lease


MAX_PIXELS = 25_000_000
PREPARED_TTL = 24 * 3600
FORMATS = {
    "JPEG": ("image/jpeg", {".jpg", ".jpeg"}),
    "PNG": ("image/png", {".png"}),
    "WEBP": ("image/webp", {".webp"}),
}
DECODE_LOCK = threading.BoundedSemaphore(1)
LOGGER = logging.getLogger(__name__)


class MediaError(ValueError):
    """Base error carrying a stable application-neutral media reason."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class MediaValidationError(MediaError):
    """Input bytes or their declared metadata violate the media contract."""


class MediaPublicationError(MediaError):
    """An immutable bundle cannot be published for the requested owner."""


class MediaScopeError(MediaError):
    """Persisted media identity disagrees with the selected namespace."""

    def __init__(self, message: str):
        super().__init__("media-scope-mismatch", message)


@dataclass(frozen=True, slots=True)
class PhotoMediaOwner:
    """Explicit owner recorded in every observation-photo bundle."""

    photo_id: UUID
    observation_id: UUID

    def __init__(self, photo_id: UUID | str, observation_id: UUID | str):
        object.__setattr__(self, "photo_id", UUID(str(photo_id)))
        object.__setattr__(self, "observation_id", UUID(str(observation_id)))

    def manifest_value(self) -> dict[str, str]:
        return {
            "kind": "observation-photo",
            "photo_id": str(self.photo_id),
            "observation_id": str(self.observation_id),
        }

    @property
    def identity_id(self) -> UUID:
        return self.photo_id


@dataclass(frozen=True, slots=True)
class SeuilPlanMediaOwner:
    """Technical file owned by one identified Plan and its Seuil."""

    plan_id: UUID
    seuil_id: UUID

    def __init__(self, plan_id: UUID | str, seuil_id: UUID | str):
        object.__setattr__(self, "plan_id", UUID(str(plan_id)))
        object.__setattr__(self, "seuil_id", UUID(str(seuil_id)))

    @property
    def identity_id(self) -> UUID:
        return self.plan_id

    def manifest_value(self) -> dict[str, str]:
        return {"kind": "seuil-plan", "plan_id": str(self.plan_id),
                "seuil_id": str(self.seuil_id)}


def validate_technical_file(stream: BinaryIO, filename: str, expected_hash: str,
                            *, max_bytes: int = 1_073_741_824) -> dict[str, str | int]:
    """Hash arbitrary Plan bytes; extensions and formats are not restricted."""
    if (not filename or len(filename) > 255 or filename in {".", ".."}
            or any(char in filename for char in "/\\:")
            or any(ord(char) < 32 or ord(char) == 127 for char in filename)):
        raise MediaValidationError("file-name-invalid", "Nom de fichier invalide.")
    stream.seek(0, 2)
    size = stream.tell()
    if size == 0:
        raise MediaValidationError("file-empty", "Le fichier est vide.")
    if size > max_bytes:
        raise MediaValidationError("file-too-large", "Le fichier dépasse la limite configurée.")
    stream.seek(0)
    digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != expected_hash:
        raise MediaValidationError("file-hash-mismatch", "Le fichier reçu est incomplet ou différent.")
    stream.seek(0)
    return {"sha256": digest, "size": size, "mime_type": "application/octet-stream",
            "verified_at_upload": True}


def _safe_parents(path: Path) -> None:
    # Check every component, not only the last directory (including broken links).
    for part in reversed((path, *path.parents)):
        if part.is_symlink():
            raise OSError("Symlink in private media path")


def open_private_file(path: Path) -> int:
    """Walk with directory descriptors: no symlink in any read path component."""
    path = Path(path).absolute()
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:-1]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory,
            )
            os.close(directory)
            directory = child
        return os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    finally:
        os.close(directory)


def validate_image(
    stream: BinaryIO,
    filename: str,
    content_type: str,
    expected_hash: str,
) -> dict[str, str | int]:
    """Validate and fully decode one original, returning its manifest descriptor."""
    if (
        not filename
        or len(filename) > 200
        or filename in {".", ".."}
        or any(character in filename for character in "/\\:")
        or any(ord(character) < 32 or ord(character) == 127 for character in filename)
    ):
        raise MediaValidationError("file-name-invalid", "Nom de fichier invalide.")
    stream.seek(0, 2)
    size = stream.tell()
    if size == 0:
        raise MediaValidationError("file-empty", "Le fichier est vide.")
    if size > ABSOLUTE_MEDIA_LIMIT_BYTES:
        raise MediaValidationError(
            "file-too-large", "Le fichier dépasse la limite de 20 Mio."
        )
    stream.seek(0)
    digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != expected_hash:
        raise MediaValidationError(
            "file-hash-mismatch",
            "Le fichier reçu est incomplet ou différent du fichier choisi.",
        )
    with DECODE_LOCK:
        try:
            stream.seek(0)
            with Image.open(stream) as picture:
                kind = picture.format
                width, height = picture.size
                if kind not in FORMATS or getattr(picture, "n_frames", 1) != 1:
                    raise MediaValidationError(
                        "file-type-forbidden",
                        "Choisissez une image JPEG, PNG ou WebP statique.",
                    )
                if width * height > MAX_PIXELS or max(width, height) > 20000:
                    raise MediaValidationError(
                        "file-dimensions",
                        "L’image dépasse la limite de 25 millions de pixels.",
                    )
                mime_type, extensions = FORMATS[kind]
                if (
                    Path(filename).suffix.lower() not in extensions
                    or content_type not in {mime_type, "application/octet-stream", ""}
                ):
                    raise MediaValidationError(
                        "file-type-mismatch",
                        "Le type ou l’extension ne correspond pas au contenu de l’image.",
                    )
                picture.verify()
            stream.seek(0)
            with Image.open(stream) as picture:
                picture.load()  # verify alone does not decode pixel data.
        except MediaValidationError:
            raise
        except Exception as exc:
            raise MediaValidationError(
                "file-invalid",
                "Le fichier n’est pas une image valide et complète.",
            ) from exc
    stream.seek(0)
    return {
        "sha256": digest,
        "size": size,
        "mime_type": mime_type,
        "width": width,
        "height": height,
    }


class MediaStorage(Protocol):
    """Provider-neutral identities and variants; paths never cross this interface."""

    def incoming(self): ...
    def prepare(
        self,
        stream: BinaryIO,
        photo_id: UUID | str,
        observation_id: UUID | str,
        filename: str,
        variant: dict,
    ) -> dict: ...
    def prepare_seuil_plan(self, stream: BinaryIO, plan_id: UUID | str,
                           seuil_id: UUID | str, filename: str, variant: dict) -> dict: ...
    def publish_revision(
        self,
        stream: BinaryIO,
        owner: PhotoMediaOwner,
        source_name: str,
        variant: dict,
    ) -> dict: ...
    def prepared_for(self, photo_id: UUID | str, *, kind: str = "observation-photo") -> dict | None: ...
    def inspect(self, storage_key: UUID | str) -> dict | None: ...
    def inspect_existing(self, storage_key: UUID | str) -> dict | None: ...
    def open_variant(self, storage_key: UUID | str, variant: str = "original") -> BinaryIO: ...
    # Both require reconciliation with SQL under the shared Photo lock.
    # An interrupted discard must remain enumerable without its media manifest.
    @namespace_writer
    def discard(self, storage_key: UUID | str): ...
    def candidates(self): ...
    @namespace_writer
    def clean_incoming(self, cutoff: float): ...


def _sync(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class FilesystemMediaStorage:
    """Private filesystem adapter for immutable, explicitly scoped bundles."""

    def __init__(
        self,
        root: str | Path,
        *,
        business_database_id: UUID | str | None = None,
        media_namespace: UUID | str | None = None,
        forbidden_roots: Iterable[str | Path] = (),
    ):
        self.root = Path(root).absolute()
        self.business_database_id = (
            UUID(str(business_database_id)) if business_database_id is not None else None
        )
        self.media_namespace = (
            UUID(str(media_namespace)) if media_namespace is not None else None
        )
        if (self.business_database_id is None) != (self.media_namespace is None):
            raise MediaScopeError("Incomplete media target")
        # Root is operator configuration, not a path supplied by a client.
        _safe_parents(self.root)
        resolved = self.root.resolve()
        if any(resolved.is_relative_to(Path(root).resolve()) for root in forbidden_roots):
            raise OSError("Media root must be outside forbidden roots")

    @namespace_writer
    def _dir(self, name: str) -> Path:
        _safe_parents(self.root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._check_namespace()
        path = self.root / name
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        else:
            _sync(self.root)
        if path.is_symlink():
            raise OSError("Unsafe media directory")
        return path

    def _identity(self) -> dict[str, str | int]:
        if self.business_database_id is None:
            return {"schema_version": 1}
        return {
            "schema_version": 2,
            "business_database_id": str(self.business_database_id),
            "media_namespace": str(self.media_namespace),
        }

    @namespace_writer
    def prepare_namespace(self) -> None:
        """Operator provisioning: initialize/verify this namespace, never delete."""
        if self.business_database_id is None:
            raise MediaScopeError("Provisioning requires an explicit business target")
        for name in ("objects", "bindings", "incoming", "deleting", "trash"):
            self._dir(name)

    def _validate_scope(self, value: dict) -> None:
        if not isinstance(value, dict) or any(
            value.get(key) != expected for key, expected in self._identity().items()
        ):
            if self.business_database_id is not None:
                raise MediaScopeError("Media namespace/manifest mismatch")
            raise ValueError("Media namespace/manifest mismatch")

    def _check_namespace(self) -> None:
        if self.business_database_id is None:
            return
        marker = self.root / "namespace.json"
        try:
            self._validate_scope(self._read(marker))
            return
        except FileNotFoundError:
            pass
        # Never silently adopt an existing non-empty directory, or overwrite a
        # concurrent marker. A new namespace is initialized only when empty.
        if any(
            path.name != "namespace.json" and not path.name.startswith(".namespace-")
            for path in self.root.iterdir()
        ):
            try:
                self._validate_scope(self._read(marker))
                return  # Another publisher initialized the namespace meanwhile.
            except FileNotFoundError:
                raise MediaScopeError("Unidentified non-empty media namespace") from None
        descriptor, name = tempfile.mkstemp(prefix=".namespace-", dir=self.root)
        try:
            with os.fdopen(descriptor, "w") as output:
                json.dump(self._identity(), output)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(name, marker)
                _sync(self.root)
            except FileExistsError:
                pass
            self._validate_scope(self._read(marker))
        finally:
            Path(name).unlink(missing_ok=True)

    def _bundle(self, key: UUID | str) -> Path:
        path = self._dir("objects") / str(UUID(str(key)))
        if path.is_symlink():
            raise OSError("Unsafe media bundle")
        return path

    @staticmethod
    def _read(path: Path) -> dict:
        descriptor = open_private_file(path)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise OSError("Not a regular media file")
            return json.loads(stream.read(16384))

    def inspect(self, storage_key: UUID | str) -> dict | None:
        try:
            bundle = self._bundle(storage_key)
            value = self._read(bundle / "manifest.json")
            self._validate_scope(value)
            if value["storage_key"] != str(storage_key):
                if self.business_database_id is not None:
                    raise MediaScopeError("Invalid manifest key")
                raise ValueError("Invalid manifest key")
            with self.open_variant(storage_key) as stream:
                if os.fstat(stream.fileno()).st_size != value["variants"]["original"]["size"]:
                    raise ValueError("Incomplete media")
            return value
        except FileNotFoundError:
            return None

    def inspect_existing(self, storage_key: UUID | str) -> dict | None:
        """Read an existing scoped bundle without creating directories or a marker.

        This checks manifest identity, original presence and size. It does not
        rehash the complete original; callers must not call it a SHA verification.
        """
        key = str(UUID(str(storage_key)))
        _safe_parents(self.root)
        if not self.root.is_dir():
            return None
        if self.business_database_id is not None:
            self._validate_scope(self._read(self.root / "namespace.json"))
        bundle = self.root / "objects" / key
        try:
            value = self._read(bundle / "manifest.json")
            self._validate_scope(value)
            if value["storage_key"] != key:
                raise MediaScopeError("Invalid manifest key")
            descriptor = value["variants"]["original"]
            with os.fdopen(open_private_file(bundle / "original"), "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise OSError("Not a regular media file")
                if os.fstat(stream.fileno()).st_size != descriptor["size"]:
                    raise ValueError("Incomplete media")
            return value
        except FileNotFoundError:
            return None

    @staticmethod
    def _binding_name(identifier: UUID | str, kind: str) -> str:
        uid = str(UUID(str(identifier)))
        if kind == "observation-photo":
            return uid + ".json"
        if kind == "seuil-plan":
            return "seuil-plan-" + uid + ".json"
        raise ValueError("Unknown media owner kind")

    def prepared_for(self, photo_id: UUID | str, *, kind: str = "observation-photo") -> dict | None:
        try:
            binding = self._read(
                self._dir("bindings") / self._binding_name(photo_id, kind)
            )
        except FileNotFoundError:
            return None
        if self.business_database_id is not None:
            self._validate_scope(binding)
        manifest = self.inspect(binding["storage_key"])
        if manifest is None:
            raise ValueError("Media binding refers to an incomplete or missing bundle")
        if manifest["owner"]["kind"] != kind:
            raise MediaScopeError("Media binding owner kind mismatch")
        return manifest

    @contextmanager
    def incoming(self):
        from contextlib import nullcontext
        with (namespace_lease(self.root,self.business_database_id) if self.business_database_id else nullcontext()):
            descriptor, name = tempfile.mkstemp(dir=self._dir("incoming"), suffix=".part")
            try:
                with os.fdopen(descriptor, "w+b") as stream:
                    fcntl.flock(stream, fcntl.LOCK_EX)
                    yield stream
            finally:
                Path(name).unlink(missing_ok=True)

    def prepare(
        self,
        stream: BinaryIO,
        photo_id: UUID | str,
        observation_id: UUID | str,
        filename: str,
        variant: dict,
    ) -> dict:
        """Publish once or return the identical active preparation."""
        owner = PhotoMediaOwner(photo_id, observation_id)
        existing = self.prepared_for(owner.photo_id)
        if existing:
            if (
                existing["owner"] != owner.manifest_value()
                or existing["variants"]["original"] != variant
            ):
                raise MediaPublicationError(
                    "upload-collision",
                    "Cet UUID Photo possède déjà un autre fichier ou rattachement.",
                )
            return existing
        return self._publish(stream, owner, filename, variant)

    def prepare_seuil_plan(self, stream: BinaryIO, plan_id: UUID | str,
                           seuil_id: UUID | str, filename: str, variant: dict) -> dict:
        owner = SeuilPlanMediaOwner(plan_id, seuil_id)
        existing = self.prepared_for(owner.identity_id, kind="seuil-plan")
        if existing:
            if (existing["owner"] != owner.manifest_value()
                    or existing["variants"]["original"] != variant):
                raise MediaPublicationError("upload-collision",
                                            "Ce Plan possède déjà un autre fichier ou Seuil.")
            return existing
        return self._publish(stream, owner, filename, variant)

    def publish_revision(
        self,
        stream: BinaryIO,
        owner: PhotoMediaOwner,
        source_name: str,
        variant: dict,
    ) -> dict:
        """Publish and bind a new immutable revision without deleting the old one.

        The caller must serialize publication for this Photo. SQL remains the
        authority deciding which revision is consumed and when an older bundle
        may be discarded.
        """
        existing = self.prepared_for(owner.photo_id)
        if existing is None:
            raise MediaPublicationError(
                "media-owner-unprepared",
                "Aucun bundle existant n’appartient à cette Photo.",
            )
        if existing["owner"] != owner.manifest_value():
            raise MediaPublicationError(
                "upload-collision",
                "Cet UUID Photo possède déjà un autre rattachement.",
            )
        return self._publish(stream, owner, source_name, variant)

    @namespace_writer
    def _publish(
        self,
        stream: BinaryIO,
        owner: PhotoMediaOwner,
        source_name: str,
        variant: dict,
    ) -> dict:
        key = str(uuid4())
        now = time.time()
        manifest = {
            **self._identity(),
            "storage_key": key,
            "owner": owner.manifest_value(),
            "source_name": source_name,
            "prepared_at": now,
            "expires_at": now + PREPARED_TTL,
            "variants": {"original": variant},
        }
        # A complete immutable bundle is published with a same-filesystem rename.
        temporary = Path(tempfile.mkdtemp(dir=self._dir("incoming")))
        lease = (temporary / "lease").open("xb")
        fcntl.flock(lease, fcntl.LOCK_EX)
        try:
            with (temporary / "original").open("xb") as output:
                stream.seek(0)
                shutil.copyfileobj(stream, output, 65536)
                output.flush()
                os.fsync(output.fileno())
            with (temporary / "manifest.json").open("x", encoding="utf-8") as output:
                json.dump(manifest, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            _sync(temporary)
            os.rename(temporary, self._bundle(key))
            _sync(self._dir("objects"))
            binding = self._dir("bindings") / self._binding_name(
                owner.identity_id, owner.manifest_value()["kind"])
            # Binding publication is atomic. Revisions replace only this index;
            # every previously published immutable bundle remains untouched.
            binding_temporary = self._dir("bindings") / (str(uuid4()) + ".tmp")
            try:
                with binding_temporary.open("x") as output:
                    json.dump({**self._identity(), "storage_key": key}, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(binding_temporary, binding)
                _sync(self._dir("bindings"))
            finally:
                binding_temporary.unlink(missing_ok=True)
            return manifest
        except BaseException:
            # Publication may already have completed: orphan reconciliation uses SQL.
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
        finally:
            lease.close()

    def open_variant(self, storage_key: UUID | str, variant: str = "original") -> BinaryIO:
        if variant not in {"original", "preview", "thumbnail"}:
            raise FileNotFoundError("Unknown variant")
        bundle = self._bundle(storage_key)
        self._validate_scope(self._read(bundle / "manifest.json"))
        descriptor = open_private_file(bundle / variant)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise OSError("Not a regular media file")
        return os.fdopen(descriptor, "rb")

    def _cleanup_identity(self, value: dict, key: str) -> dict:
        """Never authorize deletion from a directory name or absent manifest alone."""
        self._validate_scope(value)
        if value["storage_key"] != key:
            raise ValueError("Invalid cleanup identity")
        if str(UUID(key)) != key or value["owner"]["kind"] not in {"observation-photo", "seuil-plan"}:
            raise ValueError("Invalid cleanup owner")
        owner_fields = (("photo_id", "observation_id") if value["owner"]["kind"] == "observation-photo"
                        else ("plan_id", "seuil_id"))
        for field in owner_fields:
            if str(UUID(value["owner"][field])) != value["owner"][field]:
                raise ValueError("Invalid cleanup owner")
        if not math.isfinite(value["expires_at"]):
            raise ValueError("Invalid cleanup expiry")
        return value

    def _deletion(self, key: str) -> Path:
        return self._dir("deleting") / (str(UUID(str(key))) + ".json")

    def _read_deletion(self, key: str) -> dict:
        value = self._cleanup_identity(self._read(self._deletion(key)), key)
        if value.get("state") != "deleting":
            raise ValueError("Invalid deletion intent")
        return value

    def candidates(self):
        pending: set[str] = set()
        # This durable identity outlives the bundle, including its manifest.
        for path in self._dir("deleting").glob("*.json"):
            key = path.stem
            pending.add(key)
            try:
                yield self._read_deletion(key)
            except MediaScopeError:
                raise
            except (ValueError, KeyError, TypeError, OSError):
                LOGGER.warning("Intention de nettoyage média illisible : %s", key)
        for path in self._dir("objects").iterdir():
            if path.name in pending or path.is_symlink() or not path.is_dir():
                continue
            try:
                yield self._cleanup_identity(self._read(path / "manifest.json"), path.name)
            except MediaScopeError:
                raise
            except (ValueError, KeyError, TypeError, OSError):
                LOGGER.warning("Bundle média à réconcilier manuellement : %s", path.name)

    @namespace_writer
    def discard(self, storage_key: UUID | str) -> None:
        # Caller holds the Photo lock and has checked SQL, also on every retry.
        key = str(UUID(str(storage_key)))
        bundle = self._bundle(storage_key)
        intent = self._deletion(key)
        trash = self._dir("trash") / key
        if trash.is_symlink():
            raise OSError("Unsafe media quarantine")
        try:
            value = self._read_deletion(key)
        except FileNotFoundError:
            value = self._cleanup_identity(self._read(bundle / "manifest.json"), key)
            # No overwrite of an unexplained quarantine: it is not proof of SQL safety.
            if trash.exists():
                raise OSError("Untracked media quarantine")
            value = {
                **self._identity(),
                "state": "deleting",
                "storage_key": key,
                "owner": value["owner"],
                "expires_at": 0,
            }
            temporary = self._dir("bindings") / (str(uuid4()) + ".tmp")
            try:
                with temporary.open("x", encoding="utf-8") as output:
                    json.dump(value, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, intent)
                _sync(self._dir("deleting"))
            finally:
                temporary.unlink(missing_ok=True)
        if bundle.exists():
            if trash.exists():
                raise OSError("Duplicate media quarantine")
            os.rename(bundle, trash)
        # Make the move durable before destroying any data in quarantine.
        _sync(self._dir("objects"))
        _sync(self._dir("trash"))
        owner_id = value["owner"].get("photo_id", value["owner"].get("plan_id"))
        binding = self._dir("bindings") / self._binding_name(
            owner_id, value["owner"]["kind"])
        try:
            binding_value = self._read(binding)
            if self.business_database_id is not None:
                self._validate_scope(binding_value)
            if binding_value["storage_key"] == key:
                binding.unlink()
        except FileNotFoundError:
            pass
        _sync(self._dir("bindings"))
        if trash.exists():
            shutil.rmtree(trash)
        _sync(self._dir("trash"))
        # Last step only: subsequent passes can resume every partial rmtree.
        intent.unlink()
        _sync(self._dir("deleting"))

    @namespace_writer
    def clean_incoming(self, cutoff: float) -> int:
        removed = 0
        # Atomic binding publications leave no business state; stale temp files
        # after process death are safe to remove after the preparation TTL.
        for temporary in self._dir("bindings").glob("*.tmp"):
            if not temporary.is_symlink() and temporary.stat().st_mtime < cutoff:
                temporary.unlink(missing_ok=True)
                removed += 1
        for path in self._dir("incoming").iterdir():
            if path.is_symlink() or path.stat().st_mtime >= cutoff:
                continue
            if path.is_dir():
                lease_path = path / "lease"
                if not lease_path.exists():
                    # Abandoned before lease creation. An active publisher failing
                    # here cannot commit a Photo, and will retry preparation.
                    shutil.rmtree(path)
                    removed += 1
                    continue
            else:
                lease_path = path
            descriptor = os.open(lease_path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                removed += 1
        return removed
