#!/usr/bin/env python3
"""Project-local HyperFrames commands for existing-asset previews only."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
LOCAL_COMMANDS = {"init", "lint", "check", "snapshot", "preview", "timeline", "info", "history", "doctor", "usage"}


def default_project():
    series = json.loads((ROOT / "config/series.json").read_text(encoding="utf-8"))
    value = series.get("production", {}).get("preproduction", {}).get("active_preview")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("현재 활성 미리보기가 없습니다. 새 기획의 미리보기를 구성한 뒤 연결하세요. 과거 결과를 확인하려면 --project 경로를 명시하세요.")
    return ROOT / value


def node_path():
    configured = os.environ.get("SPORTS_PREVIEW_NODE")
    candidates = [configured, shutil.which("node")]
    candidates += [str(p) for p in (Path.home() / ".cache/codex-runtimes").glob("*/dependencies/node/bin/node")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            major = int(subprocess.check_output([candidate, "-p", "process.versions.node.split('.')[0]"], text=True).strip())
            if major >= 22:
                return Path(candidate)
    raise RuntimeError("Node.js 22+가 필요합니다. SPORTS_PREVIEW_NODE로 실행 파일을 지정할 수 있습니다.")


def command_and_environment(command, project, args):
    if command not in LOCAL_COMMANDS:
        raise ValueError("미리보기 전용 명령이 아닙니다. 생성·TTS·업로드·클라우드·MP4 렌더는 이 실행기에서 지원하지 않습니다.")
    project = Path(project).resolve()
    if not project.is_relative_to(ROOT) or not project.is_dir():
        raise ValueError("미리보기 폴더는 프로젝트 내부의 기존 디렉터리여야 합니다.")
    entry = ROOT / "tools/hyperframes/node_modules/hyperframes/bin/hyperframes.mjs"
    if not entry.is_file():
        raise RuntimeError("로컬 HyperFrames가 없습니다. docs/외부스킬-운영.md의 재설치 절차를 확인하세요.")
    node = node_path()
    env = dict(os.environ)
    env["PWD"] = str(project)
    env["PATH"] = str(node.parent) + os.pathsep + env.get("PATH", "")
    env["HYPERFRAMES_NO_TELEMETRY"] = "1"
    env["HYPERFRAMES_SKIP_SKILLS"] = "1"
    env["HYPERFRAMES_RUN_ID"] = "sports-local-preview-v1"
    # This CLI is deliberately outside all hosted-generation workflows.
    for key in ("HEYGEN_API_KEY", "HEYGEN_API_BASE", "FAL_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY"):
        env.pop(key, None)
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if chrome.is_file():
        env.setdefault("PUPPETEER_EXECUTABLE_PATH", str(chrome))
    # Explicit path avoids Studio selecting the parent shell's project name.
    positional = [str(project)] if command == "preview" else []
    return [str(node), str(entry), command, *positional, *args], env, project


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path)
    parser.add_argument("command")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    opts = parser.parse_args()
    try:
        project = opts.project if opts.project is not None else default_project()
        cmd, env, cwd = command_and_environment(opts.command, project, opts.arguments)
        return subprocess.call(cmd, cwd=cwd, env=env)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
