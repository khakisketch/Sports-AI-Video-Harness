#!/usr/bin/env python3
"""Execute a small local edit recipe through the pinned upstream FFmpeg skill.

The recipe accepts declared local inputs and typed parameters, never arbitrary
commands, URLs, media generation, transcription downloads or service calls.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from media_runtime import media_binary, media_environment

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".agents/skills/ffmpeg-skill/scripts"
PARAMETERS = {
    "cut": {"start", "end", "duration", "accurate", "quality", "preset"},
    "fit": {"aspect", "fit", "width", "height", "quality", "preset"},
    "caption": {"font", "size", "position", "margin", "color", "outline", "bold", "animate", "quality", "language", "max-lines", "fit-size", "emoji"},
    "loudness": {"lufs", "tp", "lra", "sample-rate", "audio-bitrate"},
}
CAPTION_INPUTS = {"srt", "ass"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_file(value: str, root: Path = ROOT) -> Path:
    if not isinstance(value, str) or "://" in value:
        raise ValueError("Only local input files are supported")
    path = Path(value).expanduser()
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_file():
        raise ValueError("Input missing: " + str(path))
    return path


def compile_recipe(plan: dict, out: Path) -> list[dict]:
    if plan.get("schema_version") not in (1, 2) or plan.get("mode") != "local_edit":
        raise ValueError("Expected schema_version=1 or 2, mode=local_edit")
    scene_gate(plan)
    inputs = {}
    for key, item in plan.get("inputs", {}).items():
        path = local_file(item["path"])
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", key):
            raise ValueError("Invalid input ID")
        if item.get("sha256") != digest(path):
            raise ValueError("Input changed or hash missing: " + key)
        inputs[key] = path
    if not inputs:
        raise ValueError("At least one fingerprinted input is required")
    if out.exists():
        raise ValueError("Output directory already exists; use a new version")
    steps, names, outputs = [], set(inputs), set()
    if not plan.get("steps"):
        raise ValueError("Empty recipe")
    for step in plan["steps"]:
        key, tool = step.get("id", ""), step.get("tool")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", key) or key in names:
            raise ValueError("Invalid or duplicate step ID")
        if tool not in PARAMETERS:
            raise ValueError("Unsupported local tool: " + str(tool))
        if set(step) - {"id", "tool", "input", "output", "params", "srt", "ass"}:
            raise ValueError("Unknown step fields")
        source = inputs.get(step.get("input"))
        if source is None:
            raise ValueError("Step input must reference a declared input or earlier output")
        filename = step.get("output", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.mp4", filename) or filename in outputs:
            raise ValueError("Output must be a unique MP4 filename")
        target = out / filename
        params = step.get("params", {})
        if not isinstance(params, dict) or set(params) - PARAMETERS[tool]:
            raise ValueError("Unsupported parameter for " + tool)
        argv = [sys.executable, str(SKILL / (tool + ".py")), str(source), "-o", str(target), "--json", "--timeout", "180"]
        for name, value in params.items():
            if isinstance(value, bool):
                if name not in {"accurate", "bold"}:
                    raise ValueError("Boolean parameter not supported")
                if value:
                    argv += ["--" + name]
            elif type(value) in (str, int, float):
                argv += ["--" + name, str(value)]
            else:
                raise ValueError("Invalid parameter type")
        caption_refs = CAPTION_INPUTS.intersection(step)
        if tool == "caption" and len(caption_refs) != 1:
            raise ValueError("Caption needs exactly one declared SRT or ASS input")
        if tool != "caption" and caption_refs:
            raise ValueError("Subtitle inputs only apply to caption")
        for name in caption_refs:
            if step[name] not in plan["inputs"]:
                raise ValueError("Subtitle must reference a declared input")
            argv += ["--" + name, str(inputs[step[name]])]
        steps.append({"id": key, "tool": tool, "argv": argv, "output": str(target)})
        inputs[key] = target
        names.add(key)
        outputs.add(filename)
    return steps


def scene_gate(plan: dict) -> dict | None:
    if plan.get("schema_version") == 2 and not plan.get("scene_package"):
        raise ValueError("새 제작안에는 장면 패키지를 연결하세요.")
    if not plan.get("scene_package"):
        return None  # Preserve explicit legacy v1 recipes.
    import scene_pipeline
    profile = plan.get("scene_profile", "production")
    if profile not in ("production", "internal_preview"):
        raise ValueError("장면 검사 범위를 구별하세요.")
    package_path = local_file(plan["scene_package"])
    result = scene_pipeline.audit(json.loads(package_path.read_text()), ROOT,
        stage="inventory" if profile == "internal_preview" else "edit", scene_ids=plan.get("scene_ids"))
    if not result["ready"]:
        raise ValueError("컷 채택/자산 점검 미완료: " + ", ".join(sorted({e["code"] for e in result["errors"]})))
    return {"profile": profile, "package_sha256": digest(package_path), "acceptance": "not_required_for_internal_preview" if profile == "internal_preview" else "record_checked",
            "content_quality_automatically_approved": False}


def probe(path: Path) -> dict:
    result = subprocess.run([media_binary("ffprobe"), "-v", "error", "-count_frames", "-show_format", "-show_streams", "-of", "json", str(path)], capture_output=True, text=True, timeout=90, check=True)
    return json.loads(result.stdout)


def run_recipe(plan_path: Path, out: Path, dry_run: bool = False) -> dict:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    commands = compile_recipe(plan, out)
    record = {"schema_version": 1, "mode": "local_edit", "status": "planned", "recipe": str(plan_path.resolve()), "recipe_sha256": digest(plan_path), "ffmpeg": media_binary("ffmpeg"), "ffprobe": media_binary("ffprobe"), "media_service_calls": 0, "steps": commands}
    record["scene_gate"] = scene_gate(plan)
    if dry_run:
        return record
    out.mkdir(parents=True, exist_ok=False)
    record["started_at"] = dt.datetime.now().astimezone().isoformat()
    try:
        for step in record["steps"]:
            current_gate = scene_gate(plan)
            if current_gate != record["scene_gate"]:
                raise ValueError("편집 도중 장면·컷 검토 기록이 변경됐습니다.")
            # Check the immutable sources again before every mutation.
            for item in plan["inputs"].values():
                if digest(local_file(item["path"])) != item["sha256"]:
                    raise ValueError("Input changed during execution")
            step["status"] = "running"
            (out / "manifest.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
            process = subprocess.run(step["argv"], env=media_environment(), capture_output=True, text=True, timeout=240)
            (out / (step["id"] + ".log")).write_text(process.stderr, encoding="utf-8")
            try:
                step["result"] = json.loads(process.stdout)
            except ValueError:
                step["result"] = {"stdout_tail": process.stdout[-1500:]}
            if process.returncode or step["result"].get("status") != "completed":
                step["status"] = "failed"
                raise RuntimeError("Local edit failed: " + step["id"])
            target = Path(step["output"])
            step.update(status="completed", sha256=digest(target), probe=probe(target))
        final = Path(commands[-1]["output"])
        decoded = subprocess.run([media_binary("ffmpeg"), "-v", "error", "-xerror", "-i", str(final), "-f", "null", "-"], capture_output=True, text=True, timeout=180)
        record["full_decode"] = {"exit_code": decoded.returncode, "stderr": decoded.stderr}
        if decoded.returncode:
            raise RuntimeError("Full decode failed")
        record.update(status="completed", final_output=str(final), final_sha256=digest(final), creative_review="pending", audio_listening="pending", expert_approval="pending")
    except Exception as exc:
        record.update(status="failed", error=str(exc), automatic_retries=0)
    (out / "manifest.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("recipe", type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    try:
        result = run_recipe(a.recipe, a.out.resolve(), a.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(result["status"] == "failed")
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
