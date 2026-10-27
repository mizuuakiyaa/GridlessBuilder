"""Interactive terminal mode (the default for `python3 main.py` with no arguments)."""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import sys
import tempfile

import charts
import core
import deps
import preview

DIFF_HELP = " / ".join(f"{i}={d}" for i, d in enumerate(core.DIFFICULTIES, 1))
AUDIO_HINT = "wav / mp3 / flac / ogg / m4a"


def new_state() -> dict:
    return {
        "jacket": "", "track": "", "track_pre": "", "preview_start": "",
        "preview_length": preview.DEFAULT_SECONDS, "combo_mode": "fix",
        "mvs": [], "charts": [],
        "title": "", "charter": "", "lyricist": "", "composer": "", "arranger": "",
        "artist": "", "vocals": "", "collab": "", "original": "",
        "is_full": True, "offset_ms": "0", "mv_offset_ms": "0",
        "default_vocal_name": "", "default_vocal_type": "sekai", "alts": [],
        "identifier": core.gen_double_uuid(),
    }


# ---------------------------------------------------------------- prompting
def clean_path(raw: str) -> str:
    """Accept drag-and-dropped paths ('quoted' or with backslash-spaces)."""
    p = raw.strip()
    if len(p) >= 2 and p[0] == p[-1] and p[0] in "'\"":
        p = p[1:-1]
    p = re.sub(r"\\(.)", r"\1", p)
    return os.path.expanduser(p)


def ask(label, default="", required=False, optional=False, validate=None) -> str:
    while True:
        shown = f" [{default}]" if default not in ("", None) else ""
        raw = input(f"{label}{shown}: ").strip()
        if raw == "-" and optional:
            return ""
        if raw == "":
            raw = str(default) if default not in ("", None) else ""
        if raw == "":
            if required:
                print("  ! required.")
                continue
            return ""
        if validate:
            err = validate(raw)
            if err:
                print(f"  ! {err}")
                continue
        return raw


def ask_path(label, default="", required=False, optional=False, validate=None) -> str:
    def check(raw):
        p = clean_path(raw)
        if not os.path.isfile(p):
            return "file not found."
        return validate(p) if validate else None

    v = ask(label, default, required, optional, check)
    return clean_path(v) if v else ""


def ask_int(label, default, lo, hi) -> int:
    def check(raw):
        try:
            n = int(raw)
        except ValueError:
            return "enter a whole number."
        return None if lo <= n <= hi else f"must be {lo}-{hi}."

    return int(ask(label, default, True, validate=check))


def ask_yn(label, default=False) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{label} [{hint}]: ").strip().lower()
        if not raw:
            return default
        if raw[0] in "yn":
            return raw[0] == "y"


def audio_ok(p: str) -> str | None:
    return None if core.has_stream(p, "audio") else "no audio stream found."


def video_ok(p: str) -> str | None:
    return None if core.has_stream(p, "video") else "no video stream found."


def image_ok(p: str) -> str | None:
    ok, msg = core.validate_image(p)
    return None if ok else msg


def head(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(3, 62 - len(title)))


# ----------------------------------------------------------------- sections
def edit_files(st: dict) -> None:
    head("Files")
    st["jacket"] = ask_path("Jacket image", st["jacket"], True, validate=image_ok)
    st["track"] = ask_path("Audio track", st["track"], True, validate=audio_ok)
    st["track_pre"] = ask_path(
        "Preview audio (Enter = auto-cut 30-40s from the track)",
        st["track_pre"], optional=True, validate=audio_ok,
    )
    dur = core.media_duration(st["track_pre"]) if st["track_pre"] else None
    if st["track_pre"] and dur is not None and dur <= core.MAX_PREVIEW_SECONDS + 0.05:
        st["preview_start"] = ""
    else:
        why = "no preview given" if not st["track_pre"] else f"preview is {dur:.1f}s (max 45s)"
        print(f"  {why}: it will be auto-cut to ~{st['preview_length']:.0f}s "
              "from the loudest part of the song.")
        st["preview_start"] = ask(
            "  Cut start in seconds (Enter = auto)", st["preview_start"], optional=True,
            validate=lambda r: None if re.fullmatch(r"\d+(\.\d+)?", r) else "enter seconds.",
        )

    n = ask_int("Number of music videos (0-2)", len(st["mvs"]), 0, 2)
    mvs = []
    for i in range(n):
        old = st["mvs"][i] if i < len(st["mvs"]) else {"kind": "", "path": ""}
        taken = {m["kind"] for m in mvs}
        kinds = [k for k in core.MV_TYPES if k not in taken]
        kind = ask(f"  MV {i + 1} type ({' / '.join(kinds)})",
                   old["kind"] if old["kind"] in kinds else kinds[0], True,
                   validate=lambda r, k=kinds: None if r in k else f"use one of {k}.")
        path = ask_path(f"  MV {i + 1} file", old["path"], True, validate=video_ok)
        mvs.append({"kind": kind, "path": path})
    st["mvs"] = mvs


def chart_info(path: str) -> tuple[int, int, str | None]:
    """(combo, layers, error)"""
    try:
        text = charts.sus_text(path)
        ok, msg = core.validate_sus_text(text)
        return charts.combo_of_sus(text), charts.layer_count(text), (None if ok else msg)
    except Exception as e:
        return 0, 0, str(e)


def edit_charts(st: dict) -> None:
    head("Charts (.sus / .usc / .json / .json.gz)")
    n = ask_int(f"Number of charts (1-{core.MAX_CHARTS})", max(1, len(st["charts"])),
                1, core.MAX_CHARTS)
    out: list[dict] = []
    for i in range(n):
        old = st["charts"][i] if i < len(st["charts"]) else {}
        print(f"- Chart {i + 1}")
        used = {c["difficulty"] for c in out}
        free = [d for d in core.DIFFICULTIES if d not in used]

        def pick(raw, used=used):
            name = core.DIFFICULTIES[int(raw) - 1] if raw.isdigit() and 1 <= int(raw) <= 6 else raw
            if name not in core.DIFFICULTIES:
                return f"use 1-6 or a name ({DIFF_HELP})."
            return "already used." if name in used else None

        raw = ask(f"  difficulty ({DIFF_HELP})",
                  old.get("difficulty") if old.get("difficulty") in free else free[0],
                  True, validate=pick)
        diff = core.DIFFICULTIES[int(raw) - 1] if raw.isdigit() else raw
        level = ask_int("  level (1-99)", old.get("level", 1), core.MIN_LEVEL, core.MAX_LEVEL)

        def chart_ok(p):
            if charts.chart_ext(p) not in charts.CHART_EXTS:
                return "use .sus, .usc, .json or .json.gz."
            combo, layers, err = chart_info(p)
            if err:
                return err
            print(f"    combo: {combo}   layers: {layers or 'none'}")
            return None

        path = ask_path("  chart file", old.get("path", ""), True, validate=chart_ok)
        out.append({"difficulty": diff, "level": level, "path": path})
    st["charts"] = out


META = [("title", "Title*", True), ("charter", "Charter*", True),
        ("lyricist", "Lyricist*", True), ("composer", "Composer*", True),
        ("arranger", "Arranger*", True), ("artist", "Artist*", True),
        ("vocals", "Vocals*", True)]


def edit_metadata(st: dict) -> None:
    head("Metadata  (* = required, '-' clears optional fields)")
    for key, label, req in META:
        st[key] = ask(label, st[key], req)
    st["is_full"] = ask_yn("Full-length (not a game-size cut)?", st["is_full"])
    num = lambda r: None if re.fullmatch(r"-?\d+", r) else "must be an integer."
    st["offset_ms"] = ask("offset_ms (chart timing offset)", st["offset_ms"], True, validate=num)
    st["mv_offset_ms"] = ask("additional_mv_offset_ms (extra MV offset)",
                             st["mv_offset_ms"], True, validate=num)
    st["original"] = ask("original URL (optional)", st["original"], optional=True)
    st["collab"] = ask("Collab (optional)", st["collab"], optional=True)


def edit_vocals(st: dict) -> None:
    head("Vocals (alt vocals / covers)")
    print("Leave off for a single-vocal song. If you add alt vocals, name the DEFAULT\n"
          "vocal (it uses the main track + jacket), then add each alternate with its\n"
          "own audio/preview and an optional jacket.")
    if not ask_yn("Add alternate vocals?", bool(st["alts"])):
        st["alts"], st["default_vocal_name"] = [], ""
        return
    types = core.VOCAL_TYPES
    tv = lambda r: None if r in types else f"use one of {types}."
    st["default_vocal_name"] = ask("Default vocal name", st["default_vocal_name"], True)
    st["default_vocal_type"] = ask(f"Default vocal type ({' / '.join(types)})",
                                   st["default_vocal_type"], True, validate=tv)
    n = ask_int("Number of alt vocals (1-9)", max(1, len(st["alts"])), 1, 9)
    alts = []
    for i in range(n):
        o = st["alts"][i] if i < len(st["alts"]) else {}
        print(f"- Alt vocal {i + 1}")
        alts.append({
            "name": ask("  name", o.get("name", ""), True),
            "vocals": ask("  vocals", o.get("vocals", ""), True),
            "vocal_type": ask(f"  type ({' / '.join(types)})",
                              o.get("vocal_type", "original_song"), True, validate=tv),
            "audio": ask_path("  audio", o.get("audio", ""), True, validate=audio_ok),
            "preview": ask_path("  preview (Enter = auto-cut)", o.get("preview", ""),
                                optional=True, validate=audio_ok),
            "jacket": ask_path("  jacket (optional)", o.get("jacket", ""),
                               optional=True, validate=image_ok),
        })
    st["alts"] = alts


def edit_identity(st: dict) -> None:
    head("Identity")
    print("This identifier uniquely names the chart. Re-exporting with the same identifier\n"
          "updates that chart in the game instead of adding a duplicate. Keep it after\n"
          "your first export. Custom values must be two valid v4 UUIDs joined by '_'.")
    print(f"Current: {st['identifier']}")
    v = ask("Identifier (Enter = keep, 'new' = regenerate)", st["identifier"], True,
            validate=lambda r: None if r == "new" or core.validate_double_uuid(r)
            else "must be two valid v4 UUIDs joined by '_'.")
    st["identifier"] = core.gen_double_uuid() if v == "new" else v.strip()


def show_summary(st: dict) -> None:
    head("Summary")
    print(f"Title      : {st['title']}   Charter: {st['charter']}")
    print(f"Artist     : {st['artist']}  | Vocals: {st['vocals']}  | Composer: {st['composer']}")
    print(f"Jacket     : {st['jacket']}")
    print(f"Track      : {st['track']}")
    print(f"Preview    : {st['track_pre'] or '(auto-cut from the track)'}")
    for m in st["mvs"]:
        print(f"MV ({m['kind']}) : {m['path']}")
    for c in st["charts"]:
        print(f"Chart      : {c['difficulty']:<7} Lv{c['level']}   {c['path']}")
    if st["alts"]:
        print(f"Vocals     : default '{st['default_vocal_name']}' + "
              + ", ".join(a["name"] for a in st["alts"]))
    print(f"Identifier : {st['identifier']}")


# ------------------------------------------------------------------ actions
def collect(st: dict, work: str):
    """Turn the state into the data dict encode.compile_chart() wants; returns (data, errors)."""
    errs: list[str] = []
    for key, label, _ in META:
        if not st[key].strip():
            errs.append(f"{label.rstrip('*')} is required.")
    if not st["charts"]:
        errs.append("At least one chart is required.")
    try:
        offset, mv_offset = int(st["offset_ms"]), int(st["mv_offset_ms"])
    except ValueError:
        errs.append("offset_ms and additional_mv_offset_ms must be integers.")
        offset = mv_offset = 0
    alts = st["alts"]
    if alts and not st["default_vocal_name"].strip():
        errs.append("Default vocal name is required when alt vocals are present.")
    mvs = {m["kind"]: m["path"] for m in st["mvs"] if m["path"]}

    tr = lambda v: {"jp": v.strip(), "en": v.strip()}
    info = {
        "identifier": st["identifier"].strip(), "format_version": core.FORMAT_VERSION,
        "title": tr(st["title"]), "charter": st["charter"].strip(),
        "difficulties": [{"difficulty": c["difficulty"], "level": c["level"], "notes": None}
                         for c in st["charts"]],
        "lyricist": tr(st["lyricist"]), "composer": tr(st["composer"]),
        "arranger": tr(st["arranger"]), "artist": tr(st["artist"]),
        "vocals": tr(st["vocals"]), "isFullLength": st["is_full"],
        "vocaloid_or_other": "other", "offset_ms": offset, "additional_mv_offset_ms": mv_offset,
        "song_duration": None, "original": st["original"].strip() or None,
        "original_music_video": "ogmv" in mvs, "2d_music_video": "2dmv" in mvs,
    }
    if st["collab"].strip():
        info["collab"] = tr(st["collab"])

    cover_files: list[tuple[str, str]] = []
    if alts:
        info["covers"] = [{"default": True, "name": tr(st["default_vocal_name"]),
                           "vocal_type": st["default_vocal_type"]}]
        for i, a in enumerate(alts, 1):
            info["covers"].append({
                "default": False, "id": i, "name": tr(a["name"]), "vocals": tr(a["vocals"]),
                "vocal_type": a["vocal_type"], "has_jacket": bool(a["jacket"]),
            })
            for kind, key in (("cover", "audio"), ("cover_pre", "preview"),
                              ("cover_jacket", "jacket")):
                if a[key]:
                    cover_files.append((a[key], f"covers/{kind}_{i}{core.ext_of(a[key])}"))

    data = {
        "info": info,
        "diffs": [{"difficulty": c["difficulty"], "level": c["level"], "notes": None,
                   "_path": c["path"]} for c in st["charts"]],
        "jacket": st["jacket"], "track": st["track"], "track_pre": st["track_pre"] or None,
        "ogmv": mvs.get("ogmv"), "2dmv": mvs.get("2dmv"), "cover_files": cover_files,
    }
    start = st.get("preview_start")
    opt = core.Options(
        preview_length=float(st.get("preview_length") or preview.DEFAULT_SECONDS),
        preview_start=float(start) if start not in ("", None) else None,
        combo_mode=st.get("combo_mode", "fix"),
    )
    errs += core.prepare_project(
        data, work, opt, lambda m: print("  " + m), lambda m: print(f"  warning: {m}")
    )
    return data, errs


def default_name(st: dict) -> str:
    return re.sub(r"[^\w\-. ]+", "_", st["title"]).strip() or "custom_chart"


def do_build_zip(st: dict, work: str) -> None:
    data, errs = collect(st, work)
    if errs:
        return report(errs)
    out = ask("Save .zip as", os.path.abspath(default_name(st) + ".zip"), True)
    out = clean_path(out)
    if not out.lower().endswith(".zip"):
        out += ".zip"
    core.write_zip(out, data)
    print(f"Built:\n  {out}\nKeep the ZIP for when you want to update the chart.")


def do_compile(st: dict, work: str) -> None:
    import encode

    data, errs = collect(st, work)
    if errs:
        return report(errs)
    missing = encode.missing_bases(deps.BASES, want_mv=bool(data["ogmv"] or data["2dmv"]))
    if missing:
        return report(["Base bundles missing from the bases folder:"] + missing)
    base = ask("Output base name (writes .android.chart.gs6 and .ios.chart.gs6)",
               os.path.abspath(default_name(st)), True)
    base = core.strip_output_suffix(clean_path(base))
    os.makedirs(os.path.dirname(os.path.abspath(base)), exist_ok=True)
    print("Compiling, this can take a minute...")
    try:
        outs = encode.compile_chart(data, deps.BASES, base, log=lambda m: print("  " + m))
    except Exception as e:
        return report([f"Compile failed: {e}"])
    print("\nCompiled:\n" + "\n".join("  " + o for o in outs))
    print(f"Identifier (save this to update the chart later):\n  {data['info']['identifier']}")


def report(errs: list[str]) -> None:
    print("\nCannot continue:")
    for e in errs:
        print("  ! " + e)


def do_save_config(st: dict) -> None:
    out = clean_path(ask("Save config as", os.path.abspath(default_name(st) + ".json"), True))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    print(f"Saved {out}")


def load_config(st: dict, path: str) -> None:
    with open(path, "r", encoding="utf-8-sig") as f:
        st.update({k: v for k, v in json.load(f).items() if k in st})
    print(f"Loaded {path}")


def import_zip(st: dict, path: str, work: str) -> None:
    data = core.load_project(path, work)
    info = data["info"]
    one = lambda v: (v.get("en") or v.get("jp") or "") if isinstance(v, dict) else (v or "")
    st.update(
        jacket=data["jacket"] or "", track=data["track"] or "", track_pre=data["track_pre"] or "",
        mvs=[{"kind": k, "path": data[k]} for k in ("ogmv", "2dmv") if data.get(k)],
        charts=[{"difficulty": d["difficulty"], "level": d["level"], "path": d["_path"]}
                for d in data["diffs"]],
        charter=info.get("charter", "") or "", is_full=bool(info.get("isFullLength", True)),
        offset_ms=str(info.get("offset_ms", 0)),
        mv_offset_ms=str(info.get("additional_mv_offset_ms", 0)),
        original=info.get("original") or "", identifier=info.get("identifier") or st["identifier"],
        title=one(info.get("title")),
    )
    for k in ("lyricist", "composer", "arranger", "artist", "vocals", "collab"):
        st[k] = one(info.get(k))
    st["alts"], st["default_vocal_name"] = [], ""
    by_name = {zn: src for src, zn in data["cover_files"]}

    def cf(kind, cid):
        return next((s for zn, s in by_name.items() if zn.startswith(f"covers/{kind}_{cid}.")), "")

    for c in info.get("covers", []):
        if c.get("default"):
            st["default_vocal_name"] = one(c.get("name"))
            st["default_vocal_type"] = c.get("vocal_type", "sekai")
        else:
            cid = c.get("id")
            st["alts"].append({
                "name": one(c.get("name")), "vocals": one(c.get("vocals")),
                "vocal_type": c.get("vocal_type", "original_song"),
                "audio": cf("cover", cid), "preview": cf("cover_pre", cid),
                "jacket": cf("cover_jacket", cid),
            })
    print(f"Imported {path}")


MENU = """
--- Menu ---------------------------------------------------
  b  Build .zip                 1  Edit files
  c  Compile .chart.gs6         2  Edit charts
  j  Save config as JSON        3  Edit metadata
  l  Load config JSON           4  Edit vocals
  i  Import .zip                5  Edit identity
  v  View summary               q  Quit"""


def run(config: str | None = None, import_path: str | None = None) -> int:
    found = deps.problems(check_bases=False)
    if found:
        print("The chart builder cannot run:\n")
        for p in found:
            print("  - " + p)
        print("\n" + deps.FIXES)
        return 1
    work = tempfile.mkdtemp(prefix="gs6tui_")
    atexit.register(shutil.rmtree, work, True)
    st = new_state()
    print("GridlessSekai6 Custom Chart Builder (terminal)")
    print("Enter keeps the [default]; '-' clears an optional field; Ctrl+C quits.")
    try:
        if config:
            load_config(st, config)
        elif import_path:
            import_zip(st, import_path, work)
            show_summary(st)
        else:
            for step in (edit_files, edit_charts, edit_metadata, edit_vocals, edit_identity):
                step(st)
            show_summary(st)
        actions = {"1": edit_files, "2": edit_charts, "3": edit_metadata,
                   "4": edit_vocals, "5": edit_identity}
        while True:
            print(MENU)
            try:
                choice = input("> ").strip().lower()
            except EOFError:
                return 0
            try:
                if choice in actions:
                    actions[choice](st)
                elif choice == "b":
                    do_build_zip(st, work)
                elif choice == "c":
                    do_compile(st, work)
                elif choice == "j":
                    do_save_config(st)
                elif choice == "l":
                    load_config(st, ask_path("Config .json", required=True))
                elif choice == "i":
                    import_zip(st, ask_path("Project .zip", required=True), work)
                elif choice == "v":
                    show_summary(st)
                elif choice in ("q", "quit", "exit"):
                    return 0
            except (OSError, ValueError, KeyError) as e:
                print(f"  ! {e}")
    except (KeyboardInterrupt, EOFError):
        print("\nBye.")
        return 130
