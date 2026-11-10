from __future__ import annotations
import io
import os
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt
from .geometry import ExportOptions, hex_to_rgb, layout_slide
from .layout import SlidePlan
from .thumbs import load_rgb, rotate_cw

CENTERED = ("header", "bar_label", "missing")


def estimate(plans: list[SlidePlan], opts: ExportOptions) -> tuple[int, float]:
    n = sum(1 for p in plans for c in p.cells if c.sample and c.channel in c.sample.files)
    per = 0.25e6 * (opts.max_px / 1600) ** 2 * (opts.jpeg_quality / 90)
    return int(n * per), n * 0.09 + len(plans) * 0.05


def _rgb(h: str) -> RGBColor:
    return RGBColor(*hex_to_rgb(h))


def _text(slide, p, bold=False):
    tb = slide.shapes.add_textbox(Inches(p.x), Inches(p.y), Inches(p.w), Inches(p.h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE if p.kind in CENTERED or p.kind == "title" else MSO_ANCHOR.TOP
    para = tf.paragraphs[0]
    para.alignment = PP_ALIGN.CENTER if p.kind in CENTERED else PP_ALIGN.LEFT
    r = para.add_run()
    r.text = p.text
    r.font.size = Pt(p.size_pt or 12)
    r.font.bold = bold or p.kind in ("title", "label", "header")
    r.font.color.rgb = _rgb(p.color or "#FFFFFF")


def export_pptx(plans: list[SlidePlan], opts: ExportOptions, out_path: str, progress=None, cancel=None) -> list[str]:
    warnings: list[str] = []
    prs = Presentation()
    first = layout_slide(plans[0], opts) if plans else (13.333, 7.5, [])
    prs.slide_width, prs.slide_height = Inches(first[0]), Inches(first[1])
    blank = prs.slide_layouts[6]
    bg = hex_to_rgb(opts.background)
    for i, plan in enumerate(plans):
        if cancel and cancel():
            return warnings + ["Cancelled; no file written."]
        _, _, shapes = layout_slide(plan, opts)
        s = prs.slides.add_slide(blank)
        s.background.fill.solid()
        s.background.fill.fore_color.rgb = _rgb(opts.background)
        for p in shapes:
            if p.kind == "image":
                try:
                    im = rotate_cw(load_rgb(p.path, bg), p.rotation)
                    im.thumbnail((opts.max_px, opts.max_px))
                    buf = io.BytesIO()
                    im.save(buf, "JPEG", quality=opts.jpeg_quality)
                    buf.seek(0)
                    s.shapes.add_picture(buf, Inches(p.x), Inches(p.y), Inches(p.w), Inches(p.h))
                except Exception as e:
                    warnings.append(f"{os.path.basename(p.path)}: {e}")
            elif p.kind == "bar":
                r = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(p.x), Inches(p.y), Inches(p.w), Inches(p.h))
                r.fill.solid()
                r.fill.fore_color.rgb = _rgb(p.color)
                r.line.fill.background()
            else:
                _text(s, p)
        if opts.notes:
            names = [os.path.basename(c.sample.files[c.channel].path) for c in plan.cells
                     if c.sample and c.channel in c.sample.files]
            s.notes_slide.notes_text_frame.text = "\n".join(names)
        for c in plan.cells:
            if not c.sample or c.channel not in c.sample.files:
                warnings.append(f"{plan.title}: {c.sample.title if c.sample else '?'} channel {c.channel} missing")
        if progress:
            progress(i + 1, len(plans))
    prs.save(out_path)
    return warnings
