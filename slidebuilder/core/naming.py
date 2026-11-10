"""Build a filename regex from parts the user highlighted in an example file name."""
from __future__ import annotations
import re

TAGS = ("id", "section", "channel", "ignore")  # section = section / magnification / any second-level token
_SEPARATORS = "_-. "
_WORD = r"[A-Za-z0-9.+\-]+"  # one token of a name: no underscore, so tokens stay separable


def _tag_of(key: str) -> str:
    return key.rstrip("0123456789")  # "ignore2" -> "ignore" (several ignored parts are allowed)


def _value_regex(tag: str, text: str) -> str:
    if tag == "id":
        return r"\d+" if text.isdigit() else r"[A-Za-z0-9]+"
    if tag == "section":
        # covers ".07", "3.6", "1x", "20x": numbers, optionally a decimal part or a letter suffix
        return r"(?:\d*\.\d+|\d+[A-Za-z]*)" if re.fullmatch(r"[\d.]+[A-Za-z]*", text) else r"[A-Za-z0-9.]+"
    return _WORD  # channel (e.g. 555, DAPI, Cy5.5) or an ignored part


def _literal_regex(text: str) -> str:
    """Untagged text: words stay literal, but tokens containing a digit (G1, S21, ROI1, 20x) usually vary
    between files, so they match any alphanumeric token."""
    out = []
    for part in re.split(r"([_\-. ]+)", text):
        if part and re.fullmatch(r"[A-Za-z0-9+]+", part) and re.search(r"\d", part):
            out.append(r"[A-Za-z0-9+]+")
        else:
            out.append(re.escape(part))
    return "".join(out)


def build_pattern(example: str, spans: dict[str, tuple[int, int]]) -> str:
    """example: a file name including extension; spans: tag -> (start, end) character offsets in it.
    Keys are "id", "section", "channel" and any number of "ignore", "ignore2", ... (parts that vary and
    are not needed, e.g. a group prefix).

    Tagged parts become named groups. id, section and ignored parts are optional together with their
    leading separator, so files without them still match. Extra tokens before the channel (a tile or
    z number, say) are tolerated. Everything else is matched literally."""
    if "channel" not in spans:
        raise ValueError("Highlight the channel part of the name")
    items = sorted((s, e, k) for k, (s, e) in spans.items() if _tag_of(k) in TAGS and e > s)
    for (s1, e1, _), (s2, _e2, _) in zip(items, items[1:]):
        if s2 < e1:
            raise ValueError("Highlighted parts overlap")
    out, pos = [], 0
    for start, end, key in items:
        tag = _tag_of(key)
        literal = example[pos:start]
        sep = ""
        if literal and literal[-1] in _SEPARATORS:
            if tag != "channel":
                literal, sep = literal[:-1], literal[-1]
            elif literal[-1] in "_-":
                # tolerate extra tokens (e.g. "_20") between the preceding text and the channel; lazy, so
                # the shortest reading wins
                literal, sep = literal[:-1], literal[-1]
        out.append(_literal_regex(literal))
        value = _value_regex(tag, example[start:end])
        if tag == "channel":
            out.append((f"(?:{re.escape(sep)}{_WORD})*?{re.escape(sep)}" if sep else "") + f"(?P<channel>{value})")
        elif tag == "ignore":
            out.append(f"(?:{re.escape(sep)}(?P<{key}>{value}))??" if sep else f"(?P<{key}>{value})")  # lazy: prefer not to swallow the next part
        else:
            out.append(f"(?:{re.escape(sep)}(?P<{tag}>{value}))?")
        pos = end
    tail = example[pos:]
    m = re.search(r"\.(?:tiff?|png|jpe?g)$", tail, re.IGNORECASE)  # any supported image type matches
    out.append(_literal_regex(tail[:m.start()]) + r"\.(?:tiff?|png|jpe?g)" if m else _literal_regex(tail))
    return "^" + "".join(out) + "$"
