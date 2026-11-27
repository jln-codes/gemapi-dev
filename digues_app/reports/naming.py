"""Nommage des fiches PDF produites.

``safe_filename`` est porté de ``sirs-suite/sirs/reports/pdf_common.py``. Le nom
du fichier reproduit celui de la fiche unitaire historique
(``desordre_<tronçon>_<désignation>.pdf``, ``sirs-suite/fiche_desordre.py:39``),
avec deux adaptations imposées par HTTP :

* les caractères interdits par les systèmes de fichiers sont neutralisés, comme
  dans l'implémentation historique du recueil ;
* l'en-tête ``Content-Disposition`` fournit un repli ASCII et la forme RFC 5987
  ``filename*`` pour préserver les accents et les tirets longs, qu'un en-tête
  HTTP ne peut pas transporter en latin-1.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import quote


FORBIDDEN_FILENAME_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(value: Any) -> str:
    """Retire les caractères interdits dans les noms de fichiers Windows."""

    cleaned = FORBIDDEN_FILENAME_CHARACTERS.sub("_", str(value or ""))
    return cleaned.rstrip(" .") or "sans_nom"


def disorder_pdf_filename(section_label: str, designation: str) -> str:
    """Nom du fichier téléchargé pour une fiche de désordre."""

    return f"desordre_{safe_filename(section_label)}_{safe_filename(designation)}.pdf"


#: Préfixes de nommage des recueils, par nœud patrimonial. Les deux premiers
#: reproduisent ``sirs-suite/desordres.py`` ; le niveau tronçon est nouveau.
CATALOG_FILENAME_PREFIXES = {
    "amenagement_hydraulique": "Desordres_amenagement_hydraulique",
    "systeme": "Desordres",
    "digue": "Desordres_digue",
    "troncon": "Desordres_troncon",
}


def disorder_catalog_filename(group_kind: str, group_label: str) -> str:
    """Nom du fichier d'un catalogue patrimonial (système, digue ou tronçon)."""

    prefix = CATALOG_FILENAME_PREFIXES.get(
        group_kind, CATALOG_FILENAME_PREFIXES["systeme"]
    )
    return f"{prefix}_{safe_filename(group_label)}.pdf"


def content_disposition(filename: str, *, fallback: str = "fiche-desordre.pdf") -> str:
    """Construit un ``Content-Disposition`` sûr, accents compris."""

    ascii_fallback = (
        unicodedata.normalize("NFKD", filename)
        .encode("ascii", "ignore")
        .decode("ascii")
    ) or fallback
    return (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )
