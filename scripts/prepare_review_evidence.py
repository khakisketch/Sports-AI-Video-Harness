#!/usr/bin/env python3
"""Extract timestamped inspection frames from an existing plan video; no AI calls."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import quality
from media_runtime import media_binary


def prepare(plan_path, asset_id, out, root=quality.ROOT):
    root, out = Path(root).resolve(), Path(out).resolve()
    if not out.is_relative_to(root):
        raise ValueError("근거는 프로젝트 내부에 저장하세요.")
    plan = quality.read(plan_path)
    asset = next(a for a in plan["assets"] if a["id"] == asset_id)
    source = quality.file(root, asset["path"])
    if asset.get("kind") != "motion_video" or quality.sha(source) != asset.get("sha256"):
        raise ValueError("해시가 일치하는 기존 동작 영상이 필요합니다.")
    probe = json.loads(subprocess.check_output([media_binary("ffprobe"), "-v", "error", "-show_format", "-of", "json", str(source)], text=True))
    out.mkdir(parents=True, exist_ok=False)
    process = subprocess.run([media_binary("ffmpeg"), "-hide_banner", "-i", str(source), "-an", "-vf", "select='isnan(prev_selected_t)+gte(t-prev_selected_t,0.4999)',showinfo", "-fps_mode", "vfr", str(out / "frame-%04d.png")], capture_output=True, text=True)
    if process.returncode:
        raise ValueError("프레임 추출 실패: " + process.stderr[-1000:])
    times = [float(t) for t in re.findall(r"\bpts_time:([0-9.]+)", process.stderr)]
    frames = sorted(out.glob("frame-*.png"))
    if len(times) != len(frames) or not frames:
        raise ValueError("프레임과 실제 타임스탬프 수가 일치하지 않습니다.")
    report = {"schema_version": 1, "source_path": str(source.relative_to(root)), "source_sha256": quality.sha(source), "representation": "motion_reference", "coverage": [0, float(probe["format"]["duration"])], "inspection": "uninspected", "note": "실제 시계열 파일 준비만 완료. 시각 검토·운동 정확성 통과 아님.", "frames": [{"time": t, "path": str(p.relative_to(root)), "sha256": quality.sha(p)} for t, p in zip(times, frames)]}
    quality.save_new(out / "sequence.json", report)
    for first in range(1, len(frames) + 1, 12):
        sheet = out / ("sheet-%02d.jpg" % (1 + (first - 1) // 12))
        subprocess.run([media_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-start_number", str(first), "-i", str(out / "frame-%04d.png"), "-vf", "tile=4x3", "-frames:v", "1", str(sheet)], check=True)
    return {"frames": len(frames), "duration": report["coverage"][1], "maximum_gap": max(b - a for a, b in zip(times, times[1:])), "sequence": str((out / "sequence.json").relative_to(root)), "inspection": "uninspected"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.plan, args.asset_id, args.out), ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, StopIteration, subprocess.SubprocessError) as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    main()
