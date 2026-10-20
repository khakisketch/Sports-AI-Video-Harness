#!/usr/bin/env python3
"""Restore the pinned project-local preview runtime; no generation or accounts."""
import os
from pathlib import Path
import shutil
import subprocess
from hyperframes import ROOT, node_path


def main():
    node = node_path()
    pnpm = shutil.which("pnpm")
    if not pnpm:
        pnpm = next((str(p) for p in (Path.home() / ".cache/codex-runtimes").glob("*/dependencies/bin/fallback/pnpm") if p.is_file()), None)
    if not pnpm:
        raise SystemExit("pnpm이 필요합니다. Node.js 22+와 pnpm을 준비한 뒤 다시 실행하세요.")
    env = dict(os.environ, PATH=str(node.parent) + os.pathsep + os.environ.get("PATH", ""), HYPERFRAMES_NO_TELEMETRY="1", HYPERFRAMES_SKIP_SKILLS="1")
    raise SystemExit(subprocess.call([pnpm, "install", "--frozen-lockfile"], cwd=ROOT / "tools/hyperframes", env=env))


if __name__ == "__main__":
    main()
