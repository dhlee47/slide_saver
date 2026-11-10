import os
import pytest
from PIL import Image
from pptx import Presentation
from slidebuilder.core.csvmap import apply_csv, auto_map, load_csv, treatments
from slidebuilder.core.export import export_pptx
from slidebuilder.core.geometry import ExportOptions, nice_length
from slidebuilder.core.layout import PRESETS, plan_slides, preset_spec
from slidebuilder.core.parse import scan_folder
from slidebuilder.core.thumbs import render_preview


@pytest.fixture()
def data(tmp_path):
    """3 VSIs (+ unnumbered), 2 sections, mixed sizes, one missing channel, one sectionless set."""
    d = tmp_path / "imgs"
    d.mkdir()
    names = []
    for vid in ("", "_01", "_02"):
        for sec in ("_.07", "_3.6"):
            for ch in ("488", "555", "647", "merge"):
                names.append(f"Image{vid}{sec}_{ch}_1x.tif")
    names += [f"Image_03_{c}_1x.tif" for c in ("488", "merge")]
    names.remove("Image_02_3.6_555_1x.tif")
    for n in names:
        size = (200, 120) if "_02_" in n else (100, 60)
        Image.new("RGBA", size, (10, 200, 10, 255)).save(d / n)
    (d / "Image_02.vsi").write_bytes(b"x")
    c = tmp_path / "meta.csv"
    c.write_text("source_id,treatment,replicate,488,555,647,pixel_size_um,sex\n"
                 ",Veh,A,GFP,NeuN,,0.5,M\nImage_01,Drug,B,GFP,NeuN,,0.5,F\n2,Veh,C,,,,,M\n", encoding="utf-8")
    return str(d), str(c)


def build(data):
    scan = scan_folder(data[0])
    table = load_csv(data[1])
    rep = apply_csv(scan, table, auto_map(table))
    return scan, rep


def test_scan_unnumbered_is_zero(data):
    scan = scan_folder(data[0])
    assert scan.samples[0].source_id == "Image_00" and scan.samples[0].unnumbered
    assert [s.title for s in scan.samples][:3] == ["Image_00 .07", "Image_00 3.6", "Image_01 .07"]
    assert "Image_02.vsi" in scan.unparsed
    assert any("Image_02 3.6" in w and "555" in w for w in scan.warnings)
    assert len(scan.distinct_sizes) == 2
    s3 = [s for s in scan.samples if s.source_id == "Image_03"]
    assert s3 and s3[0].section == ""


def test_csv_match(data):
    scan, rep = build(data)
    assert "Image_00 .07" in rep.matched and "Image_03" in " ".join(rep.images_without_row)
    s = next(s for s in scan.samples if s.source_id == "Image_01")
    assert s.meta["treatment"] == "Drug" and s.labels["488"] == "GFP" and s.pixel_size_um == 0.5
    assert s.meta["sex"] == "F"
    assert treatments(scan.samples)[:2] == ["Veh", "Drug"]
    # rep_n restarts per treatment+section
    v = [x for x in scan.samples if x.meta["treatment"] == "Veh" and x.section == ".07"]
    assert [x.meta["rep_n"] for x in v] == ["1", "2"]


def test_all_presets_plan_and_export(data, tmp_path):
    scan, _ = build(data)
    chans = scan.channels_present
    opts = ExportOptions()
    opts.scale_bar.pixel_size_um = 1.0
    for p in PRESETS:
        spec = preset_spec(p.id, scan.samples, chans)
        plans = plan_slides(scan.samples, spec)
        assert plans, p.id
        for pl in plans:
            assert all(0 <= c.row < pl.n_rows and 0 <= c.col < pl.n_cols for c in pl.cells), p.id
            pos = [(c.row, c.col) for c in pl.cells]
            assert len(pos) == len(set(pos)), f"{p.id}: collision on '{pl.title}'"
        render_preview(plans[0], opts, 400)
        out = str(tmp_path / f"{p.id}.pptx")
        export_pptx(plans, opts, out)
        assert len(Presentation(out).slides) == len(plans)


def test_sample_panel_layout_and_missing(data):
    scan, _ = build(data)
    plans = plan_slides(scan.samples, preset_spec("sample_panel", scan.samples, ["488", "555", "647", "merge"]))
    assert len(plans) == len(scan.samples)
    p = plans[0]
    assert (p.n_rows, p.n_cols) == (2, 2)
    assert [(c.channel, c.row, c.col) for c in p.cells] == [("488", 0, 0), ("555", 0, 1), ("647", 1, 0), ("merge", 1, 1)]
    miss = next(p for p in plans if p.cells[0].sample.title == "Image_02 3.6")
    assert any(c.channel not in c.sample.files for c in miss.cells)


def test_channel_toggle_and_five_channels(data):
    scan, _ = build(data)
    plans = plan_slides(scan.samples, preset_spec("sample_panel", scan.samples, ["488", "merge"]))
    assert (plans[0].n_rows, plans[0].n_cols) == (1, 2)
    five = plan_slides(scan.samples, preset_spec("sample_panel", scan.samples, ["350", "488", "555", "647", "merge"]))
    assert (five[0].n_rows, five[0].n_cols) == (2, 3)


def test_custom_extra_dimension_and_filter(data):
    scan, _ = build(data)
    spec = preset_spec("chan_by_treat", scan.samples, ["merge"])
    spec.slide_per, spec.rows, spec.cols = ["sex"], "section", "treatment"
    plans = plan_slides(scan.samples, spec)
    assert {p.title.split(" \u00b7 ")[0] for p in plans} >= {"M", "F"}
    spec.filters = {"section": ".07"}
    assert all(c.sample.section == ".07" for p in plan_slides(scan.samples, spec) for c in p.cells)


def test_scale_bar_and_mixed_sizes():
    assert nice_length(37) == 20 and nice_length(100) == 100 and nice_length(0.7) == 0.5


def test_real_folder_smoke(tmp_path):
    d = r"C:\Users\KLee\OneDrive - Neurocrine Biosciences Inc\Desktop\Image Edits"
    if not os.path.isdir(d):
        pytest.skip("real data not present")
    scan = scan_folder(d)
    plans = plan_slides(scan.samples, preset_spec("sample_panel", scan.samples, scan.channels_present))
    assert len(scan.samples) == 55 and len(plans) == 55


# ---------------------------------------------------------------- stacking, alignment, rotation, orientation
def _unequal_samples():
    """Vehicle x4, DrugA x2, DrugB x3 (one section); replicate names deliberately overlap/clash."""
    from slidebuilder.core.model import ImageFile, Sample, assign_rep_n
    out = []
    n = 0
    for treat, k in (("Vehicle", 4), ("DrugA", 2), ("DrugB", 3)):
        for _ in range(k):
            n += 1
            sid = f"Image_{n:02d}"
            s = Sample(sid, ".07")
            s.meta.update(treatment=treat, replicate="A1")  # same replicate name everywhere
            s.files = {c: ImageFile(f"{sid}_{c}.tif", sid, ".07", c, 200, 100) for c in ("488", "merge")}
            out.append(s)
    assign_rep_n(out)
    return out


def test_replicates_stack_by_position_and_pad_to_max():
    samples = _unequal_samples()
    spec = preset_spec("treat_by_rep", samples, ["488", "merge"])  # rows = treatment, cols = replicate
    plans = plan_slides(samples, spec)
    assert len(plans) == 2  # one per channel
    for p in plans:
        assert (p.n_rows, p.n_cols) == (3, 4)  # padded to the largest group (4)
        assert p.col_headers == ["Rep 1", "Rep 2", "Rep 3", "Rep 4"]
        by_row = {}
        for c in p.cells:
            by_row.setdefault(c.row, []).append(c.col)
        assert {r: sorted(v) for r, v in by_row.items()} == {0: [0, 1, 2, 3], 1: [0, 1], 2: [0, 1, 2]}  # blanks stay blank


def test_alignment_is_global_across_slides():
    samples = _unequal_samples()
    plans = plan_slides(samples, preset_spec("chan_by_rep", samples, ["488"]))  # slide per treatment
    assert len(plans) == 3 and all(p.n_cols == 4 for p in plans)  # DrugA slide still has 4 columns


def test_transpose_swaps_axes():
    samples = _unequal_samples()
    spec = preset_spec("chan_by_treat", samples, ["488", "merge"])
    a = plan_slides(samples, spec)[0]
    spec.transpose = True
    b = plan_slides(samples, spec)[0]
    assert (a.n_rows, a.n_cols) == (b.n_cols, b.n_rows) == (2, 3)
    assert a.row_headers == b.col_headers and a.col_headers == b.row_headers


def test_rotation_swaps_geometry_and_pixels(tmp_path):
    from slidebuilder.core.geometry import layout_slide
    samples = _unequal_samples()
    spec = preset_spec("big_image", samples, ["488"])
    plan0 = plan_slides(samples, spec)[0]
    spec.rotation = 90
    plan90 = plan_slides(samples, spec)[0]
    w0 = next(p for p in layout_slide(plan0, ExportOptions())[2] if p.kind == "image")
    w90 = next(p for p in layout_slide(plan90, ExportOptions())[2] if p.kind == "image")
    assert w0.w > w0.h and w90.h > w90.w and w90.rotation == 90
    assert preset_spec("channel_strip", samples, ["488"]).rotation == 90 and preset_spec("sample_panel", samples, ["488"]).rotation == 0
    from slidebuilder.core.thumbs import rotate_cw
    im = Image.new("RGB", (30, 10))
    assert rotate_cw(im, 90).size == (10, 30) and rotate_cw(im, 180).size == (30, 10)
    # pixel content really rotates clockwise: top-left red pixel ends up top-right after 90 deg cw
    im.putpixel((0, 0), (255, 0, 0))
    assert rotate_cw(im, 90).getpixel((9, 0)) == (255, 0, 0)


def test_magnification_names_and_slide_sheet_csv(tmp_path):
    """Image[_NN]_{1x|4x}_{channel}.tif plus a 'Slide ID / Group ID / Sample ID / Stain' sheet."""
    d = tmp_path / "mag"
    d.mkdir()
    for vid in ("", "_01", "_02"):
        for mag in ("1x", "4x"):
            for ch in ("555", "merge"):
                Image.new("RGB", (40, 20)).save(d / f"Image{vid}_{mag}_{ch}.tif")
    c = tmp_path / "sample_sheet.csv"
    c.write_text('Slide ID,Sample ID,Group ID,Strain,Stain\nImage,13,1,A,bAmyloid (555)\nImage-01,25,2,B,bAmyloid (555)\n'
                 'Image-02,1 sec only,3,A,no amyloid primary\n', encoding="utf-8")
    scan = scan_folder(str(d))
    assert [s.title for s in scan.samples] == ["Image_00 1x", "Image_00 4x", "Image_01 1x", "Image_01 4x", "Image_02 1x", "Image_02 4x"]
    assert scan.channels_present == ["555", "merge"] and not scan.unparsed[1:]
    table = load_csv(str(c))
    m = auto_map(table)
    assert (m.source_id, m.treatment, m.replicate, m.labels) == ("Slide ID", "Group ID", "Sample ID", {"555": "Stain"})
    rep = apply_csv(scan, table, m)
    assert len(rep.matched) == 6 and not rep.images_without_row and not rep.rows_without_images
    assert scan.samples[0].labels["555"] == "bAmyloid" and scan.samples[0].meta["treatment"] == "1"
    assert scan.samples[-1].labels["555"] == "no amyloid primary"


def _spans(name, **parts):
    return {tag: (name.index(txt), name.index(txt) + len(txt)) for tag, txt in parts.items()}


def test_build_pattern_from_highlights(tmp_path):
    import re
    from slidebuilder.core.naming import build_pattern
    ex = "Image_01_1x_555.tif"
    pat = build_pattern(ex, _spans(ex, id="01", section="1x", channel="555"))
    rx = re.compile(pat, re.IGNORECASE)
    for name, want in (("Image_01_1x_555.tif", ("01", "1x", "555")), ("Image_12_20x_merge.tiff", ("12", "20x", "merge")),
                       ("Image_1x_555.tif", (None, "1x", "555")), ("Image_03_647.tif", ("03", None, "647"))):
        m = rx.match(name)
        assert m and (m["id"], m["section"], m["channel"]) == want, name
    assert not rx.match("sample_sheet.csv")
    # a folder with an entirely different scheme: Slide3_Cy5_Z2.tif -> id=3, channel=Cy5
    ex2 = "Slide3_Cy5_Z2.tif"
    rx2 = re.compile(build_pattern(ex2, _spans(ex2, id="3", channel="Cy5")))
    assert rx2.match("Slide7_DAPI_Z2.tif")["channel"] == "DAPI"
    with pytest.raises(ValueError):
        build_pattern(ex, {"id": (6, 8)})


def test_custom_channels_and_merge_alias(tmp_path):
    from slidebuilder.core.naming import build_pattern
    d = tmp_path / "cus"
    d.mkdir()
    for sid in ("A1", "A2"):
        for ch in ("DAPI", "GFP", "Overlay"):
            Image.new("RGB", (20, 10)).save(d / f"{sid}__{ch}.tif")
    ex = "A1__DAPI.tif"
    pat = build_pattern(ex, _spans(ex, id="1", channel="DAPI"))  # "1" is the first "1" in the name
    scan = scan_folder(str(d), pat)
    assert scan.channels_present == ["DAPI", "GFP", "merge"]  # 'Overlay' is recognised as the merge image
    assert len(scan.samples) == 2 and not scan.unparsed


def test_treatment_label_displayed_but_group_organises(tmp_path):
    from slidebuilder.core.model import treatment_labels
    d = tmp_path / "g"
    d.mkdir()
    for n in ("Image_01_1x_555", "Image_02_1x_555", "Image_03_1x_555"):
        Image.new("RGB", (20, 10)).save(d / f"{n}.tif")
    c = tmp_path / "s.csv"
    c.write_text("Slide ID,Sample ID,Group ID,Description\nImage-01,1,1,3mo WT\nImage-02,2,2,5mo KO female\nImage-03,3,2,5mo KO female\n", encoding="utf-8")
    scan = scan_folder(str(d))
    table = load_csv(str(c))
    m = auto_map(table, scan.channels_present)
    assert m.treatment == "Group ID" and m.treatment_label == "Description"
    apply_csv(scan, table, m)
    assert treatment_labels(scan.samples) == {"1": "3mo WT", "2": "5mo KO female"}
    plans = plan_slides(scan.samples, preset_spec("chan_by_treat", scan.samples, ["555"]))
    assert plans[0].col_headers == ["3mo WT", "5mo KO female"]  # grouped by group id, shown by description
    assert plans[0].n_cols == 2


def test_alphanumeric_ids_match_csv(tmp_path):
    from slidebuilder.core.naming import build_pattern
    d = tmp_path / "alpha"
    d.mkdir()
    for m in ("M12", "M13"):
        for ch in ("DAPI", "GFP"):
            Image.new("RGB", (20, 10)).save(d / f"5xFAD_{m}_Ch{ch}_20x.tif")
    ex = "5xFAD_M12_ChDAPI_20x.tif"
    pat = build_pattern(ex, {"id": (6, 9), "channel": (12, 16)})
    scan = scan_folder(str(d), pat)
    assert [s.source_id for s in scan.samples] == ["M12", "M13"] and scan.channels_present == ["DAPI", "GFP"]
    c = tmp_path / "m.csv"
    c.write_text("id,treatment\nM12,A\nM13,B\n", encoding="utf-8")
    table = load_csv(str(c))
    rep = apply_csv(scan, table, auto_map(table))
    assert len(rep.matched) == 2 and [s.meta["treatment"] for s in scan.samples] == ["A", "B"]


def test_png_and_jpeg_inputs_scan_and_export(tmp_path):
    import numpy as np
    d = tmp_path / "mixed"
    d.mkdir()
    Image.new("RGB", (60, 40), (200, 30, 30)).save(d / "Image_01_1x_555.jpg")
    Image.new("RGBA", (60, 40), (0, 200, 0, 128)).save(d / "Image_01_1x_488.png")
    Image.new("P", (60, 40)).save(d / "Image_01_1x_647.png")
    Image.fromarray((np.arange(2400).reshape(40, 60) * 20).astype("uint16")).save(d / "Image_01_1x_merge.png")  # 16-bit
    Image.new("L", (60, 40), 90).save(d / "Image_02_1x_555.jpeg")
    scan = scan_folder(str(d))
    assert len(scan.files) == 5 and not scan.unparsed
    assert scan.channels_present == ["488", "555", "647", "merge"]
    plans = plan_slides(scan.samples, preset_spec("sample_panel", scan.samples, scan.channels_present))
    out = str(tmp_path / "x.pptx")
    assert not [w for w in export_pptx(plans, ExportOptions(), out) if "missing" not in w]
    assert len(Presentation(out).slides) == 2
    render_preview(plans[0], ExportOptions(), 300)


def test_group_prefix_extra_token_and_dotted_channel(tmp_path):
    from slidebuilder.core.naming import build_pattern
    d = tmp_path / "g1"
    d.mkdir()
    names = []
    for g, sid, extra in (("G1", "S1", ""), ("G1", "S21", "_20"), ("G2", "S3", "")):
        for ch in ("Cy5.5", "DAPI", "SpGreen", "Merge"):
            names.append(f"{g}_{sid}_Image{extra}_{ch}_ROI1.jpg")
    for n in names:
        Image.new("RGB", (30, 20)).save(d / n)
    ex = "G1_S1_Image_Cy5.5_ROI1.jpg"
    span = lambda t: (ex.index(t), ex.index(t) + len(t))
    pat = build_pattern(ex, {"ignore": span("G1"), "id": span("S1"), "channel": span("Cy5.5"), "section": span("ROI1")})
    scan = scan_folder(str(d), pat)
    assert not scan.unparsed and len(scan.files) == len(names)
    assert [s.source_id for s in scan.samples] == ["S1", "S3", "S21"] and scan.samples[0].section == "ROI1"
    assert scan.channels_present == ["Cy5.5", "DAPI", "SpGreen", "merge"]  # alphabetical, merge last


def test_csv_ids_match_by_number_when_names_differ(tmp_path):
    d = tmp_path / "num"
    d.mkdir()
    for sid in ("S1", "S21"):
        Image.new("RGB", (20, 10)).save(d / f"G1_{sid}_Image_DAPI_ROI1.jpg")
    from slidebuilder.core.naming import build_pattern
    ex = "G1_S1_Image_DAPI_ROI1.jpg"
    pat = build_pattern(ex, {"id": (3, 5), "channel": (12, 16)})
    scan = scan_folder(str(d), pat)
    assert [s.source_id for s in scan.samples] == ["S1", "S21"]
    c = tmp_path / "n.csv"
    c.write_text("Slide ID,Group ID\nImage-01,1\nImage_21,2\nImage-05,3\n", encoding="utf-8")
    table = load_csv(str(c))
    rep = apply_csv(scan, table, auto_map(table))
    assert len(rep.matched) == 2 and [s.meta["treatment"] for s in scan.samples] == ["1", "2"]
    assert rep.rows_without_images == ["Image_05"] and not rep.images_without_row
