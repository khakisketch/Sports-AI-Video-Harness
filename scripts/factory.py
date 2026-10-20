#!/usr/bin/env python3
"""Project-local production factory: prepare role/tool packets and run safe checks.

This orchestrator is intentionally offline. It never calls a media service,
submits a job, uploads an asset, spends credits, or publishes a deliverable.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from media_runtime import media_binary

ROOT = Path(__file__).resolve().parents[1]
STAGES = ("preproduction", "asset", "generation", "edit", "review", "delivery", "environment")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel_exists(path: str) -> bool:
    return (ROOT / path).is_file() or (ROOT / path).is_dir()


def episode_from_path(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"(?:^|/)episodes/(EP\d+)(?:/|$)", value)
    return match.group(1) if match else None


def current_context() -> dict[str, Any]:
    series = load_json(ROOT / "config/series.json")
    production = series.get("production", {})
    pre = production.get("preproduction", {})
    active_plan = pre.get("active_plan") or pre.get("next_production_plan")
    active_preview = pre.get("active_preview")
    quality_review = pre.get("active_quality_review")
    return {
        "series_name": series.get("series_name"),
        "active_episode": episode_from_path(active_plan) or "EP001",
        "active_plan": active_plan,
        "active_preview": active_preview,
        "active_quality_review": quality_review,
        "generation_authorized": pre.get("current_generation_authorized", False),
        "paid_retries_authorized": series.get("paid_retries_authorized", 0),
        "active_draft_status": pre.get("current_draft_status"),
    }


def _probe_version(binary: str, fallback: str | None = None, version_flag: str = "--version") -> dict[str, Any]:
    try:
        path = media_binary(binary) if binary in ("ffmpeg", "ffprobe") else shutil.which(binary)
    except RuntimeError:
        path = None
    if not path and fallback and Path(fallback).is_file():
        path = fallback
    result: dict[str, Any] = {"binary": binary, "path": path, "available": bool(path)}
    if not path:
        return result
    try:
        probe = subprocess.run([path, version_flag], capture_output=True, text=True, timeout=8, check=False)
        first = (probe.stdout or probe.stderr).splitlines()
        result.update({"version_probe_exit_code": probe.returncode, "version": first[0][:200] if first else None})
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["version_probe_error"] = type(exc).__name__
    return result


def inspect_tools() -> dict[str, Any]:
    registry = load_json(ROOT / "config/media-tools.json")
    tools: dict[str, Any] = {}
    for item in registry["local_tools"]:
        key = item["id"]
        if key == "python":
            tools[key] = {"label": item["label"], "path": sys.executable, "available": True, "version": sys.version.split()[0]}
        elif key == "hyperframes":
            manifest = ROOT / item["runtime_manifest"]
            version = load_json(manifest).get("version") if manifest.is_file() else None
            try:
                sys.path.insert(0, str(ROOT / "scripts"))
                from hyperframes import node_path
                node = str(node_path())
            except (ImportError, RuntimeError, OSError):
                node = None
            tools[key] = {"label": item["label"], "available": bool(version and node), "runtime_version": version, "node_path": node, "adapter": item["adapter"], "adapter_present": rel_exists(item["adapter"])}
        else:
            data = _probe_version(item["binary"], item.get("fallback"), "-version" if key in ("ffmpeg", "ffprobe") else "--version")
            data["label"] = item["label"]
            data["purpose"] = item["purpose"]
            tools[key] = data
    return tools


def inspect_skills() -> dict[str, Any]:
    lock_path = ROOT / "config/external-skills.lock.json"
    if not lock_path.is_file():
        return {"lock_present": False, "locked_count": 0, "installed_count": 0, "hash_matches": 0, "items": []}
    lock = load_json(lock_path)
    items = []
    for source in lock.get("sources", []):
        for skill in source.get("skills", []):
            items.append(skill)
    items.extend(lock.get("project_skills", []))
    checked = []
    for skill in items:
        path = ROOT / skill["path"] / "SKILL.md"
        present = path.is_file()
        actual = sha256(path) if present else None
        expected = skill.get("skill_sha256")
        file_hashes = skill.get("file_sha256", {})
        changed = [name for name, value in file_hashes.items() if not (ROOT / skill["path"] / name).is_file() or sha256(ROOT / skill["path"] / name) != value]
        checked.append({"name": skill.get("name"), "path": skill.get("path"), "installed": present, "hash_matches": bool(present and expected and actual == expected and not changed), "changed_pinned_files": changed})
    return {"lock_present": True, "locked_count": len(checked), "installed_count": sum(x["installed"] for x in checked), "hash_matches": sum(x["hash_matches"] for x in checked), "items": checked}


def audit() -> dict[str, Any]:
    import studio
    required_files = [
        "config/series.json", "config/production-policy.json", "config/tool-routing.json",
        "config/production-roles.json", "config/media-tools.json", "config/quality-rubric.json",
        "config/external-skills.lock.json", "scripts/production.py", "scripts/quality.py",
        "scripts/factory.py", "scripts/hyperframes.py", "scripts/previs.py", "tests/test_factory.py",
        "docs/제작팩토리.md", "docs/제작운영.md",
        "docs/대화형-제작운영.md", "docs/제작계획형식.md", "docs/영상연출-설계기준.md",
        "docs/납품검수운영.md", "templates/production/plan.json",
        "templates/production/reviews.json", "templates/delivery/inspection-items.csv",
        "templates/delivery/result-report-outline.md",
        "scripts/official_media.py", "config/official-media-api.json", "docs/공식API-제작운영.md",
        "scripts/scene_pipeline.py", "scripts/timeline_sync.py", "config/scene-pipeline.json", "docs/반복제작-운영.md",
        "scripts/generation.py", "scripts/production_metrics.py", "config/generation-capabilities.json", "config/runtime-profiles.json", "docs/제작엔진-구조.md",
    ]
    missing = [p for p in required_files if not rel_exists(p)]
    role_cfg = load_json(ROOT / "config/production-roles.json")
    tool_cfg = load_json(ROOT / "config/media-tools.json")
    role_ids = [r["id"] for r in role_cfg.get("roles", [])]
    role_errors = []
    if len(role_ids) != len(set(role_ids)):
        role_errors.append("역할 ID 중복")
    for role in role_cfg.get("roles", []):
        for skill in role.get("skills", []):
            if not (ROOT / ".agents/skills" / skill / "SKILL.md").is_file():
                role_errors.append(f"{role['id']} 참조 스킬 없음: {skill}")
    for stage in STAGES:
        if not any("all" in r.get("stages", []) or stage in r.get("stages", []) for r in role_cfg.get("roles", [])):
            role_errors.append(f"역할 공백: {stage}")
    tools = inspect_tools()
    skills = inspect_skills()
    ctx = current_context()
    local_ready = all(tools.get(k, {}).get("available") for k in ("python", "ffmpeg", "ffprobe", "hyperframes"))
    active_work = {
        "plan_exists": bool(ctx.get("active_plan") and rel_exists(ctx["active_plan"])),
        "preview_exists": bool(ctx.get("active_preview") and rel_exists(ctx["active_preview"])),
        "quality_review_exists": bool(ctx.get("active_quality_review") and rel_exists(ctx["active_quality_review"])),
        "current_generation_authorized": ctx["generation_authorized"],
        "draft_status": ctx.get("active_draft_status"),
    }
    studio_status = studio.audit()
    return {
        "schema_version": 1,
        "checked_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "factory": {"status": "ready" if not missing and not role_errors and local_ready and skills["locked_count"] == skills["hash_matches"] and not studio_status["errors"] else "needs_attention", "entrypoint": "scripts/factory.py", "execution_model": role_cfg.get("execution_model"), "offline_only": True},
        "studio_agents": studio_status,
        "project_files": {"required_count": len(required_files), "present_count": len(required_files) - len(missing), "missing": missing},
        "roles": {"count": len(role_ids), "stages": list(STAGES), "errors": role_errors, "registry": "config/production-roles.json"},
        "tools": tools,
        "skills": skills,
        "external_services": [{"id": x["id"], "status": "route_configured_account_not_probed", "assistant_skill": x.get("assistant_skill"), "tool_namespace": x.get("tool_namespace"), "read_only_preflight_tools": x.get("read_only_preflight_tools", []), "execution": "never called by this script; paid actions require the separate production gate"} for x in tool_cfg.get("external_services", [])],
        "official_api_readiness": __import__("official_media").doctor(ROOT),
        "generation_interface": __import__("generation").capabilities(ROOT),
        "runtime_profiles": load_json(ROOT / "config/runtime-profiles.json"),
        "current_project": ctx,
        "active_work": active_work,
        "authorization": {"generation_authorized": ctx["generation_authorized"], "automatic_paid_retries": 0, "external_calls_made": 0, "upload_or_publish": False},
        "limits": ["로컬 파일·바이너리·스킬 잠금 상태 점검이다.", "외부 서비스 로그인·잔액·모델·가격·권한은 확인하지 않았다.", "도구 설치 성공은 특정 생성 작업의 사용 권한이나 품질 승인이 아니다.", "운동 전문가 확인과 사람의 콘텐츠 승인은 별도다."],
    }


def infer_stage(request: str) -> str:
    text = request.lower()
    patterns = [
        ("environment", r"환경|설치|구축|공장|워크플로|지침|스킬|도구|agent|factory|설정"),
        ("delivery", r"납품|결과보고|검수조서|공유|전달|manifest"),
        ("edit", r"편집|자막|음량|컷|크롭|화질|수정|마감|다듬"),
        ("review", r"검수|검토|qa|품질|자연스럽|이상한지|확인해"),
        ("generation", r"생성|만들어줘|제작 실행|영상 제작|생성해"),
        ("asset", r"이미지|캐릭터|강사|음성|목소리|아바타|자산"),
    ]
    for stage, pattern in patterns:
        if re.search(pattern, text):
            return stage
    return "preproduction"


STAGE_TOOLS = {
    "preproduction": ["python", "hyperframes"],
    "asset": ["python", "blender", "hyperframes"],
    "generation": ["python"],
    "edit": ["python", "hyperframes", "ffmpeg", "ffprobe"],
    "review": ["python", "ffprobe", "hyperframes"],
    "delivery": ["python", "ffmpeg", "ffprobe"],
    "environment": ["python", "blender", "ffmpeg", "ffprobe", "hyperframes"],
}


def prepare(request: str, stage: str | None = None, episode: str | None = None) -> dict[str, Any]:
    import studio
    if not request.strip():
        raise ValueError("요청 내용이 비어 있습니다.")
    role_cfg = load_json(ROOT / "config/production-roles.json")
    registry = load_json(ROOT / "config/media-tools.json")
    ctx = current_context()
    chosen = stage or infer_stage(request)
    if chosen not in STAGES:
        raise ValueError("지원 단계: " + ", ".join(STAGES))
    ep = episode or ctx["active_episode"]
    if not re.fullmatch(r"EP\d{3,}", ep):
        raise ValueError("에피소드 형식은 EP001입니다.")
    roles = [r for r in role_cfg["roles"] if "all" in r.get("stages", []) or chosen in r.get("stages", [])]
    tool_by_id = {t["id"]: t for t in registry["local_tools"]}
    requested_tools = [tool_by_id[x] for x in STAGE_TOOLS[chosen]]
    available = inspect_tools()
    routes = []
    for tool in requested_tools:
        status = available.get(tool["id"], {})
        routes.append({"tool_id": tool["id"], "label": tool["label"], "purpose": tool["purpose"], "available": status.get("available", False), "adapter": tool.get("adapter"), "verification": tool["verification"], "cost": tool["cost"]})
    external = [x for x in registry.get("external_services", []) if chosen in ("generation", "asset") or (chosen == "edit" and x["id"] == "higgsfield")]
    outputs_by_stage = {
        "preproduction": ["brief.md", "plan.json", "script.md", "scenes.csv", "reviews.json", "local animatic when useful"],
        "asset": ["canonical source file", "asset metadata and usage", "reference compatibility check", "visual review"],
        "generation": ["production gate result", "current quote and per-scene budget", "scoped job packet", "jobs.json record if explicitly run", "actual cut review before adoption"],
        "edit": ["scene-package.json linked to current plan", "cut acceptance or explicit internal-preview scope", "dependency-based change impact", "editable composition/source", "clean source retention", "render and edit decision record", "technical check"],
        "review": ["quality-review.json", "timed observed evidence", "open defects and rechecks", "expert status kept separate"],
        "delivery": ["final media manifest", "inspection dossier", "result report", "checksums and handoff status"],
        "environment": ["tool/skill audit", "role coverage", "safe capability gaps", "test report"],
    }
    safe_note = "외부 모델/API 호출, 크레딧 사용, 업로드, 게시를 이 패킷 생성 과정에서 수행하지 않는다."
    generation_gate = None
    if chosen == "generation":
        policy = load_json(ROOT / "config/production-policy.json")
        generation_gate = {"required_checks": policy.get("required_before_generation", []), "current_generation_authorized": ctx["generation_authorized"], "requires_current_quote": True, "automatic_retries": 0, "external_job_submitted": False}
    return {
        "schema_version": 1,
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "request": request,
        "stage": chosen,
        "episode": ep,
        "current_context": ctx,
        "role_execution": role_cfg.get("execution_model"),
        "native_agent_packet": studio.packet(request, chosen, ctx.get("active_plan")),
        "roles_in_order": [{"id": r["id"], "label": r["label"], "prompt": r["prompt"], "skills": r["skills"], "guides": r["guides"], "inputs": r["inputs"], "outputs": r["outputs"]} for r in roles],
        "local_tool_routes": routes,
        "external_capabilities_for_separate_preflight": external,
        "expected_outputs": outputs_by_stage[chosen],
        "generation_gate": generation_gate,
        "scene_pipeline": {
            "guide": "docs/반복제작-운영.md", "config": "config/scene-pipeline.json",
            "scene_checker": "scripts/scene_pipeline.py", "timing_checker": "scripts/timeline_sync.py",
            "post_generation_cut_acceptance_required": chosen in ("generation", "edit", "review", "delivery"),
            "new_edit_recipe_schema": 2, "runs_tools_or_accepts_cuts": False,
            "recovery": "official_media.py resume; persisted job GET/download only, no new generation"},
        "generation_interface": {"entrypoint": "scripts/generation.py", "registry": "config/generation-capabilities.json",
            "execution_environment": "external_hosted_model_API", "research_GPU_required_for_delivery": False,
            "cloud_edit_worker": "architecture_only_not_deployed", "default_specialist_platform_required": False,
            "cost_metric": "actual_total_including_failed_attempts_per_cut_accepted_unique_result; unknown_until_verified"},
        "safety_boundary": safe_note,
    }


def markdown_packet(packet: dict[str, Any]) -> str:
    lines = [f"# 작업 패킷 — {packet['episode']} / {packet['stage']}", "", f"요청: {packet['request']}", "", "## 실행 역할 순서", ""]
    for index, role in enumerate(packet["roles_in_order"], 1):
        lines += [f"{index}. **{role['label']}** (`{role['id']}`): {role['prompt']}", f"   - 스킬: {', '.join(role['skills']) or '없음'}", f"   - 지침: {', '.join(role['guides']) or '없음'}"]
    lines += ["", "## 도구 경로", "", "| 도구 | 가능 여부 | 용도 | 확인 |", "|---|---|---|---|"]
    for route in packet["local_tool_routes"]:
        lines.append(f"| {route['label']} | {'준비' if route['available'] else '미준비/조건부'} | {', '.join(route['purpose'])} | {route['verification']} |")
    if packet["external_capabilities_for_separate_preflight"]:
        lines += ["", "## 선택형 외부 기능", "", "실제 작업에서 품질·일관성 이점이 필요한 경우에만 현재 연결·입력 지원·비용을 조회합니다. 아래 패킷은 외부 작업을 실행하지 않습니다."]
        for item in packet["external_capabilities_for_separate_preflight"]:
            tools = ", ".join(item.get("read_only_preflight_tools", [])) or "작업별 확인"
            lines.append(f"- **{item['label']}** · 스킬 `{item.get('assistant_skill', '작업별 확인')}` · 사전 확인 도구: {tools}")
    lines += ["", "## 예상 산출물", ""] + [f"- {item}" for item in packet["expected_outputs"]]
    if packet["generation_gate"]:
        lines += ["", "## 생성 전 별도 게이트", "", f"현재 생성 승인: `{packet['generation_gate']['current_generation_authorized']}`", "현재 견적·범위·중복 작업 검사를 통과해야 하며 여기서는 제출하지 않습니다."]
    lines += ["", packet["safety_boundary"], ""]
    return "\n".join(lines)


def verify(plan: str, review: str | None, profile: str, production_stage: str = "design") -> dict[str, Any]:
    plan_path = Path(plan)
    if not plan_path.is_absolute():
        plan_path = ROOT / plan_path
    if not plan_path.is_file():
        raise ValueError(f"계획 파일 없음: {plan_path}")
    run = subprocess.run([sys.executable, str(ROOT / "scripts/production.py"), "--root", str(ROOT), "check", str(plan_path), "--stage", production_stage], cwd=ROOT, capture_output=True, text=True, timeout=60, check=False)
    result: dict[str, Any] = {"production_design": _parse_json_output(run), "production_exit_code": run.returncode, "quality": None, "quality_exit_code": None, "limits": "정적 계획·검토 기록의 구조 검증이며 영상 내용의 실제 관찰이 아니다."}
    if review:
        review_path = Path(review)
        if not review_path.is_absolute():
            review_path = ROOT / review_path
        if not review_path.is_file():
            raise ValueError(f"검토 파일 없음: {review_path}")
        q = subprocess.run([sys.executable, str(ROOT / "scripts/quality.py"), "--root", str(ROOT), "check", str(plan_path), "--review", str(review_path), "--profile", profile], cwd=ROOT, capture_output=True, text=True, timeout=60, check=False)
        result["quality"] = _parse_json_output(q)
        result["quality_exit_code"] = q.returncode
    result["ready"] = run.returncode == 0 and (not review or result["quality"].get("quality_ready", False))
    result["plan_path"] = str(plan_path.relative_to(ROOT)) if plan_path.is_relative_to(ROOT) else str(plan_path)
    return result


def _parse_json_output(run: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        return json.loads(run.stdout)
    except (json.JSONDecodeError, TypeError):
        return {"error": (run.stderr or run.stdout)[-5000:], "exit_code": run.returncode}


def audit_markdown(data: dict[str, Any]) -> str:
    lines = ["# 제작 팩토리 준비 점검", "", f"- 상태: **{data['factory']['status']}**", f"- 점검 시각: {data['checked_at']}", f"- 역할 실행 방식: `{data['factory']['execution_model']}`", f"- 오케스트레이터: `{data['factory']['entrypoint']}`", "", "## 도구"]
    for name, info in data["tools"].items():
        state = "사용 가능" if info.get("available") else "미설치/미준비"
        version = info.get("version") or info.get("runtime_version") or ""
        lines.append(f"- **{info.get('label', name)}**: {state} {('— ' + str(version)) if version else ''}")
    lines += ["", f"## 역할·스킬", "", f"- 역할 {data['roles']['count']}개 / 단계 {len(data['roles']['stages'])}개 / 역할 오류 {len(data['roles']['errors'])}개", f"- 잠금된 스킬 {data['skills']['locked_count']}개 / 설치 {data['skills']['installed_count']}개 / 해시 일치 {data['skills']['hash_matches']}개"]
    if data["roles"]["errors"]:
        lines.extend(["", *["- 오류: " + e for e in data["roles"]["errors"]]])
    if data["project_files"]["missing"]:
        lines += ["", "## 빠진 필수 파일", "", *["- " + x for x in data["project_files"]["missing"]]]
    lines += ["", "## 현재 EP 상태", "", f"- 편: {data['current_project']['active_episode']}", f"- 계획/미리보기/품질 기록: {data['active_work']['plan_exists']} / {data['active_work']['preview_exists']} / {data['active_work']['quality_review_exists']}", f"- 현재 생성 승인: `{data['authorization']['generation_authorized']}`", f"- 실행 권한 밖 외부 호출: {data['authorization']['external_calls_made']}"]
    lines += ["", "## 범위와 한계", "", *["- " + x for x in data["limits"]], ""]
    return "\n".join(lines)


def main() -> int:
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    a = sub.add_parser("audit", help="로컬 도구·스킬·구성·역할을 읽기 전용 점검")
    a.add_argument("--out", type=Path, help="결과 JSON을 새 파일로 보관")
    a.add_argument("--markdown", type=Path, help="결과 Markdown을 새 파일로 보관")
    p = sub.add_parser("prepare", help="요청별 역할·도구·산출물 작업 패킷 생성")
    p.add_argument("--request", required=True)
    p.add_argument("--stage", choices=STAGES)
    p.add_argument("--episode")
    p.add_argument("--out", type=Path, help="Markdown 작업 패킷을 새 파일로 보관")
    v = sub.add_parser("verify", help="기존 계획 및 선택적 품질 기록의 오프라인 구조 검사")
    v.add_argument("--plan", required=True)
    v.add_argument("--review")
    v.add_argument("--profile", choices=("generation", "internal_preview"), default="generation")
    v.add_argument("--production-stage", choices=("design", "generate"), default="design")
    v.add_argument("--out", type=Path, help="검증 JSON을 새 파일로 보관")
    args = parser.parse_args()
    root = args.root.resolve()
    ROOT = root
    try:
        if args.command == "audit":
            data = audit()
            print(json.dumps(data, ensure_ascii=False, indent=2))
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                with args.out.open("x", encoding="utf-8") as f:
                    f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            if args.markdown:
                args.markdown.parent.mkdir(parents=True, exist_ok=True)
                with args.markdown.open("x", encoding="utf-8") as f:
                    f.write(audit_markdown(data))
            return 0 if data["factory"]["status"] == "ready" else 2
        if args.command == "prepare":
            packet = prepare(args.request, args.stage, args.episode)
            output = markdown_packet(packet)
            print(output)
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                with args.out.open("x", encoding="utf-8") as f:
                    f.write(output)
            return 0
        result = verify(args.plan, args.review, args.profile, args.production_stage)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("x", encoding="utf-8") as f:
                f.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return 0 if result["ready"] else 2
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ready": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
