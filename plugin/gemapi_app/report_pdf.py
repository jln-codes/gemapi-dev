"""A4 landscape prestation report built with QGIS layout/export APIs."""

from datetime import date
from contextlib import nullcontext
import os
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory
from textwrap import wrap
from time import perf_counter

from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtGui import (QColor, QFont, QFontDatabase, QFontInfo,
                            QFontMetrics, QGuiApplication, QImageReader, QImageWriter)
from qgis.core import (
    Qgis, QgsAbstractLayoutIterator, QgsLayoutExporter, QgsLayoutItemLabel,
    QgsLayoutItemMap, QgsLayoutItemPage,
    QgsLayoutItemPicture, QgsLayoutPoint, QgsLayoutSize, QgsLayoutUtils, QgsPrintLayout,
    QgsProject,
)

from .report_data import (GROUPS, ah_recognition_lines, direct_attachment_label,
                          distinct_records, format_measure,
                          group_label,
                          grouped_entries, measure_lines,
                          normalized_filters, own_totals, report_context)
from .photo_paths import resolve_photo
from .report_maps import (MAP_CRS, MAP_HEIGHT_MM, MAP_WIDTH_MM,
                          apply_minimum_report_scale, IgnNetworkProbe,
                          background, location_layers)
from .perf import ENABLED, _log, measure, profiled


# Institutional logo in PDF headers; the toolbar keeps icons/icon.png.
INSTITUTIONAL_LOGO = Path(__file__).parent / "assets" / "logo_institutionnel.webp"
FONT_PROBE = "Français ÉéÈèÊêÀàÂâÎîÔôÙùÛûÇçŒœ ²° —–’©"
PREFERRED_FONTS = ("DejaVu Sans", "Liberation Sans", "Arial", "Segoe UI", "Noto Sans")
_REPORT_FONT_FAMILY = None
_UNSET_BASEMAP = object()
_DETAILED_BATCH_SIZE = 10


class _ReportLayoutIterator(QgsAbstractLayoutIterator):
    """Let QGIS write several small layouts to one PDF with its native exporter."""

    def __init__(self, layouts, on_layout_complete=None):
        super().__init__()
        self._layouts = layouts
        self._index = -1
        self._on_layout_complete = on_layout_complete

    def beginRender(self):
        self._index = -1
        return bool(self._layouts)

    def endRender(self):
        return True

    def count(self):
        return len(self._layouts)

    def next(self):
        # QGIS asks for the next layout after exporting the current one.
        if 0 <= self._index < len(self._layouts) and self._on_layout_complete:
            self._on_layout_complete(self._index)
        self._index += 1
        return self._index < len(self._layouts)

    def layout(self):
        return self._layouts[self._index]

    def filePath(self, base_path, extension):
        return base_path


def _restore_qgis_pdf_metadata(destination):
    """Match single-layout QGIS PDF metadata after its iterator export."""
    from osgeo import gdal

    with gdal.ExceptionMgr():
        document = gdal.OpenEx(str(destination), gdal.OF_RASTER | gdal.OF_UPDATE)
        if document is None:
            raise RuntimeError("Métadonnées du PDF impossibles à ouvrir.")
        try:
            metadata = QgsProject.instance().metadata()
            qgis_version = f"QGIS {Qgis.QGIS_VERSION}"
            if document.GetMetadataItem("CREATOR") == qgis_version:
                return
            for key, value in (("AUTHOR", metadata.author()),
                               ("TITLE", metadata.title()),
                               ("SUBJECT", metadata.abstract()),
                               ("CREATOR", qgis_version),
                               ("PRODUCER", qgis_version)):
                if document.SetMetadataItem(key, value) != 0:
                    raise RuntimeError(f"Métadonnée PDF non enregistrée : {key}.")
        finally:
            document = None


def inspect_report_fonts():
    """Resolve a font in the same Qt process that will export the PDF."""
    database = QFontDatabase()
    families = list(database.families())
    latin = list(database.families(QFontDatabase.Latin))
    by_name = {family.casefold(): family for family in families}
    candidates = [by_name[name.casefold()] for name in PREFERRED_FONTS
                  if name.casefold() in by_name]
    candidates.extend(family for family in latin if family not in candidates)
    diagnostics = {
        "platform": QGuiApplication.platformName(),
        "qpa_environment": os.environ.get("QT_QPA_PLATFORM", ""),
        "font_directory": os.environ.get("QT_QPA_FONTDIR", ""),
        "qt_plugin_path": os.environ.get("QT_PLUGIN_PATH", ""),
        "library_paths": QCoreApplication.libraryPaths(),
        "available_families": families,
        "candidate_checks": [],
        "selected": None,
    }
    for family in candidates:
        checks = []
        for bold in (False, True):
            font = QFont(family, 10)
            font.setBold(bold)
            info = QFontInfo(font)
            metrics = QFontMetrics(font)
            missing = [char for char in dict.fromkeys(FONT_PROBE)
                       if not char.isspace() and not metrics.inFontUcs4(ord(char))]
            checks.append({"bold": bold, "effective_family": info.family(),
                           "exact_match": info.exactMatch(), "missing": missing})
        diagnostics["candidate_checks"].append({"requested_family": family,
                                                  "styles": checks})
        requested = family.split(" [", 1)[0].casefold()
        if all(check["effective_family"].split(" [", 1)[0].casefold() == requested
               and not check["missing"] for check in checks):
            diagnostics["selected"] = checks[0]["effective_family"]
            break
    return diagnostics


def configure_report_font(diagnostics=None):
    global _REPORT_FONT_FAMILY
    diagnostics = diagnostics or inspect_report_fonts()
    if not diagnostics["selected"]:
        raise RuntimeError(
            "Aucune police Unicode utilisable dans le processus PDF. "
            f"Plateforme Qt : {diagnostics['platform']} ; "
            f"polices disponibles : {', '.join(diagnostics['available_families']) or 'aucune'} ; "
            f"vérifications : {diagnostics['candidate_checks']}")
    _REPORT_FONT_FAMILY = diagnostics["selected"]
    return diagnostics


PAGE_WIDTH = 297
PAGE_HEIGHT = 210
LEFT = 10
BOTTOM = 196
CONTENT_TOP = 37
BLUE = QColor("#1764a5")
DARK_BLUE = QColor("#173e61")
GROUP_LEVEL_2 = QColor("#b7d0df")
GROUP_LEVEL_3 = QColor("#d2e2eb")
PALE = QColor("#f3f7fa")
TABLE_HEADER_TEXT = QColor("#4b6a80")
GRAY = QColor("#4b5965")
GROUP_BAND_GAP_MM = 2
COLUMNS = (
    ("Dates", 10, 28, 18), ("Type", 38, 31, 20),
    ("Statut", 69, 25, 17), ("Technicien", 94, 29, 18),
    ("Prestataire", 123, 29, 18), ("Communes", 152, 35, 23),
    ("Prestation / patrimoine", 187, 58, 33), ("Mesures", 245, 42, 26),
)


def _lines(value, width):
    value = " ".join(str(value or "").split()) or "—"
    return wrap(value, width=width, break_long_words=True, break_on_hyphens=False) or ["—"]


def _attachment_lines(text, width=182, size=7, first_width=None):
    """Wrap names at their printed width; never elide or expose internal keys."""
    font = QFont(_REPORT_FONT_FAMILY, size)
    lines, current = [], ""
    for word in str(text).split():
        candidate = (current + " " + word).strip()
        limit = first_width if not lines and first_width is not None else width
        if QgsLayoutUtils.textWidthMM(font, candidate) <= limit:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = ""
        for character in word:
            limit = first_width if not lines and first_width is not None else width
            if current and QgsLayoutUtils.textWidthMM(font, current + character) > limit:
                lines.append(current)
                current = ""
            current += character
    if current:
        lines.append(current)
    return lines


def _label(layout, value, x, y, width, height, size=8, color=None, bold=False,
           background=None):
    label = QgsLayoutItemLabel(layout)
    label.setText(str(value))
    font = QFont(_REPORT_FONT_FAMILY, size)
    font.setBold(bold)
    label.setFont(font)
    label.setFontColor(color or QColor("#22313d"))
    label.setMargin(0)
    if background is not None:
        label.setBackgroundEnabled(True)
        label.setBackgroundColor(background)
    layout.addLayoutItem(label)
    label.attemptMove(QgsLayoutPoint(x, y))
    label.attemptResize(QgsLayoutSize(width, height))
    return label


def _page(layout, number, generated_on):
    if number:
        page = QgsLayoutItemPage(layout)
        page.setPageSize(QgsLayoutSize(PAGE_WIDTH, PAGE_HEIGHT))
        layout.pageCollection().addPage(page)
    else:
        page = layout.pageCollection().pages()[0]
        page.setPageSize(QgsLayoutSize(PAGE_WIDTH, PAGE_HEIGHT))
    # QGIS inserts a 10 mm gap between pages in the layout scene.
    base = number * (PAGE_HEIGHT + 10)
    _label(layout, "Registre de prestations", LEFT, base + 8,
           202, 10, 17, BLUE, True)
    _label(layout, "Édité le " + generated_on.strftime("%d/%m/%Y"), LEFT,
           base + 20, 202, 6, 9, GRAY)
    logo = QgsLayoutItemPicture(layout)
    logo.setPicturePath(str(INSTITUTIONAL_LOGO))
    logo.setResizeMode(QgsLayoutItemPicture.Zoom)
    layout.addLayoutItem(logo)
    logo.attemptMove(QgsLayoutPoint(223, base + 4))
    logo.attemptResize(QgsLayoutSize(64, 28))
    return base


def _columns(groups):
    hidden = {"type": "Type", "technician": "Technicien",
              "provider": "Prestataire", "status": "Statut"}
    hidden_titles = {hidden[group] for group in groups if group in hidden}
    visible = [spec for spec in COLUMNS if spec[0] not in hidden_titles]
    factor = 277 / sum(spec[2] for spec in visible)
    x = LEFT
    columns = []
    for title, _, width, text_width in visible:
        scaled = width * factor
        columns.append((title, x, scaled, max(12, round(text_width * factor))))
        x += scaled
    return tuple(columns)


def _table_header(layout, y, columns):
    for title, x, width, _ in columns:
        _label(layout, " " + title, x, y, width, 7, 8,
               TABLE_HEADER_TEXT, True, PALE)
    return y + 8


def _heading_height(node):
    return 6 * len(_lines(f"{GROUPS[node[0]]} : {group_label(node[0], node[1])}", 105)) + 6


def _group_heading(layout, node, level, y):
    criterion, name, count, length, area = node
    title_lines = _lines(f"{GROUPS[criterion]} : {group_label(criterion, name)}", 105)
    height = _heading_height(node)
    summary = (f"{count} prestation(s) distincte(s)  |  "
               f"Somme des longueurs cartographiées : {format_measure(length)} m  |  "
               f"Somme des surfaces cartographiées : {format_measure(area)} m²")
    if level == 0:
        _label(layout, "", LEFT, y, 277, height, background=DARK_BLUE)
        color = QColor("#ffffff")
    elif level == 1:
        _label(layout, "", LEFT, y, 277, height, background=GROUP_LEVEL_2)
        color = DARK_BLUE
    else:
        _label(layout, "", LEFT, y, 277, height, background=GROUP_LEVEL_3)
        color = DARK_BLUE
    for line_number, line in enumerate(title_lines):
        _label(layout, line, LEFT + 2, y + 1 + 6 * line_number,
               273, 5, 9 if level == 0 else 8, color, True)
    _label(layout, summary, LEFT + 2, y + 1 + 6 * len(title_lines),
           273, 4, 7, color)
    if level == 2:
        _label(layout, "", LEFT, y + height - 0.5, 277, 0.5,
               background=QColor("#a8bfd2"))
    return y + height


def _row_cells(record):
    dates = (record["date_debut"] or "—") + " / " + (record["date_fin"] or "—")
    patrimony = [record.get("intitule") or "Sans intitulé"]
    if record.get("objects"):
        patrimony.append("Rattachement : " + direct_attachment_label(record))
    patrimony.extend(ah_recognition_lines(record))
    return (dates, record["type"], record.get("statut") or "—",
            record["technician"], record["provider"],
            record["communes"] or "Non déterminées",
            " ; ".join(patrimony) or "—", " ; ".join(measure_lines(record)))


def available_photos(record):
    photos = []
    for number in (1, 2):
        path = resolve_photo(record.get(f"photo_{number}"))
        if path is None:
            continue
        reader = QImageReader(str(path))
        if not reader.canRead() or not reader.size().isValid():
            continue
        photos.append((path, record.get(f"photo_{number}_commentaire") or "",
                       reader.size().width(), reader.size().height()))
    return photos


def photo_slots(record):
    """Keep the original slot when a recorded photograph cannot be read."""
    slots = []
    for number in (1, 2):
        reference = str(record.get(f"photo_{number}") or "").strip()
        comment = record.get(f"photo_{number}_commentaire") or ""
        if not reference:
            slots.append(("absent", None, comment, "", None))
            continue
        with measure("pdf.photo.resolve_reference"):
            path = resolve_photo(reference)
        if path is not None:
            with measure("pdf.photo.inspect_image"):
                reader = QImageReader(str(path))
                readable = reader.canRead() and reader.size().isValid()
            if readable:
                size = reader.size()
                slots.append(("available", path, comment, reference,
                              (size.width(), size.height())))
                continue
        slots.append(("unavailable", None, comment, reference, None))
    return slots


def _photos(layout, record, y, base, page_number, page_bases, generated_on):
    photos = available_photos(record)
    if not photos:
        return y, base, page_number
    slot = 277 if len(photos) == 1 else 135
    heights = [min(125, slot * height / width) for _, _, width, height in photos]
    image_height = max(heights)
    if y + image_height + 10 > base + BOTTOM:
        page_number += 1
        base = _page(layout, page_number, generated_on)
        page_bases.append(base)
        y = base + CONTENT_TOP
    _label(layout, "Photographies", LEFT, y, 277, 6, 9, BLUE, True)
    y += 7
    for index, (path, comment, width, height) in enumerate(photos):
        x = LEFT + index * 142
        draw_width = min(slot, 125 * width / height)
        draw_height = draw_width * height / width
        picture = QgsLayoutItemPicture(layout)
        picture.setPicturePath(str(path))
        picture.setResizeMode(QgsLayoutItemPicture.Zoom)
        layout.addLayoutItem(picture)
        picture.attemptMove(QgsLayoutPoint(x, y))
        picture.attemptResize(QgsLayoutSize(draw_width, draw_height))
    y += image_height + 2
    # Long comments flow onto subsequent pages rather than being clipped.
    comments = [_lines(photo[1], 115 if len(photos) == 1 else 53)
                if photo[1] else [] for photo in photos]
    while any(comments):
        if y + 5 > base + BOTTOM:
            page_number += 1
            base = _page(layout, page_number, generated_on)
            page_bases.append(base)
            y = base + CONTENT_TOP
        for index, lines in enumerate(comments):
            if lines:
                _label(layout, lines.pop(0), LEFT + index * 142, y,
                       slot, 4, 8, GRAY)
        y += 5
    return y + 3, base, page_number


def _map(layout, record, y, base, page_number, page_bases, basemap, kind, retained,
         generated_on, map_items=None):
    overlays, extent, provenance = location_layers(record)
    retained.extend(overlays)  # Keep standalone memory layers alive through export.
    if y + MAP_HEIGHT_MM + 25 > base + BOTTOM:
        page_number += 1
        base = _page(layout, page_number, generated_on)
        page_bases.append(base)
        y = base + CONTENT_TOP
    _label(layout, "Localisation", LEFT, y, 277, 6, 9, BLUE, True)
    y += 7
    if extent is None:
        _label(layout, provenance, LEFT, y, 277, 6, 8, GRAY)
        return y + 9, base, page_number
    map_item = QgsLayoutItemMap(layout)
    layout.addLayoutItem(map_item)
    map_item.attemptMove(QgsLayoutPoint(LEFT, y))
    map_item.attemptResize(QgsLayoutSize(MAP_WIDTH_MM, MAP_HEIGHT_MM))
    map_item.setCrs(MAP_CRS)
    map_item.setLayers(overlays + ([basemap] if basemap else []))
    map_item.setKeepLayerSet(True)
    map_item.setFrameEnabled(True)
    map_item.zoomToExtent(extent)
    apply_minimum_report_scale(map_item)
    if map_items is not None:
        map_items.append(map_item)
    y += MAP_HEIGHT_MM + 1
    source = ("© IGN - Géoplateforme (Plan Épuré)" if kind == "epure" else
              "© IGN - Géoplateforme (Orthophotographies)") if basemap else              "Fond IGN indisponible : géométries seules."
    _label(layout, source + " | Propre : magenta ; référence : orange.",
           LEFT, y, 277, 5, 8, GRAY)
    y += 5
    for line in _lines("Cadrage : " + provenance, 150):
        if y + 5 > base + BOTTOM:
            page_number += 1
            base = _page(layout, page_number, generated_on)
            page_bases.append(base)
            y = base + CONTENT_TOP
        _label(layout, line, LEFT, y, 277, 5, 7, GRAY)
        y += 5
    return y + 3, base, page_number


@profiled("pdf.total")
def export_report(records, filters, filter_summary, warnings, destination, include_photos=False,
                  include_maps=False, map_background="epure", progress=None,
                  export_dpi=100, export_mode="standard", map_jpeg_quality=75,
                  require_basemap=False):
    """Export a multipage PDF; no PostgreSQL or QGIS project writes."""
    records = distinct_records(records)
    if not records:
        raise ValueError("Aucune prestation : aucun PDF n'est produit.")
    if (export_mode not in ("standard", "rasterized", "hybrid") or export_dpi <= 0
            or not 0 <= map_jpeg_quality <= 100):
        raise ValueError("Paramètres d'export PDF invalides.")
    if export_mode == "hybrid" and not include_maps:
        raise ValueError("L'export hybride nécessite des cartes.")
    configure_report_font()
    filters = normalized_filters(filters)
    groups = filters["groups"]
    columns = _columns(groups)
    with measure("pdf.python.grouping"):
        entries = grouped_entries(records, groups)
    layout = QgsPrintLayout(QgsProject.instance())
    layout.initializeDefaults()
    layout.setName("Registre de prestations")
    if progress:
        progress("Préparation du fond cartographique" if include_maps else
                 "Préparation de la mise en page", 0, len(records))
    with measure("pdf.ign.background"):
        basemap = background(map_background) if include_maps else None
    if require_basemap and include_maps and basemap is None:
        raise RuntimeError("Le fond IGN demandé est indisponible.")
    retained = [basemap] if basemap else []
    map_items = [] if export_mode == "hybrid" else None
    page_number = 0
    generated_on = date.today()
    base = _page(layout, page_number, generated_on)
    page_bases = [base]
    scope, organization = report_context(records, filters)
    y = base + CONTENT_TOP
    for line in _lines(scope, 150):
        _label(layout, line, LEFT, y, 277, 5, 8, BLUE, True)
        y += 5
    _label(layout, organization, LEFT, y + 1, 277, 5, 8, GRAY)
    y += 7
    length, area = own_totals(records)
    _label(layout, f"{len(records)} prestations distinctes  |  "
           f"Somme des longueurs cartographiées : {format_measure(length)} m  |  "
           f"Somme des surfaces cartographiées : {format_measure(area)} m²",
           LEFT, y + 1, 277, 6, 9, BLUE, True)
    y += 8
    _label(layout, "Légende : mesure cartographiée = géométrie de la prestation ; "
           "réf. patrimoniale = géométrie d'un objet lié, jamais additionnée aux totaux.",
           LEFT, y, 277, 6, 7, GRAY)
    y += 7
    for warning in warnings:
        for line in _lines(warning, 155):
            _label(layout, line, LEFT, y, 277, 5, 7, QColor("#8a4e12"))
            y += 5
    if y > base + 170:
        page_number += 1
        base = _page(layout, page_number, generated_on)
        page_bases.append(base)
        y = base + CONTENT_TOP
    y += 3
    current_path = ()
    table_active = False

    def new_page():
        nonlocal page_number, base, y, table_active
        page_number += 1
        base = _page(layout, page_number, generated_on)
        page_bases.append(base)
        y = base + CONTENT_TOP
        table_active = False

    for index, (path, record) in enumerate(entries):
        if progress:
            progress("Mise en page des prestations", index + 1, len(entries))
        if include_photos:
            photos = available_photos(record)
            if photos:
                slot = 277 if len(photos) == 1 else 135
                photo_height = max(min(125, slot * height / width)
                                   for _, _, width, height in photos)
                if y + 25 + photo_height > base + BOTTOM:
                    new_page()
        prefix = 0
        if table_active:
            while prefix < min(len(path), len(current_path)) and path[prefix] == current_path[prefix]:
                prefix += 1
        headings = path[prefix:]
        if not table_active or headings:
            required = (sum(_heading_height(node) for node in headings) +
                        max(0, len(headings) - 1) * GROUP_BAND_GAP_MM +
                        GROUP_BAND_GAP_MM + 8 + 15)
            if y + required > base + BOTTOM:
                new_page()
                headings = path
            drawn = False
            for level, node in enumerate(path):
                if level >= len(path) - len(headings):
                    if drawn:
                        y += GROUP_BAND_GAP_MM
                    y = _group_heading(layout, node, level, y)
                    drawn = True
            if drawn:
                y += GROUP_BAND_GAP_MM
            y = _table_header(layout, y, columns)
            table_active = True
        current_path = path
        values = dict(zip((spec[0] for spec in COLUMNS), _row_cells(record)))
        remaining = [_lines(values[title], text_width)
                     for title, _, _, text_width in columns]
        first_piece = True
        while any(remaining):
            available = base + BOTTOM - y
            capacity = int((available - 2) / 3.8)
            # Avoid orphan fragments and keep every cell inside the page bounds.
            if available < 11 or capacity < 1 or (
                    max(map(len, remaining)) > capacity and capacity < 3):
                new_page()
                if not first_piece:
                    _label(layout, "Suite de la prestation", LEFT, y, 277, 6,
                           9, BLUE, True)
                    y += 7
                else:
                    for level, node in enumerate(path):
                        if level:
                            y += GROUP_BAND_GAP_MM
                        y = _group_heading(layout, node, level, y)
                    if path:
                        y += GROUP_BAND_GAP_MM
                y = _table_header(layout, y, columns)
                table_active = True
                continue
            pieces = [lines[:capacity] for lines in remaining]
            row_height = max(11, max(map(len, pieces)) * 3.8 + 2)
            for lines, (_, x, width, _) in zip(pieces, columns):
                _label(layout, "\n".join(lines), x + 1, y + 1, width - 2,
                       row_height - 2, 7, background=QColor("#f8fafc")
                       if (index % 2) else None)
            y += row_height
            remaining = [lines[capacity:] for lines in remaining]
            first_piece = False
        if include_photos:
            before_page = page_number
            y, base, page_number = _photos(layout, record, y, base, page_number,
                                           page_bases, generated_on)
            if page_number != before_page:
                table_active = False
        if include_maps:
            before_page = page_number
            with measure("pdf.qgis.map_layout"):
                y, base, page_number = _map(layout, record, y, base, page_number,
                                            page_bases, basemap, map_background, retained,
                                            generated_on, map_items)
            if page_number != before_page:
                table_active = False
    total_pages = page_number + 1
    for number, base in enumerate(page_bases):
        _label(layout, "GEMAPI-App — rapport en lecture seule", LEFT, base + 201,
               190, 5, 7, GRAY)
        _label(layout, f"Page {number + 1} / {total_pages}", 247, base + 201,
               40, 5, 7, GRAY)
    exporter = QgsLayoutExporter(layout)
    if progress:
        progress("Écriture du PDF", len(entries), len(entries))
    settings = QgsLayoutExporter.PdfExportSettings()
    settings.dpi = export_dpi
    settings.textRenderFormat = Qgis.TextRenderFormat.AlwaysText
    settings.rasterizeWholeImage = export_mode == "rasterized"
    with (TemporaryDirectory(prefix="sig-pdf-maps-") if map_items is not None
          else nullcontext(None)) as image_dir:
        with (IgnNetworkProbe() if ENABLED and include_maps and basemap else nullcontext()):
            if map_items is not None:
                with measure("pdf.qgis.map_rasterize"):
                    for index, map_item in enumerate(map_items):
                        image = exporter.renderRegionToImage(
                            map_item.sceneBoundingRect(), dpi=export_dpi)
                        if image.isNull():
                            raise RuntimeError("Rasterisation de carte QGIS impossible.")
                        image_path = Path(image_dir) / f"map-{index:03d}.jpg"
                        writer = QImageWriter(str(image_path), b"jpg")
                        writer.setQuality(map_jpeg_quality)
                        if not writer.write(image):
                            raise RuntimeError("Compression JPEG de carte impossible.")
                        picture = QgsLayoutItemPicture(layout)
                        picture.setPicturePath(str(image_path))
                        picture.setResizeMode(QgsLayoutItemPicture.Stretch)
                        layout.addLayoutItem(picture)
                        picture.attemptMove(map_item.positionWithUnits())
                        picture.attemptResize(map_item.sizeWithUnits())
                        map_item.setVisible(False)
            with measure("pdf.qgis.export_to_pdf"):
                result = exporter.exportToPdf(destination, settings)
    if result != QgsLayoutExporter.Success:
        raise RuntimeError("QGIS n'a pas pu exporter le rapport PDF.")
    return total_pages


def export_detailed_report(records, destination, include_photos=False,
                           editor_login=None, editor_name=None, progress=None,
                           export_dpi=150, _layout_sink=None,
                           _shared_basemap=_UNSET_BASEMAP, _generated_on=None,
                           map_background="epure"):
    """A4 portrait detailed report; include_photos is kept for existing callers."""
    records = distinct_records(records)
    if not records:
        raise ValueError("Aucune prestation : aucun PDF n'est produit.")
    if len(records) > _DETAILED_BATCH_SIZE and _layout_sink is None:
        return _export_detailed_batched(
            records, destination, include_photos, editor_login,
            editor_name, progress, export_dpi, map_background)
    configure_report_font()
    layout_started = perf_counter() if ENABLED else None
    layout = QgsPrintLayout(QgsProject.instance())
    layout.initializeDefaults()
    layout.setName("Rapport détaillé de prestations")
    if progress and _shared_basemap is _UNSET_BASEMAP:
        progress("Préparation du fond cartographique", 0, len(records))
    with measure("pdf.detailed.background"):
        basemap = (background(map_background) if _shared_basemap is _UNSET_BASEMAP
                   else _shared_basemap)
    retained = [basemap] if basemap else []
    generated_on = _generated_on or date.today().strftime("%d/%m/%Y")
    editor = editor_name or (f"identité non résolue (login : {editor_login})"
                             if editor_login else "identité non résolue (login indisponible)")
    width, height = 190, 73

    def line(value, x, y, w=190, size=8, bold=False, color=None):
        return _label(layout, value if value not in (None, "") else "—",
                      x, y, w, 5, size, color, bold)

    page_bases = []
    map_build_ms = 0.0
    map_count = 0

    def new_page():
        number = len(page_bases)
        if number:
            page = QgsLayoutItemPage(layout)
            page.setPageSize(QgsLayoutSize(210, 297))
            layout.pageCollection().addPage(page)
        else:
            layout.pageCollection().pages()[0].setPageSize(QgsLayoutSize(210, 297))
        base = number * 307
        page_bases.append(base)
        return base

    for index, record in enumerate(records):
        if progress:
            progress("Mise en page des prestations", index + 1, len(records))
        base = new_page()  # QGIS adds a 10 mm scene gap between pages.
        continuation = []
        line("Rapport de prestations — GEMAPI-App", 10, base + 9, 110, 14, True, BLUE)
        line(f"Édité par {editor} le {generated_on}", 10, base + 20, 145, 8, False, GRAY)
        logo = QgsLayoutItemPicture(layout)
        logo.setPicturePath(str(INSTITUTIONAL_LOGO))
        logo.setResizeMode(QgsLayoutItemPicture.Zoom)
        layout.addLayoutItem(logo)
        logo.attemptMove(QgsLayoutPoint(138.8, base + 10))
        logo.attemptResize(QgsLayoutSize(61.2, 29))

        card = _label(layout, "", 10, base + 33, 190, 75, background=QColor("#f5f8fb"))
        line("FICHE DE PRESTATION", 14, base + 35, 175, 9, True, BLUE)
        title = _lines(record.get("intitule"), 75)
        if len(title) > 1:
            raise ValueError("Intitulé trop long pour la fiche détaillée.")
        for n, part in enumerate(title):
            line(part, 14, base + 42 + n * 5, 182, 10, True, DARK_BLUE)
        line("Type", 14, base + 51, 20, 7, True, GRAY)
        prestation_type = str(record.get("type") or "")
        if record.get("position") and record["position"] != "Indéfini":
            prestation_type += " — Position : " + str(record["position"])
        if record.get("rive") and record["rive"] != "Indéfinie":
            prestation_type += " — Rive : " + str(record["rive"])
        line(prestation_type, 34, base + 51, 105)
        line("Statut", 140, base + 51, 19, 7, True, GRAY)
        line(record.get("statut"), 159, base + 51, 37)
        line("Prestataire", 14, base + 58, 29, 7, True, GRAY)
        line(record.get("provider"), 43, base + 58, 95)
        line("Technicien", 140, base + 58, 25, 7, True, GRAY)
        line(record.get("technician"), 165, base + 58, 31)
        line("Dates", 14, base + 65, 20, 7, True, GRAY)
        line(f"{record.get('date_debut') or '—'}  →  {record.get('date_fin') or '—'}",
             34, base + 65, 94)
        if record.get("quantite") is not None:
            line("Quantité déclarée", 129, base + 65, 39, 7, True, GRAY)
            quantity = str(record["quantite"])
            line(quantity + (" " + str(record.get("unite")) if record.get("unite") else ""),
                 169, base + 65, 31)
        line("Communes", 14, base + 72, 29, 7, True, GRAY)
        line(record.get("communes"), 43, base + 72, 153)
        line("Mesures", 14, base + 79, 29, 7, True, GRAY)
        measures = " ; ".join(measure_lines(record, include_quantity=False))
        measure_parts = _attachment_lines(measures, width=153, size=7)
        for n, part in enumerate(measure_parts):
            line(part, 43, base + 79 + n * 4, 153, 7)
        description_y = 86 + 4 * (len(measure_parts) - 1)
        line("Description", 14, base + description_y, 40, 7, True, GRAY)
        description = _attachment_lines(
            record.get("description") or "—", width=182, size=8, first_width=142)
        description_limit = 3 if record.get("objects") else 4
        if len(description) > description_limit:
            continuation.append(("Description", description[description_limit:]))
            description = description[:description_limit]
        for n, part in enumerate(description):
            line(part, 54 if n == 0 else 14, base + description_y + n * 5,
                 142 if n == 0 else 182, 8)
        attachments = (["Rattachement : " + direct_attachment_label(record)]
                       if record.get("objects") else [])
        attachments.extend(ah_recognition_lines(record, explain=True))
        attachment_lines = [part for text in attachments for part in _attachment_lines(text)]
        attachment_y = description_y + 7 + 5 * (len(description) - 1)
        body_offset = max(0, attachment_y + 4 * len(attachment_lines) - 108)
        # Keep map dimensions, scale and extent. Only its vertical position may
        # move; photos retain their aspect ratio within the remaining space.
        if body_offset > 40:
            raise ValueError("Les noms des rattachements dépassent l’espace disponible "
                             "sur une fiche A4 ; aucun texte n’a été tronqué.")
        card.attemptResize(QgsLayoutSize(190, 75 + body_offset))
        for n, text in enumerate(attachment_lines):
            _label(layout, text, 14, base + attachment_y + n * 4, 182, 4, 7, GRAY)

        line("LOCALISATION", 10, base + 110 + body_offset, 190, 9, True, BLUE)
        map_started = perf_counter() if ENABLED else None
        overlays, extent, _ = location_layers(record, width / height)
        retained.extend(overlays)
        if extent is not None:
            map_item = QgsLayoutItemMap(layout)
            layout.addLayoutItem(map_item)
            map_item.attemptMove(QgsLayoutPoint(10, base + 117 + body_offset))
            map_item.attemptResize(QgsLayoutSize(width, height))
            map_item.setCrs(MAP_CRS)
            map_item.setLayers(overlays + ([basemap] if basemap else []))
            map_item.setKeepLayerSet(True)
            map_item.setFrameEnabled(True)
            map_item.zoomToExtent(extent)
            apply_minimum_report_scale(map_item)
            map_count += 1
        else:
            _label(layout, "", 10, base + 117 + body_offset, width, height,
                   background=QColor("#edf1f4"))
            line("Aucune géométrie localisable", 25, base + 151 + body_offset, 160, 9)
        if ENABLED:
            elapsed = (perf_counter() - map_started) * 1000
            map_build_ms += elapsed
            _log(f"pdf.detailed.map_build index={index + 1} total_ms={elapsed:.2f} "
                 f"overlays={len(overlays)}")
        copyright_text = ("© IGN - Géoplateforme (Plan Épuré)" if map_background == "epure"
                          else "© IGN - Géoplateforme (Orthophotographies)")
        line(copyright_text if basemap else "Fond IGN indisponible",
             10, base + 192 + body_offset, 190, 7, False, GRAY)

        line("PHOTOGRAPHIES", 10, base + 201 + body_offset,
             190, 9, True, BLUE)
        photos = photo_slots(record)
        if any(state != "absent" for state, _, _, _, _ in photos):
            for n, (state, path, comment, reference, dimensions) in enumerate(photos):
                if state == "absent":
                    continue
                x = 10 + n * 97
                slot_y = base + 208 + body_offset
                slot_height = 70 - body_offset
                if state == "available":
                    pixel_width, pixel_height = dimensions
                    millimeters_per_pixel = min(
                        93 / pixel_width, slot_height / pixel_height, 25.4 / export_dpi)
                    image_width = pixel_width * millimeters_per_pixel
                    image_height = pixel_height * millimeters_per_pixel
                    image_x = x + (93 - image_width) / 2
                    image_y = slot_y + (slot_height - image_height) / 2
                    picture = QgsLayoutItemPicture(layout)
                    picture.setPicturePath(str(path))
                    picture.setResizeMode(QgsLayoutItemPicture.Zoom)
                    picture.setFrameEnabled(False)
                    layout.addLayoutItem(picture)
                    picture.attemptMove(QgsLayoutPoint(image_x, image_y))
                    picture.attemptResize(QgsLayoutSize(image_width, image_height))
                    image_bottom = image_y + image_height
                else:
                    _label(layout, "", x, slot_y, 93, slot_height,
                           background=QColor("#f3f7fa"))
                    filename = PureWindowsPath(reference).name
                    name_lines = wrap(filename, width=43, break_long_words=True,
                                      break_on_hyphens=False) or ["—"]
                    if len(name_lines) > 2:
                        name_lines = [name_lines[0], name_lines[1][:42].rstrip() + "…"]
                    block_y = slot_y + (slot_height - (7 + 5 * len(name_lines))) / 2
                    missing = _label(layout, "Photographie indisponible", x + 3,
                                     block_y, 87, 6, 8, GRAY, True)
                    missing.setHAlign(Qt.AlignHCenter)
                    name_label = _label(layout, "\n".join(name_lines), x + 3,
                                        block_y + 7, 87, 5 * len(name_lines), 7, GRAY)
                    name_label.setHAlign(Qt.AlignHCenter)
                    name_label.setVAlign(Qt.AlignTop)
                    image_bottom = slot_y + slot_height
                caption = comment or f"Photographie {n + 1}"
                caption_lines = _lines(caption, 48)
                if len(caption_lines) > 2:
                    continuation.append((f"Légende photographie {n + 1}",
                                         _lines(caption, 100)))
                    caption_lines = ["Légende complète en page suivante"]
                caption_label = _label(layout, "\n".join(caption_lines), x,
                                       image_bottom + 1, 93, 8, 7, GRAY)
                caption_label.setHAlign(Qt.AlignHCenter)
                caption_label.setVAlign(Qt.AlignTop)
        else:
            line("Aucune photographie associée", 10, base + 210 + body_offset,
                 190, 8, False, GRAY)
        line("Rapport détaillé de prestations", 10, base + 288, 150, 7, False, GRAY)
        if continuation:
            y = 37
            base = new_page()
            line("Suite de la prestation : " + str(record.get("intitule") or "—"),
                 10, base + 10, 190, 12, True, BLUE)
            for heading, lines in continuation:
                if y + 12 > 282:
                    base = new_page()
                    y = 20
                line(heading, 10, base + y, 190, 9, True, DARK_BLUE)
                y += 8
                for part in lines:
                    if y + 6 > 282:
                        base = new_page()
                        y = 20
                    line(part, 10, base + y, 190, 8)
                    y += 6
                y += 4

    page_labels = []
    for number, base in enumerate(page_bases, 1):
        page_labels.append(line(f"Page {number} / {len(page_bases)}", 164, base + 288,
                                36, 7, False, GRAY))
    if ENABLED:
        _log(f"pdf.detailed.layout total_ms={(perf_counter() - layout_started) * 1000:.2f} "
             f"map_build_ms={map_build_ms:.2f} pages={len(page_bases)} "
             f"maps={map_count} items={len(layout.items())} retained_layers={len(retained)}")
    if _layout_sink is not None:
        _layout_sink(layout, retained, page_labels)
        return len(page_bases)

    settings = QgsLayoutExporter.PdfExportSettings()
    settings.dpi = export_dpi
    settings.textRenderFormat = Qgis.TextRenderFormat.AlwaysText
    if progress:
        progress(f"Rendu cartographique et export PDF — lot 1/1, "
                 f"{len(page_bases)} page{'s' if len(page_bases) != 1 else ''}", 0, 1)
    with (IgnNetworkProbe() if ENABLED and basemap else nullcontext()):
        with measure("pdf.detailed.export_to_pdf"):
            result = QgsLayoutExporter(layout).exportToPdf(str(destination), settings)
    if result != QgsLayoutExporter.Success:
        raise RuntimeError("QGIS n'a pas pu exporter le rapport détaillé.")
    if progress:
        progress(f"Rendu cartographique et export PDF — lot 1/1 terminé, "
                 f"{len(page_bases)} page{'s' if len(page_bases) != 1 else ''}", 1, 1)
        progress("Finalisation du document", 0, 0)
    return len(page_bases)


def _export_detailed_batched(records, destination, include_photos, editor_login,
                             editor_name, progress, export_dpi, map_background):
    """Keep each QGIS scene small while preserving one vector PDF and V04 pages."""
    configure_report_font()
    if progress:
        progress("Préparation du fond cartographique", 0, len(records))
    with measure("pdf.detailed.background"):
        basemap = background(map_background)
    generated_on = date.today().strftime("%d/%m/%Y")
    layouts = []
    retained = [basemap] if basemap else []
    page_labels = []
    batch_pages = []
    total_pages = 0
    for offset in range(0, len(records), _DETAILED_BATCH_SIZE):
        chunk = records[offset:offset + _DETAILED_BATCH_SIZE]

        def collect(layout, layers, labels):
            layouts.append(layout)
            retained.extend(layers)
            page_labels.extend(labels)

        def report_build(stage, current, _total):
            if progress and stage == "Mise en page des prestations":
                progress(stage, offset + current, len(records))

        pages = export_detailed_report(
            chunk, destination, include_photos=include_photos,
            editor_login=editor_login, editor_name=editor_name,
            progress=report_build, export_dpi=export_dpi,
            _layout_sink=collect, _shared_basemap=basemap,
            _generated_on=generated_on, map_background=map_background)
        batch_pages.append(pages)
        total_pages += pages
    for number, label in enumerate(page_labels, 1):
        label.setText(f"Page {number} / {total_pages}")
    settings = QgsLayoutExporter.PdfExportSettings()
    settings.dpi = export_dpi
    settings.textRenderFormat = Qgis.TextRenderFormat.AlwaysText
    def report_batch(completed):
        if not progress:
            return
        index = min(completed, len(batch_pages) - 1)
        pages = batch_pages[index]
        suffix = " terminé" if completed == len(batch_pages) else ""
        progress(f"Rendu cartographique et export PDF — lot {index + 1}/"
                 f"{len(batch_pages)}{suffix}, {pages} page{'s' if pages != 1 else ''}",
                 completed, len(batch_pages))

    report_batch(0)
    with (IgnNetworkProbe() if ENABLED and basemap else nullcontext()):
        with measure("pdf.detailed.batched_export_to_pdf"):
            result, error = QgsLayoutExporter.exportToPdf(
                _ReportLayoutIterator(layouts, lambda index: report_batch(index + 1)),
                str(destination), settings)
    if result != QgsLayoutExporter.Success:
        raise RuntimeError(error or "QGIS n'a pas pu exporter le rapport détaillé.")
    if progress:
        progress("Finalisation du document", 0, 0)
    return total_pages


def export_synthetic_report(records, filters, filter_summary, warnings, destination,
                            progress=None):
    """A4 landscape table with grouped summaries, without maps or photographs."""
    allowed = {"year", "commune", "type", "technician", "provider", "status"}
    groups = normalized_filters(filters)["groups"]
    if any(group not in allowed for group in groups):
        raise ValueError("Regroupement indisponible dans le rapport synthétique.")
    return export_report(records, filters, filter_summary, warnings, str(destination),
                         include_maps=False, include_photos=False, progress=progress)
