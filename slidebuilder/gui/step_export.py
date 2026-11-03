"""Step 4: summary, output path, export with progress and cancel."""
import os

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
                               QProgressBar, QPushButton, QVBoxLayout, QWidget)

from ..core.export import export_pptx
from .step_layout import fmt_est
from .widgets import run_async


class StepExport(QWidget):
    validityChanged = Signal()

    def __init__(self, state):
        super().__init__()
        self.state, self.cfg, self.worker, self.plans = state, QSettings(), None, []
        v = QVBoxLayout(self)
        v.addWidget(QLabel("<h3>4. Export</h3>"))
        self.summary = QLabel(); self.summary.setTextFormat(Qt.RichText); self.summary.setWordWrap(True)
        v.addWidget(self.summary)
        row = QHBoxLayout()
        self.out = QLineEdit(placeholderText="Output .pptx file")
        b = QPushButton("Save As..."); b.clicked.connect(self.browse)
        row.addWidget(self.out, 1); row.addWidget(b)
        v.addLayout(row)
        row = QHBoxLayout()
        self.go = QPushButton("Export"); self.go.setObjectName("primary"); self.go.clicked.connect(self.start)
        self.cancel = QPushButton("Cancel"); self.cancel.setEnabled(False)
        self.cancel.clicked.connect(lambda: self.worker and self.worker.cancel())
        self.bar = QProgressBar()
        row.addWidget(self.go); row.addWidget(self.bar, 1); row.addWidget(self.cancel)
        v.addLayout(row)
        self.status = QLabel(); self.status.setWordWrap(True)
        self.warn = QListWidget(); self.warn.setVisible(False)
        v.addWidget(self.status); v.addWidget(self.warn, 1); v.addStretch()

    def can_next(self):
        return False

    def on_enter(self):
        s = self.state
        self.plans = s.plans()
        o, spec = s.opts, s.spec
        sb = o.scale_bar
        n_img = sum(1 for p in self.plans for c in p.cells if c.sample is not None)
        self.summary.setText(
            f"<b>{len(self.plans)}</b> slides, <b>{n_img}</b> images &nbsp;&middot;&nbsp; estimated {fmt_est(self.plans, o)}<br>"
            f"Channels: {', '.join(spec.channels)} &nbsp;&middot;&nbsp; slide {o.slide_size}, JPEG q{o.jpeg_quality}, "
            f"max {o.max_px}px<br>Scale bar: "
            + (f"{sb.pixel_size_um:g} µm/px" if sb.pixel_size_um else "no global pixel size (per-sample CSV values still apply)"))
        if not self.out.text():
            d = self.cfg.value("last_out_dir", "") or os.path.expanduser("~")
            name = (os.path.basename(s.folder.rstrip("/\\")) or "slides") + ".pptx"
            self.out.setText(os.path.join(d, name))
        self.go.setEnabled(bool(self.plans) and self.worker is None)

    def browse(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save PowerPoint", self.out.text(), "PowerPoint (*.pptx)")
        if p:
            self.out.setText(p if p.lower().endswith(".pptx") else p + ".pptx")

    def start(self, confirm=True):
        path = self.out.text().strip()
        if not path:
            return self.browse()
        if confirm and os.path.exists(path) and QMessageBox.question(self, "Overwrite?", f"{path} exists. Overwrite?") != QMessageBox.Yes:
            return
        self.cfg.setValue("last_out_dir", os.path.dirname(path))
        plans, opts = self.plans, self.state.opts
        self.go.setEnabled(False); self.cancel.setEnabled(True)
        self.warn.clear(); self.warn.setVisible(False)
        self.bar.setRange(0, max(len(plans), 1)); self.bar.setValue(0)
        self.status.setText("Exporting...")
        self.worker = w = run_async(
            lambda w: export_pptx(plans, opts, path, progress=lambda d, t: w.progress.emit(d, t), cancel=w.cancelled),
            on_result=lambda warns: self._done(w, path, warns), on_error=self._failed,
            on_progress=lambda d, t: (self.bar.setRange(0, max(t, 1)), self.bar.setValue(d)))

    def _failed(self, msg):
        self.worker = None
        self.go.setEnabled(True); self.cancel.setEnabled(False)
        self.status.setText("<span style='color:#c0392b'>Export failed: %s</span>" % msg)

    def _done(self, w, path, warns):
        self.worker = None
        self.go.setEnabled(True); self.cancel.setEnabled(False)
        if w.cancelled():
            self.status.setText("Cancelled.")
        else:
            self.bar.setValue(self.bar.maximum())
            self.status.setText(f"Saved <b>{path}</b> ({os.path.getsize(path) / 1e6:.1f} MB)." if os.path.exists(path) else "Done.")
        if warns:
            self.warn.addItems([str(x) for x in warns]); self.warn.setVisible(True)
