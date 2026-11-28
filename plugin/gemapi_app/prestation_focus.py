"""Focus the effective map geometry after a confirmed standard form save."""

import math
from uuid import UUID

from qgis.PyQt.QtCore import Qt
from qgis.core import (Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsGeometry, QgsProject, QgsRectangle, QgsUnitTypes)


def focus_prestation(iface, connection, prestation_id, project=None):
    """Read the canonical map view, transform a copy and keep extent on failure."""
    try:
        uid = str(UUID(str(prestation_id)))
        canvas = iface.mapCanvas()
        iface.mainWindow().raise_()
        iface.mainWindow().activateWindow()
        canvas.setFocus(Qt.OtherFocusReason)
        rows = connection.executeSql(
            "SELECT ST_AsText(geom),ST_SRID(geom) FROM gemapi.prestations_carte "
            f"WHERE id='{uid}'::uuid AND geom IS NOT NULL LIMIT 1")
        if len(rows) != 1 or not rows[0][0]:
            raise ValueError("No effective geometry")
        geometry = QgsGeometry.fromWkt(str(rows[0][0]))
        if geometry.isNull() or geometry.isEmpty():
            raise ValueError("Empty effective geometry")
        source = QgsCoordinateReferenceSystem.fromEpsgId(int(rows[0][1]))
        destination = canvas.mapSettings().destinationCrs()
        if not source.isValid() or not destination.isValid():
            raise ValueError("Invalid CRS")
        if source != destination:
            result = geometry.transform(QgsCoordinateTransform(source, destination, project or QgsProject.instance()))
            if result != Qgis.GeometryOperationResult.Success:
                raise ValueError("Geometry transform failed")
        bounds = geometry.boundingBox()
        if not all(math.isfinite(value) for value in (bounds.xMinimum(), bounds.yMinimum(),
                                                     bounds.xMaximum(), bounds.yMaximum())):
            raise ValueError("Invalid bounds")
        # A finite minimum span also handles points, short lines and zero-height extents.
        minimum = (0.001 if destination.isGeographic() else
                   50.0 * QgsUnitTypes.fromUnitToUnitFactor(QgsUnitTypes.DistanceMeters, destination.mapUnits()))
        center = bounds.center()
        width, height = max(bounds.width(), minimum), max(bounds.height(), minimum)
        extent = QgsRectangle(center.x() - width / 2, center.y() - height / 2,
                              center.x() + width / 2, center.y() + height / 2)
        extent.scale(1.3)
        canvas.setExtent(extent)
        canvas.refresh()
        canvas.setFocus(Qt.OtherFocusReason)
        return True
    except Exception:
        if hasattr(iface, "messageBar"):
            iface.messageBar().pushMessage(
                "GEMAPI-App", "Prestation enregistrée ; géométrie cartographique indisponible. "
                "L'emprise actuelle est conservée.", level=Qgis.Warning, duration=10)
        return False
