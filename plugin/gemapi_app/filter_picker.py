"""Reusable searchable, multi-selection picker for prestations and reports."""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QGroupBox, QLineEdit, QListWidget, QListWidgetItem,
    QVBoxLayout,
)


class FilterPicker(QGroupBox):
    """A checked item restricts a criterion; an empty selection means all."""

    def __init__(self, title, parent=None, searchable=True, compact_title=False):
        super().__init__(title, parent)
        self.title = title
        self.compact_title = compact_title
        layout = QVBoxLayout(self)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Rechercher…")
        self.search.setVisible(searchable)
        self.list = QListWidget(self)
        self.list.setSelectionMode(QAbstractItemView.NoSelection)
        self.list.setMinimumHeight(105)
        layout.addWidget(self.search)
        layout.addWidget(self.list)
        self.search.textChanged.connect(self._filter)
        self.list.itemChanged.connect(self._update_title)

    def fill(self, options, checked=()):
        checked = set(checked)
        self.list.blockSignals(True)
        self.list.clear()
        for value, label in options:
            item = QListWidgetItem(str(label), self.list)
            item.setData(Qt.UserRole, value)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if value in checked else Qt.Unchecked)
        self.list.blockSignals(False)
        self._filter(self.search.text())
        self._update_title()

    def clear_selection(self):
        self.list.blockSignals(True)
        for index in range(self.list.count()):
            self.list.item(index).setCheckState(Qt.Unchecked)
        self.list.blockSignals(False)
        self.search.clear()
        self._update_title()

    def values(self):
        return tuple(self.list.item(i).data(Qt.UserRole)
                     for i in range(self.list.count())
                     if self.list.item(i).checkState() == Qt.Checked)

    def labels(self):
        return tuple(self.list.item(i).text()
                     for i in range(self.list.count())
                     if self.list.item(i).checkState() == Qt.Checked)

    def _filter(self, value):
        query = value.strip().casefold()
        for index in range(self.list.count()):
            item = self.list.item(index)
            item.setHidden(query not in item.text().casefold() and
                           item.checkState() != Qt.Checked)

    def _update_title(self, *_):
        if self.compact_title:
            self.setTitle(self.title)
            return
        count = len(self.values())
        self.setTitle(f"{self.title} ({count} sélectionné{'s' if count > 1 else ''})" if count
                      else f"{self.title} (tous)")
