"""Documentation publique consultable depuis l'interface d'aide."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re

from fastapi import FastAPI, HTTPException


REPOSITORY_ROOT = Path(os.environ.get(
    "DIGUES_HELP_ROOT", str(Path(__file__).resolve().parents[3])
))
HEADING_PATTERN = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\([^)]*\)")
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
PUBLIC_GUIDES = (
    "docs/deployment/local-deployment.md",
    "docs/deployment/server-deployment.md",
)


@dataclass(frozen=True)
class HelpDocument:
    identifier: str
    label: str
    path: str
    file_path: Path

    def summary(self) -> dict[str, str]:
        return {"id": self.identifier, "label": self.label, "path": self.path}


def _document_identifier(relative_path: str) -> str:
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:16]


def _plain_heading(value: str) -> str:
    value = MARKDOWN_LINK_PATTERN.sub(r"\1", value)
    value = HTML_TAG_PATTERN.sub("", value)
    value = value.replace("`", "").replace("*", "").replace("_", "")
    return " ".join(value.split()).strip(" #")


def _fallback_label(file_path: Path) -> str:
    words = " ".join(file_path.stem.replace("-", " ").replace("_", " ").split())
    return words[:1].upper() + words[1:] if words else "Document"


def _document_label(file_path: Path, relative_path: str) -> str:
    if relative_path == "README.md":
        return "Présentation générale"
    content = file_path.read_text(encoding="utf-8")
    match = HEADING_PATTERN.search(content)
    if match and (heading := _plain_heading(match.group(1))):
        return heading
    return _fallback_label(file_path)


def build_help_inventory(repository_root: Path = REPOSITORY_ROOT) -> tuple[HelpDocument, ...]:
    """Whitelist des documents directs et des guides d'installation déplacés.

    Aucun parcours récursif : audits, modèles historiques et documents privés
    ne deviennent pas publics à l'occasion d'une réorganisation de ``docs``.
    """
    root = repository_root.resolve()
    candidates: list[tuple[str, Path]] = []

    readme = root / "README.md"
    if readme.is_file() and not readme.is_symlink() and readme.resolve() == readme:
        candidates.append(("README.md", readme))

    docs_directory = root / "docs"
    if docs_directory.is_dir() and not docs_directory.is_symlink():
        resolved_docs = docs_directory.resolve()
        for candidate in sorted(docs_directory.glob("*.md"), key=lambda path: path.name.casefold()):
            if candidate.is_symlink() or not candidate.is_file():
                continue
            resolved = candidate.resolve()
            if resolved.parent != resolved_docs:
                continue
            candidates.append((f"docs/{candidate.name}", candidate))

    for relative_path in PUBLIC_GUIDES:
        candidate = root / relative_path
        # resolve() also rejects symlinks in intermediate directories.
        if candidate.is_file() and candidate.resolve() == candidate:
            candidates.append((relative_path, candidate))

    return tuple(
        HelpDocument(
            identifier=_document_identifier(relative_path),
            label=_document_label(file_path, relative_path),
            path=relative_path,
            file_path=file_path,
        )
        for relative_path, file_path in candidates
    )


def register_routes(application: FastAPI, repository_root: Path = REPOSITORY_ROOT) -> None:
    @application.get("/api/help/documents", tags=["help"])
    async def list_help_documents() -> dict[str, list[dict[str, str]]]:
        return {"documents": [document.summary() for document in build_help_inventory(repository_root)]}

    @application.get("/api/help/documents/{document_id}", tags=["help"])
    async def read_help_document(document_id: str) -> dict[str, str]:
        documents = {document.identifier: document for document in build_help_inventory(repository_root)}
        document = documents.get(document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document d’aide introuvable.")
        try:
            content = document.file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise HTTPException(status_code=404, detail="Document d’aide introuvable.") from exc
        return {**document.summary(), "content": content}
