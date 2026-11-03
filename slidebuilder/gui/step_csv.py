"""Step 2: choose the CSV, map its columns, review the match report."""
from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget, QComboBox)

from ..core.csvmap import CsvMapping, apply_csv, auto_map, load_csv, treatments
from .widgets import DragList


class StepCsv(QWidget):
    validityChanged = Signal()

    def __init__(self, state):
        super().__init__()
        self.state, self.cfg, self._loading = state, QSettings(), False
        v = QVBoxLayout(self)
        v.addWidget(QLabel("<h3>2. Choose the CSV of treatment metadata</h3>"))
        row = QHBoxLayout()
        self.path = QLineEdit(placeholderText="CSV with one row per VSI (source_id, treatment, ...)")
        self.path.setReadOnly(True)
        b = QPushButton("Browse..."); b.clicked.connect(self.browse)
        clr = QPushButton("Clear"); clr.clicked.connect(self.clear)
        row.addWidget(self.path, 1); row.addWidget(b); row.addWidget(clr)
        v.addLayout(row)
        hint = QLabel("The CSV is optional: without it every sample is \"(unassigned)\".")
        hint.setObjectName("hint")
        v.addWidget(hint)

        mid = QHBoxLayout()
        self.map_box = QGroupBox("Column mapping")
        self.form = QFormLayout(self.map_box)
        self.cb = {k: QComboBox() for k in ("source_id", "treatment", "replicate", "pixel_size")}
        for k, label in (("source_id", "source_id *"), ("treatment", "treatment *"), ("replicate", "replicate"),
                         ("pixel_size", "pixel_size_um")):
            self.form.addRow(label, self.cb[k])
        self.label_cb: dict[str, QComboBox] = {}
        self.lab_form = QFormLayout()
        self.lab_form.setContentsMargins(0, 0, 0, 0)
        self.form.addRow(self.lab_form)
        self.extras = DragList(checkable=True)
        self.extras.orderChanged.connect(self._apply)
        self.form.addRow("Extra dimensions", self.extras)
        mid.addWidget(self.map_box, 1)

        right = QVBoxLayout()
        self.report_box = QGroupBox("Match report")
        rl = QVBoxLayout(self.report_box)
        self.summary = QLabel(); self.summary.setWordWrap(True)
        self.report = QPlainTextEdit(readOnly=True)
        self.excl = QCheckBox("Exclude images with no CSV row (otherwise treatment = \"(unassigned)\")")
        self.excl.toggled.connect(self._apply)
        rl.addWidget(self.summary); rl.addWidget(self.report, 1); rl.addWidget(self.excl)
        right.addWidget(self.report_box, 1)
        self.order_box = QGroupBox("Treatment order (drag; control first)")
        ol = QVBoxLayout(self.order_box)
        self.order = DragList()
        self.order.orderChanged.connect(lambda: setattr(self.state, "treatment_order", self.order.keys()))
        ol.addWidget(self.order)
        right.addWidget(self.order_box)
        mid.addLayout(right, 1)
        v.addLayout(mid, 1)

    # --- wizard API
    def can_next(self):
        return bool(self.state.scan)

    def on_enter(self):
        s = self.state
        self._build_label_rows()
        self.path.setText(s.csv_path if s.table else "")
        if s.table:
            self._fill_mapping(s.mapping or auto_map(s.table))
            self._apply()
        else:
            self.map_box.setEnabled(False)
            self.summary.setText("No CSV loaded.")
            self.report.clear()
            self._refresh_order()

    # --- actions
    def browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "CSV file", self.cfg.value("last_csv", ""), "CSV (*.csv);;All files (*)")
        if p:
            self.load(p)

    def load(self, path, mapping=None):
        try:
            table = load_csv(path)
        except Exception as e:
            QMessageBox.warning(self, "CSV error", str(e))
            return
        self.cfg.setValue("last_csv", path)
        self.state.csv_path, self.state.table = path, table
        self.path.setText(path)
        self.map_box.setEnabled(True)
        self._fill_mapping(mapping or auto_map(table))
        self._apply()

    def clear(self):
        self.state.table = self.state.mapping = None
        self.state.csv_path = ""
        for s in (self.state.scan.samples if self.state.scan else []):
            s.meta["treatment"] = "(unassigned)"
        self.on_enter()

    def _build_label_rows(self):
        while self.lab_form.rowCount():
            self.lab_form.removeRow(0)
        self.label_cb = {}
        scan = self.state.scan
        for ch in (scan.channels_present if scan else []):
            if ch != "merge":
                self.label_cb[ch] = cb = QComboBox()
                self.lab_form.addRow(f"label for {ch}", cb)

    def _fill_mapping(self, m):
        self._loading = True
        cols = self.state.table.columns

        def setc(cb, val):
            cb.blockSignals(True)
            cb.clear(); cb.addItem("(none)", None)
            for c in cols:
                cb.addItem(c, c)
            cb.setCurrentIndex(max(cb.findData(val), 0))
            cb.blockSignals(False)
            if not getattr(cb, "_wired", False):
                cb.currentIndexChanged.connect(self._apply); cb._wired = True
        setc(self.cb["source_id"], m.source_id); setc(self.cb["treatment"], m.treatment)
        setc(self.cb["replicate"], m.replicate); setc(self.cb["pixel_size"], m.pixel_size)
        for ch, cb in self.label_cb.items():
            setc(cb, m.labels.get(ch))
        used = {m.source_id, m.treatment, m.replicate, m.pixel_size, *m.labels.values()}
        self.extras.set_items([(c, c, c in m.extras) for c in cols if c not in used or c in m.extras])
        self._loading = False

    def _mapping(self):
        d = lambda k: self.cb[k].currentData()
        return CsvMapping(source_id=d("source_id"), treatment=d("treatment"), replicate=d("replicate"),
                          labels={ch: cb.currentData() for ch, cb in self.label_cb.items() if cb.currentData()},
                          pixel_size=d("pixel_size"), extras=self.extras.keys(only_checked=True))

    def _apply(self, *_):
        s = self.state
        if self._loading or not (s.table and s.scan):
            return
        s.mapping = m = self._mapping()
        rep = apply_csv(s.scan, s.table, m)
        s.exclude_unmatched = self.excl.isChecked()
        missing = set(rep.images_without_row)
        for smp in s.scan.samples:
            if smp.source_id in missing:
                smp.excluded = s.exclude_unmatched
        self.summary.setText(f"<b>{len(rep.matched)}</b> matched &nbsp; "
                             f"<b>{len(rep.images_without_row)}</b> images without CSV row &nbsp; "
                             f"<b>{len(rep.rows_without_images)}</b> CSV rows without images"
                             + (f" &nbsp; <b>{len(rep.duplicates)}</b> duplicates" if rep.duplicates else ""))
        lines = []
        for title, items in (("Images with no CSV row", rep.images_without_row),
                             ("CSV rows with no images", rep.rows_without_images),
                             ("Duplicate source_id rows", rep.duplicates), ("Warnings", rep.warnings)):
            if items:
                lines += [title + ":"] + ["  " + str(i) for i in items] + [""]
        self.report.setPlainText("\n".join(lines) or "Everything matched.")
        self._refresh_order()
        self.validityChanged.emit()

    def _refresh_order(self):
        s = self.state
        cur = treatments(s.scan.samples) if s.scan else []
        order = [t for t in s.treatment_order if t in cur] + [t for t in cur if t not in s.treatment_order]
        s.treatment_order = order
        self.order.set_items([(t, t, True) for t in order])
