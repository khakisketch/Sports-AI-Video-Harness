#!/usr/bin/env python3
"""Assemble a local review composition from a plan and verified existing assets."""
import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
import subprocess
from media_runtime import media_binary

ROOT = Path(__file__).resolve().parents[1]
FONT = ROOT / "assets/fonts/korean-bold.otf"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source(asset):
    path = (ROOT / asset["path"]).resolve()
    if not path.is_relative_to(ROOT) or not path.is_file():
        raise ValueError("프로젝트 내부의 기존 자산이 필요합니다: " + asset["id"])
    if sha(path) != asset["sha256"]:
        raise ValueError("기준 자산 해시가 바뀌었습니다: " + asset["id"])
    return path


def build(plan_path, out):
    plan_path = plan_path.resolve()
    out = out.resolve()
    if not out.is_relative_to(ROOT):
        raise ValueError("출력은 프로젝트 내부여야 합니다.")
    if (out / "build-manifest.json").exists():
        raise ValueError("기존 검토본을 덮어쓰지 않습니다. 새 버전 출력 폴더를 지정하세요.")
    if (out / "index.html").exists() and '>Title</h1>' not in (out / "index.html").read_text():
        raise ValueError("편집된 HTML이 있습니다. 새 버전 출력 폴더를 지정하세요.")
    plan = json.loads(plan_path.read_text())
    assets = {a["id"]: a for a in plan["assets"]}
    inputs = {key: source(assets[key]) for key in ("instructor-front", "instructor-ready", "motion-reference")}
    duration = plan["brief"]["target_seconds"]
    cycles = plan["motion_cycles"]
    if len(cycles) != 1 or cycles[0]["shot_ids"] != [plan["shots"][0]["id"]] or len(plan["shots"]) != 1:
        raise ValueError("이 프리뷰 템플릿은 컷 없는 동작 한 주기용입니다.")
    phases = cycles[0]["phases"]
    cursor = 0
    for phase in phases:
        if phase["start"] != cursor or phase["end"] <= phase["start"]:
            raise ValueError("동작 구간이 비거나 겹칩니다.")
        cursor = phase["end"]
    if cursor != duration:
        raise ValueError("동작 구간 합계와 전체 길이가 다릅니다.")
    probe = json.loads(subprocess.check_output([media_binary("ffprobe"), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(inputs["motion-reference"])], text=True))
    if abs(float(probe["format"]["duration"]) - duration) > 0.1:
        raise ValueError("참고 영상과 계획의 길이가 다릅니다.")
    if not FONT.is_file():
        raise ValueError("전체 한글 글리프가 있는 로컬 기준 폰트가 없습니다.")
    gsap = ROOT / "tools/hyperframes/node_modules/gsap/dist/gsap.min.js"
    if not gsap.is_file():
        raise ValueError("프로젝트 로컬 GSAP를 먼저 설치하세요.")
    # Resolve every input before writing any output.
    out.mkdir(parents=True, exist_ok=True)
    target = out / "assets"
    target.mkdir(exist_ok=True)
    manifest = []
    copies = [(inputs["instructor-front"], "instructor-full-body.png"), (inputs["instructor-ready"], "instructor-bridge-ready.png"), (inputs["motion-reference"], "motion.mp4"), (FONT, "korean-bold.otf"), (gsap, "gsap.min.js"), (gsap.parent / "MotionPathPlugin.min.js", "MotionPathPlugin.min.js")]
    for src, name in copies:
        shutil.copy2(src, target / name)
        manifest.append({"source": str(src.relative_to(ROOT)), "local": "assets/" + name, "sha256": sha(src)})
    clips = []
    for i, phase in enumerate(phases):
        clips.append(f'<div id="phase-{i}" class="phase-copy clip" data-start="{phase["start"]}" data-duration="{phase["end"]-phase["start"]}" data-track-index="1"><p class="phase-label">{html.escape(phase["label"])}</p><p class="screen-copy">{html.escape(phase["screen_text"])}</p></div>')
    template = (ROOT / "templates/hyperframes/index.html").read_text()
    composition = template.replace("@@DURATION@@", str(duration)).replace("@@TITLE@@", html.escape(plan["brief"]["topic"])).replace("@@PHASES@@", "\n".join(clips))
    (out / "index.html").write_text(composition)
    config = {"paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"}, "media": {"autoProxy": False}}
    (out / "hyperframes.json").write_text(json.dumps(config, indent=2) + "\n")
    (out / "meta.json").write_text(json.dumps({"id": out.name, "name": "기존 자산 · 연출 검토"}, ensure_ascii=False, indent=2) + "\n")
    base = os.path.relpath(ROOT / "scripts/hyperframes.py", out)
    package = {"name": out.name, "private": True, "scripts": {"dev": "python3 " + base + " --project . preview --background", "check": "python3 " + base + " --project . check", "stop": "python3 " + base + " --project . preview --stop"}}
    (out / "package.json").write_text(json.dumps(package, indent=2) + "\n")
    shutil.copy2(ROOT / "templates/hyperframes/AGENTS.md", out / "AGENTS.md")
    (out / "CLAUDE.md").write_text("이 폴더의 AGENTS.md와 저장소 루트 AGENTS.md를 따릅니다. 기존 자산의 로컬 미리보기만 구성합니다.\n")
    (out / "build-manifest.json").write_text(json.dumps({"plan": str(plan_path.relative_to(ROOT)), "plan_sha256": sha(plan_path), "composition_sha256": sha(out / "index.html"), "duration_seconds": duration, "width": 1280, "height": 720, "assets": manifest, "source_probe": probe, "external_generation_calls": 0, "cloud_render_calls": 0, "new_voice": False, "expert_motion_approved": False, "note": "기존 자산을 조합한 로컬 미리보기. 원본 화질 향상·새 가상 강사 시범 생성 아님."}, ensure_ascii=False, indent=2) + "\n")
    print(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    opts = parser.parse_args()
    try:
        build(opts.plan, opts.out)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    main()
