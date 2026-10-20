import os
import shutil
from pathlib import Path


def executable(name):
    if name in ('ffmpeg', 'ffprobe'):
        # Keep existing explicit overrides while sharing the project resolver.
        override = os.environ.get(name.upper() + '_BIN')
        if override and Path(override).is_file() and os.access(override, os.X_OK):
            return str(Path(override).resolve())
        from media_runtime import media_binary
        return media_binary(name)
    candidates = [os.environ.get(name.upper() + '_BIN'), shutil.which(name)]
    if name == 'blender':
        candidates += ['/Applications/Blender.app/Contents/MacOS/Blender', '/Applications/Blender.app/Contents/MacOS/blender']
    candidates += ['/opt/homebrew/bin/' + name, '/usr/local/bin/' + name]
    for item in candidates:
        if item and Path(item).is_file() and os.access(item, os.X_OK):
            return str(Path(item).resolve())
    raise RuntimeError(name + ' 실행 파일을 찾지 못했습니다.')
