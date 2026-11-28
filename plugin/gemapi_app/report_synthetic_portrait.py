"""A4 portrait register with one IGN map per terminal group."""

from contextlib import nullcontext
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from qgis.PyQt.QtGui import QColor, QImageWriter
from qgis.core import (
    Qgis, QgsCoordinateTransform, QgsFeature, QgsFillSymbol, QgsGeometry,
    QgsLayoutExporter, QgsLayoutItemMap, QgsLayoutItemPage, QgsLayoutItemPicture,
    QgsLayoutPoint, QgsLayoutSize, QgsLineSymbol, QgsMarkerSymbol, QgsPrintLayout,
    QgsProject, QgsRectangle, QgsSingleSymbolRenderer, QgsVectorLayer, QgsWkbTypes,
)

from .report_data import (GROUPS, ah_recognition_lines, direct_attachment_label,
                          distinct_records, format_measure, site_child_measure,
                          group_label,
                          grouped_entries, normalized_filters,
                          own_totals, report_context, report_warnings)
from .report_maps import (DATA_CRS, MAP_CRS, apply_minimum_report_scale,
                          background, fit_extent, IgnNetworkProbe, location)
from .perf import ENABLED, _log, measure
from .report_pdf import (BLUE, DARK_BLUE, GRAY, GROUP_BAND_GAP_MM,
                         GROUP_LEVEL_2, GROUP_LEVEL_3, INSTITUTIONAL_LOGO,
                         PALE, TABLE_HEADER_TEXT, _ReportLayoutIterator, _label, _lines,
                         configure_report_font)


PAGE_WIDTH_MM = 210
PAGE_HEIGHT_MM = 297
LEFT_MM = 10
CONTENT_WIDTH_MM = 190
CONTENT_BOTTOM_MM = 282
MAP_WIDTH_MM = 190
MAP_HEIGHT_MM = 92
MAP_MARGIN_METERS = 75
ALLOWED_GROUPS = frozenset(GROUPS)
TABLE_COLUMNS = (("Dates", 28, 16), ("Prestation", 52, 29),
                 ("Intervenants", 48, 27), ("Territoire / mesures", 62, 35))
_SYNTHETIC_BATCH_SIZE = 10


def terminal_groups(records, filters):
    """Keep grouped_entries' ordering and UUID deduplication unchanged."""
    groups = normalized_filters(filters)["groups"]
    if any(group not in ALLOWED_GROUPS for group in groups):
        raise ValueError("Critère indisponible dans le registre cartographique.")
    result = []
    for path, record in grouped_entries(records, groups):
        if not result or result[-1][0] != path:
            result.append((path, {}))
        result[-1][1][str(record["id"])] = record
    return [(path, list(unique.values())) for path, unique in result]


def group_layers_and_extent(records):
    """Map each prestation by its own shape or its direct heritage references."""
    transform = QgsCoordinateTransform(DATA_CRS, MAP_CRS, QgsProject.instance())
    geometries = {}
    references = set()
    extent = None
    for record in records:
        sources = location(record)
        if not sources:
            raise ValueError(f"Localisation patrimoniale absente : {record['id']}")
        for wkt, source_kind, label in sources:
            if source_kind == "reference":
                key = (wkt, label)
                if key in references:
                    continue
                references.add(key)
            geometry = QgsGeometry.fromWkt(wkt)
            if geometry.isNull() or geometry.isEmpty():
                continue
            geometry.transform(transform)
            bounds = geometry.boundingBox()
            if extent is None:
                extent = QgsRectangle(bounds)
            else:
                extent.combineExtentWith(bounds)
            geometries.setdefault((geometry.wkbType(), source_kind == "propre"), []).append(geometry)
    if extent is None:
        raise ValueError("Groupe sans géométrie localisable.")
    layers = []
    for (wkb, own), shapes in geometries.items():
        kind = QgsWkbTypes.geometryType(wkb)
        layer = QgsVectorLayer(QgsWkbTypes.displayString(wkb) + "?crs=EPSG:2154",
                               "Prestations du groupe" if own else "Patrimoine de référence", "memory")
        if not layer.isValid():
            raise RuntimeError("Couche cartographique temporaire invalide.")
        features = []
        for shape in shapes:
            feature = QgsFeature()
            feature.setGeometry(shape)
            features.append(feature)
        if not layer.dataProvider().addFeatures(features)[0]:
            raise RuntimeError("Géométrie de prestation non ajoutée à la carte.")
        if kind == QgsWkbTypes.PointGeometry:
            symbol = QgsMarkerSymbol.createSimple({
                "name": "circle", "color": "#b90055" if own else "#f3ac00", "size": "3.5",
                "outline_color": "white", "outline_width": "0.5"})
        elif kind == QgsWkbTypes.LineGeometry:
            symbol = QgsLineSymbol.createSimple({
                "line_color": "#b90055" if own else "#f3ac00", "line_width": "0.8"})
        else:
            symbol = QgsFillSymbol.createSimple({
                "color": "185,0,85,55" if own else "243,172,0,55",
                "outline_color": "#b90055" if own else "#f3ac00",
                "outline_width": "0.7"})
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        layers.append((kind, own, layer))
    # The first layers are drawn over the following ones by QgsLayoutItemMap.
    order = {QgsWkbTypes.PointGeometry: 0, QgsWkbTypes.LineGeometry: 1,
             QgsWkbTypes.PolygonGeometry: 2}
    layers.sort(key=lambda entry: (order[entry[0]], not entry[1]))
    # A fixed 75 m buffer is too narrow for point symbols on territory-wide maps.
    extent.grow(max(MAP_MARGIN_METERS, 0.02 * max(extent.width(), extent.height())))
    return [layer for _, _, layer in layers], fit_extent(
        extent, MAP_WIDTH_MM / MAP_HEIGHT_MM)


def _page(layout, number):
    if number:
        page = QgsLayoutItemPage(layout)
        page.setPageSize(QgsLayoutSize(PAGE_WIDTH_MM, PAGE_HEIGHT_MM))
        layout.pageCollection().addPage(page)
    else:
        page = layout.pageCollection().pages()[0]
        page.setPageSize(QgsLayoutSize(PAGE_WIDTH_MM, PAGE_HEIGHT_MM))
    base = number * (PAGE_HEIGHT_MM + 10)
    _label(layout, "Registre cartographique des prestations", LEFT_MM,
           base + 8, 143, 10, 14, BLUE, True)
    _label(layout, "Édité le " + date.today().strftime("%d/%m/%Y"), LEFT_MM,
           base + 21, 143, 6, 8, GRAY)
    logo = QgsLayoutItemPicture(layout)
    logo.setPicturePath(str(INSTITUTIONAL_LOGO))
    logo.setResizeMode(QgsLayoutItemPicture.Zoom)
    layout.addLayoutItem(logo)
    logo.attemptMove(QgsLayoutPoint(155, base + 5))
    logo.attemptResize(QgsLayoutSize(45, 24))
    return base


def _group_bands(layout, path, records, y):
    nodes = path or ((None, "Toutes les prestations sélectionnées",
                      len(records), *own_totals(records)),)
    for level, node in enumerate(nodes):
        if level:
            y += GROUP_BAND_GAP_MM
        criterion, name, count, length, area = node
        title = (f"{GROUPS[criterion]} : {group_label(criterion, name)}" if criterion else name)
        lines = _lines(title, 85)
        height = 6 * len(lines) + 11
        fill = (DARK_BLUE if level == 0 else
                GROUP_LEVEL_2 if level == 1 else GROUP_LEVEL_3)
        color = QColor("#ffffff") if level == 0 else DARK_BLUE
        _label(layout, "", LEFT_MM, y, CONTENT_WIDTH_MM, height,
               background=fill)
        for index, line in enumerate(lines):
            _label(layout, line, LEFT_MM + 2, y + 1 + 6 * index,
                   CONTENT_WIDTH_MM - 4, 5, 9 if level == 0 else 8,
                   color, True)
        _label(layout, f"{count} prestation(s) distincte(s)",
               LEFT_MM + 2, y + 1 + 6 * len(lines),
               CONTENT_WIDTH_MM - 4, 4, 7, color)
        summary = (f"Somme des longueurs cartographiées : {format_measure(length)} m  |  "
                   f"Somme des surfaces cartographiées : {format_measure(area)} m²")
        _label(layout, summary, LEFT_MM + 2, y + 6 + 6 * len(lines),
               CONTENT_WIDTH_MM - 4, 4, 7, color)
        y += height
    return y


def _table_header(layout, y):
    x = LEFT_MM
    for title, width, _ in TABLE_COLUMNS:
        _label(layout, " " + title, x, y, width, 7, 8,
               TABLE_HEADER_TEXT, True, PALE)
        x += width
    return y + 8


def _cell_lines(record, grouped):
    def pieces(parts, width):
        return [line for part in parts if part for line in _lines(part, width)] or ["—"]

    prestation = [record.get("intitule") or "Sans intitulé"]
    if "type" not in grouped:
        prestation.append("Type : " + str(record.get("type") or "—"))
    if "status" not in grouped:
        prestation.append("Statut : " + str(record.get("statut") or "—"))
    intervenants = []
    if "provider" not in grouped:
        intervenants.append("Prestataire : " + str(record.get("provider") or "—"))
    if "technician" not in grouped:
        intervenants.append("Technicien : " + str(record.get("technician") or "—"))
    territory = []
    if "commune" not in grouped:
        territory.append("Commune(s) : " + str(record.get("communes") or "Non déterminées"))
    if record.get("objects"):
        territory.append("Rattachement : " + direct_attachment_label(record))
    recognition = ah_recognition_lines(record)
    if "ah" in grouped and recognition:
        # The enclosing AH heading already names the concerned parent. Avoid
        # repeating every other AH and its long name in each group's narrow cell.
        territory.append("Reconnaissance indirecte par intersection.")
    else:
        territory.extend(recognition)
    if record.get("position") and record["position"] != "Indéfini":
        territory.append("Position : " + str(record["position"]))
    if record.get("rive") and record["rive"] != "Indéfinie":
        territory.append("Rive : " + str(record["rive"]))
    if record.get("quantite") is not None:
        territory.append("Quantité déclarée : " + str(record["quantite"]) +
                         (" " + str(record.get("unite") or "") if record.get("unite") else ""))
    if record.get("own_length") is not None:
        territory.append("Longueur cartographiée : " + format_measure(record["own_length"]) + " m")
    if record.get("own_area") is not None:
        territory.append("Surface cartographiée : " + format_measure(record["own_area"]) + " m²")
    if record.get("own_points") is not None:
        territory.append(f"Nombre de points cartographiés : {record['own_points']}")
    child_measure = site_child_measure(record)
    if child_measure:
        territory.append(child_measure)
    dates = [str(record.get("date_debut") or "—"),
             str(record.get("date_fin") or "—")]
    return (pieces(dates, TABLE_COLUMNS[0][2]),
            pieces(prestation, TABLE_COLUMNS[1][2]),
            pieces(intervenants, TABLE_COLUMNS[2][2]),
            pieces(territory, TABLE_COLUMNS[3][2]))


def export_synthetic_portrait_report(records, filters, destination, progress=None,
                                     diagnostics=None, basemap_layer=None,
                                     _terminal_override=None, _start_group_index=0,
                                     _layout_sink=None, _shared_basemap=None,
                                     map_background="epure"):
    """Export the cartographic A4 portrait register without changing V04."""
    records = distinct_records(records)
    if not records:
        raise ValueError("Aucune prestation : aucun PDF n'est produit.")
    configure_report_font()
    layout_started = perf_counter() if ENABLED else None
    normalized = normalized_filters(filters)
    groups = normalized["groups"]
    terminal = (_terminal_override if _terminal_override is not None else
                terminal_groups(records, normalized))
    if _terminal_override is None and len(terminal) > _SYNTHETIC_BATCH_SIZE:
        return _export_synthetic_batched(records, normalized, terminal,
                                         destination, progress, diagnostics,
                                         basemap_layer, map_background)
    with measure("pdf.synthetic.background"):
        basemap = (_shared_basemap if _shared_basemap is not None else
                   basemap_layer if basemap_layer is not None else background(map_background))
    if basemap is None or not basemap.isValid():
        raise RuntimeError("Fond IGN sélectionné indisponible : export cartographique annulé.")
    layout = QgsPrintLayout(QgsProject.instance())
    layout.initializeDefaults()
    layout.setName("Registre synthétique cartographique A4 portrait")
    page_number = 0
    base = _page(layout, page_number)
    pages = [base]
    retained = [basemap]
    map_items = []
    if _start_group_index == 0:
        length, area = own_totals(list({str(r["id"]): r for r in records}.values()))
        general = (f"Total général : {len({str(r['id']) for r in records})} prestations")
        scope, organization = report_context(records, normalized)
        y = base + 30
        for line in _lines(scope, 90):
            _label(layout, line, LEFT_MM, y, CONTENT_WIDTH_MM, 5, 8, BLUE, True)
            y += 5
        y += 1
        for line in _lines(organization, 90):
            _label(layout, line, LEFT_MM, y, CONTENT_WIDTH_MM, 5, 8, GRAY)
            y += 5
        y += 2
        _label(layout, general, LEFT_MM, y, CONTENT_WIDTH_MM, 5, 8, BLUE, True)
        y += 7
        _label(layout, f"Somme des longueurs cartographiées : {format_measure(length)} m  |  "
               f"Somme des surfaces cartographiées : {format_measure(area)} m²",
               LEFT_MM, y, CONTENT_WIDTH_MM, 5, 7, BLUE)
        y += 6
        for warning in report_warnings(records, normalized):
            if "groupes AH" in warning:
                for text in _lines(warning, 100):
                    _label(layout, text, LEFT_MM, y, CONTENT_WIDTH_MM, 4, 7, GRAY)
                    y += 4
                y += 2
    else:
        y = base + 37

    def next_page(path):
        nonlocal page_number, base, y
        page_number += 1
        base = _page(layout, page_number)
        pages.append(base)
        y = base + 37
        if path:
            title = "Suite : " + " > ".join(group_label(node[0], node[1]) for node in path)
        else:
            title = "Suite : toutes les prestations"
        for line in _lines(title, 95):
            _label(layout, line, LEFT_MM, y, CONTENT_WIDTH_MM, 5,
                   8, DARK_BLUE, True)
            y += 5
        y += 2
        y = _table_header(layout, y)

    for group_index, (path, members) in enumerate(terminal):
        if progress:
            progress("Mise en page des groupes", group_index + 1, len(terminal))
        if group_index:
            page_number += 1
            base = _page(layout, page_number)
            pages.append(base)
            y = base + 37
        y = _group_bands(layout, path, members, y)
        map_started = perf_counter() if ENABLED else None
        overlays, extent = group_layers_and_extent(members)
        retained.extend(overlays)
        y += 3
        map_item = QgsLayoutItemMap(layout)
        layout.addLayoutItem(map_item)
        map_item.attemptMove(QgsLayoutPoint(LEFT_MM, y))
        map_item.attemptResize(QgsLayoutSize(MAP_WIDTH_MM, MAP_HEIGHT_MM))
        map_item.setCrs(MAP_CRS)
        map_item.setLayers(overlays + [basemap])
        map_item.setKeepLayerSet(True)
        map_item.setFrameEnabled(True)
        map_item.zoomToExtent(extent)
        apply_minimum_report_scale(map_item)
        if diagnostics is not None:
            diagnostics.append({"path": [node[1] for node in path],
                                "prestations": len(members),
                                "scale": map_item.scale(),
                                "extent_width_m": extent.width(),
                                "extent_height_m": extent.height()})
        map_items.append(map_item)
        if ENABLED:
            _log(f"pdf.synthetic.map_build index={group_index + 1} "
                 f"total_ms={(perf_counter() - map_started) * 1000:.2f} "
                 f"records={len(members)} overlays={len(overlays)}")
        y += MAP_HEIGHT_MM + 1
        copyright_text = ("© IGN - Géoplateforme (Plan Épuré)" if map_background == "epure"
                          else "© IGN - Géoplateforme (Orthophotographies)")
        _label(layout, copyright_text, LEFT_MM,
               y, CONTENT_WIDTH_MM, 5, 7, GRAY)
        y += 8
        if y + 20 > base + CONTENT_BOTTOM_MM:
            next_page(path)
        else:
            y = _table_header(layout, y)
        for row_index, record in enumerate(members):
            remaining = [list(lines) for lines in _cell_lines(record, groups)]
            first_piece = True
            while any(remaining):
                available = base + CONTENT_BOTTOM_MM - y
                capacity = int((available - 2) / 4.7)
                if available < 12 or capacity < 2:
                    next_page(path)
                    continue
                pieces = [lines[:capacity] for lines in remaining]
                row_height = max(12, max(len(part) for part in pieces) * 4.7 + 2)
                x = LEFT_MM
                for index, (part, (_, width, _)) in enumerate(zip(pieces, TABLE_COLUMNS)):
                    value = "\n".join(part)
                    if not first_piece and index == 0 and not value:
                        value = "(suite)"
                    _label(layout, value, x + 1, y + 1, width - 2,
                           row_height - 2, 8,
                           background=QColor("#f8fafc") if row_index % 2 else None)
                    x += width
                y += row_height
                remaining = [lines[capacity:] for lines in remaining]
                first_piece = False

    page_labels = []
    for index, page_base in enumerate(pages, 1):
        _label(layout, "GEMAPI-App — registre synthétique", LEFT_MM,
               page_base + 287, 150, 5, 7, GRAY)
        page_labels.append(_label(layout, f"Page {index} / {len(pages)}", 165,
                                  page_base + 287, 35, 5, 7, GRAY))
    if ENABLED:
        _log(f"pdf.synthetic.layout total_ms={(perf_counter() - layout_started) * 1000:.2f} "
             f"pages={len(pages)} maps={len(map_items)} items={len(layout.items())} "
             f"retained_layers={len(retained)}")
    if _layout_sink is not None:
        _layout_sink(layout, retained, page_labels, map_items)
        return len(pages), len(terminal)
    settings = QgsLayoutExporter.PdfExportSettings()
    settings.dpi = 150
    settings.textRenderFormat = Qgis.TextRenderFormat.AlwaysText
    exporter = QgsLayoutExporter(layout)
    with TemporaryDirectory(prefix="sig-synthetic-portrait-") as directory:
        if progress:
            progress("Rendu cartographique", 0, len(map_items))
        with (IgnNetworkProbe() if ENABLED and basemap_layer is None else nullcontext()):
            _rasterize_synthetic_maps(layout, map_items, directory, 0,
                                      len(map_items), progress)
        if progress:
            progress(f"Rendu cartographique et export PDF — lot 1/1, "
                     f"{len(terminal)} groupe{'s' if len(terminal) != 1 else ''}", 0, 1)
        with (IgnNetworkProbe() if ENABLED and basemap_layer is None else nullcontext()):
            with measure("pdf.synthetic.export_to_pdf"):
                result = exporter.exportToPdf(str(destination), settings)
        if result != QgsLayoutExporter.Success:
            raise RuntimeError("QGIS n'a pas pu exporter le registre cartographique.")
        if progress:
            progress(f"Rendu cartographique et export PDF — lot 1/1 terminé, "
                     f"{len(terminal)} groupe{'s' if len(terminal) != 1 else ''}", 1, 1)
    if progress:
        progress("Finalisation du document", 0, 0)
    return len(pages), len(terminal)


def _rasterize_synthetic_maps(layout, map_items, directory, offset, total, progress):
    exporter = QgsLayoutExporter(layout)
    for index, map_item in enumerate(map_items):
        started = perf_counter() if ENABLED else None
        image = exporter.renderRegionToImage(map_item.sceneBoundingRect(), dpi=150)
        if image.isNull():
            raise RuntimeError("Rendu de la carte IGN impossible.")
        path = Path(directory) / f"carte-{offset + index:03d}.jpg"
        writer = QImageWriter(str(path), b"jpg")
        writer.setQuality(85)
        if not writer.write(image):
            raise RuntimeError("Écriture de la carte IGN impossible.")
        picture = QgsLayoutItemPicture(layout)
        picture.setPicturePath(str(path))
        picture.setResizeMode(QgsLayoutItemPicture.Stretch)
        layout.addLayoutItem(picture)
        picture.attemptMove(map_item.positionWithUnits())
        picture.attemptResize(map_item.sizeWithUnits())
        map_item.setVisible(False)
        if ENABLED:
            _log(f"pdf.synthetic.map_render index={offset + index + 1} "
                 f"total_ms={(perf_counter() - started) * 1000:.2f} "
                 f"jpeg_bytes={path.stat().st_size}")
        if progress:
            progress("Rendu cartographique", offset + index + 1, total)


def _export_synthetic_batched(records, normalized, terminal, destination,
                              progress, diagnostics, basemap_layer, map_background):
    """Export large group trees through small QGIS scenes and one native PDF."""
    with measure("pdf.synthetic.background"):
        basemap = basemap_layer if basemap_layer is not None else background(map_background)
    if basemap is None or not basemap.isValid():
        raise RuntimeError("Fond IGN sélectionné indisponible : export cartographique annulé.")
    layouts = []
    map_groups = []
    retained = [basemap]
    page_labels = []
    batch_groups = []
    total_pages = 0
    for offset in range(0, len(terminal), _SYNTHETIC_BATCH_SIZE):
        chunk = terminal[offset:offset + _SYNTHETIC_BATCH_SIZE]

        def collect(layout, layers, labels, map_items):
            layouts.append(layout)
            retained.extend(layers)
            page_labels.extend(labels)
            map_groups.append((offset, layout, map_items))

        def report_build(stage, current, _total):
            if progress and stage == "Mise en page des groupes":
                progress(stage, offset + current, len(terminal))

        pages, _maps = export_synthetic_portrait_report(
            records, normalized, destination, progress=report_build,
            diagnostics=diagnostics, basemap_layer=basemap,
            _terminal_override=chunk, _start_group_index=offset,
            _layout_sink=collect, _shared_basemap=basemap,
            map_background=map_background)
        total_pages += pages
        batch_groups.append(len(chunk))
    for number, label in enumerate(page_labels, 1):
        label.setText(f"Page {number} / {total_pages}")
    settings = QgsLayoutExporter.PdfExportSettings()
    settings.dpi = 150
    settings.textRenderFormat = Qgis.TextRenderFormat.AlwaysText
    with TemporaryDirectory(prefix="sig-synthetic-portrait-") as directory:
        if progress:
            progress("Rendu cartographique", 0, len(terminal))
        with (IgnNetworkProbe() if ENABLED and basemap_layer is None else nullcontext()):
            for offset, layout, map_items in map_groups:
                _rasterize_synthetic_maps(layout, map_items, directory,
                                          offset, len(terminal), progress)
        def report_batch(completed):
            if not progress:
                return
            index = min(completed, len(batch_groups) - 1)
            groups_count = batch_groups[index]
            suffix = " terminé" if completed == len(batch_groups) else ""
            progress(f"Rendu cartographique et export PDF — lot {index + 1}/"
                     f"{len(batch_groups)}{suffix}, {groups_count} "
                     f"groupe{'s' if groups_count != 1 else ''}",
                     completed, len(batch_groups))

        report_batch(0)
        with (IgnNetworkProbe() if ENABLED and basemap_layer is None else nullcontext()):
            with measure("pdf.synthetic.batched_export_to_pdf"):
                result, error = QgsLayoutExporter.exportToPdf(
                    _ReportLayoutIterator(layouts, lambda index: report_batch(index + 1)),
                    str(destination), settings)
        if result != QgsLayoutExporter.Success:
            raise RuntimeError(error or "QGIS n'a pas pu exporter le registre cartographique.")
    if progress:
        progress("Finalisation du document", 0, 0)
    return total_pages, len(terminal)
