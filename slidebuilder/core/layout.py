from __future__ import annotations
import math
import re
from dataclasses import asdict, dataclass, field, fields
from .model import Sample, DEFAULT_LABELS, assign_rep_n, sample_sort_key, section_sort, treatment_labels

STD_DIMS = ["channel", "treatment", "replicate", "rep_n", "section", "sample"]
# order in which dimensions are added to split slides when a grid would otherwise collide
SPLIT_CANDIDATES = ["section", "treatment", "rep_n", "replicate", "sample", "channel"]


@dataclass
class LayoutSpec:
    mode: str = "grid"  # "grid" | "flow"
    slide_per: list[str] = field(default_factory=list)
    rows: str | None = None
    cols: str | None = None
    flow_rows: int | None = None
    flow_cols: int | None = None
    flow_order: str = "channel_major"  # or "sample_major"
    filters: dict[str, str] = field(default_factory=dict)
    channels: list[str] = field(default_factory=list)
    treatment_order: list[str] = field(default_factory=list)
    transpose: bool = False  # grid mode: swap rows and columns
    rotation: int = 0  # rotate every image clockwise by 0/90/180/270
    tile_ids: bool = False  # grid mode: print the sample id on each tile

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "LayoutSpec":
        names = {f.name for f in fields(LayoutSpec)}
        return LayoutSpec(**{k: v for k, v in d.items() if k in names})


@dataclass
class Preset:
    id: str
    name: str
    description: str


PRESETS = [
    Preset("sample_panel", "Sample panel", "One slide per sample; all channels in a grid (e.g. 488 NW / 555 NE / 647 SW / merge SE)."),
    Preset("channel_strip", "Channel strip", "One slide per sample; channels in a single row."),
    Preset("chan_by_rep", "Channels × replicates, per treatment", "One slide per treatment: rows = channels, columns = replicates."),
    Preset("treat_by_rep", "Treatments × replicates, per channel", "One slide per channel: rows = treatments, columns = replicates."),
    Preset("chan_by_treat", "Channels × treatments, per replicate #", "One slide per replicate number: rows = channels, columns = treatments."),
    Preset("section_cmp", "Section comparison", "Rows = sections, columns = treatments; one slide per channel and replicate #."),
    Preset("contact_sheet", "Merge overview (contact sheet)", "Merge images only, grouped by treatment, several per slide."),
    Preset("big_image", "One big image per slide", "Every selected image on its own slide."),
]


def preset_spec(preset_id: str, samples: list[Sample], channels: list[str]) -> LayoutSpec:
    from .csvmap import treatments
    base = dict(channels=list(channels), treatment_order=treatments(samples))
    if preset_id == "sample_panel":
        return LayoutSpec(mode="flow", slide_per=["sample"], **base)
    if preset_id == "channel_strip":
        return LayoutSpec(mode="flow", slide_per=["sample"], flow_rows=1, flow_cols=max(1, len(channels)), rotation=90, **base)
    if preset_id == "chan_by_rep":
        return LayoutSpec(slide_per=["treatment", "section"], rows="channel", cols="replicate", **base)
    if preset_id == "treat_by_rep":
        return LayoutSpec(slide_per=["channel", "section"], rows="treatment", cols="replicate", **base)
    if preset_id == "chan_by_treat":
        return LayoutSpec(slide_per=["rep_n", "section"], rows="channel", cols="treatment", **base)
    if preset_id == "section_cmp":
        return LayoutSpec(slide_per=["channel", "rep_n"], rows="section", cols="treatment", **base)
    if preset_id == "contact_sheet":
        ch = ["merge"] if "merge" in channels else list(channels[:1])
        base["channels"] = ch
        return LayoutSpec(mode="flow", slide_per=["treatment"], flow_rows=3, flow_cols=4, flow_order="sample_major", **base)
    if preset_id == "big_image":
        return LayoutSpec(mode="flow", slide_per=[], flow_rows=1, flow_cols=1, flow_order="sample_major", **base)
    raise ValueError(f"Unknown preset {preset_id}")


def available_dimensions(samples: list[Sample]) -> list[str]:
    extras: list[str] = []
    for s in samples:
        for k in s.meta:
            if k not in STD_DIMS and k not in extras and k != "treatment_label":
                extras.append(k)
    return STD_DIMS + extras


@dataclass
class Cell:
    row: int
    col: int
    sample: Sample | None
    channel: str | None
    label: str = ""


@dataclass
class SlidePlan:
    title: str
    n_rows: int
    n_cols: int
    row_headers: list[str] = field(default_factory=list)
    col_headers: list[str] = field(default_factory=list)
    cells: list[Cell] = field(default_factory=list)
    rotation: int = 0


# ---------------------------------------------------------------- helpers
def _val(item, dim: str) -> str:
    s, ch = item
    if dim == "channel":
        return ch
    if dim == "sample":
        return s.key
    if dim == "section":
        return s.section
    if dim == "replicate":  # stacked by position within the group, so alignment ignores replicate names
        dim = "rep_n"
    return str(s.meta.get(dim, ""))


def _natural(v: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", v)]


def _sorter(dim: str, spec: LayoutSpec, samples: list[Sample]):
    sidx = {s.key: i for i, s in enumerate(samples)}
    if dim == "channel":
        return lambda v: spec.channels.index(v) if v in spec.channels else 99
    if dim == "treatment":
        return lambda v: (spec.treatment_order.index(v) if v in spec.treatment_order else 999, _natural(v))
    if dim == "section":
        return section_sort
    if dim in ("rep_n", "replicate"):
        return lambda v: int(v) if v.isdigit() else 0
    if dim == "sample":
        return lambda v: sidx.get(v, 0)
    return _natural


def _display(dim: str, v: str, tl: dict | None = None) -> str:
    if dim == "treatment":
        return (tl or {}).get(v, v)
    if dim == "section":
        return v or "(no section)"
    if dim == "sample":
        return v.replace("|", " ").strip()
    if dim in ("rep_n", "replicate"):
        return f"Rep {v}"
    return v or "(blank)"


def _channel_text(ch: str, items) -> str:
    labs = {s.label(ch) for s, c in items if c == ch}
    if len(labs) != 1:
        return DEFAULT_LABELS.get(ch, ch) if ch == "merge" else ch
    lab = next(iter(labs))
    return ch if lab == ch else (lab if ch == "merge" else f"{lab} ({ch})")


def _header(dim: str, v: str, items, tl=None) -> str:
    return _channel_text(v, items) if dim == "channel" else _display(dim, v, tl)


def _title(dims: list[str], key: tuple, items, tl=None) -> str:
    parts = []
    for d, v in zip(dims, key):
        if d == "channel":
            parts.append(_channel_text(v, items))
        elif d == "sample":
            s = next(s for s, _ in items if s.key == v)
            parts += [s.source_id, *([s.section] if s.section else []), (tl or {}).get(s.meta.get("treatment", ""), s.meta.get("treatment", ""))]
        else:
            parts.append(_display(d, v, tl))
    seen: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.append(p)
    return " · ".join(seen)


def _group(items, dims, spec, samples):
    groups: dict[tuple, list] = {}
    for it in items:
        groups.setdefault(tuple(_val(it, d) for d in dims), []).append(it)
    sorts = [_sorter(d, spec, samples) for d in dims]
    return sorted(groups.items(), key=lambda kv: tuple(f(k) for f, k in zip(sorts, kv[0])))


# ---------------------------------------------------------------- planning
def plan_slides(samples: list[Sample], spec: LayoutSpec) -> list[SlidePlan]:
    samples = [s for s in sorted(samples, key=sample_sort_key) if not s.excluded]
    assign_rep_n(samples)
    items = [(s, c) for s in samples for c in spec.channels]
    for d, v in spec.filters.items():
        items = [it for it in items if _val(it, d) == v]
    if not items:
        return []
    return _plan_flow(items, spec, samples) if spec.mode == "flow" else _plan_grid(items, spec, samples)


def _plan_grid(items, spec, samples):
    rows_dim, cols_dim = (spec.cols, spec.rows) if spec.transpose else (spec.rows, spec.cols)
    ax_dim = {"rows": rows_dim, "cols": cols_dim}
    dims = list(spec.slide_per)
    used = set(dims) | {d for d in (rows_dim, cols_dim) if d}
    extras = [d for d in available_dimensions(samples) if d not in STD_DIMS]
    tl = treatment_labels(samples)
    sidx = {s.key: i for i, s in enumerate(samples)}
    refine = {"rows": False, "cols": False}  # True: axis key also includes the sample (same value on >1 sample)

    def axis_key(it, which):
        dim = ax_dim[which]
        if not dim:
            return ("", "")
        return (_val(it, dim), it[0].key if refine[which] else "")

    def colliding(dims_):
        out = []
        for _, g in _group(items, dims_, spec, samples):
            keys = [(axis_key(it, "rows"), axis_key(it, "cols")) for it in g]
            if len(set(keys)) < len(keys):
                out.append(g)
        return out

    for which in ("cols", "rows"):
        if colliding(dims) and ax_dim[which] and ax_dim[which] not in ("channel", "sample"):
            refine[which] = True
    while True:
        bad = colliding(dims)
        if not bad:
            break
        for cand in SPLIT_CANDIDATES + extras:
            if cand in used or cand in dims:
                continue
            if any(len({_val(it, cand) for it in g}) > 1 for g in bad):
                dims.append(cand)
                break
        else:
            break

    def axis(g, which):
        dim = ax_dim[which]
        if not dim:
            return [("", "")], []
        sort = _sorter(dim, spec, samples)
        # keys come from ALL items, so every slide has the same rows/columns and groups with fewer
        # members (e.g. fewer replicates) leave blank spots instead of shifting the alignment
        src = g if refine[which] else items
        keys = sorted({axis_key(it, which) for it in src}, key=lambda t: (sort(t[0]), sidx.get(t[1], 0)))
        heads = []
        for base, sk in keys:
            h = _header(dim, base, g, tl)
            heads.append(h + (f" \u00b7 {sk.replace('|', ' ').strip()}" if sk else ""))
        return keys, heads

    plans = []
    for key, g in _group(items, dims, spec, samples):
        rkeys, rheads = axis(g, "rows")
        ckeys, cheads = axis(g, "cols")
        plan = SlidePlan(_title(dims, key, g, tl), len(rkeys), len(ckeys), rheads, cheads, rotation=spec.rotation)
        ri = {k: i for i, k in enumerate(rkeys)}
        ci = {k: i for i, k in enumerate(ckeys)}
        bare = not rows_dim and not cols_dim
        for it in g:
            s, ch = it
            label = f"{s.title} \u00b7 {_channel_text(ch, [it])}" if bare else (s.title if spec.tile_ids else "")
            plan.cells.append(Cell(ri[axis_key(it, "rows")], ci[axis_key(it, "cols")], s, ch, label))
        plans.append(plan)
    return plans


def _auto_grid(n: int) -> tuple[int, int]:
    """(rows, cols) for n tiles."""
    if n <= 1:
        return 1, 1
    if n == 2:
        return 1, 2
    if n <= 4:
        return 2, 2
    if n <= 6:
        return 2, 3
    cols = math.ceil(math.sqrt(n))
    return math.ceil(n / cols), cols


def _plan_flow(items, spec, samples):
    tl = treatment_labels(samples)
    sidx = {s.key: i for i, s in enumerate(samples)}
    cidx = {c: i for i, c in enumerate(spec.channels)}
    groups = _group(items, spec.slide_per, spec, samples) if spec.slide_per else [((), items)]
    if spec.flow_order == "sample_major":
        order = lambda it: (sidx[it[0].key], cidx.get(it[1], 0))
    else:
        order = lambda it: (cidx.get(it[1], 0), sidx[it[0].key])
    groups = [(k, sorted(g, key=order)) for k, g in groups]
    maxn = max(len(g) for _, g in groups)
    if spec.flow_rows and spec.flow_cols:
        rows, cols = spec.flow_rows, spec.flow_cols
    elif spec.flow_rows:
        rows, cols = spec.flow_rows, max(1, math.ceil(maxn / spec.flow_rows))
    elif spec.flow_cols:
        cols, rows = spec.flow_cols, max(1, math.ceil(maxn / spec.flow_cols))
    else:
        rows, cols = _auto_grid(maxn)
    per = rows * cols
    plans = []
    for key, g in groups:
        pages = [g[i:i + per] for i in range(0, len(g), per)]
        for pi, page in enumerate(pages):
            multi_s = len({s.key for s, _ in page}) > 1
            multi_c = len({c for _, c in page}) > 1
            title = _title(spec.slide_per, key, g, tl) if spec.slide_per else ""
            if not title and len(page) == 1:
                s, c = page[0]
                title = " · ".join([s.source_id, *([s.section] if s.section else []), tl.get(s.meta.get("treatment", ""), s.meta.get("treatment", "")), _channel_text(c, page)])
            if len(pages) > 1:
                title += f" ({pi + 1}/{len(pages)})"
            plan = SlidePlan(title, rows, cols, rotation=spec.rotation)
            for i, it in enumerate(page):
                s, c = it
                ch_txt = _channel_text(c, [it])
                label = (s.title + (f" · {ch_txt}" if multi_c else "")) if multi_s else ch_txt
                plan.cells.append(Cell(i // cols, i % cols, s, c, label))
            plans.append(plan)
    return plans
