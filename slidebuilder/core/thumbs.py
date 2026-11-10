from __future__ import annotations
import hashlib
import textwrap
import os
import tempfile
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from .geometry import ExportOptions, hex_to_rgb, layout_slide
from .layout import SlidePlan

_CACHE = os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "SlideBuilder", "cache")


def load_rgb(path: str, bg: tuple[int, int, int] = (0, 0, 0)) -> Image.Image:
    """Load a TIFF as 8-bit RGB (alpha composited on bg, 16-bit scaled to full range)."""
    im = Image.open(path)
    im.load()
    if im.mode in ("I;16", "I;16L", "I;16B", "I", "F"):
        a = np.asarray(im).astype(np.float32)
        top = float(a.max()) or 1.0
        im = Image.fromarray((a / top * 255).clip(0, 255).astype(np.uint8))
    if im.mode in ("RGBA", "LA", "PA"):
        rgba = im.convert("RGBA")
        base = Image.new("RGB", rgba.size, bg)
        base.paste(rgba, mask=rgba.split()[3])
        return base
    return im.convert("RGB")


def rotate_cw(im: Image.Image, deg: int) -> Image.Image:
    T = Image.Transpose
    return {0: im, 90: im.transpose(T.ROTATE_270), 180: im.transpose(T.ROTATE_180), 270: im.transpose(T.ROTATE_90)}[deg % 360]


def get_thumbnail(path: str, max_px: int = 256) -> Image.Image:
    st = os.stat(path)
    key = hashlib.sha1(f"{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}|{max_px}".encode()).hexdigest()
    cp = os.path.join(_CACHE, key + ".jpg")
    if os.path.exists(cp):
        try:
            im = Image.open(cp)
            im.load()
            return im
        except Exception:
            pass
    im = load_rgb(path)
    im.thumbnail((max_px, max_px), Image.LANCZOS)
    try:
        os.makedirs(_CACHE, exist_ok=True)
        im.save(cp, "JPEG", quality=85)
    except OSError:
        pass
    return im


def _font(pt: float):
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, max(6, int(pt)))
        except OSError:
            continue
    return ImageFont.load_default()


def render_preview(plan: SlidePlan, opts: ExportOptions, width_px: int = 960) -> Image.Image:
    sw, sh, shapes = layout_slide(plan, opts)
    k = width_px / sw
    img = Image.new("RGB", (width_px, round(sh * k)), hex_to_rgb(opts.background))
    d = ImageDraw.Draw(img)
    for p in shapes:
        x, y, w, h = p.x * k, p.y * k, p.w * k, p.h * k
        if p.kind == "image":
            t = rotate_cw(get_thumbnail(p.path, max(64, int(max(w, h)))), p.rotation)
            img.paste(t.resize((max(1, round(w)), max(1, round(h))), Image.BILINEAR), (round(x), round(y)))
        elif p.kind == "bar":
            d.rectangle([x, y, x + w, y + h], fill=hex_to_rgb(p.color))
        elif p.text:
            f = _font(p.size_pt * k / 72)  # points -> px at this scale (72 pt per inch)
            col = hex_to_rgb(p.color or "#FFFFFF")
            if p.kind in ("header", "bar_label", "missing"):
                px = max(6.0, p.size_pt * k / 72)
                txt = "\n".join(textwrap.wrap(p.text, max(4, int(w / (px * 0.52))))) if p.kind != "bar_label" else p.text
                d.multiline_text((x + w / 2, y + h / 2), txt, font=f, fill=col, anchor="mm", align="center")
            else:
                d.text((x, y), p.text, font=f, fill=col)
    return img
