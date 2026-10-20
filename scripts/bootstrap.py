#!/usr/bin/env python3
"""Check local prerequisites; download pinned preview packages only on explicit request."""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--install-preview', action='store_true')
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        parser.error('Python 3.10 or newer is required')
    if args.install_preview:
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/setup_hyperframes.py')], cwd=ROOT)
        if result.returncode:
            return result.returncode
    from media_runtime import media_binary
    def available(name):
        try:
            return str(media_binary(name))
        except (FileNotFoundError, RuntimeError):
            return None
    output = {
        'python_supported': True,
        'platform': sys.platform,
        'ffmpeg': available('ffmpeg'),
        'ffprobe': available('ffprobe'),
        'node_on_path': shutil.which('node'),
        'pnpm_on_path': shutil.which('pnpm'),
        'blender_on_path': shutil.which('blender'),
        'preview_packages_present': (ROOT / 'tools/hyperframes/node_modules/hyperframes').exists(),
        'paid_API_tested_by_this_check': False,
        'media_created': False,
        'note': 'Missing media tools limit those features; core offline checks remain usable. See docs/설치.md.'
    }
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/setup_studio_agents.py'), '--check'], cwd=ROOT, capture_output=True, text=True)
    output['agent_definitions_match'] = result.returncode == 0
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
