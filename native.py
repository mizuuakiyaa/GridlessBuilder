"""Loader for UntitledCharts (UnCh) exports.

An export is a .zip (or several, one folder per difficulty) with no builder-made
ChartInfo.json but instead:

    level.json                    song / chart metadata
    NSLevelData.json(.gz)         the chart (NextSEKAI level data)
    music.mp3 | .ogg | ...        full song
    preview.mp3 | .ogg | ...      preview (optional; cut automatically when missing or too long)
    jacket.png | .jpg | ...       cover art

unch.py does the real work (level data -> .sus, one chart per difficulty, music offset,
identifier). load() wraps its result into the same dict core.load_project() returns for
a normal project, asking for (or warning about) the fields level.json does not have, so
everything after that (preview cutting, combo check, compiling) is shared.
"""

from __future__ import annotations

import os

import core

PLACEHOLDER = "-"  # what the game gets for credits an export does not carry


def load(zip_path: str, workdir: str, overrides: dict | None = None, ask=None) -> dict | None:
    """Build the project dict from an already-extracted export, or return None if
    `workdir` holds no UnCh export (level.json + NSLevelData.json[.gz]).

    `ask(name, default) -> str` is called for every field the export lacks and no
    override gives; without it the default is used and a warning is recorded."""
    ov = {k: v for k, v in (overrides or {}).items() if v is not None and v != ""}

    try:
        import unch
    except ImportError as e:
        raise ValueError(f"unch.py could not be loaded ({e}).") from None
    if not unch.find_all_unch(workdir):
        return None

    try:
        cfg = unch.build_cfg(workdir, os.path.join(workdir, "_converted"))
    except unch.UnchError as e:
        raise ValueError(str(e)) from None
    except ImportError as e:
        raise ValueError(
            "converting UntitledCharts data needs sonolus-level-converters "
            f"(pip install -U -r requirements.txt): {e}"
        ) from None
    meta = cfg.pop("_unch")
    warnings: list[str] = list(meta["warnings"])
    charts = cfg["charts"]
    single = len(charts) == 1

    def missing(name: str, default: str) -> str:
        """The export has no `name`: ask for it, or keep the default (with a warning)."""
        if ask:
            answer = ask(name, default)
            if answer not in (None, ""):
                return str(answer)
        else:
            warnings.append(f"level.json has no usable {name}; using '{default}' (--{name} to change).")
        return default

    # ---- text fields the export may lack ----------------------------------
    artist_was_missing = "artist" in meta["missing_fields"]
    for name in ("title", "artist", "charter"):
        if name in meta["missing_fields"] and name not in ov:
            cfg[name] = missing(name, cfg[name])
    if artist_was_missing and "vocals" not in ov:
        cfg["vocals"] = cfg["artist"]
    # credits are never in UnCh data
    absent = [k for k in ("lyricist", "composer", "arranger") if k not in ov]
    if ask:
        for k in absent:
            cfg[k] = missing(k, PLACEHOLDER)
    elif absent:
        warnings.append(
            f"{', '.join(absent)} {'is' if len(absent) == 1 else 'are'} not in UnCh data, "
            f"using '{PLACEHOLDER}' (--{absent[0]} / --set {absent[0]}=... to fill in)."
        )

    # ---- difficulty / level of a single chart ------------------------------
    if single:
        c = charts[0]
        if meta["assumed_difficulty"] and "difficulty" not in ov:
            c["difficulty"] = missing("difficulty", c["difficulty"])
        if meta["rating_missing"] and "level" not in ov:
            c["level"] = int(missing("level", str(c["level"])))
    else:
        for d in meta["assumed_difficulty"]:
            warnings.append(f"{d}: no difficulty tag in level.json, assumed '{d}'.")

    # ---- the project dict ---------------------------------------------------
    tr = lambda v: {"jp": str(v).strip(), "en": str(v).strip()}
    info = {
        "identifier": cfg["identifier"],
        "format_version": core.FORMAT_VERSION,
        "title": tr(cfg["title"]),
        "charter": str(cfg["charter"]).strip(),
        "difficulties": [
            {"difficulty": c["difficulty"], "level": c["level"], "notes": None} for c in charts
        ],
        "lyricist": tr(cfg["lyricist"]),
        "composer": tr(cfg["composer"]),
        "arranger": tr(cfg["arranger"]),
        "artist": tr(cfg["artist"]),
        "vocals": tr(cfg["vocals"]),
        "isFullLength": bool(cfg["is_full"]),
        "vocaloid_or_other": "other",
        "offset_ms": int(cfg["offset_ms"]),
        "additional_mv_offset_ms": int(cfg["mv_offset_ms"]),
        "song_duration": None,
        "original": None,
        "original_music_video": False,
        "2d_music_video": False,
    }
    data = {
        "info": info,
        "diffs": [
            {"difficulty": c["difficulty"], "level": c["level"], "notes": None, "_path": c["path"]}
            for c in charts
        ],
        "jacket": cfg["jacket"],
        "track": cfg["track"],
        "track_pre": cfg["track_pre"] or None,
        "ogmv": None,
        "2dmv": None,
        "cover_files": [],
        # raw combos counted from the level data, to check the conversion kept every note
        "expected_combos": {c["difficulty"]: n for c, n in zip(charts, meta["expected_combos"])},
    }
    try:
        core.apply_overrides(data, ov)
    except ValueError as e:
        raise ValueError(f"override failed: {e}") from None

    n = len(data["diffs"])
    lines = [
        f"Detected UntitledCharts export: {info['title']['en']} - {info['artist']['en']}",
        f"  {n} chart{'s' if n != 1 else ''}: "
        + ", ".join(f"{c['difficulty']} Lv{c['level']}" for c in data["diffs"]),
        f"  music offset {meta['bgm_offset']:+.3f}s -> offset_ms={info['offset_ms']}",
    ]
    if not cfg["track_pre"]:
        lines.append("  no preview in the export (one will be cut from the music)")
    if meta["ignored"]:
        lines.append(f"  not used by the game mod: {', '.join(meta['ignored'])}")
    data["source_note"] = "\n".join(lines)
    data["warnings"] = warnings
    return data
