"""Thin wrappers around the ffmpeg / ffprobe command-line tools."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


def available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _run(args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise FFmpegError(proc.stderr.decode(errors="replace")[-800:])
    return proc


def duration_seconds(src: Path) -> float | None:
    try:
        out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(src)], 60)
        return float(json.loads(out.stdout)["format"]["duration"])
    except (FFmpegError, KeyError, ValueError, subprocess.TimeoutExpired):
        return None


def has_audio_stream(src: Path) -> bool:
    try:
        out = _run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                    "-of", "json", str(src)], 60)
        return bool(json.loads(out.stdout).get("streams"))
    except (FFmpegError, ValueError, subprocess.TimeoutExpired):
        return False


def to_speech_mp3(src: Path, workdir: Path) -> Path:
    """Convert any audio/video file to a small mono 16 kHz MP3, ideal for speech-to-text."""
    dst = workdir / "speech.mp3"
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
          "-b:a", "48k", str(dst)])
    return dst


def video_frames(src: Path, workdir: Path, count: int = 4) -> list[Path]:
    """Grab `count` evenly spaced still frames (JPEG, max 1280px wide) from a video."""
    dur = duration_seconds(src) or 0
    frames: list[Path] = []
    stamps = [dur * (i + 0.5) / count for i in range(count)] if dur > 0 else [0]
    for i, t in enumerate(stamps):
        dst = workdir / f"frame_{i}.jpg"
        try:
            _run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
                  "-vf", "scale='min(1280,iw)':-2", "-q:v", "4", str(dst)], 120)
            if dst.exists() and dst.stat().st_size > 0:
                frames.append(dst)
        except FFmpegError:
            continue
    return frames


def image_to_jpeg(src: Path, workdir: Path, max_width: int = 1600) -> Path:
    """Re-encode an image (HEIC, BMP, huge PNG...) as a JPEG Claude accepts."""
    dst = workdir / "image.jpg"
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", f"scale='min({max_width},iw)':-2",
          "-q:v", "4", str(dst)], 120)
    return dst


def workdir() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="bridge43_")
