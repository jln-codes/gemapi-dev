"""Temporary QGIS canvas capture, independent of the prestation form.

Left click places vertices; when QGIS tracing is enabled, connected snapped
vertices follow its canvas tracer. Right click completes a line or polygon;
Esc cancels. The previous map tool and all temporary canvas items are restored
or removed on completion, cancellation, external tool changes, or plugin unload.
"""

from math import isfinite

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QCursor
from qgis.PyQt.QtWidgets import QToolTip
from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry,
    QgsMessageLog,
    QgsPointXY, QgsProject, QgsWkbTypes,
)
from qgis.gui import QgsMapCanvasTracer, QgsMapTool, QgsRubberBand, QgsVertexMarker


KINDS = {"point": "Point", "line": "Ligne", "polygon": "Polygone"}
TARGET_CRS = "EPSG:3950"
POINT_SIZE_MM = 4.0
VERTEX_SIZE_MM = 3.0
STROKE_WIDTH_MM = 0.66
POLYGON_FILL_ALPHA = 36
SNAP_MARKER_SIZE_MM = 6.0


def _canvas_pixels(canvas, millimetres):
    """Rubber-band sizes use Qt logical pixels; keep physical size across DPI."""
    return millimetres * canvas.logicalDpiX() / 25.4


def geometry_from_vertices(kind, vertices, source_crs, project=None):
    """Return a valid geometry transformed from canvas CRS to EPSG:3950."""
    if kind not in KINDS:
        raise ValueError("Type de géométrie inconnu.")
    if not source_crs or not source_crs.isValid():
        raise ValueError("Le SCR du canevas QGIS est absent ou invalide.")
    target = QgsCoordinateReferenceSystem(TARGET_CRS)
    if not target.isValid():
        raise ValueError("Le SCR EPSG:3950 n'est pas disponible dans QGIS.")
    points = [QgsPointXY(point) for point in vertices]
    if not points or any(not (isfinite(point.x()) and isfinite(point.y())) for point in points):
        raise ValueError("La géométrie est vide ou comporte des coordonnées invalides.")
    distinct = {(point.x(), point.y()) for point in points}
    if kind == "point":
        if len(points) != 1:
            raise ValueError("Un point doit comporter un seul sommet.")
        geometry = QgsGeometry.fromPointXY(points[0])
        expected_type = QgsWkbTypes.PointGeometry
    elif kind == "line":
        if len(points) < 2 or len(distinct) < 2:
            raise ValueError("Une ligne exige au moins deux sommets distincts.")
        geometry = QgsGeometry.fromPolylineXY(points)
        expected_type = QgsWkbTypes.LineGeometry
    else:
        if len(points) > 1 and points[-1] == points[0]:
            points.pop()
        if len({(point.x(), point.y()) for point in points}) < 3:
            raise ValueError("Un polygone exige au moins trois sommets distincts.")
        geometry = QgsGeometry.fromPolygonXY([points + [points[0]]])
        expected_type = QgsWkbTypes.PolygonGeometry
    _check_geometry(geometry, expected_type)
    if source_crs != target:
        try:
            result = geometry.transform(QgsCoordinateTransform(
                source_crs, target, project or QgsProject.instance()))
        except Exception as error:
            raise ValueError("Transformation vers EPSG:3950 impossible.") from error
        if result != Qgis.GeometryOperationResult.Success:
            raise ValueError("La transformation vers EPSG:3950 a échoué.")
        _check_geometry(geometry, expected_type)
    return geometry


def _check_geometry(geometry, expected_type):
    if geometry.isNull() or geometry.isEmpty() or geometry.type() != expected_type:
        raise ValueError("La géométrie dessinée est vide ou de type incorrect.")
    if expected_type == QgsWkbTypes.LineGeometry and geometry.length() <= 0:
        raise ValueError("La ligne dessinée est dégénérée.")
    if expected_type == QgsWkbTypes.PolygonGeometry and geometry.area() <= 0:
        raise ValueError("Le polygone dessiné est dégénéré.")
    if not geometry.isGeosValid():
        raise ValueError("La géométrie dessinée est invalide.")


def geometry_part_count(geometry):
    """Count parts without changing a simple or multipart geometry."""
    return len(geometry.asGeometryCollection()) if geometry.isMultipart() else 1


def _parts(geometry):
    family = QgsWkbTypes.geometryType(geometry.wkbType())
    if family == QgsWkbTypes.PointGeometry:
        return family, list(geometry.asMultiPoint() if geometry.isMultipart()
                            else [geometry.asPoint()])
    if family == QgsWkbTypes.LineGeometry:
        return family, list(geometry.asMultiPolyline() if geometry.isMultipart()
                            else [geometry.asPolyline()])
    if family == QgsWkbTypes.PolygonGeometry:
        return family, list(geometry.asMultiPolygon() if geometry.isMultipart()
                            else [geometry.asPolygon()])
    raise ValueError("Seuls les points, lignes et polygones sont acceptés.")


def _from_parts(family, parts):
    if family == QgsWkbTypes.PointGeometry:
        result = (QgsGeometry.fromPointXY(parts[0]) if len(parts) == 1 else
                  QgsGeometry.fromMultiPointXY(parts))
    elif family == QgsWkbTypes.LineGeometry:
        result = (QgsGeometry.fromPolylineXY(parts[0]) if len(parts) == 1 else
                  QgsGeometry.fromMultiPolylineXY(parts))
    else:
        result = (QgsGeometry.fromPolygonXY(parts[0]) if len(parts) == 1 else
                  QgsGeometry.fromMultiPolygonXY(parts))
    _check_geometry(result, family)
    return result


def append_geometry_part(existing, new_part):
    """Append a disjoint or contiguous part, preserving every original part."""
    family, parts = _parts(existing)
    new_family, additions = _parts(new_part)
    if family != new_family or len(additions) != 1:
        raise ValueError("La nouvelle partie doit appartenir à la même famille géométrique.")
    return _from_parts(family, parts + additions)


def remove_last_geometry_part(geometry):
    family, parts = _parts(geometry)
    if len(parts) <= 1:
        raise ValueError("La géométrie ne contient qu'une partie.")
    return _from_parts(family, parts[:-1])


class CaptureTool(QgsMapTool):
    completed = pyqtSignal(object)
    cancelled = pyqtSignal(str)
    trace_failed = pyqtSignal(str)

    def __init__(self, canvas, kind, source_crs):
        super().__init__(canvas)
        self._finished = False
        self.previous_tool = None
        self.kind = kind
        self.source_crs = source_crs
        self.vertices = []
        self._last_was_snapped = False
        red = QColor(255, 0, 0)
        self._vertices_preview = QgsRubberBand(canvas, QgsWkbTypes.PointGeometry)
        self._vertices_preview.setColor(red)
        self._vertices_preview.setIcon(QgsRubberBand.ICON_CIRCLE)
        self._vertices_preview.setIconSize(_canvas_pixels(
            canvas, POINT_SIZE_MM if kind == "point" else VERTEX_SIZE_MM))
        self._snap_marker = QgsVertexMarker(canvas)
        self._snap_marker.setIconType(QgsVertexMarker.ICON_BOX)
        self._snap_marker.setIconSize(round(_canvas_pixels(canvas, SNAP_MARKER_SIZE_MM)))
        self._snap_marker.setPenWidth(2)
        self._snap_marker.setColor(QColor(255, 215, 0))
        self._snap_marker.setFillColor(QColor(255, 255, 255, 80))
        self._snap_marker.hide()
        self._preview = None
        self._segment_preview = None
        if kind != "point":
            geometry_type = (QgsWkbTypes.PolygonGeometry if kind == "polygon"
                             else QgsWkbTypes.LineGeometry)
            self._preview = QgsRubberBand(canvas, geometry_type)
            self._preview.setStrokeColor(red)
            self._preview.setWidth(_canvas_pixels(canvas, STROKE_WIDTH_MM))
            if kind == "polygon":
                self._preview.setFillColor(QColor(255, 0, 0, POLYGON_FILL_ALPHA))
                # One or two vertices form a provisional segment, not a polygon.
                self._segment_preview = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
                self._segment_preview.setStrokeColor(red)
                self._segment_preview.setWidth(_canvas_pixels(canvas, STROKE_WIDTH_MM))

    def canvasReleaseEvent(self, event):
        if self._finished:
            return
        if event.button() == Qt.RightButton and self.kind != "point":
            self._complete()
        elif event.button() == Qt.LeftButton:
            point = self._snapped_point(event)
            if point is None:
                return
            match = self._last_match
            if self.kind != "point" and self.vertices:
                path, error = self._trace_segment(point, match)
                if error:
                    self.trace_failed.emit(error)
                    return
                self.vertices.extend(path[1:] if path is not None else [point])
            else:
                self.vertices.append(point)
            self._last_was_snapped = match.isValid()
            if self.kind == "point":
                self._complete()
            else:
                self._draw_preview()

    def canvasMoveEvent(self, event):
        if self._finished:
            return
        point = self._snapped_point(event)
        if point is not None:
            path, error = self._trace_segment(point, self._last_match)
            self._draw_preview(point, path, error)

    def _snapped_point(self, event):
        """Use the canvas' existing project snapping configuration unchanged."""
        if self._finished:
            return None
        point = (event.originalMapPoint() if hasattr(event, "originalMapPoint")
                 else event.mapPoint())
        match = self.canvas().snappingUtils().snapToMap(point)
        if self._finished:
            return None
        self._last_match = match
        if match.isValid():
            point = match.point()
            self._snap_marker.setCenter(point)
            self._snap_marker.show()
        else:
            self._snap_marker.hide()
        return QgsPointXY(point)

    def _active_tracer(self):
        """Respect the user's QGIS trace action; a standalone canvas has none."""
        tracer = QgsMapCanvasTracer.tracerForCanvas(self.canvas())
        if tracer is None:
            return None
        action = tracer.actionEnableTracing()
        snapping_action = tracer.actionEnableSnapping()
        if (action is None or not action.isChecked() or
                (snapping_action is not None and not snapping_action.isChecked()) or
                not self.canvas().snappingUtils().config().enabled()):
            return None
        return tracer

    def _trace_segment(self, point, match):
        """Return QGIS's traced route, or a reason to keep the current vertices."""
        if not self.vertices or self.kind == "point" or not self._last_was_snapped:
            return None, None
        tracer = self._active_tracer()
        if tracer is None or not match.isValid():
            return None, None
        path, error = tracer.findShortestPath(self.vertices[-1], point)
        if error == tracer.ErrTooManyFeatures:
            return None, "Tracé impossible : trop d'entités visibles. Zoomez ou limitez les couches accrochables."
        if error != tracer.ErrNone or len(path) < 2:
            return None, "Aucun chemin connecté entre ces points. Désactivez Tracé pour dessiner manuellement."
        if path[0] != self.vertices[-1] or path[-1] != point:
            return None, "Le chemin calculé ne correspond pas aux points accrochés. Tracé annulé."
        return path, None

    def keyPressEvent(self, event):
        if self._finished:
            return
        if event.key() == Qt.Key_Escape:
            event.accept()
            self.cancelled.emit("Numérisation annulée.")

    def _draw_preview(self, cursor=None, trace=None, trace_error=None):
        if self._finished:
            return
        points = self.vertices + (list(trace[1:]) if trace is not None else
                                  ([QgsPointXY(cursor)] if cursor is not None and not trace_error else []))
        if points:
            markers = (QgsGeometry.fromPointXY(points[0]) if len(points) == 1
                       else QgsGeometry.fromMultiPointXY(points))
            self._vertices_preview.setToGeometry(markers, None)
        else:
            self._vertices_preview.reset(QgsWkbTypes.PointGeometry)
        if self._preview is None:
            return
        if self.kind == "polygon":
            if len(points) > 1 and points[-1] == points[0]:
                points = points[:-1]
            if len(points) >= 3:
                self._preview.setToGeometry(
                    QgsGeometry.fromPolygonXY([points + [points[0]]]), None)
                self._segment_preview.reset(QgsWkbTypes.LineGeometry)
            else:
                self._preview.reset(QgsWkbTypes.PolygonGeometry)
                if len(points) >= 2:
                    self._segment_preview.setToGeometry(
                        QgsGeometry.fromPolylineXY(points), None)
                else:
                    self._segment_preview.reset(QgsWkbTypes.LineGeometry)
        elif len(points) >= 2:
            self._preview.setToGeometry(QgsGeometry.fromPolylineXY(points), None)
        else:
            self._preview.reset(QgsWkbTypes.LineGeometry)

    def _complete(self):
        if self._finished:
            return
        try:
            geometry = geometry_from_vertices(self.kind, self.vertices, self.source_crs)
        except ValueError as error:
            self.cancelled.emit(str(error))
        else:
            self.completed.emit(geometry)

    def clear_preview(self):
        self._finished = True
        for band in (self._vertices_preview, self._preview,
                     self._segment_preview, self._snap_marker):
            if band is None:
                continue
            try:
                self.canvas().scene().removeItem(band)
            except RuntimeError:
                pass  # The canvas may already be shutting down.
        self._vertices_preview = None
        self._preview = None
        self._segment_preview = None
        self._snap_marker = None
        self._last_was_snapped = False
        self.vertices.clear()


class GeometryCapture:
    """Own one capture tool and restore the map canvas on every exit path."""

    def __init__(self, canvas, kind, callback):
        if kind not in KINDS:
            raise ValueError("Type de géométrie inconnu.")
        self.canvas = canvas
        self.kind = kind
        self.callback = callback
        self.source_crs = QgsCoordinateReferenceSystem(canvas.mapSettings().destinationCrs())
        if not self.source_crs.isValid():
            raise ValueError("Le SCR du canevas QGIS est absent ou invalide.")
        self.previous_tool = canvas.mapTool()
        # A new capture may deactivate an earlier capture. Restore its original
        # navigation tool, never a capture that has just been retired.
        if isinstance(self.previous_tool, CaptureTool):
            self.previous_tool = self.previous_tool.previous_tool
        self.tool = CaptureTool(canvas, kind, self.source_crs)
        self.tool.previous_tool = self.previous_tool
        self.active = False
        self.finished = False
        self.tool.completed.connect(self._completed)
        self.tool.cancelled.connect(self._cancelled)
        self.tool.trace_failed.connect(self._trace_failed)
        self.tool.deactivated.connect(self._deactivated)
        canvas.destinationCrsChanged.connect(self._crs_changed)

    def start(self):
        if self.finished or self.active:
            return
        try:
            self.canvas.setMapTool(self.tool)
            if self.canvas.mapTool() is not self.tool:
                raise RuntimeError("L'outil de numérisation n'a pas pu être activé.")
            self.active = True
        except Exception:
            self._end(None, "Impossible d'activer l'outil de numérisation.", restore=True)

    def cancel(self, notify=True):
        self._end(None, "Numérisation annulée.", restore=True, notify=notify)

    def _completed(self, geometry):
        if self.finished:
            return
        if self.canvas.mapSettings().destinationCrs() != self.source_crs:
            self._end(None, "Le SCR du canevas a changé pendant le dessin.", restore=True)
        else:
            self._end(geometry, "Géométrie définie en EPSG:3950.", restore=True)

    def _cancelled(self, message):
        self._end(None, message, restore=True)

    def _trace_failed(self, message):
        if self.finished:
            return
        QgsMessageLog.logMessage(message, "GEMAPI-App", Qgis.Warning)
        QToolTip.showText(QCursor.pos(), message, self.canvas)

    def _deactivated(self):
        if not self.finished:
            self._end(None, "Numérisation interrompue par un autre outil.", restore=False)

    def _crs_changed(self):
        self._end(None, "Le SCR du canevas a changé pendant le dessin.", restore=True)

    def _end(self, geometry, message, restore, notify=True):
        if self.finished:
            return
        self.finished = True
        self.active = False
        # Invalidate input before setMapTool(), whose deactivation signals can
        # synchronously invoke application code or deliver another mouse event.
        self.tool._finished = True
        for signal, slot in ((self.canvas.destinationCrsChanged, self._crs_changed),
                             (self.tool.completed, self._completed),
                             (self.tool.cancelled, self._cancelled),
                             (self.tool.trace_failed, self._trace_failed),
                             (self.tool.deactivated, self._deactivated)):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        callback, self.callback = self.callback, None
        try:
            if restore and self.canvas.mapTool() is self.tool:
                try:
                    if self.previous_tool is not None:
                        self.canvas.setMapTool(self.previous_tool)
                    else:
                        self.canvas.unsetMapTool(self.tool)
                except RuntimeError:
                    self.canvas.unsetMapTool(self.tool)
        except RuntimeError:
            pass  # Canvas destruction: there is no live tool left to restore.
        finally:
            QToolTip.hideText()
            # External deactivation owns its replacement; never overwrite it.
            # All late events are inert, including during this synchronous exit.
            self.tool.clear_preview()
            self.tool.previous_tool = None
            self.previous_tool = None
            self.tool.deleteLater()
        if notify and callback is not None:
            callback(geometry, message)
