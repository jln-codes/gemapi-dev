"""Two-level PostgreSQL reference picker, retaining the QComboBox value API."""

from qgis.PyQt.QtCore import Qt, QPoint
from qgis.PyQt.QtWidgets import QComboBox, QFrame, QListWidget, QListWidgetItem, QVBoxLayout


class TypePicker(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._categories = ()
        self._types = ()
        self.popup = QFrame(self, Qt.Popup)
        layout = QVBoxLayout(self.popup)
        self.entries = QListWidget(self.popup)
        self.entries.setTextElideMode(Qt.ElideRight)
        self.entries.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.entries.setWordWrap(False)
        layout.addWidget(self.entries)
        self.entries.itemClicked.connect(self._choose)
        self.entries.itemActivated.connect(self._choose)

    def set_reference(self, categories, types):
        """Rows: category (id, label), type (id, label, category_id, active)."""
        self._categories = tuple(categories)
        self._types = tuple(types)
        for uid, label, category, active in self._types:
            if active in (True, 1, "t", "true"):
                self.addItem(str(label), str(uid))

    def showPopup(self):
        if not self.isEnabled():
            return
        self._show_categories()
        self.popup.resize(self.width(), 240)
        self._sync_popup_geometry()
        self.popup.show()
        self.entries.setFocus()

    def _sync_popup_geometry(self):
        self.popup.setFixedWidth(self.width())
        self.popup.move(self.mapToGlobal(QPoint(0, self.height())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.popup.isVisible():
            self._sync_popup_geometry()

    def hidePopup(self):
        self.popup.hide()
        super().hidePopup()

    def _entry(self, label, value=None, enabled=True):
        item = QListWidgetItem(label, self.entries)
        item.setToolTip(label)
        item.setData(Qt.UserRole, value)
        if not enabled:
            item.setFlags(Qt.NoItemFlags)
        return item

    def _show_categories(self):
        self.entries.clear()
        for uid, label in self._categories:
            self._entry(str(label) + "  ›", ("category", str(uid)))
        if not self._categories:
            self._entry("Aucune catégorie disponible", enabled=False)
        self.entries.setCurrentRow(0)

    def _choose(self, item):
        value = item.data(Qt.UserRole)
        if not value:
            return
        kind, uid = value
        if kind == "back":
            self._show_categories()
        elif kind == "category":
            self.entries.clear()
            self._entry("← Retour aux catégories", ("back", ""))
            count = 0
            for type_id, label, category, active in self._types:
                if str(category) == uid and active in (True, 1, "t", "true"):
                    self._entry(str(label), ("type", str(type_id)))
                    count += 1
            if not count:
                self._entry("Aucun type disponible", enabled=False)
            self.entries.setCurrentRow(0)
        else:
            index = self.findData(uid)
            if index >= 0:
                self.setCurrentIndex(index)
                self.activated.emit(index)
            self.hidePopup()

    def wheelEvent(self, event):
        event.ignore()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter,
                           Qt.Key_Down, Qt.Key_Up, Qt.Key_F4):
            self.showPopup()
        elif event.key() == Qt.Key_Escape:
            self.hidePopup()
        else:
            event.ignore()
