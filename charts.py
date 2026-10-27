from __future__ import annotations

import gzip
import io
import json
import os
import re

import compat

CHART_EXTS = (".sus", ".usc", ".json", ".json.gz")
CHART_FILETYPES = [
    ("Chart (SUS / USC / JSON)", "*.sus *.usc *.json *.gz"),
    ("SUS chart", "*.sus"),
    ("USC chart", "*.usc"),
    ("JSON chart (.json / .json.gz)", "*.json *.gz"),
    ("All", "*.*"),
]


def chart_ext(path: str) -> str:
    """Lower-case extension; '.json.gz' counts as one extension."""
    low = path.lower()
    if low.endswith(".json.gz"):
        return ".json.gz"
    return os.path.splitext(low)[1]


def read_text(path: str) -> str:
    """The file's text. Gzip is detected by content, so a gzipped file named .json (or a
    plain one named .json.gz) still loads."""
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8-sig")


def _is_level_data(data) -> bool:
    """UntitledCharts / NextSEKAI level data: {"bgmOffset": ..., "entities": [...]}."""
    return isinstance(data, dict) and isinstance(data.get("entities"), list)


def _load_level_data(data: dict):
    try:
        import unch
    except ImportError as e:
        raise ValueError(f"cannot read UntitledCharts level data ({e})") from None
    try:
        score = unch.level_data_to_score(data)
    except ImportError:
        raise ValueError(
            "reading UntitledCharts level data needs sonolus-level-converters "
            "(pip install -U -r requirements.txt)"
        ) from None
    score.metadata.waveoffset = 0.0  # the music offset goes into offset_ms, not the SUS
    return score


def _load_json(text: str):
    """A .json / .json.gz chart: UntitledCharts level data (converted by unch.py),
    USC JSON, or, with rg_converters, anything its other loaders accept."""
    data = json.loads(text)
    if _is_level_data(data):
        return _load_level_data(data)
    if isinstance(data, dict) and ("usc" in data or "objects" in data):
        if "usc" not in data:  # bare USC body without the {"usc": ..., "version": ...} wrapper
            data = {"usc": data, "version": 2}
        return compat.usc_load(io.StringIO(json.dumps(data)))

    tried: list[str] = []
    for name, load in compat.extra_json_loaders():
        try:
            return load(io.StringIO(text))
        except Exception as e:  # not this format, try the next loader
            tried.append(f"{name}: {e}")
    raise ValueError(
        "unrecognised JSON chart (expected UntitledCharts level data or USC JSON"
        + (f"; also tried {'; '.join(tried)}" if tried else "")
        + ")"
    )


def load_score(path: str):
    ext = chart_ext(path)
    if ext not in CHART_EXTS:
        raise ValueError(
            f"unsupported chart type '{ext}' (use .sus, .usc, .json or .json.gz)"
        )
    text = read_text(path)
    if ext == ".sus":
        return compat.sus_load(io.StringIO(text))
    if ext == ".usc":
        return compat.usc_load(io.StringIO(text))
    return _load_json(text)


def sus_text(path: str) -> str:
    """The chart as SUS text: a .sus as it is, anything else converted with its TIL layers kept."""
    if chart_ext(path) == ".sus":
        return read_text(path)
    buf = io.StringIO()
    compat.export_chart(buf, load_score(path))
    return buf.getvalue()


def level_data_info(path: str) -> tuple[int, float] | None:
    """(combo, music offset in seconds) counted straight from raw UntitledCharts level
    data, as an independent cross-check of the conversion; None for any other chart."""
    if chart_ext(path) not in (".json", ".json.gz"):
        return None
    try:
        data = json.loads(read_text(path))
        if not _is_level_data(data):
            return None
        import unch

        return unch.expected_combo(data), float(data.get("bgmOffset", 0.0) or 0.0)
    except Exception:
        return None


_TIL = re.compile(r"^#TIL(\w+)\s*:", re.IGNORECASE | re.MULTILINE)


def layer_count(text: str) -> int:
    """Number of TIL (timeline / hi-speed layer) definitions in SUS text."""
    return len(set(m.upper() for m in _TIL.findall(text)))


def combo_of_sus(text: str) -> int:
    return int(compat.sus_load(io.StringIO(text)).combo_count)


def combo(path: str) -> int:
    return combo_of_sus(sus_text(path))
