"""Resolve project media tools without changing the user's shell or Homebrew links."""
from __future__ import annotations

import os
from pathlib import Path
import shutil


def media_binary(name: str) -> str:
    if name not in ("ffmpeg", "ffprobe"):
        raise ValueError("Unsupported media binary")
    override = os.environ.get("SPORTS_" + name.upper() + "_BIN")
    if override:
        path = Path(override).expanduser().resolve()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise RuntimeError("Invalid media executable override: " + name)
        return str(path)
    for prefix in ("/opt/homebrew/opt/ffmpeg-full/bin", "/usr/local/opt/ffmpeg-full/bin"):
        path = Path(prefix) / name
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    path = shutil.which(name)
    if not path:
        raise RuntimeError("Media executable missing: " + name)
    return path


def media_environment() -> dict[str, str]:
    env = os.environ.copy()
    directories = list(dict.fromkeys(str(Path(media_binary(n)).parent) for n in ("ffmpeg", "ffprobe")))
    env["PATH"] = os.pathsep.join(directories + [env.get("PATH", "")])
    return env
