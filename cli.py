"""Terminal mode:  python3 main.py <project>.zip [<project2>.zip ...] [options]"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import traceback
import zipfile

import core
import deps
import preview

# --flag -> key understood by core.apply_overrides()
_OVERRIDES = {
    "title": "title", "artist": "artist", "charter": "charter", "vocals": "vocals",
    "lyricist": "lyricist", "composer": "composer", "arranger": "arranger",
    "difficulty": "difficulty", "level": "level", "identifier": "identifier",
    "offset_ms": "offset_ms", "mv_offset_ms": "mv_offset_ms",
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description=(
            "Compile custom chart projects into .android.chart.gs6 and .ios.chart.gs6. "
            "A project is a .zip from the builder's 'Build zip...' (ChartInfo.json) or an "
            "UntitledCharts export (level.json + NSLevelData.json[.gz] + music.mp3/.ogg + "
            "preview.mp3/.ogg + jacket.png; a zip with one folder per difficulty becomes one "
            "multi-difficulty chart). A missing or over-long preview is cut automatically. "
            "Run without arguments for the interactive wizard."
        ),
    )
    p.add_argument("projects", nargs="+", metavar="project", help="chart project .zip (one or more)")
    p.add_argument(
        "-o", "--output", metavar="BASE",
        help="output base name; writes BASE.android.chart.gs6 and BASE.ios.chart.gs6 "
        "(one zip only; default: next to the zip, same name)",
    )
    p.add_argument("--outdir", metavar="DIR", help="write the outputs into this folder instead of next to each zip")
    p.add_argument("--bases", default=deps.BASES, help="folder with the base bundles")

    g = p.add_argument_group("preview")
    g.add_argument(
        "--preview-length", type=float, default=preview.DEFAULT_SECONDS, metavar="SEC",
        help=f"length of an automatically cut preview, {preview.MIN_SECONDS:.0f}-"
        f"{preview.MAX_SECONDS:.0f}s (default {preview.DEFAULT_SECONDS:.0f})",
    )
    g.add_argument(
        "--preview-start", type=float, metavar="SEC",
        help="where the automatic cut starts (default: loudest part of the song)",
    )
    g.add_argument(
        "--preview-file", metavar="FILE",
        help="use this audio file as the preview (cut if it is too long)",
    )
    g.add_argument(
        "--force-preview", action="store_true",
        help="cut the preview even if it is already short enough",
    )
    g.add_argument(
        "--no-auto-preview", action="store_true",
        help="fail instead of cutting when the preview is missing or longer than 45s",
    )

    n = p.add_argument_group(
        "metadata overrides",
        "Replace fields of the zip (all zips). For an UntitledCharts export, anything "
        "level.json lacks is asked for in the terminal unless it is given here.",
    )
    n.add_argument(
        "--set", action="append", default=[], metavar="KEY=VALUE",
        help="override a field, e.g. --set composer=Name --set offset_ms=300 (repeatable); "
        f"keys: {', '.join(core.OVERRIDE_KEYS)}",
    )
    n.add_argument("--difficulty", choices=core.DIFFICULTIES, help="difficulty (single-chart zips only)")
    n.add_argument("--level", type=int, metavar=f"{core.MIN_LEVEL}-{core.MAX_LEVEL}", help="difficulty level number")
    for name in ("title", "artist", "charter", "vocals", "lyricist", "composer", "arranger"):
        n.add_argument(f"--{name}", metavar="TEXT")
    n.add_argument("--identifier", metavar="UUID_UUID", help="two v4 UUIDs joined by '_' (UnCh exports: derived from the level name)")
    n.add_argument("--offset-ms", type=int, dest="offset_ms", metavar="MS", help="chart timing offset")
    n.add_argument("--mv-offset-ms", type=int, dest="mv_offset_ms", metavar="MS", help="extra MV offset")

    n.add_argument(
        "--no-prompt", action="store_true",
        help="never ask for fields level.json lacks; use the defaults and warn (automatic when not run in a terminal)",
    )

    p.add_argument(
        "--allow-combo-mismatch", action="store_true",
        help="UnCh level data: build even if some source notes can't be converted to .sus "
        "(they are left out; a warning is printed)",
    )
    p.add_argument(
        "--combo", choices=("fix", "strict", "keep"), default="fix",
        help="when ChartInfo's note count differs from the chart's real combo: "
        "fix = use the real combo and warn (default), strict = fail, keep = keep ChartInfo's",
    )
    p.add_argument(
        "--save-zip", metavar="PATH",
        help="also write the corrected project (cut preview, fixed combos) as a builder .zip (one zip only)",
    )
    p.add_argument("--no-compile", action="store_true", help="validate/fix only; skip compiling")
    p.add_argument("--keep-temp", action="store_true", help="keep the temporary work folders")
    p.add_argument("-q", "--quiet", action="store_true", help="only print warnings and errors")
    p.add_argument("--debug", action="store_true", help="print a traceback on failure")
    return p


def _error(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr, flush=True)


DIFF_HELP = " / ".join(f"{i}={d}" for i, d in enumerate(core.DIFFICULTIES, 1))


def make_asker():
    """Prompt callback for native packages: asks for each field level.json lacks."""
    shown = [False]

    def ask(name: str, default: str) -> str:
        if not shown[0]:
            print("level.json is missing some info. Enter keeps the [default].", flush=True)
            shown[0] = True
        label = {"difficulty": f"difficulty ({DIFF_HELP})", "level": f"level ({core.MIN_LEVEL}-{core.MAX_LEVEL})"}.get(
            name, name.capitalize()
        )
        while True:
            try:
                raw = input(f"{label} [{default}]: ").strip()
            except EOFError:
                return default
            if not raw:
                return default
            if name == "difficulty":
                raw = core.DIFFICULTIES[int(raw) - 1] if raw.isdigit() and 1 <= int(raw) <= len(core.DIFFICULTIES) else raw.lower()
                if raw not in core.DIFFICULTIES:
                    print(f"  ! use 1-{len(core.DIFFICULTIES)} or a name ({DIFF_HELP}).")
                    continue
            elif name == "level":
                if not (raw.isdigit() and core.MIN_LEVEL <= int(raw) <= core.MAX_LEVEL):
                    print(f"  ! must be {core.MIN_LEVEL}-{core.MAX_LEVEL}.")
                    continue
                raw = str(int(raw))
            return raw

    return ask


def build_one(path: str, args: argparse.Namespace, opt: core.Options) -> bool:
    """Validate and compile one project zip. Prints its own report; True on success."""

    def log(msg: str) -> None:
        if not args.quiet:
            print(msg, flush=True)

    def warn(msg: str) -> None:
        print(f"warning: {msg}", file=sys.stderr, flush=True)

    if not os.path.isfile(path):
        _error(f"'{path}' not found.")
        return False
    if not zipfile.is_zipfile(path):
        _error(f"'{path}' is not a .zip project.")
        return False

    overrides = dict(args.set_values)
    overrides.update(
        {key: getattr(args, flag) for flag, key in _OVERRIDES.items() if getattr(args, flag, None) is not None}
    )
    work = tempfile.mkdtemp(prefix="gs6cli_")
    try:
        try:
            asker = make_asker() if sys.stdin.isatty() and not args.no_prompt else None
            data = core.load_project(path, work, overrides, asker)
        except Exception as e:
            if args.debug:
                traceback.print_exc()
            _error(str(e))
            return False

        errs = core.prepare_project(data, work, opt, log, warn)
        if errs:
            print("Cannot build:", file=sys.stderr)
            for e in errs:
                print("  - " + e, file=sys.stderr)
            return False

        if args.save_zip:
            core.write_zip(args.save_zip, data)
            log(f"Wrote {args.save_zip}")
        if args.no_compile:
            log("Validated (--no-compile, nothing compiled).")
            return True

        import encode

        missing = encode.missing_bases(args.bases, want_mv=bool(data["ogmv"] or data["2dmv"]))
        if missing:
            _error(f"base bundles missing from '{args.bases}':\n  " + "\n  ".join(missing))
            return False

        stem = os.path.splitext(os.path.basename(path))[0]
        if args.output:
            base = args.output
        elif args.outdir:
            base = os.path.join(args.outdir, stem)
        else:
            base = os.path.splitext(os.path.abspath(path))[0]
        base = core.strip_output_suffix(base)
        os.makedirs(os.path.dirname(os.path.abspath(base)), exist_ok=True)

        log("Compiling .chart.gs6, this can take a minute...")
        try:
            outputs = encode.compile_chart(data, args.bases, base, log=log)
        except Exception as e:
            if args.debug:
                traceback.print_exc()
            _error(f"compile failed: {e}")
            return False

        print("Compiled (one encrypted bundle set per platform):")
        for o in outputs:
            print(o)
        print("Identifier (save this to update the chart later):")
        print(data["info"]["identifier"])
        return True
    finally:
        if args.keep_temp:
            print(f"Work folder kept: {work}", file=sys.stderr)
        else:
            shutil.rmtree(work, ignore_errors=True)


def parse_sets(parser: argparse.ArgumentParser, items: list[str]) -> dict:
    out: dict = {}
    for item in items:
        if "=" not in item:
            parser.error(f"--set expects KEY=VALUE, got '{item}'")
        key, value = item.split("=", 1)
        key = key.strip().lower().replace("-", "_")
        if key not in core.OVERRIDE_KEYS:
            parser.error(f"--set: unknown key '{key}' (try: {', '.join(core.OVERRIDE_KEYS)})")
        if key in ("offset_ms", "mv_offset_ms", "level"):
            try:
                value = int(value)
            except ValueError:
                parser.error(f"--set {key} must be an integer")
        out[key] = value.strip() if isinstance(value, str) else value
    return out


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if len(args.projects) > 1:
        for flag, value in (("-o/--output", args.output), ("--save-zip", args.save_zip),
                            ("--identifier", args.identifier)):
            if value:
                parser.error(f"{flag} only works with a single zip")
    args.set_values = parse_sets(parser, args.set)
    if args.level is not None and not (core.MIN_LEVEL <= args.level <= core.MAX_LEVEL):
        parser.error(f"--level must be {core.MIN_LEVEL}-{core.MAX_LEVEL}")
    if not (preview.MIN_SECONDS <= args.preview_length <= preview.MAX_SECONDS):
        clamped = preview.clamp_length(args.preview_length)
        print(f"warning: --preview-length {args.preview_length:g} is outside "
              f"{preview.MIN_SECONDS:.0f}-{preview.MAX_SECONDS:.0f}s, using {clamped:g}s.",
              file=sys.stderr, flush=True)
        args.preview_length = clamped

    found = deps.problems(bases=args.bases, check_bases=False)
    if found:
        print("The chart builder cannot run:\n", file=sys.stderr)
        for line in found:
            print("  - " + line, file=sys.stderr)
        print("\n" + deps.FIXES, file=sys.stderr)
        return 1

    opt = core.Options(
        preview_length=args.preview_length,
        preview_start=args.preview_start,
        preview_file=args.preview_file,
        force_preview=args.force_preview,
        auto_preview=not args.no_auto_preview,
        combo_mode=args.combo,
        allow_combo_mismatch=args.allow_combo_mismatch,
    )

    ok = failed = 0
    try:
        for path in args.projects:
            print(f"##### {path}", flush=True)
            if build_one(path, args, opt):
                ok += 1
            else:
                failed += 1
    except KeyboardInterrupt:
        _error("interrupted.")
        return 130
    print(f"Done: {ok} ok, {failed} failed.")
    return 0 if failed == 0 else 1
