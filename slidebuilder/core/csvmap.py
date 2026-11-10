from __future__ import annotations
import csv
import re
from dataclasses import dataclass, field
from .model import DEFAULT_LABELS, assign_rep_n, id_number, sample_sort_key


STAIN_RX = re.compile(r"^(.*?)\s*\(([A-Za-z0-9]+)\)\s*$")


@dataclass
class CsvTable:
    path: str
    columns: list[str]
    rows: list[dict[str, str]]


@dataclass
class CsvMapping:
    source_id: str | None = None
    treatment: str | None = None
    replicate: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    pixel_size: str | None = None
    extras: list[str] = field(default_factory=list)
    treatment_label: str | None = None  # text shown on slides for each treatment/group


@dataclass
class MatchReport:
    matched: list[str] = field(default_factory=list)
    images_without_row: list[str] = field(default_factory=list)
    rows_without_images: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def load_csv(path: str) -> CsvTable:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            with open(path, newline="", encoding=enc) as f:
                rd = csv.DictReader(f)
                rows = [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in rd]
                return CsvTable(path, [c.strip() for c in (rd.fieldnames or [])], rows)
        except UnicodeDecodeError:
            continue
    raise ValueError("Cannot decode CSV")


def _find(cols, names):
    low = {c.lower().replace(" ", "_"): c for c in cols}
    for n in names:
        if n in low:
            return low[n]
    return None


def auto_map(table: CsvTable, channels: list[str] | None = None) -> CsvMapping:
    cols = table.columns
    m = CsvMapping()
    m.source_id = _find(cols, ["source_id", "slide_id", "slide", "image_id", "image", "vsi", "id", "source", "file"])
    m.treatment = _find(cols, ["treatment", "treatment_group", "group_id", "group", "condition"])
    m.replicate = _find(cols, ["replicate", "sample_id", "sample", "animal", "animal_id", "subject", "mouse", "rep"])
    chans = [c for c in (channels or ["350", "488", "555", "647"]) if c != "merge"]
    for c in cols:
        name = c.strip().lower()
        for ch in chans:
            if name == ch.lower() or (ch.isdigit() and name.startswith(ch)):
                m.labels.setdefault(ch, c)
    for c in cols:  # a column whose cells look like "bAmyloid (555)" labels that channel
        for r in table.rows:
            mm = STAIN_RX.match(r.get(c, ""))
            if mm and mm.group(2) in chans:
                m.labels.setdefault(mm.group(2), c)
                break
    m.treatment_label = _find(cols, ["description", "treatment_label", "group_name", "group_description"])
    m.pixel_size = next((c for c in cols if re.search(r"pixel|um_per|µm", c.lower())), None)
    used = {m.source_id, m.treatment, m.replicate, m.pixel_size, m.treatment_label, *m.labels.values()}
    m.extras = [c for c in cols if c not in used]
    return m


def normalize_id(raw: str) -> str | None:
    """'Image_01','01','1' -> 'Image_01'; blank/0 -> 'Image_00'; other ids are kept as written."""
    raw = (raw or "").strip()
    t = re.sub(r"^image|[_\s-]", "", raw, flags=re.IGNORECASE)
    if t == "":
        return "Image_00"
    if t.isdigit():
        return f"Image_{int(t):02d}"
    return raw  # non-numeric ids (e.g. "M12") must match the file name part exactly


def apply_csv(scan, table: CsvTable, mapping: CsvMapping) -> MatchReport:
    rep = MatchReport()
    rows: dict[str, dict] = {}
    for r in table.rows:
        raw = r.get(mapping.source_id, "") if mapping.source_id else ""
        sid = normalize_id(raw)
        if sid is None:
            rep.warnings.append(f"Unrecognised source_id '{raw}'")
            continue
        if sid in rows:
            rep.duplicates.append(sid)
            continue
        rows[sid] = r
    by_num: dict[int, list[str]] = {}
    for k in rows:
        by_num.setdefault(id_number(k), []).append(k)
    used = set()
    for s in scan.samples:
        key = s.source_id if s.source_id in rows else None
        if key is None:  # "S21" in the files vs "21" / "Image_21" in the CSV: fall back to the number
            cand = by_num.get(id_number(s.source_id), [])
            key = cand[0] if len(cand) == 1 else None
        r = rows.get(key) if key else None
        if r is None:
            rep.images_without_row.append(s.title)
            s.meta = {"treatment": "(unassigned)", "replicate": s.source_id}
            s.labels = dict(DEFAULT_LABELS)
            s.pixel_size_um = None
            continue
        used.add(key)
        rep.matched.append(s.title)
        meta = {"treatment": (r.get(mapping.treatment, "") if mapping.treatment else "") or "(unassigned)",
                "replicate": (r.get(mapping.replicate, "") if mapping.replicate else "") or s.source_id}
        if mapping.treatment_label:
            meta["treatment_label"] = r.get(mapping.treatment_label, "")
        for c in mapping.extras:
            meta[c] = r.get(c, "")
        s.meta = meta
        s.labels = dict(DEFAULT_LABELS)
        for ch, col in mapping.labels.items():
            val = r.get(col, "")
            mm = STAIN_RX.match(val)
            if mm and mm.group(2) == ch:  # "marker (channel)": use the marker name
                val = mm.group(1)
            elif mm and mm.group(2) in scan.channels_present:
                val = ""
            if val:
                s.labels[ch] = val
        s.pixel_size_um = None
        if mapping.pixel_size and r.get(mapping.pixel_size):
            try:
                s.pixel_size_um = float(r[mapping.pixel_size])
            except ValueError:
                rep.warnings.append(f"{s.title}: bad pixel size '{r[mapping.pixel_size]}'")
    first: dict[str, str] = {}  # one display text per treatment: the first description seen in the group
    for s in scan.samples:
        t, lab = s.meta["treatment"], s.meta.get("treatment_label", "")
        if lab and t not in first:
            first[t] = lab
        elif lab and lab != first[t] and f"Group {t}" not in " ".join(rep.warnings):
            rep.warnings.append(f"Group {t}: descriptions differ; showing '{first[t]}'")
    for s in scan.samples:
        if s.meta["treatment"] in first:
            s.meta["treatment_label"] = first[s.meta["treatment"]]
        else:
            s.meta.pop("treatment_label", None)
    rep.rows_without_images = [k for k in rows if k not in used]
    assign_rep_n(scan.samples)
    return rep


def treatments(samples) -> list[str]:
    out: list[str] = []
    for s in sorted(samples, key=sample_sort_key):
        t = s.meta.get("treatment", "(unassigned)")
        if t not in out:
            out.append(t)
    return out
