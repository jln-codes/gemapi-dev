"""Initial window geometry shared by the plugin dialogs."""

from qgis.PyQt.QtCore import QEvent, QObject, QPoint, QTimer, Qt
from qgis.PyQt.QtGui import QGuiApplication
from qgis.PyQt.QtWidgets import QLayout, QPushButton


INITIAL_SIZES = {
    "assistant": (760, 800),
    "manage": (1000, 800),
    "prestation": (1000, 800),
    "report": (1000, 800),
}


def initialize_window(dialog, kind, parent=None):
    """Fit the opening size to the screen and centre it over QGIS."""
    screen = parent.screen() if parent is not None else QGuiApplication.primaryScreen()
    available = screen.availableGeometry() if screen is not None else None
    width, height = INITIAL_SIZES[kind]
    if available is not None:
        width = min(width, available.width())
        height = min(height, available.height())
    dialog.resize(width, height)
    centre = parent.frameGeometry().center() if parent is not None else available.center()
    frame = dialog.frameGeometry()
    frame.moveCenter(centre)
    if available is not None:
        frame.moveLeft(max(available.left(), min(frame.left(), available.right() - frame.width() + 1)))
        frame.moveTop(max(available.top(), min(frame.top(), available.bottom() - frame.height() + 1)))
    dialog.move(frame.topLeft())


class CompactHomePositioner(QObject):
    """A canvas child overlay, with no native window or draggable frame."""

    def __init__(self, home, canvas, window):
        super().__init__(home)
        self.home = home
        self.canvas = canvas
        home.setParent(canvas, Qt.Widget)
        home.setAttribute(Qt.WA_ShowWithoutActivating, True)
        home.setStyleSheet("#HomeDialog { background: palette(window); border: 1px solid palette(mid); } "
                           "QPushButton { padding: 0px 8px; }")
        for button in home.findChildren(QPushButton):
            button.setFixedHeight(31)
        home.layout().setSizeConstraint(QLayout.SetFixedSize)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.reposition)
        for widget in (home, canvas):
            widget.installEventFilter(self)
        home.adjustSize()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Show, QEvent.FontChange, QEvent.ApplicationFontChange):
            self.timer.start(0)
        return False

    def reposition(self):
        self.home.adjustSize()
        x = max(0, self.canvas.width() - self.home.width() - 10)
        y = min(10, max(0, self.canvas.height() - self.home.height()))
        self.home.move(x, y)
        self.home.raise_()
