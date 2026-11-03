"""Application entry point and shared project state."""
import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

STYLE = """
QWidget { font-size: 9.5pt; }
QPushButton { padding: 5px 14px; }
QPushButton#primary { background:#2b7de9; color:white; border:1px solid #1f63bd; border-radius:3px; padding:6px 20px; font-weight:bold; }
QPushButton#primary:disabled { background:#b9c6d8; border-color:#b9c6d8; }
QGroupBox { font-weight:bold; border:1px solid #d3d6da; border-radius:4px; margin-top:10px; padding-top:8px; }
QGroupBox::title { subcontrol-origin: margin; left:8px; padding:0 4px; }
QLabel#hint { color:#666; }
QLabel#warn { color:#b25b00; }
"""


class State(QObject):
    """Everything the wizard steps share (also what a project file stores)."""
    def __init__(self):
        super().__init__()
        from ..core.geometry import ExportOptions
        from ..core.layout import LayoutSpec
        self.folder = self.csv_path = self.pattern = ""
        self.scan = self.table = self.mapping = None
        self.exclude_unmatched = False
        self.treatment_order: list = []
        self.preset = "sample_panel"
        self.spec = LayoutSpec()
        self.opts = ExportOptions()
        self.restore = None  # layout settings loaded from a project, applied on entering step 3
        self.project_path = ""

    def plans(self):
        from ..core.layout import plan_slides
        return plan_slides(self.scan.samples, self.spec) if self.scan else []


def make_app(argv=None) -> QApplication:
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setStyle("Fusion")
    pal = QPalette()
    for role, c in ((QPalette.Window, "#f4f5f7"), (QPalette.Base, "#ffffff"), (QPalette.Text, "#202124"),
                    (QPalette.WindowText, "#202124"), (QPalette.Button, "#eceef1"), (QPalette.ButtonText, "#202124"),
                    (QPalette.Highlight, "#2b7de9"), (QPalette.HighlightedText, "#ffffff"),
                    (QPalette.AlternateBase, "#f6f7f9"), (QPalette.ToolTipBase, "#ffffe0"), (QPalette.ToolTipText, "#202124")):
        pal.setColor(role, QColor(c))
    app.setPalette(pal)
    app.setStyleSheet(STYLE)
    app.setOrganizationName("SlideBuilder")
    app.setApplicationName("SlideBuilder")
    return app


def main():
    app = make_app()
    from .main_window import MainWindow
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
