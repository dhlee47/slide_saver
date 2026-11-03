from __future__ import annotations
import re
from dataclasses import dataclass, field

CHANNELS = ["350", "488", "555", "647", "merge"]
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
    try:
        return (0, float(section))
    except ValueError:
        return (0, 1e9)


def sample_sort_key(s: Sample) -> tuple:
    return (id_number(s.source_id), section_sort(s.section))


def assign_rep_n(samples: list[Sample]) -> None:
    """rep_n = 1-based index of the replicate label within (treatment, section), in sample order."""
    seen: dict[tuple, list[str]] = {}
    for s in sorted(samples, key=sample_sort_key):
        reps = seen.setdefault((s.meta["treatment"], s.section), [])
        rep = s.meta["replicate"]
        if rep not in reps:
            reps.append(rep)
        s.meta["rep_n"] = str(reps.index(rep) + 1)
