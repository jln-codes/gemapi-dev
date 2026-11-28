"""Isolated QGIS layout maps for prestation locations (no project edits)."""

import json
import hashlib
from time import perf_counter
from urllib.request import urlopen
from .perf import measure
from .record import TARGETS

from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsDataSourceUri,
    QgsFeature, QgsFillSymbol, QgsGeometry, QgsLineSymbol, QgsMapBoxGlStyleConverter,
    QgsMarkerSymbol, QgsProject, QgsRasterLayer, QgsRectangle, QgsSingleSymbolRenderer,
    QgsVectorLayer, QgsVectorTileLayer, QgsWkbTypes, QgsNetworkAccessManager,
)


MARGIN_METERS = 75
MAP_WIDTH_MM = 277
MAP_HEIGHT_MM = 60
MIN_REPORT_MAP_SCALE_DENOMINATOR = 7500
MAP_CRS = QgsCoordinateReferenceSystem("EPSG:2154")
DATA_CRS = QgsCoordinateReferenceSystem("EPSG:3950")
IGN_STYLE = "https://data.geopf.fr/annexes/ressources/vectorTiles/styles/PLAN.IGN/epure.json"
IGN_TILES = "https://data.geopf.fr/tms/1.0.0/PLAN.IGN/{z}/{x}/{y}.pbf"
IGN_WMS = "https://data.geopf.fr/wms-r"


class IgnNetworkProbe:
    """Count QGIS IGN requests during PDF export without logging their URLs."""

    def __init__(self):
        self.manager = None
        self.pending = {}
        self.durations = []
        self.started = 0
        self.request_fingerprints = set()
        self.repeated = 0

    def __enter__(self):
        self.manager = QgsNetworkAccessManager.instance()
        self.manager.requestCreated.connect(self._created)
        self.manager.finished.connect(self._finished)
        return self

    def __exit__(self, *_):
        self.manager.requestCreated.disconnect(self._created)
        self.manager.finished.disconnect(self._finished)
        from .perf import _log
        _log(f"pdf.ign.qgis_requests started={self.started} finished={len(self.durations)} "
             f"pending={len(self.pending)} unique={len(self.request_fingerprints)} "
             f"repeated={self.repeated} summed_ms={sum(self.durations):.2f}")

    def _created(self, params):
        if params.request().url().host().lower() == "data.geopf.fr":
            self.started += 1
            self.pending[params.requestId()] = perf_counter()
            fingerprint = hashlib.sha256(
                params.request().url().toString().encode("utf-8")).digest()
            if fingerprint in self.request_fingerprints:
                self.repeated += 1
            self.request_fingerprints.add(fingerprint)

    def _finished(self, reply):
        started = self.pending.pop(reply.requestId(), None)
        if started is not None:
            self.durations.append((perf_counter() - started) * 1000)


def fit_extent(rect, ratio=MAP_WIDTH_MM / MAP_HEIGHT_MM):
    """Expand a rectangle to the map aspect ratio without cropping it."""
    width = max(rect.width(), 1)
    height = max(rect.height(), 1)
    x, y = rect.center().x(), rect.center().y()
    if width / height < ratio:
        width = height * ratio
    else:
        height = width / ratio
    return QgsRectangle(x - width / 2, y - height / 2,
                        x + width / 2, y + height / 2)


def apply_minimum_report_scale(map_item):
    """Widen overly detailed views around their center without changing the map frame."""
    if map_item.scale() < MIN_REPORT_MAP_SCALE_DENOMINATOR:
        map_item.setScale(MIN_REPORT_MAP_SCALE_DENOMINATOR)


def background(kind):
    """Create a standalone IGN layer once per export; failure means no basemap."""
    try:
        if kind == "epure":
            with measure("pdf.ign.style_fetch"):
                with urlopen(IGN_STYLE, timeout=6) as response:
                    style = json.load(response)
            uri = QgsDataSourceUri()
            uri.setParam("type", "xyz")
            uri.setParam("url", IGN_TILES)
            layer = QgsVectorTileLayer(bytes(uri.encodedUri()).decode(), "IGN Plan Épuré")
            converter = QgsMapBoxGlStyleConverter()
            if not layer.isValid() or converter.convert(style) != QgsMapBoxGlStyleConverter.Success:
                return None
            layer.setRenderer(converter.renderer())
            if converter.labeling():
                layer.setLabeling(converter.labeling())
            return layer
        if kind == "ortho":
            check = IGN_WMS + "?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetCapabilities"
            with measure("pdf.ign.capabilities_fetch"):
                with urlopen(check, timeout=6) as response:
                    response.read(1)
            uri = ("crs=EPSG:3857&format=image/jpeg&layers=ORTHOIMAGERY.ORTHOPHOTOS"
                   "&styles=normal&url=" + IGN_WMS)
            layer = QgsRasterLayer(uri, "IGN Orthophotographies", "wms")
            return layer if layer.isValid() else None
    except Exception:
        return None
    return None


def location(record):
    """Keep the complete work geometry and show its site as map context."""
    references = record.get("reference_geometries") or []
    own = record.get("own_wkt")
    if own:
        references = [reference for reference in references
                      if reference.get("famille") == "sites_terrestres"]
    seen = set()
    result = [(own, "propre", "Géométrie propre")] if own else []
    for reference in references:
        if not reference.get("direct", True) or reference.get("famille") not in TARGETS:
            continue
        key = (reference.get("famille"), reference.get("objet_id"))
        if key in seen or not reference.get("wkt"):
            continue
        seen.add(key)
        family = "Site terrestre" if key[0] == "sites_terrestres" else key[0]
        result.append((reference["wkt"], "reference",
                       f"{family} : {reference.get('nom') or key[1]} (directe)"))
    return result


def location_layers(record, map_ratio=MAP_WIDTH_MM / MAP_HEIGHT_MM):
    """Return overlay layers, a fitted extent, and readable provenance."""
    sources = location(record)
    if not sources:
        return [], None, "Aucune géométrie localisable."
    transform = QgsCoordinateTransform(DATA_CRS, MAP_CRS, QgsProject.instance())
    layers = []
    extent_data = None
    for wkt, kind, label in sources:
        geometry = QgsGeometry.fromWkt(wkt)
        if geometry.isNull() or geometry.isEmpty():
            continue
        bounds = geometry.boundingBox()
        if extent_data is None:
            extent_data = QgsRectangle(bounds)
        else:
            extent_data.combineExtentWith(bounds)
        geometry.transform(transform)
        wkb = geometry.wkbType()
        layer = QgsVectorLayer(QgsWkbTypes.displayString(wkb) + "?crs=EPSG:2154",
                               label, "memory")
        if not layer.isValid():
            continue
        feature = QgsFeature()
        feature.setGeometry(geometry)
        if not layer.dataProvider().addFeatures([feature])[0]:
            continue
        own = kind == "propre"
        color = "#b90055" if own else "#f3ac00"
        geometry_type = QgsWkbTypes.geometryType(wkb)
        if geometry_type == QgsWkbTypes.PointGeometry:
            symbol = QgsMarkerSymbol.createSimple({"name": "circle", "color": color,
                                                     "outline_color": "white", "outline_width": "0.7",
                                                     "size": "5"})
        elif geometry_type == QgsWkbTypes.LineGeometry:
            symbol = QgsLineSymbol.createSimple({"line_color": color, "line_width": "1.1",
                                                   "line_style": "solid" if own else "dash"})
        else:
            symbol = QgsFillSymbol.createSimple({"color": "185,0,85,55" if own else "243,172,0,55",
                                                   "outline_color": color,
                                                   "outline_width": "0.9",
                                                   "outline_style": "solid" if own else "dash"})
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        layers.append(layer)
    if not layers or extent_data is None:
        return [], None, "Aucune géométrie localisable."
    extent_data.grow(MARGIN_METERS)
    target = transform.transformBoundingBox(extent_data)
    return layers, fit_extent(target, map_ratio), "; ".join(label for _, _, label in sources)
