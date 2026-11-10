"""Step 1: choose the image folder and review the parsed files."""
import os
import re
from collections import Counter

from PySide6.QtCore import QRectF, QSettings, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap, QTextCursor
from PySide6.QtWidgets import (QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QPlainTextEdit, QProgressBar, QPushButton, QStyledItemDelegate,
                               QTableWidget, QTableWidgetItem, QTextEdit, QToolButton, QVBoxLayout, QWidget)

from ..core.naming import build_pattern
from ..core.naming import TAGS
from ..core.parse import IMAGE_EXTS, ScanResult, scan_folder
from ..core.thumbs import get_thumbnail
from .widgets import pil_to_qimage, run_async

CHIP_COL = {"350": "#5b6fd6", "488": "#3aa65a", "555": "#e0932b", "647": "#d6504b", "merge": "#7a7f87"}
COLS = ["Use", "Preview", "source_id", "Level", "Channels", "Size (px)", "Warnings"]
TAG_COL = {"id": "#bfdcff", "section": "#ffd9a3", "channel": "#c4efb8", "ignore": "#dcdcdc"}
_EXTRA_CHIPS = ["#8e5bd6", "#2a9d9d", "#c2569b", "#9a8a2a", "#4f7cac"]


def _base(key):  # "ignore2" -> "ignore"
    return key.rstrip("0123456789")


def chip_color(ch):
    return CHIP_COL.get(ch) or _EXTRA_CHIPS[sum(map(ord, ch)) % len(_EXTRA_CHIPS)]


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
            p.setBrush(QColor(chip_color(ch)))
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
        self.spans, self._example_name = {}, ""
        v = QVBoxLayout(self)
        v.addWidget(QLabel("<h3>1. Choose the image folder and show how the files are named</h3>"))
        row = QHBoxLayout()
        self.folder = QLineEdit(placeholderText="Folder containing the channel images (TIFF, PNG or JPEG)")
        self.folder.setReadOnly(True)
        b = QPushButton("Browse...")
        b.clicked.connect(self.browse)
        self.rescan = QPushButton("Rescan")
        self.rescan.clicked.connect(lambda: self.state.folder and self.start_scan(self.state.folder, keep_examples=True))
        row.addWidget(self.folder, 1); row.addWidget(b); row.addWidget(self.rescan)
        v.addLayout(row)

        # ---- naming: highlight parts of an example file name and tag them
        self.name_box = QGroupBox("File naming")
        nl = QVBoxLayout(self.name_box)
        self.naming_msg = QLabel(); self.naming_msg.setWordWrap(True)
        self.naming_msg.setStyleSheet("font-size:13px;")
        nl.addWidget(self.naming_msg)
        er = QHBoxLayout()
        self.example = QComboBox(); self.example.setMinimumWidth(320)
        self.example.currentIndexChanged.connect(self._example_changed)
        er.addWidget(QLabel("Example file:")); er.addWidget(self.example, 1)
        nl.addLayout(er)
        self.sample_edit = QPlainTextEdit(readOnly=True)
        self.sample_edit.setFixedHeight(44)
        f = self.sample_edit.font(); f.setFamily("Consolas"); f.setPointSize(14); self.sample_edit.setFont(f)
        self.sample_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        nl.addWidget(self.sample_edit)
        tr = QHBoxLayout()
        self.tag_btns = {}
        for tag, text, tip in (("channel", "Channel", "Which channel/stain the image is (e.g. DAPI, 555). Required."),
                               ("id", "ID", "The sample / slide number: the part that differs between samples."),
                               ("section", "Level", "A second level such as magnification (1x, 4x), section or region (ROI1)."),
                               ("ignore", "Ignore", "A part that varies but you don't need (e.g. a group prefix).")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setStyleSheet(f"background:{TAG_COL[tag]}; color:#222; padding:6px 16px; font-weight:600;")
            b.clicked.connect(lambda _=False, t=tag: self._tag(t))
            tr.addWidget(b); self.tag_btns[tag] = b
        clear = QPushButton("Clear tags"); clear.clicked.connect(self._clear_tags)
        tr.addSpacing(12); tr.addWidget(clear); tr.addStretch()
        nl.addLayout(tr)
        self.naming_stats = QLabel(); self.naming_stats.setWordWrap(True)
        self.naming_stats.setStyleSheet("font-weight:600;")
        nl.addWidget(self.naming_stats)
        self.show_re = QToolButton(text="Show pattern (advanced)", checkable=True)
        self.show_re.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.pattern = QLineEdit(placeholderText="regex with named groups id, section, channel")
        self.pattern.setVisible(False)
        self.pattern.editingFinished.connect(self._pattern_edited)
        self.show_re.toggled.connect(self.pattern.setVisible)
        nl.addWidget(self.show_re); nl.addWidget(self.pattern)
        v.addWidget(self.name_box)

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
        self.table.setColumnWidth(4, 380)
        self.table.itemChanged.connect(self._use_changed)
        v.addWidget(self.table, 1)

        self.unp_btn = QToolButton(text="Unparsed files (0)", checkable=True)
        self.unp_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.unparsed = QListWidget(); self.unparsed.setVisible(False); self.unparsed.setMaximumHeight(110)
        self.unp_btn.toggled.connect(self.unparsed.setVisible)
        self.unp_btn.toggled.connect(lambda _: self._unp_label())
        v.addWidget(self.unp_btn); v.addWidget(self.unparsed)
        self._show_example()

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
            self.pattern.clear()  # a new folder starts with no naming scheme (the last working one is tried below)
            self.start_scan(d)

    def _cancel(self):
        for w in (self.worker, self.thumb_worker):
            if w:
                w.cancel()
        self.bar.setVisible(False); self.cancel.setVisible(False)

    def _image_names(self, folder):
        try:
            return sorted(n for n in os.listdir(folder) if n.lower().endswith(IMAGE_EXTS))
        except OSError:
            return []

    def _saved_pattern(self, names):
        """The scheme that worked last time, if it fits at least half of this folder's images."""
        pat = self.cfg.value("naming_pattern", "") or ""
        try:
            rx = re.compile(pat, re.IGNORECASE)
        except re.error:
            return ""
        return pat if pat and names and sum(bool(rx.match(n)) for n in names) >= 0.5 * len(names) else ""

    def start_scan(self, folder, on_done=None, keep_examples=False):
        self._cancel()
        self.state.folder = folder
        self.cfg.setValue("last_folder", folder)
        self.folder.setText(folder)
        names = self._image_names(folder)
        if not keep_examples:
            self._fill_examples(names)
            if not self.pattern.text().strip():
                self.pattern.setText(self._saved_pattern(names))
        pat = self.pattern.text().strip()
        self.state.pattern = pat
        if not pat:  # nothing to scan with yet: wait for the user to tag the example
            self.state.scan, self.state.table, self.state.mapping = None, None, None
            self._fill(ScanResult(folder))
            self.naming_stats.setText("")
            self._show_example()
            self.validityChanged.emit()
            return
        self.bar.setRange(0, 0); self.bar.setVisible(True); self.cancel.setVisible(True)
        self.worker = w = run_async(
            lambda w: scan_folder(folder, pat, progress=lambda d, t: w.progress.emit(d, t)),
            on_result=lambda res: self._scanned(w, res, on_done), on_error=lambda m: self._scan_failed(m),
            on_progress=lambda d, t: (self.bar.setRange(0, t), self.bar.setValue(d)))

    # --- naming helpers
    def _fill_examples(self, names):
        """Example box: one name per digit-masked shape first, then more of them."""
        seen, firsts, rest = set(), [], []
        for n in names:
            shape = re.sub(r"\d+", "#", n)
            (rest if shape in seen else firsts).append(n)
            seen.add(shape)
        self.example.blockSignals(True)
        self.example.clear(); self.example.addItems(firsts + rest[:60])
        self.example.blockSignals(False)
        self._example_name = self.example.currentText()
        self.spans = {}
        self._show_example()

    def _example_changed(self, *_):
        self._example_name = self.example.currentText()
        self.spans = {}
        self._highlight_from_pattern()
        self._show_example()

    def _highlight_from_pattern(self):
        """Show the parts an existing pattern finds in the example (e.g. a scheme saved from last time)."""
        pat = self.pattern.text().strip()
        if not pat or not self._example_name:
            return
        try:
            m = re.match(pat, self._example_name, re.IGNORECASE)
        except re.error:
            return
        if m:
            self.spans = {k: m.span(k) for k in m.groupdict() if _base(k) in TAG_COL and m.span(k)[0] >= 0}

    def _prompt(self):
        if not self.state.folder:
            return "Choose the folder with your images (Browse...)."
        if self.example.count() == 0:
            return "No image files (.tif, .png, .jpg) were found in this folder."
        if "channel" not in self.spans:
            return ("<b>Step 1:</b> drag across the part of the example name that says the <b>channel</b> "
                    "(e.g. DAPI or 555), then click <b>Channel</b>.")
        if self.state.scan and self.state.scan.samples and "id" not in self.spans and len(self.state.scan.samples) == 1:
            return ("<b>Next:</b> drag across the <b>sample ID</b> (the part that differs between samples, "
                    "like S1 or 01) and click <b>ID</b>.")
        return ("Looks good? Optionally mark a <b>Level</b> (magnification, section or region such as 1x or ROI1) or "
                "<b>Ignore</b> parts you don't need. Colours show what each part was read as.")

    def _show_example(self):
        self.sample_edit.setPlainText(self._example_name)
        sels = []
        for tag, (s, e) in self.spans.items():
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(QColor(TAG_COL[_base(tag)]))
            cur = self.sample_edit.textCursor()
            cur.setPosition(s); cur.setPosition(e, QTextCursor.KeepAnchor)
            sel.cursor = cur
            sels.append(sel)
        self.sample_edit.setExtraSelections(sels)
        c = self.sample_edit.textCursor(); c.clearSelection(); self.sample_edit.setTextCursor(c)
        have = {_base(k) for k in self.spans}
        for tag, b in self.tag_btns.items():
            b.setText(("✓ " if tag in have else "") + {"channel": "Channel", "id": "ID", "section": "Level",
                                                       "ignore": "Ignore"}[tag])
        self.name_box.setEnabled(bool(self.state.folder))
        self.naming_msg.setText(self._prompt())

    def _stats(self, res, bad):
        n = len(res.files) + len(bad)
        msg = f"Matched {len(res.files)} of {n} image files into {len(res.samples)} sample(s)."
        if bad:
            msg += f"  Not matched, e.g. {bad[0]}  (pick it in the Example list to see why)."
        if len(res.samples) == 1 and len(res.files) > 8:
            msg += "  Only one sample found: tag the ID."
        self.naming_stats.setText(msg)

    def _tag(self, tag):
        cur = self.sample_edit.textCursor()
        s, e = cur.selectionStart(), cur.selectionEnd()
        if e <= s:
            self.naming_msg.setText("Drag across part of the file name first, then click what it is.")
            return
        key = tag
        if tag == "ignore":  # several parts can be ignored
            n = sum(k.startswith("ignore") for k in self.spans)
            key = "ignore" if n == 0 else f"ignore{n + 1}"
        for k in [k for k, (a, b) in self.spans.items() if a < e and s < b]:  # a re-tag replaces what it overlaps
            del self.spans[k]
        self.spans[key] = (s, e)
        self._show_example()
        if "channel" not in self.spans:
            return
        try:
            pat = build_pattern(self._example_name, self.spans)
        except ValueError as ex:
            self.naming_msg.setText(str(ex))
            return
        self.pattern.setText(pat)
        if self.state.folder:
            self.start_scan(self.state.folder, keep_examples=True)

    def _clear_tags(self):
        self.spans = {}
        self.pattern.clear()
        if self.state.folder:
            self.start_scan(self.state.folder, keep_examples=True)
        self._show_example()

    def _pattern_edited(self):
        if self.state.folder and self.pattern.text().strip() != self.state.pattern:
            self.spans = {}
            self._highlight_from_pattern()
            self.start_scan(self.state.folder, keep_examples=True)

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
        bad = [n for n in res.unparsed if n.lower().endswith(IMAGE_EXTS)]
        self._stats(res, bad)
        if res.samples and len(bad) <= 0.5 * (len(res.files) + len(bad)):
            self.cfg.setValue("naming_pattern", self.pattern.text().strip())  # reuse next time
        if not self.spans:
            self._highlight_from_pattern()
        self._show_example()
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
                    s.section or "-", "", dims, ("missing " + ", ".join(missing)) if missing else ""]
            for c, txt in enumerate(vals):
                it = QTableWidgetItem(txt)
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                if c == 0:
                    it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                    it.setCheckState(Qt.Unchecked if s.excluded else Qt.Checked)
                if c == 4:
                    it.setData(Qt.UserRole, [ch for ch in res.channels_present if ch in s.files])
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
        picks = [next((s.files[c].path for c in ("merge", *res.channels_present) if c in s.files), None)
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
