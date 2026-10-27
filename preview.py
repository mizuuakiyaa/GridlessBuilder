"""Automatic preview cutter.

Decodes the audio to 8 kHz mono, finds the loudest window (usually the chorus),
and cuts it out with a short fade in / fade out. The loudest window is searched
between 10 s and 75 % of the song, so it neither lands on the silent intro nor on
the outro.
"""

from __future__ import annotations

import array
import os
import subprocess
import sys

import deps

MIN_SECONDS = 30.0
MAX_SECONDS = 40.0
DEFAULT_SECONDS = 35.0

_RATE = 8000
_STEP = 0.5  # seconds per loudness bucket
_FLAGS = 0x08000000 if os.name == "nt" else 0


def clamp_length(length: float) -> float:
    return max(MIN_SECONDS, min(MAX_SECONDS, float(length)))


def _levels(src: str) -> tuple[list[float], float]:
    """Mean-square loudness per _STEP bucket, and the track length in seconds."""
    cp = subprocess.run(
        [
            deps.FFMPEG, "-hide_banner", "-loglevel", "error", "-i", src,
            "-vn", "-ac", "1", "-ar", str(_RATE), "-f", "s16le", "-",
        ],
        capture_output=True,
        creationflags=_FLAGS,
    )
    if cp.returncode != 0 or len(cp.stdout) < 2:
        msg = cp.stderr.decode(errors="replace").strip()
        raise RuntimeError(msg or "ffmpeg could not decode the audio")
    samples = array.array("h")
    samples.frombytes(cp.stdout[: len(cp.stdout) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    per = int(_RATE * _STEP)
    levels = [
        sum(s * s for s in samples[i : i + per]) / per
        for i in range(0, len(samples) - per + 1, per)
    ]
    return levels, len(samples) / _RATE


def pick_start(levels: list[float], total: float, length: float) -> float:
    """Start (seconds) of the loudest `length`-second window."""
    k = int(round(length / _STEP))
    if total <= length + 1 or len(levels) <= k:
        return 0.0
    last_start = min(total - length, total * 0.75)
    first_start = min(10.0, last_start)
    prefix = [0.0]
    for v in levels:
        prefix.append(prefix[-1] + v)
    best_i, best = 0, -1.0
    for i in range(int(first_start / _STEP), int(last_start / _STEP) + 1):
        if i + k > len(levels):
            break
        energy = prefix[i + k] - prefix[i]
        if energy > best:
            best_i, best = i, energy
    return best_i * _STEP


def cut_preview(
    src: str,
    out_dir: str,
    length: float = DEFAULT_SECONDS,
    start: float | None = None,
    name: str = "track_pre_auto",
) -> tuple[str, float, float]:
    """Cut a 30-40 s preview of `src` into out_dir/<name>.wav.

    Returns (path, start_seconds, length_seconds). `start=None` picks the loudest
    window. A track shorter than the requested length is used whole.
    """
    length = clamp_length(length)
    levels, total = _levels(src)
    if total <= length:
        length, start = total, 0.0
    elif start is None:
        start = pick_start(levels, total, length)
    else:
        start = max(0.0, min(float(start), total - length))

    fade_out = min(2.0, length / 4)
    af = (
        f"afade=t=in:st=0:d=0.5,"
        f"afade=t=out:st={length - fade_out:.3f}:d={fade_out:.3f}"
    )
    dst = os.path.join(out_dir, name + ".wav")
    cp = subprocess.run(
        [
            deps.FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", src,
            "-vn", "-af", af, "-acodec", "pcm_s16le", "-ac", "2", "-ar", "44100", dst,
        ],
        capture_output=True,
        creationflags=_FLAGS,
    )
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.decode(errors="replace").strip() or "ffmpeg failed")
    return dst, start, length
