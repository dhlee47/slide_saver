# Microscopy Slide Builder — Specification (v1)

Desktop app (Windows first) that turns a folder of channel TIFFs plus a CSV of treatment metadata into a PowerPoint deck whose layout the user picks graphically.

**Stack:** Python 3.14, PySide6 (GUI), python-pptx (deck), Pillow + numpy (images). Package as one `.exe` with PyInstaller. Everything runs locally.

**Repo layout**
```
slidebuilder/
  core/            # no GUI imports; fully unit-testable   (owner: core)
    model.py parse.py csvmap.py layout.py geometry.py thumbs.py export.py
  gui/             # PySide6                                (owner: gui)
    app.py main_window.py step_folder.py step_csv.py step_layout.py step_export.py widgets.py
  __main__.py      # `python -m slidebuilder` launches GUI
tests/
```

## 1. Workflow (wizard, 4 steps, Back/Next)
1. **Folder** – choose folder, see parsed files table.
2. **CSV** – choose CSV, map columns, see match report.
3. **Layout** – pick a preset card or Custom, channels, pixel size/scale bar, other options, live slide preview.
4. **Export** – summary (slide count, estimated size/time), Save As `.pptx`, progress bar + cancel. Also File menu: Save/Open project (JSON of all settings).

## 2. Filenames
Default pattern: `Image[_{id}][_{section}]_{channel}_1x.tif`, where
- `id` = digits (e.g. `01`), optional.
- `section` = `.07` or `3.6` style token (digits with a dot), optional.
- `channel` ∈ `350 | 488 | 555 | 647 | merge`.

Examples: `Image_01_.07_488_1x.tif`, `Image_01_3.6_merge_1x.tif`, `Image_27_555_1x.tif` (no section), `Image_.07_488_1x.tif` and `Image_3.6_647_1x.tif` (no id).

- **No id ⇒ index 0.** `source_id` becomes `Image_00`; flagged `unnumbered=True` (UI shows "(unnumbered)").
- No section ⇒ `section=""` (shown as "(no section)" in section-based layouts).
- Sorting is numeric by id (0 first), then section (".07" before "3.6"; blank last), then channel order.
- Non-matching files (e.g. `.vsi`) go to `unparsed`. A sample missing some channels is a warning, not an error.
- The pattern is user-editable (regex with named groups `id`, `section`, `channel`) in an "Advanced" box.
- Image width/height are read from the TIFF header (mixed sizes are expected).

## 3. Channels
`CHANNELS = ["350","488","555","647","merge"]`, default order 350, 488, 555, 647, merge (user-reorderable). A channel appears in the UI only if at least one file of it exists. Each is checkable on/off in step 3 (at least one must stay on). 350 defaults to label "DAPI"; others default to their wavelength. Merge is used as exported; the app never recomputes it.

## 4. CSV (keyed on the VSI)
One row per VSI (`source_id`, e.g. `Image_01`); sections inherit the row's metadata.

| column | notes |
|---|---|
| `source_id` | required. `Image_01`, `01`, `1` all match. `Image_00`, `00`, `0`, or blank ⇒ the unnumbered set (index 0). Duplicates are flagged. |
| `treatment` | required |
| `replicate` | optional; if absent, `source_id` is used as the replicate label |
| `350`,`488`,`555`,`647` | optional per-VSI channel labels (marker names). Blank ⇒ default label |
| `pixel_size_um` | optional per-VSI µm/pixel (overrides the global value) |
| anything else | extra columns, each becomes a layout dimension |

Step 2 auto-maps columns by name (case-insensitive) and lets the user override via dropdowns. The match report lists: matched samples, images with no CSV row, CSV rows with no images; the user may proceed with unmatched images (treatment = "(unassigned)") or exclude them. Treatment order is drag-sortable (control first).

## 5. Layout engine
**Dimensions:** `channel`, `treatment`, `replicate`, `rep_n` (1-based index of the replicate within its treatment+section, in sorted order), `section`, `sample` (= source_id+section), plus any extra CSV column.
A *cell* is one image = (sample, channel).

**Grid mode** (`LayoutSpec.mode="grid"`): choose `slide_per` (list of dims that start new slides), `rows` dim, `cols` dim. Any dimension not assigned is implicitly added to `slide_per` (so slides never collide). Rows/cols keys are sorted by user order (channel order, treatment order, otherwise natural order). Headers come from keys (channel → label from CSV, e.g. "NeuN (555)"). `filters` optionally restricts a dimension to one value.

**Flow mode** (`mode="flow"`): items (cells) are laid out left-to-right, top-to-bottom in a `flow_rows × flow_cols` grid; `slide_per` splits slides; overflow paginates. Items within a slide are ordered by channel order then sample order (or sample then channel for `flow_order="sample_major"`).

**Presets** (card = wireframe sketch + live slide count):
1. **Sample panel** – flow, slide_per=[sample]; grid auto: 4 ch → 2×2, 5 ch → 3×2, 3 → 2×2, 2 → 1×2, 1 → 1×1. (Reproduces NW 488 / NE 555 / SW 647 / SE merge.)
2. **Channel strip** – flow, slide_per=[sample], 1×N.
3. **Channels × replicates, per treatment** – grid, slide_per=[treatment, section], rows=channel, cols=replicate.
4. **Treatments × replicates, per channel** – grid, slide_per=[channel, section], rows=treatment, cols=replicate.
5. **Channels × treatments, per replicate #** – grid, slide_per=[rep_n, section], rows=channel, cols=treatment.
6. **Section comparison** – grid, slide_per=[channel, rep_n], rows=section, cols=treatment.
7. **Merge overview (contact sheet)** – flow, merge only (falls back to first selected channel if merge is unchecked/absent), slide_per=[treatment], flow 3×4 (user adjustable); sample_major.
8. **One big image per slide** – flow 1×1, slide_per=[], every selected cell gets its own slide.
9. **Custom** – three dropdowns (Slide per, Rows, Columns) + mode toggle; preview updates live.

(The "representative figure" preset is explicitly NOT in v1.)

Slide title = the `slide_per` key values joined, e.g. "Vehicle · .07" (flow/sample panel: "Image_01 · .07 · Vehicle").

## 6. Geometry & export
- **Slide:** 16:9 (13.333×7.5 in) or 4:3 (10×7.5). Background colour default black (white optional). A title strip (~0.4 in) on top; optional row/column header strips (~0.3 in) when headers exist; remaining area divided into equal cells with a small gap (default 0.05 in).
- **Mixed image sizes:** each image is scaled to fit its cell preserving aspect ratio, centred (letterbox shows background). Option **Match size within slide**: all images on a slide use the displayed scale of the smallest-fitting one.
- **Scale bar:** global `pixel_size_um` (empty ⇒ none), overridden per sample by CSV `pixel_size_um`. If a sample has no pixel size, no bar on it. Bar length auto ("nice" 1/2/5×10ⁿ µm ≈ 15–20 % of displayed image width) or manual µm; position bottom-right (or left); white or black; optional "100 µm" label. Drawn as native PowerPoint shapes (rectangle + textbox), length = `bar_um / pixel_size_um × displayed_width_in / image_width_px` inches. Warn in the UI when images of different pixel dimensions are mixed under a single global pixel size.
- **Labels:** channel tag in the corner of each tile (marker label or wavelength) with per-channel text colour; titles as above; row/col headers.
- **Missing image:** red "<channel>: missing" placeholder in the cell.
- **Images embedded** as JPEG (quality default 90, max long edge default 1600 px), RGBA composited on the background. Streamed one at a time. Optional speaker notes listing source filenames.
- Progress callback and cancel support. Output must open cleanly in PowerPoint.
- `geometry.layout_slide()` is shared by the exporter and the GUI preview so they always agree.

## 7. GUI details
- Step 1 table columns: source_id, section, channels present (chips), dimensions (e.g. `2207×1271`, "N distinct sizes" banner), warnings; unparsed files in a collapsible list; exclude checkbox per sample.
- Thumbnails come from `thumbs.get_thumbnail` (disk-cached, loaded in a worker thread).
- Step 3 right side: live preview of slide *k* of *N* (render via `render_preview`), prev/next buttons, slide count readout, estimated export time.
- Long work (scan, thumbnails, export) runs off the UI thread (QThreadPool / QThread) with progress and cancel; UI never freezes.
- Settings remembered: last folder/CSV/output directory (QSettings).
- Light neutral theme, readable at 1366×768.

## 8. Out of scope for v1
Reading `.vsi`, image editing (contrast/crop), cloud, representative-image picker, channels beyond 350/488/555/647/merge, Mac build.

## 9. Core API contract (binding; the GUI codes against this)

```python
# slidebuilder/core/model.py
CHANNELS: list[str]
DEFAULT_LABELS: dict[str,str]          # {"350":"DAPI","488":"488","555":"555","647":"647","merge":"Merge"}

@dataclass class ImageFile: path:str; source_id:str; section:str; channel:str; width:int; height:int; unnumbered:bool
@dataclass class Sample:
    source_id:str; section:str; unnumbered:bool
    files: dict[str, ImageFile]        # channel -> file
    meta: dict[str,str]                # treatment, replicate, rep_n, extras...  (set by csvmap; defaults treatment="(unassigned)")
    labels: dict[str,str]              # channel -> display label
    pixel_size_um: float|None
    excluded: bool = False
    @property key -> str               # "Image_01|.07"

# slidebuilder/core/parse.py
DEFAULT_PATTERN: str
@dataclass class ScanResult: folder:str; files:list[ImageFile]; samples:list[Sample]; unparsed:list[str]; warnings:list[str]; channels_present:list[str]; distinct_sizes:list[tuple[int,int]]
def scan_folder(folder:str, pattern:str|None=None, progress=None) -> ScanResult

# slidebuilder/core/csvmap.py
@dataclass class CsvTable: path:str; columns:list[str]; rows:list[dict[str,str]]
@dataclass class CsvMapping: source_id:str|None; treatment:str|None; replicate:str|None; labels:dict[str,str]  # channel->column; pixel_size:str|None; extras:list[str]
@dataclass class MatchReport: matched:list[str]; images_without_row:list[str]; rows_without_images:list[str]; duplicates:list[str]; warnings:list[str]
def load_csv(path:str) -> CsvTable
def auto_map(table:CsvTable) -> CsvMapping
def apply_csv(scan:ScanResult, table:CsvTable, mapping:CsvMapping) -> MatchReport   # mutates scan.samples' meta/labels/pixel_size_um/rep_n
def treatments(samples) -> list[str]            # in first-seen sorted order; GUI may reorder and pass order to LayoutSpec

# slidebuilder/core/layout.py
@dataclass class LayoutSpec:
    mode:str="grid"                    # "grid" | "flow"
    slide_per:list[str]=[]; rows:str|None=None; cols:str|None=None
    flow_rows:int|None=None; flow_cols:int|None=None; flow_order:str="channel_major"  # or "sample_major"
    filters:dict[str,str]={}
    channels:list[str]=[]              # selected, in display order
    treatment_order:list[str]=[]
    def to_dict(self)->dict; @staticmethod def from_dict(d)->LayoutSpec
@dataclass class Preset: id:str; name:str; description:str
PRESETS: list[Preset]                  # ids: "sample_panel","channel_strip","chan_by_rep","treat_by_rep","chan_by_treat","section_cmp","contact_sheet","big_image"
def preset_spec(preset_id:str, samples:list[Sample], channels:list[str]) -> LayoutSpec
def available_dimensions(samples:list[Sample]) -> list[str]      # for Custom dropdowns
@dataclass class Cell: row:int; col:int; sample:Sample|None; channel:str|None; label:str
@dataclass class SlidePlan: title:str; n_rows:int; n_cols:int; row_headers:list[str]; col_headers:list[str]; cells:list[Cell]
def plan_slides(samples:list[Sample], spec:LayoutSpec) -> list[SlidePlan]   # skips excluded samples

# slidebuilder/core/geometry.py
@dataclass class ScaleBarOptions: pixel_size_um:float|None=None; length_um:float|None=None  # None=auto
    position:str="bottom_right"; color:str="#FFFFFF"; show_label:bool=True
@dataclass class ExportOptions:
    slide_size:str="16:9"; background:str="#000000"; label_pt:int=12; title_pt:int=14
    jpeg_quality:int=90; max_px:int=1600; match_size_within_slide:bool=False; notes:bool=False
    gap_in:float=0.05; scale_bar:ScaleBarOptions=...; channel_colors:dict[str,str]={}
    def to_dict / from_dict
@dataclass class Placed: kind:str  # "image"|"label"|"title"|"header"|"bar"|"bar_label"|"missing"
    x:float; y:float; w:float; h:float; text:str=""; path:str=""; color:str=""; size_pt:int=0
def layout_slide(plan:SlidePlan, opts:ExportOptions) -> tuple[float,float,list[Placed]]   # slide w,h in inches + shapes

# slidebuilder/core/thumbs.py
def get_thumbnail(path:str, max_px:int=256) -> PIL.Image.Image            # disk-cached (%LOCALAPPDATA%/SlideBuilder/cache)
def render_preview(plan:SlidePlan, opts:ExportOptions, width_px:int=960) -> PIL.Image.Image

# slidebuilder/core/export.py
def estimate(plans:list[SlidePlan], opts:ExportOptions) -> tuple[int,float]   # (approx bytes, approx seconds)
def export_pptx(plans, opts, out_path:str, progress=None, cancel=None) -> list[str]   # progress(done,total); cancel()->bool; returns warnings
```
`progress` callbacks are `Callable[[int,int],None]`.
