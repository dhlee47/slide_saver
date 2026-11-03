from __future__ import annotations
import os
import re
from dataclasses import dataclass, field
from PIL import Image
from .model import CHANNELS, ImageFile, Sample, assign_rep_n, sample_sort_key

DEFAULT_PATTERN = (r"^Image(?:_(?P<id>\d+))?(?:_(?P<section>\d*\.\d+))?"
                   r"_(?P<channel>350|488|555|647|merge)(?:_1x)?\.tiff?$")


@dataclass
class ScanResult:
    folder: str
    files: list[ImageFile] = field(default_factory=list)
    samples: list[Sample] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    channels_present: list[str] = field(default_factory=list)
    distinct_sizes: list[tuple[int, int]] = field(default_factory=list)


def scan_folder(folder: str, pattern: str | None = None, progress=None) -> ScanResult:
    rx = re.compile(pattern or DEFAULT_PATTERN, re.IGNORECASE)
    res = ScanResult(folder=folder)
    names = sorted(os.listdir(folder))
    samples: dict[tuple, Sample] = {}
    for i, name in enumerate(names):
        if progress:
            progress(i, len(names))
        full = os.path.join(folder, name)
        if not os.path.isfile(full):
            continue
        m = rx.match(name)
        if not m:
            res.unparsed.append(name)
            continue
        g = m.groupdict()
        ch = (g.get("channel") or "").lower()
        if ch not in CHANNELS:
            res.unparsed.append(name)
            continue
        num = g.get("id")
        unnumbered = num is None
        n = int(num) if num else 0
        sid = f"Image_{n:02d}"
        section = g.get("section") or ""
        try:
            with Image.open(full) as im:
                w, h = im.size
        except Exception as e:  # unreadable image
            res.warnings.append(f"{name}: cannot read ({e})")
            res.unparsed.append(name)
            continue
        f = ImageFile(full, sid, section, ch, w, h, unnumbered)
        s = samples.setdefault((sid, section), Sample(sid, section, unnumbered))
        if ch in s.files:
            res.warnings.append(f"{name}: duplicate of {os.path.basename(s.files[ch].path)}; ignored")
            continue
        s.files[ch] = f
        res.files.append(f)
    if progress:
        progress(len(names), len(names))
    res.samples = sorted(samples.values(), key=sample_sort_key)
    res.channels_present = [c for c in CHANNELS if any(c in s.files for s in res.samples)]
    res.distinct_sizes = sorted({(f.width, f.height) for f in res.files})
    for s in res.samples:
        miss = [c for c in res.channels_present if c not in s.files]
        if miss:
            res.warnings.append(f"{s.title}: missing channel(s) {', '.join(miss)}")
    assign_rep_n(res.samples)
    return res
