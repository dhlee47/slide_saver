"""Small reusable widgets and the background-worker helper."""
import sys
import traceback

from PySide6.QtCore import QRectF, QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QColorDialog, QListWidget, QListWidgetItem,
                               QMenu, QMessageBox, QPushButton, QToolButton, QWidget)

# ---------------------------------------------------------------- workers
_live: set = set()


class Worker(QThread):
    """Runs fn(worker) off the UI thread. fn may call worker.progress.emit(done,total),
    worker.item.emit(obj) and should poll worker.cancelled()."""
    progress = Signal(int, int)
    item = Signal(object)
    result = Signal(object)
    error = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn, self._cancel = fn, False

    def cancel(self):
        self._cancel = True

    def cancelled(self):
        return self._cancel

    def run(self):
        try:
            r = self.fn(self)
        except Exception as e:  # reported to the UI thread
            traceback.print_exc()
            self.error.emit(f"{type(e).__name__}: {e}")
            return
        self.result.emit(r)


def run_async(fn, on_result=None, on_error=None, on_progress=None, on_item=None) -> Worker:
    w = Worker(fn)
    q = Qt.QueuedConnection
    if on_result:
        w.result.connect(on_result, q)
    w.error.connect(on_error or (lambda m: QMessageBox.warning(None, "Error", m)), q)
    if on_progress:
        w.progress.connect(on_progress, q)
    if on_item:
        w.item.connect(on_item, q)
    _live.add(w)
    w.finished.connect(lambda: _live.discard(w), q)
    w.start()
    return w


def shutdown_workers():
    for w in list(_live):
        w.cancel()
    for w in list(_live):
        w.wait(3000)


def pil_to_qimage(img) -> QImage:
    img = img.convert("RGB")
    return QImage(img.tobytes(), img.width, img.height, img.width * 3, QImage.Format_RGB888).copy()


# ---------------------------------------------------------------- lists / combos
class DragList(QListWidget):
    """Drag-reorderable list; items carry a key in UserRole, optional checkboxes."""
    orderChanged = Signal()

    def __init__(self, checkable=False, parent=None):
        super().__init__(parent)
        self.checkable = checkable
        self.setDragDropMode(QListWidget.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.itemChanged.connect(lambda *_: self.orderChanged.emit())

    def dropEvent(self, e):
        super().dropEvent(e)
        self.orderChanged.emit()

    def set_items(self, items):  # items: [(key, text, checked)]
        self.blockSignals(True)
        self.clear()
        for key, text, on in items:
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, key)
            if self.checkable:
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)
            self.addItem(it)
        self.blockSignals(False)
        self.setFixedHeight(min(max(len(items), 1), 7) * 22 + 6)

    def keys(self, only_checked=False):
        its = [self.item(i) for i in range(self.count())]
        return [i.data(Qt.UserRole) for i in its if not (only_checked and i.checkState() != Qt.Checked)]


class MultiCombo(QToolButton):
    """Dropdown with checkable entries (for 'slide per')."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPopupMode(QToolButton.InstantPopup)
        self.setMenu(QMenu(self))
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.setMinimumWidth(130)

    def set_options(self, opts, checked=()):
        m = self.menu()
        m.clear()
        for o in opts:
            a = m.addAction(o)
            a.setCheckable(True)
            a.setChecked(o in checked)
            a.toggled.connect(self._toggled)
        self._retext()

    def values(self):
        return [a.text() for a in self.menu().actions() if a.isChecked()]

    def set_values(self, vals):
        for a in self.menu().actions():
            a.blockSignals(True)
            a.setChecked(a.text() in vals)
            a.blockSignals(False)
        self._retext()

    def _toggled(self):
        self._retext()
        self.changed.emit()

    def _retext(self):
        self.setText(", ".join(self.values()) or "(none)")


class ColorButton(QPushButton):
    colorChanged = Signal(str)

    def __init__(self, color="#000000", parent=None):
        super().__init__(parent)
        self.setFixedWidth(48)
        self.set_color(color)
        self.clicked.connect(self._pick)

    def color(self):
        return self._c

    def set_color(self, c):
        self._c = c
        self.setStyleSheet(f"background:{c}; border:1px solid #888; min-height:18px;")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._c), self)
        if c.isValid():
            self.set_color(c.name())
            self.colorChanged.emit(c.name())


class ImageLabel(QWidget):
    """Shows a pixmap scaled to fit, preserving aspect; placeholder text when empty."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pm, self.text = None, ""
        self.setMinimumSize(320, 180)

    def set_pixmap(self, pm):
        self.pm, self.text = pm, ""
        self.update()

    def set_text(self, t):
        self.pm, self.text = None, t
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#e9ebee"))
        if self.pm:
            s = self.pm.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap((self.width() - s.width()) // 2, (self.height() - s.height()) // 2, s)
        else:
            p.setPen(QColor("#777"))
            p.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap, self.text)


# ---------------------------------------------------------------- preset cards
DIMCOL = {"channel": "#4caf50", "treat": "#f39c12", "rep": "#3b82f6", "section": "#9b59b6"}
SKETCH = {  # preset id -> (rows, cols, row-header dim, col-header dim)
    "sample_panel": (2, 2, None, None), "channel_strip": (1, 4, None, None),
    "chan_by_rep": (4, 3, "channel", "rep"), "treat_by_rep": (3, 3, "treat", "rep"),
    "chan_by_treat": (4, 3, "channel", "treat"), "section_cmp": (2, 3, "section", "treat"),
    "contact_sheet": (3, 4, None, None), "big_image": (1, 1, None, None),
}


class PresetCard(QAbstractButton):
    def __init__(self, pid, name, tip, parent=None):
        super().__init__(parent)
        self.pid, self.name, self.count = pid, name, ""
        self.setCheckable(True)
        self.setToolTip(tip)
        self.setFixedSize(142, 128)
        self.setCursor(Qt.PointingHandCursor)

    def set_count(self, n):
        self.count = "" if n is None else f"{n} slide{'s' if n != 1 else ''}"
        self.update()

    def sizeHint(self):
        return QSize(142, 128)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        p.setPen(QPen(QColor("#2b7de9" if on else "#c5c9ce"), 2 if on else 1))
        p.setBrush(QColor("#eaf2fe" if on else "#ffffff"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)
        self._sketch(p, QRectF(10, 8, self.width() - 20, 68))
        p.setPen(QColor("#222"))
        f = p.font(); f.setPointSize(8); f.setBold(True); p.setFont(f)
        p.drawText(QRectF(6, 78, self.width() - 12, 32), Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, self.name)
        f.setBold(False); p.setFont(f); p.setPen(QColor("#2b7de9"))
        p.drawText(QRectF(6, 110, self.width() - 12, 16), Qt.AlignCenter, self.count)

    def _sketch(self, p, r):
        p.setPen(Qt.NoPen)
        if self.pid not in SKETCH:  # custom
            p.setPen(QPen(QColor("#999"), 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r.adjusted(14, 4, -14, -4))
            for i, y in enumerate((16, 30, 44)):  # three "dropdowns"
                p.drawRect(QRectF(r.x() + 26, r.y() + y - 4, r.width() - 52, 9))
            return
        rows, cols, rd, cd = SKETCH[self.pid]
        hh = 7
        x0 = r.x() + (hh + 2 if rd else 0)
        y0 = r.y() + (hh + 2 if cd else 0)
        w, h = r.right() - x0, r.bottom() - y0
        if rows == 1 and cols == 1:  # keep the single image roughly 16:9
            h = min(h, w * 0.56)
        g = 3
        tw, th = (w - g * (cols - 1)) / cols, (h - g * (rows - 1)) / rows
        for i in range(rows):
            for j in range(cols):
                tr = QRectF(x0 + j * (tw + g), y0 + i * (th + g), tw, th)
                p.setBrush(QColor("#34383d"))
                p.drawRoundedRect(tr, 2, 2)
                p.setBrush(QColor("#7fa7d8"))
                p.drawEllipse(tr.center(), min(tw, th) * 0.16, min(tw, th) * 0.16)
        if rd:
            p.setBrush(QColor(DIMCOL[rd]))
            for i in range(rows):
                p.drawRect(QRectF(r.x(), y0 + i * (th + g), hh, th))
        if cd:
            p.setBrush(QColor(DIMCOL[cd]))
            for j in range(cols):
                p.drawRect(QRectF(x0 + j * (tw + g), r.y(), tw, hh))
