"""Rendu PDF d'une fiche de désordre.

Portage de ``fiche_desordre.make_pdf`` de ``sirs-suite``. La mise en page est
reproduite à l'identique : mêmes sections, mêmes libellés, mêmes tableaux,
mêmes largeurs de colonnes, mêmes polices, mêmes tailles, mêmes couleurs, mêmes
seuils de saut de page et mêmes comportements de repli. Le PDF de référence
produit par l'implémentation historique sert de golden master.

Trois adaptations seulement, imposées par le changement de source :

* le rendu ne reçoit plus de client CouchDB : l'observateur et les libellés
  sont déjà résolus dans :class:`DisorderSheetData` ;
* la photo n'est plus lue sur un chemin réseau Windows mais fournie par un
  adaptateur média injecté (``MediaStorage`` de digues-app) ;
* les images transitent en mémoire et non par des fichiers temporaires globaux
  (``_tmp_p.jpg``, ``_tmp_map.png``), qui ne sont pas sûrs en service
  concurrent.

Aucune autre différence de présentation n'est introduite.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

from .disorder_sheet import DisorderMapContext, DisorderPhoto, DisorderSheetData


PHOTO_THUMBNAIL_SIZE = (450, 350)
PHOTO_DRAW_WIDTH = 110
PHOTO_DRAW_HEIGHT = 82
MAP_DRAW_HEIGHT = 75
#: Couleur d'accent de la fiche. Seule divergence graphique volontaire avec le
#: renderer historique de ``sirs-suite`` : le bleu ``#1b7ab2`` est remplacé par
#: le vert ``#3f685e``. Aucune autre propriété graphique n'est modifiée, et les
#: couleurs fonctionnelles de la carte (ligne de tronçon bleue, repère rouge)
#: ne sont pas concernées.
PAGE_COLOR = "#3f685e"

PhotoLoader = Callable[[DisorderPhoto], bytes | None]
MapRenderer = Callable[[DisorderMapContext], bytes | None]


def mmx(value: float) -> float:
    return value * mm


def section_title(c: canvas.Canvas, y: float, txt: str) -> float:
    c.setFillColor(colors.HexColor(PAGE_COLOR))
    c.rect(mmx(10), y - mmx(7), A4[0] - mmx(20), mmx(7), fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(mmx(13), y - mmx(5), txt)
    c.setFillColor(colors.black)
    return y - mmx(12)


def draw_table(
    c: canvas.Canvas,
    data: Sequence[Sequence[str]],
    x: float,
    y: float,
    colWidths: Sequence[float] | None = None,
) -> float:
    if not colWidths:
        colWidths = [mmx(60), mmx(130)]  # Total 190mm
    t = Table(data, colWidths=colWidths)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    w, h = t.wrapOn(c, A4[0] - mmx(20), 1000)
    t.drawOn(c, x, y - h)
    return y - h - mmx(5)


def _photo_bytes(photo: DisorderPhoto | None, photo_loader: PhotoLoader | None) -> bytes | None:
    """Octets du média, ou ``None`` si aucun contenu n'est disponible.

    Un média absent, un stockage non configuré et une erreur de lecture
    produisent tous « (Pas de photo) », comme l'implémentation historique
    lorsqu'aucun fichier n'était trouvé sur le partage. Une image présente mais
    inexploitable par Pillow ou ReportLab produit « (Erreur photo) ».
    """

    if photo is None or photo_loader is None:
        return None
    try:
        return photo_loader(photo) or None
    except Exception:
        return None


def render_disorder_sheet_pdf(
    sheet: DisorderSheetData,
    *,
    photo_loader: PhotoLoader | None = None,
    map_renderer: MapRenderer | None = None,
) -> bytes:
    """Rend une fiche de désordre et retourne les octets du PDF.

    Le renderer n'interroge aucune base : toutes les données sont déjà dans
    ``sheet``, la photo passe par ``photo_loader`` et la carte par
    ``map_renderer``. Il peut donc être testé avec une simple fixture.
    """

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # Header
    c.setFont("Helvetica-Bold", 16)
    c.setFillColor(colors.HexColor(PAGE_COLOR))
    c.drawCentredString(width / 2, height - 20 * mm, "FICHE DE SUIVI DE DÉSORDRE")
    c.setFont("Helvetica-Bold", 12)
    c.setFillColor(colors.black)
    c.drawCentredString(width / 2, height - 28 * mm, f"Tronçon : {sheet.section_label}")
    y = height - 40 * mm

    # Section 1 : Description
    y = section_title(c, y, "Description du désordre")
    desc_data = [
        ["Désignation", sheet.designation],
        ["Type de désordre", sheet.disorder_type],
        ["Commentaire", sheet.comment],
    ]
    y = draw_table(c, desc_data, mmx(10), y, [mmx(60), mmx(130)])

    # Section 2 : Observations
    if y < 50 * mm:
        c.showPage()
        y = height - 20 * mm
    y = section_title(c, y, "Historique des observations")
    obs_data = [["Date", "Observateur", "Évolution / État"]]
    for observation in sheet.observations:
        obs_data.append([observation.date, observation.observer, observation.comment])
    if len(obs_data) == 1:
        obs_data.append(["-", "-", "-"])
    y = draw_table(c, obs_data, mmx(10), y, [mmx(25), mmx(45), mmx(120)])

    # Section 3 : Photo (Centrée et Espacée)
    if y < 100 * mm:
        c.showPage()
        y = height - 20 * mm
    y = section_title(c, y, "Photo de terrain")
    photo_file = _photo_bytes(sheet.photo, photo_loader)

    if photo_file:
        try:
            from PIL import Image

            with Image.open(BytesIO(photo_file)) as im:
                im.thumbnail(PHOTO_THUMBNAIL_SIZE)
                thumbnail = BytesIO()
                im.convert("RGB").save(thumbnail, "JPEG")
            img_w, img_h = mmx(PHOTO_DRAW_WIDTH), mmx(PHOTO_DRAW_HEIGHT)
            y_photo_start = y - img_h - mmx(8)  # 8mm d'espace sous le titre
            x_centered = (width - img_w) / 2
            c.drawImage(
                ImageReader(BytesIO(thumbnail.getvalue())),
                x_centered,
                y_photo_start,
                width=img_w,
                height=img_h,
                preserveAspectRatio=True,
                anchor="sw",
            )
            y = y_photo_start - mmx(10)
        except Exception:
            c.drawString(mmx(15), y - mmx(5), "(Erreur photo)")
            y -= mmx(10)
    else:
        c.drawString(mmx(15), y - mmx(5), "(Pas de photo)")
        y -= mmx(12)

    # Section 4 : Localisation
    if y < 80 * mm:
        c.showPage()
        y = height - 20 * mm
    y = section_title(c, y, "Localisation précise")
    loc_data = [
        ["Coordonnées (WGS84)", "Valeur"],
        ["Latitude (Y)", sheet.latitude],
        ["Longitude (X)", sheet.longitude],
        ["Côté de la digue", sheet.side],
        ["Position", sheet.position],
    ]
    y = draw_table(c, loc_data, mmx(10), y, [mmx(70), mmx(120)])

    # Section 5 : Carte
    if y < 90 * mm:
        c.showPage()
        y = height - 20 * mm
    y = section_title(c, y, "Carte de situation")
    map_image = None
    if sheet.map_context is not None and map_renderer is not None:
        map_image = map_renderer(sheet.map_context)
    if map_image:
        c.drawImage(
            ImageReader(BytesIO(map_image)),
            mmx(10),
            y - mmx(80),
            width=width - mmx(20),
            height=mmx(MAP_DRAW_HEIGHT),
        )

    c.save()
    return buffer.getvalue()
