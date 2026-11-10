from __future__ import annotations
import re
from dataclasses import dataclass, field

CHANNELS = ["350", "488", "555", "647", "merge"]
MERGE_ALIASES = {"merge", "merged", "overlay", "composite"}


def channel_sort_key(ch: str) -> tuple:
    """Wavelengths ascending, other names alphabetical, merge always last."""
    return (ch == "merge", 0 if ch.isdigit() else 1, int(ch) if ch.isdigit() else 0, ch.lower())


DEFAULT_LABELS = {"350": "DAPI", "488": "488", "555": "555", "647": "647", "merge": "Merge"}


@dataclass
class ImageFile:
    path: str
    source_id: str
    section: str
    channel: str
    width: int = 0
    height: int = 0
    unnumbered: bool = False


@dataclass
class Sample:
    source_id: str
    section: str = ""
    unnumbered: bool = False
    files: dict[str, ImageFile] = field(default_factory=dict)
    meta: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_LABELS))
    pixel_size_um: float | None = None
    excluded: bool = False

    def __post_init__(self):
        self.meta.setdefault("treatment", "(unassigned)")
        self.meta.setdefault("replicate", self.source_id)

    @property
    def key(self) -> str:
        return f"{self.source_id}|{self.section}"

    @property
    def title(self) -> str:
        return f"{self.source_id} {self.section}".strip()

    def label(self, channel: str) -> str:
        return self.labels.get(channel) or DEFAULT_LABELS.get(channel, channel)


def id_number(source_id: str) -> int:
    m = re.search(r"\d+", source_id)
    return int(m.group()) if m else 0


def section_sort(section: str) -> tuple:
    if not section:
        return (1, 0.0)
    m = re.match(r"\d*\.?\d+", section)
    return (0, float(m.group())) if m else (0, 1e9)


def sample_sort_key(s: Sample) -> tuple:
    return (id_number(s.source_id), section_sort(s.section))


def assign_rep_n(samples: list[Sample]) -> None:
    """rep_n = 1-based position of the sample within its (treatment, section) group, in sample order.
    Replicates are stacked by position (names are ignored) so groups line up regardless of labels."""
    count: dict[tuple, int] = {}
    for s in sorted(samples, key=sample_sort_key):
        k = (s.meta["treatment"], s.section)
        count[k] = count.get(k, 0) + 1
        s.meta["rep_n"] = str(count[k])


def treatment_labels(samples: list[Sample]) -> dict[str, str]:
    """treatment key (e.g. group number) -> text shown on slides (CSV description), falling back to the key."""
    out: dict[str, str] = {}
    for s in samples:
        t = s.meta.get("treatment", "(unassigned)")
        out.setdefault(t, s.meta.get("treatment_label") or t)
    return out
