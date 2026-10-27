"""GridlessSekai6 Custom Chart Builder.

    python3 main.py                      interactive terminal wizard + menu
    python3 main.py <config>.json        same, starting from a saved config
    python3 main.py <a>.zip [<b>.zip]    non-interactive build of one or more zips (see --help):
                                         a builder project.zip, or an UntitledCharts export
                                         (level.json + NSLevelData.json.gz + music/preview/jacket)
    python3 main.py --import <x>.zip     wizard menu with a built zip / export loaded
    python3 main.py --config <x>.json    same as <config>.json
    python3 main.py --gui                open the optional Tk window
"""

from __future__ import annotations

import sys


def _gui() -> int:
    try:
        import tkinter

        tkinter.Tk().destroy()
    except Exception as e:
        print(f"The GUI cannot open here ({e}). Run `python3 main.py` for the terminal mode.",
              file=sys.stderr)
        return 2
    import gui

    gui.run()
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--gui":
        return _gui()
    if not argv or (len(argv) == 1 and argv[0].lower().endswith(".json")):
        import tui

        return tui.run(argv[0] if argv else None)
    if len(argv) == 2 and argv[0] in ("--config", "--import"):
        import tui

        return tui.run(argv[1]) if argv[0] == "--config" else tui.run(import_path=argv[1])
    import cli

    return cli.main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
