"""Step 1: choose the image folder and review the parsed files."""
from collections import Counter

from PySide6.QtCore import QRectF, QSettings, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (QFileDialog, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QProgressBar, QPushButton, QStyledItemDelegate, QTableWidget,
                               QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from ..core.model import CHANNELS
from ..core.parse import DEFAULT_PATTERN, scan_folder
from ..core.thumbs import get_thumbnail
from .widgets import pil_to_qimage, run_async

CHIP_COL = {"350": "#5b6fd6", "488": "#3aa65a", "555": "#e0932b", "647": "#d6504b", "merge": "#7a7f87"}
COLS = ["Use", "Preview", "source_id", "Section", "Channels", "Size (px)", "Warnings"]


class ChipDelegate(QStyledItemDelegate):
    def paint(self, p, opt, idx):
        super().paint(p, opt, idx)  # background/selection (cell text is empty)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        x = opt.rect.x() + 4
        f = p.font(); f.setPointSize(8); p.setFont(f)
        for ch in idx.data(Qt.UserRole) or []:
            w = p.fontMetrics().horizontalAdvance(ch) + 12
            r = QRectF(x, opt.rect.center().y() - 9, w, 18)
            p.setBrush(QColor(CHIP_COL.get(ch, "#888")))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(r, 9, 9)
            p.setPen(Qt.white)
            p.drawText(r, Qt.AlignCenter, ch)
            x += w + 4
        p.restore()


class StepFolder(QWidget):
    validityChanged = Signal()

    def __init__(self, state):
        super().__init__()
        self.state, self.cfg = state, QSettings()
        self.worker = self.thumb_worker = None
        v = QVBoxLayout(self)
        v.addWidget(QLabel("<h3>1. Choose the folder of channel TIFFs</h3>"))
        row = QHBoxLayout()
        self.folder = QLineEdit(placeholderText="Folder containing Image_..._488_1x.tif files")
        self.folder.setReadOnly(True)
        b = QPushButton("Browse...")
        b.clicked.connect(self.browse)
        self.rescan = QPushButton("Rescan")
        self.rescan.clicked.connect(lambda: self.state.folder and self.start_scan(self.state.folder))
        row.addWidget(self.folder, 1); row.addWidget(b); row.addWidget(self.rescan)
        v.addLayout(row)

        self.adv = QGroupBox("Advanced: filename pattern (regex with named groups id, section, channel)")
        self.adv.setCheckable(True); self.adv.setChecked(False)
        al = QHBoxLayout(self.adv)
        self.pattern = QLineEdit(DEFAULT_PATTERN)
        reset = QPushButton("Reset"); reset.clicked.connect(lambda: self.pattern.setText(DEFAULT_PATTERN))
        al.addWidget(self.pattern, 1); al.addWidget(reset)
        self.adv.toggled.connect(lambda on: [al.itemAt(i).widget().setVisible(on) for i in range(al.count())])
        for i in range(al.count()):
            al.itemAt(i).widget().setVisible(False)
        v.addWidget(self.adv)

        pr = QHBoxLayout()
        self.bar = QProgressBar(); self.bar.setVisible(False)
        self.cancel = QPushButton("Cancel"); self.cancel.setVisible(False)
        self.cancel.clicked.connect(self._cancel)
        pr.addWidget(self.bar, 1); pr.addWidget(self.cancel)
        v.addLayout(pr)
        self.summary = QLabel(); self.summary.setObjectName("hint")
        self.banner = QLabel(); self.banner.setObjectName("warn"); self.banner.setWordWrap(True)
        self.banner.setVisible(False)
        v.addWidget(self.summary); v.addWidget(self.banner)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setIconSize(QSize(72, 48))
        self.table.verticalHeader().setDefaultSectionSize(54)
        self.table.setItemDelegateForColumn(4, ChipDelegate(self.table))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(6, QHeaderView.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.Fixed)
        self.table.setColumnWidth(4, 250)
        self.table.itemChanged.connect(self._use_changed)
        v.addWidget(self.table, 1)

        self.unp_btn = QToolButton(text="Unparsed files (0)", checkable=True)
        self.unp_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.unparsed = QListWidget(); self.unparsed.setVisible(False); self.unparsed.setMaximumHeight(110)
        self.unp_btn.toggled.connect(self.unparsed.setVisible)
        self.unp_btn.toggled.connect(lambda _: self._unp_label())
        v.addWidget(self.unp_btn); v.addWidget(self.unparsed)

    # --- wizard API
    def can_next(self):
        return bool(self.state.scan and self.state.scan.samples)

    def on_enter(self):
        if self.state.scan:  # excludes may have changed in step 2
            self.table.blockSignals(True)
            for r, s in enumerate(self.state.scan.samples):
                self.table.item(r, 0).setCheckState(Qt.Unchecked if s.excluded else Qt.Checked)
            self.table.blockSignals(False)

    # --- actions
    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Image folder", self.cfg.value("last_folder", ""))
        if d:
            self.start_scan(d)

    def _cancel(self):
        for w in (self.worker, self.thumb_worker):
            if w:
                w.cancel()
        self.bar.setVisible(False); self.cancel.setVisible(False)

    def start_scan(self, folder, on_done=None):
        self._cancel()
        self.state.folder = folder
        self.state.pattern = self.pattern.text().strip()
        self.cfg.setValue("last_folder", folder)
        self.folder.setText(folder)
        self.bar.setRange(0, 0); self.bar.setVisible(True); self.cancel.setVisible(True)
        pat = self.state.pattern if self.state.pattern != DEFAULT_PATTERN else None
        self.worker = w = run_async(
            lambda w: scan_folder(folder, pat, progress=lambda d, t: w.progress.emit(d, t)),
            on_result=lambda res: self._scanned(w, res, on_done), on_error=lambda m: self._scan_failed(m),
            on_progress=lambda d, t: (self.bar.setRange(0, t), self.bar.setValue(d)))

    def _scan_failed(self, msg):
        self.bar.setVisible(False); self.cancel.setVisible(False)
        self.summary.setText("Scan failed: " + msg)

    def _scanned(self, w, res, on_done):
        if w.cancelled():
            return
        self.bar.setVisible(False); self.cancel.setVisible(False)
        self.state.scan, self.state.table, self.state.mapping = res, None, None
        self.state.treatment_order = []
        self._fill(res)
        self.validityChanged.emit()
        if on_done:
            on_done()

    def _fill(self, res):
        t = self.table
        t.blockSignals(True)
        t.setRowCount(len(res.samples))
        for r, s in enumerate(res.samples):
            sizes = Counter((f.width, f.height) for f in s.files.values())
            dims = "mixed" if len(sizes) > 1 else "%d×%d" % next(iter(sizes)) if sizes else ""
            missing = [c for c in res.channels_present if c not in s.files]
            vals = ["", "", s.source_id + ("  (unnumbered)" if s.unnumbered else ""),
                    s.section or "(no section)", "", dims, ("missing " + ", ".join(missing)) if missing else ""]
            for c, txt in enumerate(vals):
                it = QTableWidgetItem(txt)
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                if c == 0:
                    it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                    it.setCheckState(Qt.Unchecked if s.excluded else Qt.Checked)
                if c == 4:
                    it.setData(Qt.UserRole, [ch for ch in CHANNELS if ch in s.files])
                if c == 6 and txt:
                    it.setForeground(QColor("#b25b00"))
                t.setItem(r, c, it)
        t.blockSignals(False)
        n_ex = sum(s.excluded for s in res.samples)
        self.summary.setText(f"{len(res.files)} image files, {len(res.samples)} samples; channels: "
                             f"{', '.join(res.channels_present) or 'none'}; {len(res.unparsed)} unparsed")
        sizes = res.distinct_sizes
        self.banner.setVisible(len(sizes) > 1)
        warn = "; ".join(res.warnings[:3])
        self.banner.setText(f"{len(sizes)} distinct image sizes ({', '.join('%dx%d' % s for s in sizes[:4])}"
                            f"{'...' if len(sizes) > 4 else ''}). Images are scaled to fit each cell."
                            + (f"  {warn}" if warn else ""))
        self.unparsed.clear(); self.unparsed.addItems(res.unparsed)
        self._unp_label()
        # thumbnails in a worker thread
        if self.thumb_worker:
            self.thumb_worker.cancel()
        picks = [next((s.files[c].path for c in ("merge", "488", "555", "647", "350") if c in s.files), None)
                 for s in res.samples]

        def work(w):
            for i, p in enumerate(picks):
                if w.cancelled():
                    return
                if p:
                    try:
                        w.item.emit((i, pil_to_qimage(get_thumbnail(p, 96))))
                    except Exception:
                        pass
        self.thumb_worker = run_async(work, on_item=self._thumb)

    def _unp_label(self):
        n = self.unparsed.count()
        self.unp_btn.setText(f"{'Hide' if self.unp_btn.isChecked() else 'Show'} unparsed files ({n})")

    def _thumb(self, it):
        i, img = it
        if i < self.table.rowCount():
            self.table.item(i, 1).setIcon(QPixmap.fromImage(img))

    def _use_changed(self, item):
        if item.column() == 0 and self.state.scan:
            self.state.scan.samples[item.row()].excluded = item.checkState() != Qt.Checked
            self.validityChanged.emit()
