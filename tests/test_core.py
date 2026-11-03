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
