# BuilderGS6

A modified builder for the offline game Gridless Sekai6.

the official repo: https://github.com/UnknownSekai/GridlessSekai6

Report any problems/bugs through issues

# What's implemented?

- Command for making chart even if they have mismatched combos
- Preview music cutter (automatic, 30-40s, from the loudest part of the song)
- .json/.json.gz files can be transformed into charts
- It allows layers to be added to the chart
- Sonolus zips are converted automatically
- The builder asks for the info the sonolus zip doesn't have (lyricist, composer, arranger, difficulty, level)
- Several zips can be built at once 
- music/preview can be .mp3 or .ogg (also .wav / .flac / .m4a)
- Every converted chart is checked against the source data, so lost notes don't go unnoticed

# Setup

1. Install Python (3.10 or newer), FFmpeg (ffmpeg.org) and git
2. Install the requirements `pip install -r requirements.txt`
3. Check that these work: `python3 --version`, `ffmpeg -version`, `ffprobe -version`

# Commands

| What | Command |
| --- | --- |
| terminal builder | `python3 main.py` |
| build one zip | `python3 main.py yourchart.zip` |
| build several zips | `python3 main.py a.zip b.zip` |
| build from made zip| `python3 main.py --import yourchart.zip` |
| makes config | `python3 main.py myconfig.json` |
| Tk window | `python3 main.py --gui` |
| all options | `python3 main.py --help` |

allow combo mismatch: --allow-combo-mismatch (example: python3 main.py yourconvertedchart.zip --allow-combo-mismatch)

preview music cutter: it runs by itself when the preview is missing or longer than 45s. Options:

| Option | What it does |
| --- | --- |
| `--preview-length SEC` | length of the cut, 30-40 (default 35) |
| `--preview-start SEC` | start the cut here instead of the loudest part |
| `--preview-file FILE` | use another audio file as the preview |
| `--force-preview` | cut even if the preview is already short |
| `--no-auto-preview` | fail instead of cutting |

(manual way to cut preview audio: 
open in the chart folder: ffmpeg -ss 30 -t 30 -i preview.mp3 -af "afade=t=in:d=1,afade=t=out:st=29:d=1" preview_short.mp3)

combo check for ChartInfo.json zips: `--combo fix` (default, uses the real combo and warns), `--combo strict` (stops), `--combo keep` (keeps the stored number)

set info yourself (no questions asked): `--set KEY=VALUE`, example: python3 main.py yourchart.zip --set composer="Name" --set offset_ms=300

| Option | What it does |
| --- | --- |
| `--set KEY=VALUE` | override a field, can be repeated (keys: title, artist, charter, vocals, lyricist, composer, arranger, collab, original, identifier, offset_ms, mv_offset_ms, is_full, difficulty, level) |
| `--title` `--artist` `--charter` `--vocals` `--lyricist` `--composer` `--arranger` | same as --set for that field |
| `--difficulty` `--level` | difficulty / level (zips with one chart) |
| `--offset-ms MS` `--mv-offset-ms MS` | timing offsets |
| `--identifier UUID_UUID` | set the identifier (one zip only) |
| `--no-prompt` | never ask questions, use the defaults and warn |

output:

| Option | What it does |
| --- | --- |
| `-o BASE` | output name (BASE.android.chart.gs6 and BASE.ios.chart.gs6), one zip only |
| `--outdir DIR` | put the outputs in a folder |
| `--save-zip PATH` | also save the fixed project (cut preview, fixed combos) as a zip, one zip only |
| `--no-compile` | only check and fix, don't compile |
| `-q` | only print warnings and errors |
| `--debug` | print the traceback when something fails |

# How to make charts from .json/.json.gz (sonolus) file zips
in builder : python3 main.py yourchartfolder.zip

lyricist/composer/arranger aren't in the Sonolus zips the builder asks for them (Enter keeps `-`). To skip the questions use `--set composer=... --set lyricist=... --set arranger=...` or `--no-prompt`.

If you put a `.json` / `.json.gz` chart in a normal builder zip, name it after its difficulty (`expert.json.gz`, `master.json`, ...).

