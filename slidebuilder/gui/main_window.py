"""Main window: 4-step wizard, project save/open."""
import json
from dataclasses import asdict

from PySide6.QtCore import QSettings
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from ..core.csvmap import CsvMapping
from .app import State
from .step_csv import StepCsv
from .step_export import StepExport
from .step_folder import StepFolder
from .step_layout import StepLayout
from .widgets import shutdown_workers

NAMES = ["1  Folder", "2  CSV", "3  Layout", "4  Export"]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.state, self.cfg = State(), QSettings()
        self.setWindowTitle("Microscopy Slide Builder")
        self.resize(1280, 720)
        self.steps = [StepFolder(self.state), StepCsv(self.state), StepLayout(self.state), StepExport(self.state)]
        root = QWidget(); self.setCentralWidget(root)
        v = QVBoxLayout(root)
        bar = QHBoxLayout()
        self.crumbs = [QLabel(n) for n in NAMES]
        for c in self.crumbs:
            bar.addWidget(c)
            bar.addSpacing(18)
        bar.addStretch()
        v.addLayout(bar)
        self.stack = QStackedWidget()
        for s in self.steps:
            self.stack.addWidget(s)
            s.validityChanged.connect(self._update_nav)
        v.addWidget(self.stack, 1)
        nav = QHBoxLayout()
        self.back, self.next = QPushButton("◀  Back"), QPushButton("Next  ▶")
        self.next.setObjectName("primary")
        self.back.clicked.connect(lambda: self.go(self.stack.currentIndex() - 1))
        self.next.clicked.connect(lambda: self.go(self.stack.currentIndex() + 1))
        nav.addWidget(self.back); nav.addStretch(); nav.addWidget(self.next)
        v.addLayout(nav)

        m = self.menuBar().addMenu("&File")
        for text, fn, key in (("&Open project...", self.open_project, QKeySequence.Open),
                              ("&Save project", self.save_project, QKeySequence.Save),
                              ("Save project &as...", lambda: self.save_project(True), QKeySequence.SaveAs),
                              ("E&xit", self.close, None)):
            a = QAction(text, self)
            a.triggered.connect(lambda _=False, fn=fn: fn())
            if key:
                a.setShortcut(key)
            m.addAction(a)
        self.go(0)

    # --- navigation
    def go(self, i):
        i = max(0, min(i, len(self.steps) - 1))
        self.stack.setCurrentIndex(i)
        self.steps[i].on_enter()
        for j, c in enumerate(self.crumbs):
            c.setStyleSheet("font-weight:bold; color:#2b7de9; font-size:11pt;" if j == i else
                            "color:#222; font-size:11pt;" if j < i else "color:#999; font-size:11pt;")
        self._update_nav()

    def _update_nav(self):
        i = self.stack.currentIndex()
        self.back.setEnabled(i > 0)
        self.next.setVisible(i < len(self.steps) - 1)
        self.next.setEnabled(self.steps[i].can_next())

    # --- projects
    def save_project(self, as_new=False, path=None):
        s = self.state
        path = path or s.project_path
        if as_new or not path:
            path, _ = QFileDialog.getSaveFileName(self, "Save project", self.cfg.value("last_project", ""), "Slide project (*.json)")
            if not path:
                return
        d = dict(folder=s.folder, csv_path=s.csv_path, pattern=s.pattern,
                 mapping=asdict(s.mapping) if s.mapping else None, exclude_unmatched=s.exclude_unmatched,
                 excluded=[x.key for x in (s.scan.samples if s.scan else []) if x.excluded],
                 preset=s.preset, spec=s.spec.to_dict(), options=s.opts.to_dict())
        with open(path, "w", encoding="utf8") as f:
            json.dump(d, f, indent=2)
        s.project_path = path
        self.cfg.setValue("last_project", path)
        self.setWindowTitle(f"Microscopy Slide Builder - {path}")

    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open project", self.cfg.value("last_project", ""), "Slide project (*.json)")
        if path:
            self.load_project(path)

    def load_project(self, path):
        try:
            with open(path, encoding="utf8") as f:
                d = json.load(f)
        except Exception as e:
            return QMessageBox.warning(self, "Open project", str(e))
        s = self.state
        s.project_path = path
        self.cfg.setValue("last_project", path)
        self.setWindowTitle(f"Microscopy Slide Builder - {path}")
        self.go(0)
        f = self.steps[0]
        if d.get("pattern"):
            f.pattern.setText(d["pattern"])
        f.start_scan(d["folder"], on_done=lambda: self._project_scanned(d))

    def _project_scanned(self, d):
        s = self.state
        for x in s.scan.samples:
            x.excluded = x.key in d.get("excluded", [])
        s.treatment_order = d.get("spec", {}).get("treatment_order", [])
        if d.get("csv_path"):
            try:
                m = d.get("mapping")
                self.steps[1].excl.setChecked(bool(d.get("exclude_unmatched")))
                self.steps[1].load(d["csv_path"], CsvMapping(**m) if m else None)
            except Exception as e:
                QMessageBox.warning(self, "Open project", f"Could not restore CSV: {e}")
        s.restore = dict(spec=d["spec"], opts=d["options"], preset=d.get("preset", "custom"))
        self.go(2)

    def closeEvent(self, e):
        shutdown_workers()
        super().closeEvent(e)
