# Slide Builder

Desktop app (Windows, PySide6) that turns a folder of microscopy channel TIFFs plus a CSV of treatment metadata into a PowerPoint deck, with a graphical choice of slide layout.

Workflow: **1. Folder** (parse `Image_01_.07_488_1x.tif`-style names) → **2. CSV** (one row per VSI; treatment, replicate, per-channel labels, optional pixel size) → **3. Layout** (preset cards or custom rows/columns/slide-per, channel checkboxes, scale bar, downsampling, live preview) → **4. Export** (`.pptx`).

See [SPEC.md](SPEC.md) for the full specification (filename rules, CSV format, layout engine, presets).

## Run from source

```bash
pip install -r requirements.txt
python -m slidebuilder
```

## Try it with demo data

```bash
python tools/make_demo_data.py demo_data      # 219 synthetic TIFFs + treatments.csv
python -m slidebuilder                        # pick demo_data/images and demo_data/treatments.csv
```

`examples/mockup_treatments.csv` shows the CSV format for a 55-set folder (Image_00 = the unnumbered set, blank `source_id`).

## Tests

```bash
python -m pytest -q tests
```

## Portable build (no Python needed on the target PC)

```bash
pip install pyinstaller
python tools/build_portable.py       # -> dist/SlideBuilder/SlideBuilder.exe and SlideBuilder_portable.zip
```

## Layout

```
slidebuilder/core/   parsing, CSV matching, layout engine, geometry, thumbnails, PPTX export (no GUI imports)
slidebuilder/gui/    PySide6 wizard
tools/               demo-data generator, build script, GUI driver scripts
tests/               core tests
```

Not in v1: reading `.vsi`, image editing, channels beyond 350/488/555/647/merge, representative-image picker.
