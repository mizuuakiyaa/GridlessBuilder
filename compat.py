"""The one place that talks to the chart converter library.

The builder uses `sonolus_converters` (installed by requirements.txt, and required by
unch.py for UntitledCharts exports). If only `rg_converters` is installed it is used
for plain .sus / .usc charts instead; UntitledCharts conversion then reports that it
needs sonolus-level-converters.
"""

from __future__ import annotations

import importlib
import pkgutil
from types import SimpleNamespace

_BACKENDS = ("sonolus_converters", "rg_converters")
_errors: dict[str, str] = {}


def _module(name: str):
    try:
        return importlib.import_module(name)
    except Exception as e:  # missing, or broken install
        _errors[name] = str(e)
        return None


def backend() -> str | None:
    """Name of the converter library in use (sonolus_converters preferred), or None."""
    for name in _BACKENDS:
        if _module(name) is not None:
            return name
    return None


def backend_error() -> str:
    return "; ".join(f"{n}: {_errors.get(n, 'not installed')}" for n in _BACKENDS)


def _need() -> str:
    name = backend()
    if name is None:
        raise RuntimeError(
            "no chart converter library installed (pip install -U -r requirements.txt): "
            + backend_error()
        )
    return name


def _sus():
    if _need() == "sonolus_converters":
        return importlib.import_module("sonolus_converters").sus
    return importlib.import_module("rg_converters.converters.sus")


def _usc():
    if _need() == "sonolus_converters":
        usc = getattr(importlib.import_module("sonolus_converters"), "usc", None)
    else:
        usc = importlib.import_module("rg_converters.converters.usc")
    if usc is None:
        raise ValueError("this converter library cannot read .usc charts")
    return usc


def sus_load(fileobj):
    return _sus().load(fileobj)


def usc_load(fileobj):
    return _usc().load(fileobj)


def export_chart(target, score) -> None:
    """Chart SUS as it goes into the bundle: hi-speed layers kept."""
    if _need() == "sonolus_converters":
        _sus().export(target, score, skip_shift=True, allow_layers=True)
    else:
        _sus().export(target, score, allow_layers=True)


def export_plain(target, score) -> None:
    """info / empty-difficulty SUS."""
    _sus().export(target, score, skip_shift=True)


def notes() -> SimpleNamespace:
    """Score classes needed to build the info / empty charts."""
    if _need() == "sonolus_converters":
        pre = "sonolus_converters.notes."
    else:
        pre = "rg_converters.core."
    imp = importlib.import_module
    return SimpleNamespace(
        Bpm=imp(pre + "bpm").Bpm,
        MetaData=imp(pre + "metadata").MetaData,
        Score=imp(pre + "score").Score,
        Skill=imp(pre + "single").Skill,
        FeverStart=imp(pre + "single").FeverStart,
        FeverChance=imp(pre + "single").FeverChance,
    )


def extra_json_loaders() -> list[tuple[str, object]]:
    """(name, load) for any other JSON chart loaders the library ships (rg_converters only)."""
    if _need() != "rg_converters":
        return []
    conv = importlib.import_module("rg_converters.converters")
    out = []
    for mod in pkgutil.iter_modules(conv.__path__):
        if mod.name in ("sus", "usc") or mod.name.startswith("_"):
            continue
        load = getattr(importlib.import_module(f"rg_converters.converters.{mod.name}"), "load", None)
        if callable(load):
            out.append((mod.name, load))
    return out
