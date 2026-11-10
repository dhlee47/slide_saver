from __future__ import annotations
import math
from dataclasses import asdict, dataclass, field, fields
from .layout import SlidePlan

SLIDE_SIZES = {"16:9": (13.333, 7.5), "4:3": (10.0, 7.5)}
TITLE_H = 0.4
ROW_HEADER_W = 1.1
COL_HEADER_H = 0.3
MARGIN = 0.1
DEFAULT_TAG_COLOR = "#FFFF00"


@dataclass
class ScaleBarOptions:
    pixel_size_um: float | None = None
    length_um: float | None = None  # None = auto
    position: str = "bottom_right"  # or "bottom_left"
    color: str = "#FFFFFF"
    show_label: bool = True


@dataclass
class ExportOptions:
    slide_size: str = "16:9"
    background: str = "#000000"
    label_pt: int = 12
    title_pt: int = 14
    jpeg_quality: int = 90
    max_px: int = 1600
    match_size_within_slide: bool = False
    notes: bool = False
    gap_in: float = 0.05
    scale_bar: ScaleBarOptions = field(default_factory=ScaleBarOptions)
    channel_colors: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ExportOptions":
        names = {f.name for f in fields(ExportOptions)}
        kw = {k: v for k, v in d.items() if k in names and k != "scale_bar"}
        sb = d.get("scale_bar") or {}
        sbn = {f.name for f in fields(ScaleBarOptions)}
        return ExportOptions(scale_bar=ScaleBarOptions(**{k: v for k, v in sb.items() if k in sbn}), **kw)


@dataclass
class Placed:
    kind: str  # image | label | title | header | bar | bar_label | missing
    x: float
    y: float
    w: float
    h: float
    text: str = ""
    path: str = ""
    color: str = ""
    size_pt: int = 0
    rotation: int = 0  # images: clockwise degrees applied to the file


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def contrast_color(bg: str) -> str:
    r, g, b = hex_to_rgb(bg)
    return "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else "#FFFFFF"


def nice_length(v: float) -> float:
    """Largest 1/2/5 x 10^n that is <= v."""
    if v <= 0:
        return 0.0
    p = 10 ** math.floor(math.log10(v))
    best = p
    for m in (1, 2, 5):
        if m * p <= v:
            best = m * p
    return best


def _fmt_um(v: float) -> str:
    return f"{v:g} µm"


def layout_slide(plan: SlidePlan, opts: ExportOptions) -> tuple[float, float, list[Placed]]:
    sw, sh = SLIDE_SIZES.get(opts.slide_size, SLIDE_SIZES["16:9"])
    fg = contrast_color(opts.background)
    out: list[Placed] = []
    if plan.title:
        out.append(Placed("title", 0.15, 0.03, sw - 0.3, TITLE_H - 0.05, plan.title, color=fg, size_pt=opts.title_pt))
    chw = 0.55 * opts.label_pt / 72  # approximate character width (in)
    longest_row = max((len(t) for t in plan.row_headers), default=0)
    rhw = (ROW_HEADER_W if longest_row * chw <= ROW_HEADER_W - 0.15 else 1.9) if plan.row_headers else 0.0
    x0 = MARGIN + rhw
    aw = sw - x0 - MARGIN
    gp = opts.gap_in
    cw = (aw - gp * (plan.n_cols - 1)) / plan.n_cols
    lines = max((math.ceil(len(t) * chw / max(cw - 0.1, 0.3)) for t in plan.col_headers), default=1)
    chh = min(COL_HEADER_H + 0.2 * (lines - 1), 1.3) if plan.col_headers else 0.0
    y0 = TITLE_H + chh
    ah = sh - y0 - MARGIN
    g = gp
    chgt = (ah - g * (plan.n_rows - 1)) / plan.n_rows
    for i, t in enumerate(plan.row_headers):
        out.append(Placed("header", MARGIN, y0 + i * (chgt + g), rhw - 0.05, chgt, t, color=fg, size_pt=opts.label_pt))
    for j, t in enumerate(plan.col_headers):
        out.append(Placed("header", x0 + j * (cw + g), TITLE_H, cw, chh, t, color=fg, size_pt=opts.label_pt))

    rot = plan.rotation % 360

    def dims(c):  # displayed pixel size (after rotation)
        f = c.sample.files.get(c.channel) if c.sample else None
        if not (f and f.width and f.height):
            return None
        return (f.height, f.width) if rot in (90, 270) else (f.width, f.height)

    fits = {id(c): (min(cw / d[0], chgt / d[1]) if (d := dims(c)) else None) for c in plan.cells}
    common = min((v for v in fits.values() if v), default=None) if opts.match_size_within_slide else None
    for c in plan.cells:
        cx, cy = x0 + c.col * (cw + g), y0 + c.row * (chgt + g)
        d = dims(c)
        if not d:
            out.append(Placed("missing", cx, cy, cw, chgt, f"{c.sample.label(c.channel) if c.sample else c.channel}: missing",
                              color="#FF5A5A", size_pt=opts.label_pt))
            continue
        scale = common or fits[id(c)]
        w, h = d[0] * scale, d[1] * scale
        ix, iy = cx + (cw - w) / 2, cy + (chgt - h) / 2
        out.append(Placed("image", ix, iy, w, h, path=c.sample.files[c.channel].path, rotation=rot))
        if c.label:
            out.append(Placed("label", ix + 0.04, iy + 0.02, max(0.5, w - 0.08), 0.28, c.label,
                              color=opts.channel_colors.get(c.channel, DEFAULT_TAG_COLOR), size_pt=opts.label_pt))
        px = c.sample.pixel_size_um or opts.scale_bar.pixel_size_um
        if px:
            um_per_in = px * d[0] / w
            sb = opts.scale_bar
            bar_um = sb.length_um or nice_length(0.18 * w * um_per_in)
            bar_in = bar_um / um_per_in
            if bar_in > 0 and bar_in < w:
                bx = ix + w - 0.1 - bar_in if sb.position == "bottom_right" else ix + 0.1
                by = iy + h - 0.14
                out.append(Placed("bar", bx, by, bar_in, 0.04, color=sb.color))
                if sb.show_label:
                    out.append(Placed("bar_label", bx - 0.2, by - 0.22, bar_in + 0.4, 0.2, _fmt_um(bar_um),
                                      color=sb.color, size_pt=max(8, opts.label_pt - 2)))
    return sw, sh, out
