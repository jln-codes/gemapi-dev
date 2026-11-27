"""Catalogue de fiches de désordre, structuré par le patrimoine.

Portage du second usage historique de ``sirs-suite``
(``desordres.py:generate_disorder_reports``), adapté au périmètre patrimonial de
digues-app. Un catalogue correspond à un nœud du patrimoine et suit sa
hiérarchie :

    Catalogue (système d'endiguement, digue ou tronçon)
    └── système
        └── digues
            └── tronçons
                └── désordres

Règles retenues :

* le périmètre est résolu **par le backend** : l'appelant fournit un identifiant
  patrimonial, jamais une liste d'UUID de désordres ;
* un désordre multi-tronçons est rattaché au **premier tronçon du périmètre**
  selon l'ordre déterministe ; la fiche, elle, conserve tous ses tronçons ;
* un désordre n'apparaît **qu'une fois** dans un catalogue ;
* les désordres **sans aucun tronçon** ne peuvent être rattachés à un périmètre
  patrimonial : ils sont exclus, sans avertissement dans le catalogue, car leur
  appartenance est précisément inconnue. Un futur outil de contrôle de qualité
  les traitera séparément ;
* les nœuds vides (digue ou tronçon sans fiche) sont exclus ;
* une digue sans système reçoit un nœud racine synthétique
  « Sans système d'endiguement », uniquement dans le modèle, sans écriture
  PostgreSQL.

Présentation strictement fidèle à l'historique : le catalogue est la
concaténation des fiches, dans l'ordre hiérarchique. Aucune page de titre, aucun
séparateur, aucun sommaire, aucun signet. Ce module ne dessine rien : les fiches
sont produites par l'unique moteur
``digues_app.reports.pdf.render_disorder_sheet_pdf``, puis assemblées par
``pypdf``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from .repository import (
    load_disorder_sheet,
    load_scope_disorder_rows,
    load_scope_node,
)


SYSTEME_SCOPE = "systeme"
DIGUE_SCOPE = "digue"
TRONCON_SCOPE = "troncon"
SCOPE_KINDS = (SYSTEME_SCOPE, DIGUE_SCOPE, TRONCON_SCOPE, "amenagement_hydraulique")

#: Nœud racine synthétique d'une digue sans système d'endiguement. Il n'existe
#: que dans le modèle du catalogue, jamais dans PostgreSQL.
NO_SYSTEM_LABEL = "Sans système d'endiguement"
NO_DIGUE_LABEL = "Sans digue"

PhotoLoader = Callable[[Any], bytes | None]
MapRenderer = Callable[[Any], bytes | None]


class CatalogScopeNotFound(LookupError):
    """Le nœud patrimonial demandé n'existe pas dans PostgreSQL."""


@dataclass(frozen=True)
class CatalogScope:
    """Nœud patrimonial demandé : système d'endiguement, digue ou tronçon."""

    kind: str
    identifier: str
    label: str


@dataclass(frozen=True)
class CatalogEntry:
    """Une fiche de désordre dans un tronçon."""

    disorder_id: str
    designation: str | None


@dataclass(frozen=True)
class CatalogTroncon:
    identifier: str
    label: str
    entries: tuple[CatalogEntry, ...]


@dataclass(frozen=True)
class CatalogDigue:
    identifier: str | None
    label: str
    troncons: tuple[CatalogTroncon, ...]


@dataclass(frozen=True)
class CatalogSysteme:
    """Nœud système ; ``identifier`` est ``None`` pour une digue sans système."""

    identifier: str | None
    label: str
    digues: tuple[CatalogDigue, ...]


@dataclass(frozen=True)
class DisorderCatalog:
    """Arbre patrimonial ordonné et avertissements de complétude."""

    scope: CatalogScope
    systemes: tuple[CatalogSysteme, ...]
    warnings: tuple[str, ...] = ()

    @property
    def entry_count(self) -> int:
        return sum(1 for _ in self.iter_entries())

    @property
    def digue_count(self) -> int:
        # Presentation groups for an absent parent are not business Digues.
        return sum(digue.identifier is not None for systeme in self.systemes for digue in systeme.digues)

    @property
    def troncon_count(self) -> int:
        return sum(
            len(digue.troncons)
            for systeme in self.systemes
            for digue in systeme.digues
        )

    def iter_entries(self) -> Iterator[CatalogEntry]:
        """Parcourt les fiches dans l'ordre de concaténation du catalogue."""

        for systeme in self.systemes:
            for digue in systeme.digues:
                for troncon in digue.troncons:
                    yield from troncon.entries


def _fold(value: Any) -> str:
    return str(value or "").casefold()


def _entry_sort_key(entry: CatalogEntry) -> tuple[str, str]:
    """Ordre déterministe des fiches : désignation puis identifiant."""

    return (_fold(entry.designation), _fold(entry.disorder_id))


def load_patrimoine_catalog(
    connection: Any, scope_kind: str, scope_identifier: Any
) -> DisorderCatalog:
    """Charge le catalogue d'un nœud patrimonial, ou lève ``CatalogScopeNotFound``.

    Le tronçon retenu pour chaque désordre est le premier **du périmètre**
    (ordre déterministe) : un désordre multi-tronçons n'est donc jamais dupliqué,
    et il est bien inclus dès qu'un de ses tronçons appartient au périmètre.
    """

    node = load_scope_node(connection, scope_kind, scope_identifier)
    if node is None:
        raise CatalogScopeNotFound(str(scope_identifier))
    (
        scope_label,
        _systeme_id,
        _systeme_label,
        _digue_id,
        _digue_label,
        _troncon_id,
        _troncon_label,
    ) = node

    best: dict[str, tuple[tuple[str, str], tuple[Any, ...]]] = {}
    for row in load_scope_disorder_rows(connection, scope_kind, scope_identifier):
        (
            _row_systeme_id,
            _row_systeme_label,
            _row_digue_id,
            _row_digue_label,
            row_troncon_id,
            row_troncon_label,
            disorder_id,
            _designation,
        ) = row
        key = (_fold(row_troncon_label), str(row_troncon_id))
        current = best.get(disorder_id)
        if current is None or key < current[0]:
            best[disorder_id] = (key, row)

    systems: dict[str | None, dict[str, Any]] = {}
    for _disorder_id, (_key, row) in best.items():
        (
            row_systeme_id,
            row_systeme_label,
            row_digue_id,
            row_digue_label,
            row_troncon_id,
            row_troncon_label,
            disorder_id,
            designation,
        ) = row
        systeme = systems.setdefault(
            row_systeme_id,
            {
                "label": row_systeme_label or row_systeme_id or NO_SYSTEM_LABEL,
                "digues": {},
            },
        )
        digue = systeme["digues"].setdefault(
            row_digue_id,
            {"label": row_digue_label or row_digue_id or NO_DIGUE_LABEL, "troncons": {}},
        )
        troncon = digue["troncons"].setdefault(
            row_troncon_id,
            {"label": row_troncon_label or row_troncon_id, "entries": []},
        )
        troncon["entries"].append(CatalogEntry(disorder_id, designation))

    systemes = tuple(
        CatalogSysteme(
            identifier=systeme_id,
            label=systeme["label"],
            digues=tuple(
                CatalogDigue(
                    identifier=digue_id,
                    label=digue["label"],
                    troncons=tuple(
                        CatalogTroncon(
                            identifier=troncon_id,
                            label=troncon["label"],
                            entries=tuple(
                                sorted(troncon["entries"], key=_entry_sort_key)
                            ),
                        )
                        for troncon_id, troncon in sorted(
                            digue["troncons"].items(),
                            key=lambda item: (_fold(item[1]["label"]), item[0]),
                        )
                    ),
                )
                for digue_id, digue in sorted(
                    systeme["digues"].items(),
                    key=lambda item: (_fold(item[1]["label"]), item[0] or ""),
                )
            ),
        )
        for systeme_id, systeme in sorted(
            systems.items(),
            key=lambda item: (_fold(item[1]["label"]), item[0] or ""),
        )
    )

    return DisorderCatalog(
        scope=CatalogScope(
            kind=scope_kind,
            identifier=str(scope_identifier),
            label=scope_label or str(scope_identifier),
        ),
        systemes=systemes,
        warnings=(),
    )


def merge_pdf_documents(documents: Sequence[bytes]) -> bytes:
    """Concatène des PDF en mémoire, page à page, avec ``pypdf``.

    Reproduit ``desordres.py:_merge_pdfs`` sans repli ``PyPDF2`` et sans fichier
    temporaire : les fiches sont déjà rendues par l'unique moteur.
    """

    if not documents:
        raise ValueError("Aucun document PDF à assembler.")
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for document in documents:
        for page in PdfReader(BytesIO(document)).pages:
            writer.add_page(page)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def render_catalog_pdf(
    connection: Any,
    catalog: DisorderCatalog,
    *,
    photo_loader: PhotoLoader | None = None,
    map_renderer: MapRenderer | None = None,
) -> bytes:
    """Rend le PDF du catalogue, dans l'ordre hiérarchique, en un document unique.

    Chaque fiche est produite par l'unique moteur de fiche ; aucune structure de
    présentation supplémentaire n'est ajoutée, conformément à l'historique.
    """

    from .pdf import render_disorder_sheet_pdf

    documents = [
        render_disorder_sheet_pdf(
            load_disorder_sheet(connection, entry.disorder_id),
            photo_loader=photo_loader,
            map_renderer=map_renderer,
        )
        for entry in catalog.iter_entries()
    ]
    return merge_pdf_documents(documents)
