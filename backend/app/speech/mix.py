"""Join spoken chunks into one loudness-normalised MP3 with ffmpeg.

ffmpeg comes from the ``imageio-ffmpeg`` wheel: a static build (about 80 MB)
with ``apad``, ``concat``, ``loudnorm`` and ``libmp3lame``, so the image needs no
system package. Chunks are joined with a short silence between them and no
crossfade (they end at speaker turns, a fade would smear speech), then
normalised to the usual podcast loudness of -16 LUFS.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

#: Silence between two chunks, in seconds.
GAP_S = 0.3
#: EBU R128 target for podcasts: integrated loudness, true peak, loudness range.
LOUDNESS_LUFS = -16.0
TRUE_PEAK_DB = -1.5
LOUDNESS_RANGE = 11.0

_TIME = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


class MixError(RuntimeError):
    """ffmpeg is missing or could not process the audio."""


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg

        return str(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception as exc:  # noqa: BLE001 - any failure means: no usable ffmpeg
        raise MixError(f"ffmpeg is not available: {exc}") from exc


def duration_s(path: Path) -> float:
    """The decoded length of an audio file, as ffmpeg plays it."""
    result = _run([ffmpeg_exe(), "-hide_banner", "-i", str(path), "-f", "null", "-"])
    matches = _TIME.findall(result.stderr)
    if not matches:
        raise MixError(f"could not read the length of {path.name}")
    hours, minutes, seconds = matches[-1]
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def join(
    paths: Sequence[Path],
    out: Path,
    *,
    gap_s: float = GAP_S,
    bitrate: str = "128k",
) -> None:
    """Concatenate ``paths`` with ``gap_s`` of silence between them, normalised, as MP3."""
    if not paths:
        raise MixError("nothing to join")
    command = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y"]
    for path in paths:
        command += ["-i", str(path)]
    parts = []
    labels = []
    for index in range(len(paths)):
        if index < len(paths) - 1:
            parts.append(f"[{index}:a]apad=pad_dur={gap_s}[a{index}]")
            labels.append(f"[a{index}]")
        else:
            labels.append(f"[{index}:a]")
    parts.append(
        "".join(labels)
        + f"concat=n={len(paths)}:v=0:a=1,"
        + f"loudnorm=I={LOUDNESS_LUFS}:TP={TRUE_PEAK_DB}:LRA={LOUDNESS_RANGE}[out]"
    )
    command += [
        "-filter_complex",
        ";".join(parts),
        "-map",
        "[out]",
        "-ar",
        "44100",
        "-c:a",
        "libmp3lame",
        "-b:a",
        bitrate,
        str(out),
    ]
    _run(command)


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-3:]
        raise MixError("ffmpeg failed: " + " / ".join(tail))
    return result
