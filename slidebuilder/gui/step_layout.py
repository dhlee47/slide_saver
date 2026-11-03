"""Step 3: pick a layout, channels, scale bar and options, with a live slide preview."""
from dataclasses import replace

from PySide6.QtCore import QSettings, QTimer, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton, QScrollArea,
                               QSpinBox, QSplitter, QVBoxLayout, QWidget)

from ..core.csvmap import treatments
from ..core.export import estimate
from ..core.geometry import ScaleBarOptions
from ..core.layout import LayoutSpec, PRESETS, available_dimensions, plan_slides, preset_spec
from ..core.model import CHANNELS, DEFAULT_LABELS
from ..core.thumbs import render_preview
from .widgets import ColorButton, DragList, ImageLabel, MultiCombo, PresetCard, pil_to_qimage, run_async

CUSTOM = "custom"


def fmt_est(plans, opts):
    try:
        b, s = estimate(plans, opts)
        return f"~{b / 1e6:.1f} MB, ~{s:.0f} s" if s >= 1 else f"~{b / 1e6:.1f} MB, <1 s"
    except Exception:
        return ""


class StepLayout(QWidget):
    validityChanged = Signal()

    def __init__(self, state):
        super().__init__()
        self.state, self._loading, self._gen, self._scan_id = state, False, 0, None
        self.plans, self.k, self.chcolors = [], 0, {}
        self.timer = QTimer(singleShot=True, interval=250)
        self.timer.timeout.connect(self._refresh)
        self.render_worker = None

        split = QSplitter(Qt.Horizontal)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(split)

        # ------------- left: controls (scrollable)
        scroll = QScrollArea(widgetResizable=True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left = QWidget()
        scroll.setWidget(left)
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("<h3>3. Layout</h3>"))
        grid = QGridLayout()
        grid.setSpacing(6)
        self.cards = {}
        for i, p in enumerate(PRESETS + [None]):
            c = PresetCard(p.id if p else CUSTOM, p.name if p else "Custom", p.description if p else "Choose slide split, rows and columns yourself.")
            c.clicked.connect(lambda _=False, pid=c.pid: self.pick_preset(pid))
            self.cards[c.pid] = c
            grid.addWidget(c, i // 3, i % 3)
        lv.addLayout(grid)

        # custom / fine controls
        box = QGroupBox("Arrangement")
        f = QFormLayout(box)
        self.mode_grid, self.mode_flow = QRadioButton("Grid (rows × columns)"), QRadioButton("Flow (fill in order)")
        f.addRow(self.mode_grid); f.addRow(self.mode_flow)
        self.slide_per = MultiCombo()
        self.cb_rows, self.cb_cols = QComboBox(), QComboBox()
        self.cb_order = QComboBox()
        self.cb_order.addItem("channel, then sample", "channel_major")
        self.cb_order.addItem("sample, then channel", "sample_major")
        self.fr, self.fc = QSpinBox(), QSpinBox()
        for s in (self.fr, self.fc):
            s.setRange(0, 20); s.setSpecialValueText("auto")
        fl = QHBoxLayout(); fl.addWidget(self.fr); fl.addWidget(QLabel("×")); fl.addWidget(self.fc)
        f.addRow("Slide per", self.slide_per)
        f.addRow("Rows", self.cb_rows); f.addRow("Columns", self.cb_cols)
        f.addRow("Flow rows × cols", fl); f.addRow("Flow order", self.cb_order)
        lv.addWidget(box)
        for w in (self.mode_grid, self.mode_flow):
            w.toggled.connect(self._arr_changed)
        self.slide_per.changed.connect(self._arr_changed)
        for w in (self.cb_rows, self.cb_cols, self.cb_order):
            w.currentIndexChanged.connect(self._arr_changed)
        for w in (self.fr, self.fc):
            w.valueChanged.connect(self._arr_changed)

        # channels + treatments
        box = QGroupBox("Channels (check to include, drag to reorder)")
        bl = QVBoxLayout(box)
        self.chan_list = DragList(checkable=True)
        self.chan_list.orderChanged.connect(self._chan_changed)
        bl.addWidget(self.chan_list)
        cr = QHBoxLayout()
        self.col_ch = QComboBox()
        self.col_btn = ColorButton("#ffffff")
        self.col_ch.currentIndexChanged.connect(lambda *_: self.col_btn.set_color(self.chcolors.get(self.col_ch.currentData(), "#ffffff")))
        self.col_btn.colorChanged.connect(self._tag_color)
        cr.addWidget(QLabel("Tag colour:")); cr.addWidget(self.col_ch); cr.addWidget(self.col_btn); cr.addStretch()
        bl.addLayout(cr)
        lv.addWidget(box)
        box = QGroupBox("Treatment order (drag)")
        bl = QVBoxLayout(box)
        self.treat_list = DragList()
        self.treat_list.orderChanged.connect(self._treat_changed)
        bl.addWidget(self.treat_list)
        lv.addWidget(box)

        # options
        box = QGroupBox("Scale bar")
        f = QFormLayout(box)
        self.pixel = QLineEdit(placeholderText="e.g. 0.65 (empty = none)")
        f.addRow("Pixel size (µm/px)", self.pixel)
        self.bar_auto = QCheckBox("Automatic length"); self.bar_auto.setChecked(True)
        self.bar_len = QDoubleSpinBox(); self.bar_len.setRange(0.1, 100000); self.bar_len.setValue(100); self.bar_len.setSuffix(" µm")
        self.bar_len.setEnabled(False)
        self.bar_auto.toggled.connect(lambda on: self.bar_len.setEnabled(not on))
        h = QHBoxLayout(); h.addWidget(self.bar_auto); h.addWidget(self.bar_len)
        f.addRow("Length", h)
        self.bar_pos = QComboBox(); self.bar_pos.addItem("Bottom right", "bottom_right"); self.bar_pos.addItem("Bottom left", "bottom_left")
        self.bar_col = QComboBox(); self.bar_col.addItem("White", "#FFFFFF"); self.bar_col.addItem("Black", "#000000")
        self.bar_lbl = QCheckBox("Show length label"); self.bar_lbl.setChecked(True)
        f.addRow("Position", self.bar_pos); f.addRow("Colour", self.bar_col); f.addRow(self.bar_lbl)
        self.size_warn = QLabel(); self.size_warn.setObjectName("warn"); self.size_warn.setWordWrap(True)
        f.addRow(self.size_warn)
        lv.addWidget(box)

        box = QGroupBox("Slide and export options")
        f = QFormLayout(box)
        self.slide_size = QComboBox(); self.slide_size.addItems(["16:9", "4:3"])
        self.bg = ColorButton("#000000")
        self.match = QCheckBox("Match image size within slide")
        self.jpeg = QSpinBox(); self.jpeg.setRange(10, 100); self.jpeg.setValue(90)
        self.maxpx = QSpinBox(); self.maxpx.setRange(200, 8000); self.maxpx.setSingleStep(100); self.maxpx.setValue(1600); self.maxpx.setSuffix(" px")
        self.notes = QCheckBox("Source filenames in speaker notes")
        for lbl, w in (("Slide size", self.slide_size), ("Background", self.bg), ("JPEG quality", self.jpeg),
                       ("Max long edge", self.maxpx)):
            f.addRow(lbl, w)
        f.addRow(self.match); f.addRow(self.notes)
        lv.addWidget(box)
        lv.addStretch()
        for w, sig in ((self.pixel, "textChanged"), (self.bar_auto, "toggled"), (self.bar_len, "valueChanged"),
                       (self.bar_pos, "currentIndexChanged"), (self.bar_col, "currentIndexChanged"),
                       (self.bar_lbl, "toggled"), (self.slide_size, "currentIndexChanged"), (self.bg, "colorChanged"),
                       (self.match, "toggled"), (self.jpeg, "valueChanged"), (self.maxpx, "valueChanged"),
                       (self.notes, "toggled")):
            getattr(w, sig).connect(self._opt_changed)

        # ------------- right: preview
        right = QWidget()
        rv = QVBoxLayout(right)
        self.preview = ImageLabel()
        rv.addWidget(self.preview, 1)
        nav = QHBoxLayout()
        self.prev, self.next = QPushButton("◀"), QPushButton("▶")
        self.prev.clicked.connect(lambda: self._goto(self.k - 1))
        self.next.clicked.connect(lambda: self._goto(self.k + 1))
        self.pos = QLabel(alignment=Qt.AlignCenter)
        self.est = QLabel(); self.est.setObjectName("hint")
        nav.addWidget(self.prev); nav.addWidget(self.pos, 1); nav.addWidget(self.next)
        rv.addLayout(nav)
        rv.addWidget(self.est, 0, Qt.AlignCenter)
        split.addWidget(scroll); split.addWidget(right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 1)
        split.setSizes([500, 700])
        scroll.setMinimumWidth(480)
        left.setMinimumWidth(0)

    # ------------------------------------------------------------ wizard API
    def can_next(self):
        return bool(self.plans)

    def on_enter(self):
        s = self.state
        sc = s.scan
        key = (id(sc), tuple(sc.channels_present))
        self._loading = True
        dims = available_dimensions(sc.samples)
        self.slide_per.set_options(dims)
        for cb in (self.cb_rows, self.cb_cols):
            cb.clear(); cb.addItem("(none)", None)
            for d in dims:
                cb.addItem(d, d)
        # channel list
        r = s.restore
        if key != self._scan_id or r:
            order = [c for c in CHANNELS if c in sc.channels_present]
            sel = (r or {}).get("spec", {}).get("channels") or order
            if r:
                order = [c for c in sel if c in order] + [c for c in order if c not in sel]
            self.chan_list.set_items([(c, f"{c}  ({DEFAULT_LABELS.get(c, c)})" if c == "350" else c, c in sel) for c in order])
            self.col_ch.clear()
            for c in order:
                self.col_ch.addItem(c, c)
            self._scan_id = key
        cur = treatments(sc.samples)
        order = [t for t in s.treatment_order if t in cur] + [t for t in cur if t not in s.treatment_order]
        s.treatment_order = order
        self.treat_list.set_items([(t, t, True) for t in order])
        if r:  # restore from project
            self._apply_opts(r["opts"])
            s.preset = r["preset"]
            self._fill_arr(LayoutSpec.from_dict(r["spec"]))
            s.restore = None
        else:
            if s.preset not in self.cards:
                s.preset = "sample_panel"
            if s.preset != CUSTOM:
                self._fill_arr(self._preset_spec(s.preset))
            else:
                self._fill_arr(s.spec)
        self._loading = False
        self._sync_cards()
        self._read()
        self._refresh()

    # ------------------------------------------------------------ helpers
    def _checked_channels(self):
        return self.chan_list.keys(only_checked=True)

    def _preset_spec(self, pid):
        spec = preset_spec(pid, self.state.scan.samples, self._checked_channels())
        spec.treatment_order = list(self.state.treatment_order)
        return spec

    def _fill_arr(self, spec):
        self._loading = True
        (self.mode_flow if spec.mode == "flow" else self.mode_grid).setChecked(True)
        self.slide_per.set_values(spec.slide_per)
        for cb, v in ((self.cb_rows, spec.rows), (self.cb_cols, spec.cols), (self.cb_order, spec.flow_order)):
            cb.setCurrentIndex(max(cb.findData(v), 0))
        self.fr.setValue(spec.flow_rows or 0); self.fc.setValue(spec.flow_cols or 0)
        self._loading = False
        self._show_mode()

    def _show_mode(self):
        flow = self.mode_flow.isChecked()
        for w in (self.cb_rows, self.cb_cols):
            w.setEnabled(not flow)
        for w in (self.fr, self.fc, self.cb_order):
            w.setEnabled(flow)

    def _sync_cards(self):
        for pid, c in self.cards.items():
            c.setChecked(pid == self.state.preset)

    # ------------------------------------------------------------ change handlers
    def pick_preset(self, pid):
        if self._loading:
            return
        self.state.preset = pid
        if pid != CUSTOM:
            self._fill_arr(self._preset_spec(pid))
        self._sync_cards()
        self._read()
        self.timer.start()

    def _arr_changed(self, *_):  # user edited an arrangement control => Custom
        self._show_mode()
        if self._loading:
            return
        self.state.preset = CUSTOM
        self._sync_cards()
        self._read()
        self.timer.start()

    def _chan_changed(self, *_):
        if self._loading:
            return
        if not self._checked_channels():  # at least one channel must stay on
            self.chan_list.item(0).setCheckState(Qt.Checked)
            return
        if self.state.preset != CUSTOM:
            self._fill_arr(self._preset_spec(self.state.preset))
        self._read()
        self.timer.start()

    def _treat_changed(self):
        self.state.treatment_order = self.treat_list.keys()
        self._read()
        self.timer.start()

    def _tag_color(self, c):
        self.chcolors[self.col_ch.currentData()] = c
        self._opt_changed()

    def _opt_changed(self, *_):
        if not self._loading:
            self._read()
            self.timer.start()

    def _goto(self, k):
        self.k = max(0, min(k, len(self.plans) - 1))
        self._render()

    # ------------------------------------------------------------ state <-> controls
    def _pixel(self):
        try:
            v = float(self.pixel.text().replace(",", "."))
            return v if v > 0 else None
        except ValueError:
            return None

    def _read(self):
        """Controls -> state.spec / state.opts (cheap)."""
        s = self.state
        spec = LayoutSpec(
            mode="flow" if self.mode_flow.isChecked() else "grid", slide_per=self.slide_per.values(),
            rows=self.cb_rows.currentData(), cols=self.cb_cols.currentData(),
            flow_rows=self.fr.value() or None, flow_cols=self.fc.value() or None,
            flow_order=self.cb_order.currentData(), filters=dict(s.spec.filters),
            channels=self._checked_channels(), treatment_order=list(s.treatment_order))
        if s.preset not in (CUSTOM, None) and s.scan:  # presets can override channels (contact sheet)
            spec.channels = self._preset_spec(s.preset).channels
        s.spec = spec
        sb = ScaleBarOptions(pixel_size_um=self._pixel(), length_um=None if self.bar_auto.isChecked() else self.bar_len.value(),
                             position=self.bar_pos.currentData(), color=self.bar_col.currentData(),
                             show_label=self.bar_lbl.isChecked())
        s.opts = replace(s.opts, slide_size=self.slide_size.currentText(), background=self.bg.color(),
                         jpeg_quality=self.jpeg.value(), max_px=self.maxpx.value(),
                         match_size_within_slide=self.match.isChecked(), notes=self.notes.isChecked(),
                         scale_bar=sb, channel_colors=dict(self.chcolors))

    def _apply_opts(self, o):
        from ..core.geometry import ExportOptions
        opts = ExportOptions.from_dict(o)
        self.state.opts = opts
        sb = opts.scale_bar
        self.pixel.setText("" if sb.pixel_size_um is None else f"{sb.pixel_size_um:g}")
        self.bar_auto.setChecked(sb.length_um is None)
        if sb.length_um:
            self.bar_len.setValue(sb.length_um)
        self.bar_pos.setCurrentIndex(max(self.bar_pos.findData(sb.position), 0))
        self.bar_col.setCurrentIndex(max(self.bar_col.findData(sb.color.upper()), 0))
        self.bar_lbl.setChecked(sb.show_label)
        self.slide_size.setCurrentText(opts.slide_size)
        self.bg.set_color(opts.background)
        self.jpeg.setValue(opts.jpeg_quality); self.maxpx.setValue(opts.max_px)
        self.match.setChecked(opts.match_size_within_slide); self.notes.setChecked(opts.notes)
        self.chcolors = dict(opts.channel_colors)

    # ------------------------------------------------------------ plans + preview
    def _refresh(self):
        s = self.state
        if not s.scan:
            return
        try:
            self.plans = plan_slides(s.scan.samples, s.spec)
        except Exception as e:
            self.plans = []
            self.preview.set_text(f"Cannot build layout: {e}")
        for pid, c in self.cards.items():
            try:
                spec = s.spec if pid == CUSTOM else self._preset_spec(pid)
                c.set_count(len(plan_slides(s.scan.samples, spec)))
            except Exception:
                c.set_count(None)
        sizes = s.scan.distinct_sizes
        mixed_px = self._pixel() and len(sizes) > 1
        self.size_warn.setText("Images have different pixel dimensions; one global pixel size is applied to all "
                               "(use pixel_size_um in the CSV for per-sample values)." if mixed_px else "")
        self.k = max(0, min(self.k, len(self.plans) - 1))
        self.est.setText(f"{len(self.plans)} slides, export {fmt_est(self.plans, s.opts)}")
        self.validityChanged.emit()
        self._render()

    def _render(self):
        n = len(self.plans)
        self.pos.setText(f"Slide {self.k + 1} of {n}" if n else "No slides")
        self.prev.setEnabled(self.k > 0); self.next.setEnabled(self.k < n - 1)
        if not n:
            self.preview.set_text("No slides: check channels, excluded samples and layout.")
            return
        self._gen += 1
        gen, plan, opts = self._gen, self.plans[self.k], self.state.opts
        if self.render_worker:
            self.render_worker.cancel()
        self.render_worker = run_async(
            lambda w: pil_to_qimage(render_preview(plan, opts, 1100)),
            on_result=lambda img: gen == self._gen and self.preview.set_pixmap(QPixmap.fromImage(img)),
            on_error=lambda m: self.preview.set_text("Preview failed: " + m))
