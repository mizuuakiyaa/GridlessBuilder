"""Logic shared by the GUI (gui.py) and the terminal mode (cli.py). No tkinter in here."""

from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass

import charts
import deps
import preview

DIFFICULTIES = ["easy", "normal", "hard", "expert", "master", "append"]
MV_TYPES = ["ogmv", "2dmv"]
VOCAL_TYPES = ["sekai", "original_song", "another_vocal"]
MIN_LEVEL, MAX_LEVEL = 1, 99
MAX_CHARTS = 6
MAX_PREVIEW_SECONDS = 45.0
FORMAT_VERSION = 1

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tga", ".tif", ".tiff"}

Log = Callable[[str], None]


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    flags = 0x08000000 if os.name == "nt" else 0
    return subprocess.run(cmd, capture_output=True, text=True, creationflags=flags)


def media_duration(path: str) -> float | None:
    cp = _run(
        [
            deps.FFPROBE, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
        ]
    )
    if cp.returncode != 0:
        return None
    try:
        return float(cp.stdout.strip())
    except ValueError:
        return None


def has_stream(path: str, kind: str) -> bool:
    cp = _run(
        [
            deps.FFPROBE, "-v", "error", "-select_streams", kind[0],
            "-show_entries", "stream=codec_type", "-of", "csv=p=0", path,
        ]
    )
    return cp.returncode == 0 and kind in cp.stdout


def validate_image(path: str) -> tuple[bool, str]:
    from PIL import Image

    try:
        with Image.open(path) as im:
            im.verify()
        return True, ""
    except Exception as e:
        return False, f"Not a readable image: {e}"


_TIME_SIG_RE = re.compile(r"^#\d{3}02:", re.IGNORECASE)


def validate_sus_text(text: str) -> tuple[bool, str]:
    lines = [ln.strip() for ln in text.splitlines()]
    sig_lines = [ln for ln in lines if _TIME_SIG_RE.match(ln)]
    if len(sig_lines) != 1:
        return (
            False,
            f"must have exactly one time-signature line, found {len(sig_lines)}",
        )
    if not any(ln.replace(" ", "") == "#00002:4" for ln in lines):
        return False, "missing the 4/4 time signature at measure 0 (#00002: 4)"
    return True, ""


def gen_double_uuid() -> str:
    return f"{uuid.uuid4()}_{uuid.uuid4()}"


def validate_double_uuid(s: str) -> bool:
    parts = s.strip().split("_")
    if len(parts) != 2:
        return False
    for p in parts:
        try:
            u = uuid.UUID(p)
        except (ValueError, AttributeError):
            return False
        if u.version != 4 or str(u) != p.lower():
            return False
    return True


def ext_of(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def stem_of(filename: str) -> str:
    """File name without extension; 'expert.json.gz' -> 'expert'."""
    base = os.path.basename(filename)
    if base.lower().endswith(".json.gz"):
        return base[: -len(".json.gz")]
    return os.path.splitext(base)[0]


def find_file(root: str, stem: str) -> str | None:
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__MACOSX"]
        for fn in sorted(files):
            if not fn.startswith("._") and stem_of(fn) == stem:
                return os.path.join(cur, fn)
    return None


def strip_output_suffix(path: str) -> str:
    base, changed = path, True
    while changed:
        changed = False
        for suf in (".chart.gs6", ".gs6", ".zip", ".android", ".ios"):
            if base.lower().endswith(suf):
                base, changed = base[: -len(suf)], True
    return base


def write_zip(out: str, data: dict) -> None:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "ChartInfo.json", json.dumps(data["info"], ensure_ascii=False, indent=2)
        )
        z.write(data["jacket"], "jacket" + ext_of(data["jacket"]))
        z.write(data["track"], "track" + ext_of(data["track"]))
        z.write(data["track_pre"], "track_pre" + ext_of(data["track_pre"]))
        for d in data["diffs"]:
            z.write(d["_path"], d["difficulty"] + charts.chart_ext(d["_path"]))
        if data["ogmv"]:
            z.write(data["ogmv"], "mv" + ext_of(data["ogmv"]))
        if data["2dmv"]:
            z.write(data["2dmv"], "2dmv" + ext_of(data["2dmv"]))
        for src, zip_name in data.get("cover_files", []):
            z.write(src, zip_name)


OVERRIDE_KEYS = (
    "title", "artist", "charter", "vocals", "lyricist", "composer", "arranger",
    "collab", "original", "identifier", "offset_ms", "mv_offset_ms", "is_full",
    "difficulty", "level",
)


def apply_overrides(data: dict, overrides: dict | None) -> None:
    """Apply command-line overrides (--set KEY=VALUE, --title, ...) to a loaded project."""
    ov = {k: v for k, v in (overrides or {}).items() if v is not None and v != ""}
    info = data["info"]
    tr = lambda v: {"jp": str(v).strip(), "en": str(v).strip()}
    for k in ("title", "artist", "lyricist", "composer", "arranger", "vocals", "collab"):
        if k in ov:
            info[k] = tr(ov[k])
    if "charter" in ov:
        info["charter"] = str(ov["charter"]).strip()
    if "original" in ov:
        info["original"] = str(ov["original"]).strip() or None
    if "identifier" in ov:
        if not validate_double_uuid(str(ov["identifier"])):
            raise ValueError("identifier must be two valid v4 UUIDs joined by '_'.")
        info["identifier"] = str(ov["identifier"]).strip()
    if "offset_ms" in ov:
        info["offset_ms"] = int(ov["offset_ms"])
    if "mv_offset_ms" in ov:
        info["additional_mv_offset_ms"] = int(ov["mv_offset_ms"])
    if "is_full" in ov:
        v = ov["is_full"]
        info["isFullLength"] = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "y")
    for k in ("difficulty", "level"):
        if k in ov and len(data["diffs"]) != 1:
            raise ValueError(f"{k} can only be set when the project has exactly one chart.")
    if "level" in ov:
        lvl = int(ov["level"])
        if not (MIN_LEVEL <= lvl <= MAX_LEVEL):
            raise ValueError(f"level must be {MIN_LEVEL}-{MAX_LEVEL}.")
        data["diffs"][0]["level"] = lvl
        for e in info.get("difficulties", []):
            e["level"] = lvl
    if "difficulty" in ov:
        new = str(ov["difficulty"]).lower()
        if new not in DIFFICULTIES:
            raise ValueError(f"difficulty must be one of {', '.join(DIFFICULTIES)}.")
        old = data["diffs"][0]["difficulty"]
        data["diffs"][0]["difficulty"] = new
        for e in info.get("difficulties", []):
            if e.get("difficulty") == old:
                e["difficulty"] = new
        exp = data.get("expected_combos")
        if exp and old in exp:
            exp[new] = exp.pop(old)


def load_project(zip_path: str, workdir: str, overrides: dict | None = None, ask=None) -> dict:
    """Unpack a project .zip into the dict that encode.compile_chart() takes.

    Two layouts are understood: the builder's own ('Build zip...': ChartInfo.json +
    track / track_pre / jacket / <difficulty> files) and a native package (level.json +
    NSLevelData.json[.gz] + music / preview / jacket, an UntitledCharts export, see
    native.py). `overrides` (title, artist, difficulty, level, identifier, ...) are
    applied on top; `ask(name, default)` is how an UntitledCharts export asks for fields
    its level.json lacks."""
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(workdir)
    info_path = None
    for cur, dirs, files in os.walk(workdir):
        dirs[:] = [d for d in dirs if d != "__MACOSX"]
        if "ChartInfo.json" in files:
            info_path = os.path.join(cur, "ChartInfo.json")
            break
    if not info_path:
        import native

        data = native.load(zip_path, workdir, overrides, ask)
        if data is None:
            raise ValueError(
                "neither ChartInfo.json nor level.json found in the zip "
                "(expected a builder project or an UntitledCharts export)."
            )
        return data
    with open(info_path, "r", encoding="utf-8-sig") as f:
        info = json.load(f)
    root = os.path.dirname(info_path)

    def find(stem: str) -> str | None:
        return find_file(root, stem)

    diffs = []
    for d in info.get("difficulties", []):
        name = d.get("difficulty", "")
        path = find(name)
        if not path:
            raise ValueError(
                f"{name}: chart file not found in the zip "
                f"(expected {name}.sus / .usc / .json / .json.gz)."
            )
        try:
            level = int(d.get("level", 1))
        except (TypeError, ValueError):
            raise ValueError(f"{name}: level must be a number.") from None
        diffs.append(
            {"difficulty": name, "level": level, "notes": d.get("notes"), "_path": path}
        )
    if not diffs:
        raise ValueError("ChartInfo.json lists no difficulties.")

    cover_files: list[tuple[str, str]] = []
    for c in info.get("covers", []):
        if c.get("default"):
            continue
        cid = c.get("id")
        for kind in ("cover", "cover_pre", "cover_jacket"):
            p = find(f"{kind}_{cid}")
            if p:
                cover_files.append((p, f"covers/{kind}_{cid}{ext_of(p)}"))

    data = {
        "info": info,
        "diffs": diffs,
        "jacket": find("jacket"),
        "track": find("track") or find("music"),
        "track_pre": find("track_pre") or find("preview"),
        "ogmv": find("mv"),
        "2dmv": find("2dmv"),
        "cover_files": cover_files,
    }
    apply_overrides(data, overrides)
    return data


@dataclass
class Options:
    preview_length: float = preview.DEFAULT_SECONDS
    preview_start: float | None = None  # main preview only; None = loudest window
    preview_file: str | None = None  # use this file as the main preview source
    force_preview: bool = False  # cut even when the preview is already short enough
    auto_preview: bool = True  # cut when the preview is missing or too long
    combo_mode: str = "fix"  # fix | strict | keep
    allow_combo_mismatch: bool = False  # build even if converted level data lost notes


def prepare_project(
    data: dict, workdir: str, opt: Options, log: Log, warn: Log
) -> list[str]:
    """Validate a project loaded by load_project(), cutting previews and
    reconciling combo counts. Mutates `data`; returns a list of errors."""
    errs: list[str] = []
    info = data["info"]
    n_cut = [0]

    note = data.pop("source_note", None)
    if note:
        log(note)
    for w in data.pop("warnings", []):
        warn(w)

    if not validate_double_uuid(str(info.get("identifier") or "")):
        errs.append("Identifier must be two valid v4 UUIDs joined by '_'.")

    jacket, track = data.get("jacket"), data.get("track")
    if not jacket or not os.path.isfile(jacket):
        errs.append("jacket.* is missing from the zip.")
    else:
        ok, msg = validate_image(jacket)
        if not ok:
            errs.append(f"Jacket: {msg}")
    if not track or not os.path.isfile(track):
        errs.append("track.* is missing from the zip.")
    elif not has_stream(track, "audio"):
        errs.append("Audio track has no audio stream.")

    def preview_for(pre, full, label, override=None, start=None):
        if override:
            if not os.path.isfile(override):
                errs.append(f"{label}: preview file '{override}' not found.")
                return pre
            pre = override
        dur = media_duration(pre) if pre and os.path.isfile(pre) else None
        usable = dur is not None
        too_long = usable and dur > MAX_PREVIEW_SECONDS + 0.05
        if usable and not too_long and not opt.force_preview:
            return pre  # already fits (an explicit --preview-file that fits is used as given)
        if not opt.auto_preview:
            errs.append(
                f"{label}: preview is missing, unreadable or longer than "
                f"{MAX_PREVIEW_SECONDS:.0f}s (automatic cutting is off)."
            )
            return pre
        source = pre if usable else full
        if not source or not os.path.isfile(source):
            errs.append(f"{label}: no audio to cut a preview from.")
            return pre
        n_cut[0] += 1
        try:
            path, st, ln = preview.cut_preview(
                source,
                workdir,
                length=opt.preview_length,
                start=start,
                name=f"preview_auto_{n_cut[0]}",
            )
        except Exception as e:
            errs.append(f"{label}: automatic preview cut failed ({e}).")
            return pre
        was = f"{dur:.1f}s long" if usable else "missing"
        log(f"{label}: preview was {was}; cut {ln:.0f}s starting at {st:.1f}s")
        return path

    if track and os.path.isfile(track):
        data["track_pre"] = preview_for(
            data.get("track_pre"), track, "Preview", opt.preview_file, opt.preview_start
        )

    cover_files: list[tuple[str, str]] = data.setdefault("cover_files", [])

    def cover_idx(kind: str, cid) -> int | None:
        for i, (_src, zn) in enumerate(cover_files):
            if zn.startswith(f"covers/{kind}_{cid}."):
                return i
        return None

    for c in info.get("covers", []):
        if c.get("default"):
            continue
        cid = c.get("id")
        ai = cover_idx("cover", cid)
        if ai is None:
            errs.append(f"Alt vocal {cid}: audio file is missing from the zip.")
            continue
        audio = cover_files[ai][0]
        if not has_stream(audio, "audio"):
            errs.append(f"Alt vocal {cid}: audio has no audio stream.")
            continue
        pi = cover_idx("cover_pre", cid)
        old = cover_files[pi][0] if pi is not None else None
        new = preview_for(old, audio, f"Alt vocal {cid} preview")
        if new and new != old:
            entry = (new, f"covers/cover_pre_{cid}{ext_of(new)}")
            if pi is None:
                cover_files.append(entry)
            else:
                cover_files[pi] = entry
        if c.get("has_jacket") and cover_idx("cover_jacket", cid) is None:
            errs.append(f"Alt vocal {cid}: declares a jacket but none is in the zip.")

    for label, key in (("ogmv", "ogmv"), ("2dmv", "2dmv")):
        p = data.get(key)
        if p and not has_stream(p, "video"):
            errs.append(f"{label}: no video stream.")
    info["original_music_video"] = bool(data.get("ogmv"))
    info["2d_music_video"] = bool(data.get("2dmv"))

    seen: set[str] = set()
    for d in data["diffs"]:
        name, path = d["difficulty"], d["_path"]
        if name not in DIFFICULTIES:
            errs.append(f"{name}: unknown difficulty (use {', '.join(DIFFICULTIES)}).")
        if name in seen:
            errs.append(f"Duplicate difficulty '{name}'.")
        seen.add(name)
        if not (MIN_LEVEL <= d["level"] <= MAX_LEVEL):
            errs.append(f"{name}: level must be {MIN_LEVEL}-{MAX_LEVEL}.")
        if charts.chart_ext(path) not in charts.CHART_EXTS:
            errs.append(f"{name}: chart must be .sus, .usc, .json or .json.gz.")
            continue
        try:
            text = charts.sus_text(path)
        except Exception as e:
            errs.append(f"{name}: cannot read the chart ({e}).")
            continue
        ok, msg = validate_sus_text(text)
        if not ok:
            errs.append(f"{name}: {msg}.")
        try:
            combo = charts.combo_of_sus(text)
        except Exception as e:
            errs.append(f"{name}: combo derivation failed ({e}).")
            continue
        if combo == 0:
            errs.append(
                f"{name}: the chart has 0 combo (no playable notes), so the chart data was "
                "probably not read correctly"
            )
            continue
        expected = (data.get("expected_combos") or {}).get(name)
        lvl = None
        if expected is None:
            lvl = charts.level_data_info(path)
            expected = lvl[0] if lvl else None
        if expected is not None and expected != combo:
            msg = (
                f"{name}: conversion check failed: the converted chart has combo {combo}, "
                f"the source data has {expected} ({abs(expected - combo)} notes differ)"
            )
            if opt.allow_combo_mismatch:
                warn(msg + "; building anyway (--allow-combo-mismatch).")
            else:
                errs.append(
                    msg + ". Refusing to build (re-run with --allow-combo-mismatch to build anyway)"
                )
                continue
        if lvl and lvl[1]:
            want = int(round(-lvl[1] * 1000))
            if int(info.get("offset_ms") or 0) != want:
                warn(
                    f"{name}: the level data has a music offset of {lvl[1]:+.3f}s; "
                    f"offset_ms is {info.get('offset_ms') or 0}, the matching value is {want}."
                )
        layers = charts.layer_count(text)
        log(
            f"{name}: level {d['level']}, {combo} combo, "
            + (f"{layers} layer(s)" if layers else "no layers")
        )
        declared = d.get("notes")
        if declared is None:
            d["notes"] = combo
        elif declared != combo:
            msg = f"{name}: combo mismatch, ChartInfo says {declared} but the chart has {combo}"
            if opt.combo_mode == "strict":
                errs.append(msg + ".")
            elif opt.combo_mode == "keep":
                warn(msg + "; keeping the ChartInfo value (--combo keep).")
            else:
                warn(msg + f"; using {combo}.")
                d["notes"] = combo

    by_diff = {d["difficulty"]: d["notes"] for d in data["diffs"]}
    for entry in info.get("difficulties", []):
        if entry.get("difficulty") in by_diff:
            entry["notes"] = by_diff[entry["difficulty"]]

    if not info.get("song_duration") and track and os.path.isfile(track):
        dur = media_duration(track)
        info["song_duration"] = round(dur, 3) if dur is not None else None

    data["combo_mode"] = opt.combo_mode
    return errs
