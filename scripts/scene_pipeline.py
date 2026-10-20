#!/usr/bin/env python3
"""Local scene inventory, cut acceptance gate and dependency-based change impact.

Does not generate media, dispatch agents, alter approval records or declare motion accuracy.
"""
import argparse
import hashlib
import json
from pathlib import Path
import production as prod

ROOT = prod.ROOT


def node_fingerprint(nodes, node_id, active=None):
    active = set() if active is None else active
    if node_id in active:
        raise ValueError("자산 의존 관계가 순환합니다: " + node_id)
    if node_id not in nodes:
        raise ValueError("연결되지 않은 자산: " + node_id)
    node = nodes[node_id]
    return prod.digest({"node": node, "dependencies": {
        dep: node_fingerprint(nodes, dep, active | {node_id}) for dep in node.get("depends_on", [])}})


def impact(package, changed):
    nodes = package["nodes"]
    if not changed or not set(changed) <= set(nodes):
        raise ValueError("실제 변경한 자산을 지정하세요.")
    for key in nodes:
        node_fingerprint(nodes, key)
    affected = set(changed)
    while True:
        next_set = affected | {key for key, node in nodes.items() if set(node.get("depends_on", [])) & affected}
        if next_set == affected:
            break
        affected = next_set
    affected_scenes = [s["id"] for s in package["scenes"] if set(s["components"].values()) & affected]
    renders = [k for k in sorted(affected) if nodes[k]["kind"] in ("scene_render", "final_render")]
    return {"changed": sorted(changed), "affected_nodes": sorted(affected), "affected_scenes": affected_scenes,
            "rebuild_renders": renders, "preserve_nodes": sorted(set(nodes) - affected),
            "reviews_to_repeat": sorted(affected), "new_generation_authorized": False,
            "action": "dependency_plan_only; no automatic rebuild or regeneration"}


def audit(package, root=ROOT, stage="inventory", scene_ids=None):
    errors, pending = [], []
    def fail(code, message):
        errors.append({"code": code, "message": message})
    if package.get("schema_version") != 1 or stage not in ("inventory", "edit", "delivery"):
        raise ValueError("장면 패키지 버전·검사 단계를 확인하세요.")
    reference = package.get("plan_ref", {})
    try:
        plan_path = prod.asset_path(root, reference.get("path"))
        if hashlib.sha256(plan_path.read_bytes()).hexdigest() != reference.get("sha256"):
            fail("plan_changed", "기준 제작안이 변경됐습니다. 연결 버전과 영향 범위를 갱신하세요.")
    except (ValueError, OSError):
        fail("plan_ref", "기존 제작 계획 또는 편집안에 연결하세요.")
    nodes, scenes = package.get("nodes", {}), package.get("scenes", [])
    if not nodes or not scenes:
        fail("empty", "실제 자산과 장면 연결이 필요합니다.")
    fingerprints = {}
    for key, node in nodes.items():
        try:
            if not prod.text(node.get("version")):
                raise ValueError("자산 버전이 없습니다: " + key)
            fingerprints[key] = node_fingerprint(nodes, key)
            if node.get("path"):
                path = prod.asset_path(root, node["path"])
                if hashlib.sha256(path.read_bytes()).hexdigest() != node.get("sha256"):
                    fail("asset_changed", "원본 내용이 기록과 달라졌습니다: " + key)
            elif node.get("kind") not in ("scene_render", "final_render"):
                fail("asset_missing", "실제 원본이 필요합니다: " + key)
        except (ValueError, OSError) as exc:
            fail("asset_graph", str(exc))
    ids = [s.get("id") for s in scenes]
    if len(set(ids)) != len(ids) or any(not prod.text(x) for x in ids):
        fail("scene_id", "장면 ID가 없거나 중복됩니다.")
    if scene_ids and not set(scene_ids) <= set(ids):
        fail("scene_selection", "선택한 장면이 없습니다.")
    for scene in scenes:
        if scene_ids and scene.get("id") not in scene_ids:
            continue
        sid = scene["id"]
        start, end = scene.get("start"), scene.get("end")
        if not prod.number(start) or not prod.number(end) or not 0 <= start < end:
            fail("scene_time", "장면 시간 오류: " + sid)
        components = scene.get("components", {})
        if not components or not set(components.values()) <= set(nodes):
            fail("scene_components", "장면과 실제 자산을 연결하세요: " + sid)
            continue
        for role, node_id in components.items():
            node = nodes[node_id]
            if role not in ("video", "voice"):
                continue
            expected_kinds = ("video", "motion_video", "blender_render") if role == "video" else ("audio", "voice")
            if node.get("kind") not in expected_kinds:
                fail("component_kind", "사진·자막을 동작 영상이나 실제 음성으로 전용하지 않습니다: " + node_id)
            required = ["technical", "identity", "framing"] if role == "video" else ["technical", "voice_identity", "pronunciation"]
            if role == "video" and scene.get("performance"):
                required.append("motion")
                if scene.get("exact_motion_required"):
                    required.append("motion_match")
            review = package.get("cut_reviews", {}).get(node_id, {})
            ready = review.get("verdict") == "pass" and review.get("asset_fingerprint") == fingerprints.get(node_id) and prod.text(review.get("reviewer"))
            if review.get("open_defects"):
                ready = False
            for criterion in required:
                check = review.get("checks", {}).get(criterion, {})
                evidence = check.get("evidence", [])
                valid = check.get("verdict") == "pass" and prod.text(check.get("observation")) and bool(evidence)
                if criterion in ("motion", "motion_match"):
                    valid = valid and check.get("method") in ("continuous_video", "time_series_visual", "expert_observation")
                    span = check.get("observed_range_seconds", [])
                    duration = node.get("duration_seconds")
                    valid = valid and len(span) == 2 and all(prod.number(x) for x in span) and prod.number(duration) and span[0] <= .05 and span[1] >= duration - .05
                if criterion in ("pronunciation", "voice_identity"):
                    valid = valid and check.get("method") in ("direct_listening", "expert_observation")
                if criterion == "motion_match":
                    reference_id = scene.get("motion_reference")
                    reference = nodes.get(reference_id, {})
                    valid = valid and bool(reference) and check.get("reference_fingerprint") == fingerprints.get(reference_id)
                    valid = valid and reference.get("expert_status") == "approved" and prod.text(reference.get("expert_record"))
                    if valid:
                        try:
                            prod.asset_path(root, reference["expert_record"])
                        except (ValueError, OSError):
                            valid = False
                for ev in evidence:
                    try:
                        ev_path = prod.asset_path(root, ev.get("path"))
                        valid = valid and hashlib.sha256(ev_path.read_bytes()).hexdigest() == ev.get("sha256")
                    except (ValueError, OSError, AttributeError):
                        valid = False
                ready = ready and valid
            if not ready:
                pending.append({"scene": sid, "asset": node_id, "required_checks": required})
                if stage != "inventory":
                    fail("cut_not_accepted", "실제 컷 검수·변경 후 재검토가 필요합니다: " + sid + "/" + node_id)
        if stage == "delivery":
            if scene.get("voice_required") and "voice" not in components:
                fail("voice_missing", "요청된 안내 음성이 없습니다: " + sid)
            final = package.get("final_review", {})
            render_id = package.get("final_render")
            if not nodes.get(render_id, {}).get("path"):
                fail("final_render_missing", "실제 최종 출력이 없습니다.")
            if not (final.get("verdict") == "pass" and final.get("asset_fingerprint") == fingerprints.get(render_id) and prod.text(final.get("evidence"))):
                fail("final_review_pending", "최종 합성의 별도 전체 검토가 필요합니다.")
            else:
                try:
                    prod.asset_path(root, final["evidence"])
                except (ValueError, OSError) as exc:
                    fail("final_review_evidence", str(exc))
    return {"stage": stage, "ready": not errors, "errors": errors, "pending_cut_reviews": pending,
            "fingerprints": fingerprints, "quality_approved_by_this_check": False,
            "expert_approved_by_this_check": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("check")
    a.add_argument("package", type=Path)
    a.add_argument("--stage", choices=("inventory", "edit", "delivery"), default="inventory")
    a.add_argument("--scene", action="append")
    b = sub.add_parser("impact")
    b.add_argument("package", type=Path)
    b.add_argument("--changed", action="append", required=True)
    args = p.parse_args()
    try:
        package = prod.read_json(args.package)
        result = audit(package, stage=args.stage, scene_ids=args.scene) if args.command == "check" else impact(package, args.changed)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(result.get("ready") is False)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"ready": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
