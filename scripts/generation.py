#!/usr/bin/env python3
"""Capability-level generation interface; existing plans/reviews/jobs stay canonical.

Implemented routes delegate to the existing gated adapter. Unsupported model families
and task kinds fail closed. No automatic fallback, price conversion or media generation.
"""
import argparse
import json
import os
from pathlib import Path
import official_media as api
import production as prod

ROOT = prod.ROOT
ADAPTERS = {"official_media": api}


def registry(root=ROOT):
    return prod.read_json(root / "config/generation-capabilities.json")


def capabilities(root=ROOT):
    cfg = registry(root)
    return {**cfg, "readiness": {name: {
        "credential_present": bool(os.environ.get(route["credential_env"])),
        "actual_account_access": "not_probed", "live_quality_verified": False}
        for name, route in cfg["routes"].items()}, "external_calls": 0}


def compile_task(task, plan, root=ROOT):
    cfg = registry(root)
    if task.get("schema_version") != 1 or task.get("kind") not in cfg["kinds"]:
        raise ValueError("확인되지 않은 공통 작업 형식입니다.")
    allowed = {"schema_version", "id", "kind", "route", "model", "shot_ids", "purpose", "success_criteria", "input"}
    if set(task) - allowed:
        raise ValueError("지원하지 않는 작업 필드를 임의로 버리지 않습니다.")
    route = cfg["routes"].get(task.get("route"))
    if not route or route["kind"] != task["kind"] or task.get("model") not in route["models"] or route.get("adapter") not in ADAPTERS:
        raise ValueError("이 작업·모델·공급자 조합은 아직 연결되지 않았습니다.")
    if not prod.text(task.get("id")) or not prod.text(task.get("purpose")) or not task.get("success_criteria"):
        raise ValueError("작업 ID·제작 목적·성공 기준이 필요합니다.")
    shot_ids = task.get("shot_ids", [])
    if not shot_ids or not set(shot_ids) <= {s.get("id") for s in plan.get("shots", [])}:
        raise ValueError("현재 제작안의 장면에 연결하세요.")
    adapter = ADAPTERS[route["adapter"]]
    params = adapter.compile_input(task, plan, root)
    request = {"id": task["id"], "provider": route["legacy_provider"], "shot_ids": shot_ids,
               "purpose": task["purpose"], "success_criteria": task["success_criteria"],
               "task": task, "params": params}
    adapter.validate(request, root)
    return request


def validate_request(request, plan, root=ROOT):
    """Bind optional normalized task to the exact approved legacy wire parameters."""
    if request.get("task"):
        expected = compile_task(request["task"], plan, root)
        for field in ("id", "provider", "shot_ids", "purpose", "success_criteria", "params"):
            if request.get(field) != expected[field]:
                raise ValueError("공통 작업과 실제 실행 입력이 다릅니다: " + field)
        return registry(root)["routes"][request["task"]["route"]]
    # Read existing production records without rewriting hashes, quotes or approvals.
    for route in registry(root)["routes"].values():
        if request.get("provider") == route["legacy_provider"] and request.get("params", {}).get("model") in route["models"]:
            api.validate(request, root)
            return route
    raise ValueError("공통 인터페이스에 실행 가능한 경로가 없습니다. 기존 전문 플랫폼은 별도 연결 도구를 사용하세요.")


def receipt(job):
    state = {"submitted": "queued", "in_progress": "running", "submitting": "submission_unknown"}.get(job.get("status"), job.get("status", "unknown"))
    return {"task_id": job.get("request_id"), "kind": job.get("kind"), "model": job.get("model"),
            "provider_adapter": job.get("provider"), "provider_job_id": job.get("job_id"),
            "state": state, "downloaded": bool(job.get("output_sha256")),
            "output_path": job.get("output_path"), "output_sha256": job.get("output_sha256"),
            "estimate": job.get("quote"), "actual_charge": job.get("actual_charge"),
            "cut_review": job.get("cut_review", "pending"), "generation_completion_is_adoption": False}


def estimate(plan_path, request_id):
    plan, request = api.load_request(plan_path, request_id)
    route = validate_request(request, plan, ROOT)
    return ADAPTERS[route["adapter"]].estimate(request, ROOT)


def submit(plan_path, request_id, transport=api.http):
    plan, request = api.load_request(plan_path, request_id)
    route = validate_request(request, plan, ROOT)
    return receipt(ADAPTERS[route["adapter"]].submit(plan_path, request_id, transport=transport))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("capabilities")
    for name in ("compile", "estimate", "submit", "status", "resume"):
        p = sub.add_parser(name)
        p.add_argument("--plan", required=True, type=Path)
        if name == "compile":
            p.add_argument("--task", required=True, type=Path)
            p.add_argument("--out", type=Path)
        else:
            p.add_argument("--request-id", required=True)
        if name == "submit":
            p.add_argument("--execute", action="store_true")
        if name == "resume":
            p.add_argument("--timeout-seconds", type=int, default=300)
        if name == "status":
            p.add_argument("--download", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "capabilities":
            result = capabilities()
        elif args.command == "compile":
            plan, task = prod.read_json(args.plan), prod.read_json(args.task)
            result = compile_task(task, plan)
            if args.out:
                with args.out.open("x", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
        elif args.command == "estimate":
            result = estimate(args.plan, args.request_id)
        elif args.command == "submit":
            if not args.execute:
                raise ValueError("유료 전송은 현재 승인 기록과 --execute가 필요합니다.")
            result = submit(args.plan, args.request_id)
        else:
            plan_path = prod.asset_path(ROOT, str(args.plan))
            jobs = prod.read_json(plan_path.parent / "jobs.json")
            selected = [j for j in jobs if j.get("request_id") == args.request_id]
            if len(selected) != 1:
                raise ValueError("조회할 기존 작업을 하나 지정하세요.")
            job = selected[0]
            if job.get("provider") == "openai_tts":
                result = receipt(job)
            elif job.get("provider") == "ltx":
                if args.command == "resume":
                    raw = api.resume(plan_path, args.request_id, timeout_seconds=args.timeout_seconds)
                    result = {**raw, "job": receipt(raw["job"])} if "job" in raw else raw
                else:
                    result = receipt(api.status(plan_path, args.request_id, download=args.download))
            else:
                raise ValueError("이 작업은 아직 공통 조회에 연결되지 않았습니다.")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else "작업을 완료하지 못했습니다: " + type(exc).__name__
        print(json.dumps({"ready": False, "error": message}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
