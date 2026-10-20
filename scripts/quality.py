#!/usr/bin/env python3
"""Evidence-bound preproduction review records. No model/API/generation calls."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
VISUAL = {"ai_visual", "human_visual", "ai_temporal", "human_temporal", "ai_sequence", "human_sequence"}
TEMPORAL = {"ai_temporal", "human_temporal"}


def read(path):
    def invalid(value):
        raise ValueError("비유한 JSON 수치: " + value)
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=invalid)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def file(root, value):
    if not nonempty(value):
        raise ValueError("근거 경로가 비어 있습니다.")
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("프로젝트 내부의 실제 파일이 필요합니다: " + str(value))
    return path


def save_new(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def requirements(plan, rubric):
    total = plan["brief"]["target_seconds"]
    units = {"episode": [("episode", [0, total])],
             "shot": [(s["id"], [s["start"], s["end"]]) for s in plan["shots"]],
             "transition": [(t["from"] + "->" + t["to"], [next(s["end"] for s in plan["shots"] if s["id"] == t["from"]) - .25, next(s["start"] for s in plan["shots"] if s["id"] == t["to"]) + .25]) for t in plan["transitions"]],
             "cycle": [(c["id"], [c["phases"][0]["start"], c["phases"][-1]["end"]]) for c in plan["motion_cycles"]]}
    items = [{"key": c["id"] + ":" + target, "criterion": c["id"], "unit": c["unit"], "target": target, "window": window, "evidence_mode": c["evidence"], "question": c["question"]} for c in rubric["criteria"] for target, window in units[c["unit"]]]
    graphic_ids = {s["id"] for s in plan["shots"] if s.get("shot_type") in ("title_card", "graphic")}
    graphic_questions = {
        "action_clarity": "제목·도식이 전달할 정보가 실제 화면에서 읽히며, 없는 인물 행동을 보여준다고 오해시키지 않는가?",
        "staging_performance": "실제 표시 시간에 읽기·강조·정적 유지의 이유가 성립하는가? 정지 카드라면 불필요한 움직임 없이도 목적을 전달하는가?",
        "framing_focus": "글자·도식의 크기·대비·안전 영역·겹침이 적합하고 필요한 정보를 가리지 않는가?",
        "identity_role": "제목·출처·스타일·인물 표시가 앞뒤 맥락과 일치하며 역할이나 실제 동작을 오해시키지 않는가?",
        "instruction_alignment": "문구·도식·실제 대사의 내용과 표시 시점이 맞는가? 무음·제목만의 구간은 그 목적에 맞는가?",
    }
    for item in items:
        if item["unit"] == "shot" and item["target"] in graphic_ids:
            item["question"] = graphic_questions.get(item["criterion"], item["question"])
    return items


def preview_snapshot(root, manifest_path, plan_path):
    manifest_file = file(root, manifest_path)
    data = read(manifest_file)
    if data.get("plan_sha256") != sha(plan_path) or file(root, data.get("plan")) != Path(plan_path).resolve():
        raise ValueError("미리보기와 현재 계획의 경로·해시가 다릅니다.")
    project = manifest_file.parent
    composition = file(root, str((project / "index.html").relative_to(root)))
    if data.get("composition_sha256") != sha(composition):
        raise ValueError("미리보기 HTML과 조립 기록이 다릅니다. 수정한 결과와 기록을 먼저 대조하세요.")
    files = {manifest_file, composition}
    for folder in ("assets", "compositions", "styles", "scripts"):
        for path in (project / folder).rglob("*"):
            if path.is_file():
                files.add(file(root, str(path.relative_to(root))))
    for path in project.glob("*"):
        if path.is_file() and path.suffix in (".html", ".css", ".js", ".mjs"):
            files.add(path)
    if (project / "hyperframes.json").exists():
        files.add(project / "hyperframes.json")
    # Check original-to-copy provenance, rather than trusting a manifest flag.
    for asset in data.get("assets", []):
        copied = file(root, str((project / asset["local"]).relative_to(root)))
        if not copied.is_relative_to(project) or sha(copied) != asset["sha256"] or sha(file(root, asset["source"])) != asset["sha256"]:
            raise ValueError("미리보기 자산과 원본 해시 불일치: " + asset["local"])
    rows = [{"path": str(p.relative_to(root)), "sha256": sha(p)} for p in sorted(files)]
    return {"manifest_path": manifest_path, "files": rows, "hash": digest(rows)}


def initialize(plan_path, review_path, manifest_path=None, root=ROOT, profile="generation"):
    import production
    root, plan_path = Path(root).resolve(), Path(plan_path).resolve()
    plan = read(plan_path)
    rubric = read(root / "config/quality-rubric.json")
    result = {"schema_version": 1, "profile": profile, "plan_path": str(plan_path.relative_to(root)), "design_hash": production.fingerprints(plan, root)["design_hash"], "rubric_hash": digest(rubric), "reviewer": {"kind": "ai_assisted", "passes": "same_agent_sequential", "scope": "preproduction_only"}, "preview": preview_snapshot(root, manifest_path, plan_path) if manifest_path else None, "technical_evidence_id": None, "technical_warnings": [], "evidence": [], "assessments": [], "findings": [], "risks": [], "overall_verdict": "pending"}
    for item in requirements(plan, rubric):
        result["assessments"].append({**item, "verdict": "pending", "rating": None, "observation": "", "countercheck": "", "rationale": "", "evidence_ids": [], "revision": ""})
    save_new(review_path, result)
    return result


def temporal(evidence, window, root, gap):
    coverage = evidence.get("coverage", [])
    if len(coverage) != 2 or coverage[0] > window[0] + .01 or coverage[1] < window[1] - .01:
        return False
    if evidence.get("kind") == "video" and evidence.get("inspection") in TEMPORAL:
        return Path(evidence["path"]).suffix.lower() in (".mp4", ".mov", ".webm")
    if evidence.get("kind") == "frame_sequence" and evidence.get("inspection") in {"ai_sequence", "human_sequence"}:
        sequence = read(file(root, evidence["path"]))
        if sha(file(root, sequence["source_path"])) != sequence["source_sha256"]:
            return False
        samples = sequence.get("frames", [])
        if not samples or any(sha(file(root, sample["path"])) != sample["sha256"] for sample in samples):
            return False
        times = [sample["time"] for sample in samples]
        return times == sorted(set(times)) and times[0] <= window[0] + gap and times[-1] >= window[1] - gap and max((b - a for a, b in zip(times, times[1:])), default=999) <= gap + .001
    return False


def visible_at(evidence, window, root, gap):
    if evidence.get("inspection") not in VISUAL:
        return False
    if evidence.get("kind") == "frame":
        return any(window[0] <= t <= window[1] for t in evidence.get("times", []))
    return temporal(evidence, window, root, gap)


def audit(plan_path, review_path, root=ROOT, required_profile="generation"):
    import production
    root, plan_path = Path(root).resolve(), Path(plan_path).resolve()
    errors = []
    def require(ok, code, message):
        if not ok:
            errors.append({"code": code, "message": message})
    plan, review = read(plan_path), read(review_path)
    rubric = read(root / "config/quality-rubric.json")
    fp = production.fingerprints(plan, root)
    require(review.get("schema_version") == 1, "quality_schema", "품질 검토 버전 오류")
    require(review.get("profile") == required_profile, "quality_scope", "내부 도구 시안을 생성용 검토 승인으로 전용할 수 없습니다.")
    if required_profile == "generation":
        require(plan["brief"].get("format") != "existing_asset_previsualization", "quality_representation", "기존 자산 참고 보드를 고객용 강사 시범 설계로 승인할 수 없습니다.")
        policy = read(root / "config/production-policy.json")
        if policy.get("direction_design", {}).get("required", False):
            errors.extend(production.direction_errors(plan, require_animatic=True))
    require(file(root, review.get("plan_path")) == plan_path, "quality_plan", "다른 계획의 검토입니다.")
    require(review.get("design_hash") == fp["design_hash"], "quality_stale_design", "설계·기준 자산이 바뀌어 품질 재검토가 필요합니다.")
    require(review.get("rubric_hash") == digest(rubric), "quality_stale_rubric", "품질 기준이 바뀌었습니다.")
    if review.get("preview"):
        current = preview_snapshot(root, review["preview"]["manifest_path"], plan_path)
        require(review["preview"] == current, "quality_stale_preview", "HTML·폰트·JS·미디어·구성이 바뀌어 실제 화면을 다시 검토해야 합니다.")
    else:
        require(False, "quality_preview_missing", "현재 계획과 연결된 실제 HyperFrames 미리보기가 없습니다.")
    evidence = {}
    for item in review.get("evidence", []):
        eid = item.get("id")
        require(nonempty(eid) and eid not in evidence, "quality_evidence_id", "근거 ID 누락·중복")
        require(item.get("sha256") == sha(file(root, item.get("path"))), "quality_evidence_changed", "근거 파일 변경: " + str(eid))
        suffixes = {"frame": (".png", ".jpg", ".jpeg", ".webp"), "video": (".mp4", ".mov", ".webm"), "frame_sequence": (".json",)}
        if item.get("kind") in suffixes:
            require(Path(item["path"]).suffix.lower() in suffixes[item["kind"]], "quality_evidence_type", "선언한 근거 종류와 실제 파일 종류가 다릅니다: " + str(eid))
        evidence[eid] = item
    technical = evidence.get(review.get("technical_evidence_id"), {})
    if technical and technical.get("kind") == "technical_report":
        result = read(file(root, technical["path"]))
        require(technical.get("preview_hash") == (review.get("preview") or {}).get("hash"), "quality_technical_binding", "기술 검사를 수행한 미리보기 버전을 연결하세요.")
        require(result.get("ok") is True and result.get("browserSkipped") is False, "quality_technical", "실제 브라우저 기술 검사가 통과하지 않았습니다.")
        require(result.get("lint", {}).get("errorCount") == 0 and result.get("runtime", {}).get("errorCount") == 0 and result.get("layout", {}).get("errorCount") == 0, "quality_technical", "기술 오류가 남았습니다.")
        for warning in result.get("lint", {}).get("findings", []):
            if warning.get("severity") == "warning":
                require(any(w.get("code") == warning["code"] and w.get("decision") == "accepted" and nonempty(w.get("reason")) for w in review.get("technical_warnings", [])), "quality_warning_unreviewed", "기술 권고의 처리 근거가 필요합니다: " + warning["code"])
    else:
        require(False, "quality_technical", "HyperFrames check의 실제 보고서를 연결하세요.")
    required = requirements(plan, rubric)
    assessments = review.get("assessments", [])
    keys = [a.get("key") for a in assessments]
    require(len(keys) == len(set(keys)) and set(keys) == {r["key"] for r in required}, "quality_coverage", "편·각 장면·모든 전환·동작 주기를 빠짐없이 각각 평가해야 합니다.")
    rows = {a.get("key"): a for a in assessments}
    ratings = []
    for req in required:
        item = rows.get(req["key"], {})
        label = req["key"]
        require(item.get("verdict") == "pass", "quality_unpassed", "미통과: " + label)
        rating = item.get("rating")
        require(type(rating) is int and rubric["minimum_rating"] <= rating <= 4, "quality_rating", "미평가 또는 근거 있는 기준 점수 미달: " + label)
        if type(rating) is int:
            ratings.append(rating)
        require(all(nonempty(item.get(k)) for k in ("observation", "countercheck", "rationale")), "quality_reasoning", "관찰·반례 확인·판단 근거가 필요합니다: " + label)
        ids = item.get("evidence_ids", [])
        require(bool(ids) and all(eid in evidence for eid in ids), "quality_evidence_missing", "평가에 실제 근거를 연결하세요: " + label)
        proofs = [evidence[eid] for eid in ids if eid in evidence]
        if req["unit"] == "cycle":
            cycle = next(c for c in plan["motion_cycles"] if c["id"] == req["target"])
            expected = next(a["sha256"] for a in plan["assets"] if a["id"] == cycle["proof_asset_id"])
            def relates(e):
                if e.get("kind") == "video":
                    return e.get("sha256") == expected
                if e.get("kind") == "frame_sequence":
                    return read(file(root, e["path"])).get("source_sha256") == expected
                return e.get("kind") == "frame" and e.get("preview_hash") == (review.get("preview") or {}).get("hash")
            proofs = [e for e in proofs if relates(e)]
            require(bool(proofs), "quality_motion_source", "다른 동작 자료를 이 주기의 근거로 전용할 수 없습니다: " + label)
        if req["evidence_mode"] in ("visual", "temporal"):
            require(any(visible_at(e, req["window"], root, rubric["maximum_sequence_gap_seconds"]) for e in proofs), "quality_visual_unseen", "해당 시점의 실제 관찰이 필요합니다. 자동 로그·계획표만으로 통과할 수 없습니다: " + label)
        if req["evidence_mode"] == "temporal":
            require(any(temporal(e, req["window"], root, rubric["maximum_sequence_gap_seconds"]) for e in proofs), "quality_temporal_unseen", "한 주기 전체를 관찰한 영상 또는 충분한 시계열 근거가 필요합니다: " + label)
        if req["unit"] == "transition":
            cut = sum(req["window"]) / 2
            observed = [time for e in proofs if e.get("inspection") in VISUAL for time in e.get("times", [])]
            require(any(t < cut for t in observed) and any(t >= cut for t in observed), "quality_seam_pair", "전환 직전·직후를 쌍으로 관찰해야 합니다: " + label)
        if req["criterion"] == "support_visibility":
            cycle = next(c for c in plan["motion_cycles"] if c["id"] == req["target"])
            for phase in cycle["phases"]:
                require(any(visible_at(e, [phase["start"], phase["end"]], root, rubric["maximum_sequence_gap_seconds"]) for e in proofs), "quality_phase_unseen", "지지점은 모든 동작 단계에서 확인하세요: " + req["target"] + "/" + phase["phase"])
    for finding in review.get("findings", []):
        require(finding.get("assessment_key") in rows, "quality_finding_target", "결함과 평가 항목을 연결하세요.")
        require(finding.get("severity") in ("blocker", "major", "minor"), "quality_finding_severity", "결함 심각도 미정")
        if finding.get("severity") in ("blocker", "major"):
            require(finding.get("status") == "resolved", "quality_open_defect", "중대 결함이 남았습니다: " + str(finding.get("id")))
        if finding.get("status") == "resolved":
            recheck = finding.get("recheck", {})
            after = recheck.get("evidence_ids", [])
            require(nonempty(finding.get("fix")) and recheck.get("design_hash") == fp["design_hash"] and nonempty(recheck.get("observation")) and bool(after) and all(e in evidence for e in after), "quality_unverified_fix", "수정했다고 적는 것만으로 결함을 닫을 수 없습니다.")
            before_hashes = {evidence[e]["sha256"] for e in finding.get("before_evidence_ids", []) if e in evidence}
            after_hashes = {evidence[e]["sha256"] for e in after if e in evidence}
            require(bool(before_hashes) and bool(after_hashes - before_hashes), "quality_same_fix_evidence", "결함 전·후의 다른 실제 근거가 필요합니다.")
            require(any(evidence[e].get("inspection") in VISUAL for e in after if e in evidence), "quality_fix_unseen", "시각 결함의 수정 후 결과를 실제로 관찰해야 합니다.")
        elif finding.get("severity") == "minor" and finding.get("status") == "accepted":
            require(nonempty(finding.get("acceptance_reason")), "quality_unexplained_exception", "경미한 예외도 처리 이유가 필요합니다.")
        elif finding.get("severity") not in ("blocker", "major"):
            require(finding.get("status") == "resolved", "quality_open_defect", "열린 결함이 남았습니다: " + str(finding.get("id")))
    requests = {r["id"]: r for r in plan.get("execution", {}).get("requests", [])}
    for risk in review.get("risks", []):
        require(nonempty(risk.get("unknown")) and nonempty(risk.get("validation")), "quality_risk", "미확인 사항과 확인 방법을 기록하세요.")
        require(risk.get("status") in ("resolved", "test_first"), "quality_unresolved_risk", "해결되지 않은 불확실성이 있습니다.")
        if risk.get("status") == "test_first":
            require(risk.get("request_id") in requests and bool(requests.get(risk.get("request_id"), {}).get("success_criteria")), "quality_risk_test", "불확실성은 성공 기준이 있는 최소 시험 요청과 연결해야 합니다.")
        if risk.get("status") == "resolved":
            require(bool(risk.get("evidence_ids")) and all(e in evidence for e in risk["evidence_ids"]), "quality_risk_proof", "불확실성 해결 근거가 없습니다.")
    return {"quality_ready": not errors, "profile": required_profile, "design_hash": fp["design_hash"], "required_assessments": len(required), "passed_assessments": sum(a.get("verdict") == "pass" and type(a.get("rating")) is int and a["rating"] >= rubric["minimum_rating"] for a in assessments), "minimum_rating": min(ratings) if ratings else None, "errors": errors, "limits": "기록의 일관성과 근거 범위를 검사합니다. AI 관찰의 진실성·운동 정확성·흥행·생성 결과를 자동 보증하지 않습니다."}


def packet(plan_path, review_path, root=ROOT):
    review = read(review_path)
    result = audit(plan_path, review_path, root, review["profile"])
    lines = ["# 생성 전 품질 검토", "", "판정: " + ("통과" if result["quality_ready"] else "보류"), "프로필: " + result["profile"], "", "기술 통과·내용 통과·사용자 채택·실행 승인을 각각 구분합니다.", "", "| 범위 | 질문 | 점수·상태 | 실제 관찰 | 오해·반례 확인 | 수정 |", "|---|---|---|---|---|---|"]
    clean = lambda value: str(value or "미확인").replace("|", "／").replace("\n", " ")
    for item in review["assessments"]:
        lines.append("| " + " | ".join(clean(v) for v in [item["key"], item["question"], str(item.get("rating")) + " / " + item["verdict"], item["observation"], item["countercheck"], item["revision"]]) + " |")
    lines += ["", "## 결함과 조치", ""]
    for issue in review["findings"]:
        lines.append("- " + clean(issue.get("id")) + " · " + clean(issue.get("severity")) + " · " + clean(issue.get("status")) + ": " + clean(issue.get("description")) + " → " + clean(issue.get("fix")))
    lines += ["", "## 실제 근거", ""]
    for e in review["evidence"]:
        target = file(Path(root), e["path"])
        lines.append("- [" + e["id"] + "](<" + str(target) + ">) · " + e["kind"] + " · " + str(e.get("inspection", "uninspected")))
        if e["kind"] == "frame":
            lines.append("\n![" + e["id"] + "](<" + str(target) + ">)\n")
    lines += ["", "## 다음 단계 전 해결할 항목", ""] + ["- " + e["code"] + ": " + e["message"] for e in result["errors"]]
    lines += ["", result["limits"], ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "check", "packet"):
        cmd = sub.add_parser(name)
        cmd.add_argument("plan", type=Path)
        cmd.add_argument("--review", type=Path, required=True)
        if name == "init":
            cmd.add_argument("--preview-manifest")
            cmd.add_argument("--profile", choices=("generation", "internal_preview"), default="generation")
        elif name == "check":
            cmd.add_argument("--profile", choices=("generation", "internal_preview"), default="generation")
            cmd.add_argument("--report", type=Path)
        else:
            cmd.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        if args.command == "init":
            initialize(args.plan, args.review, args.preview_manifest, root, args.profile)
            print(args.review)
        elif args.command == "packet":
            with args.out.open("x", encoding="utf-8") as handle:
                handle.write(packet(args.plan, args.review, root))
            print(args.out)
        else:
            result = audit(args.plan, args.review, root, args.profile)
            if args.report:
                save_new(args.report, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["quality_ready"] else 2
    except (ValueError, OSError, KeyError, TypeError, IndexError, StopIteration) as exc:
        print(json.dumps({"quality_ready": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
