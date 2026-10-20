#!/usr/bin/env python3
"""Offline production planning checks. Never submits, uploads or bills anything."""
import argparse
import hashlib
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROLES = ("story", "direction", "motion", "feasibility")
GRAPHIC_SHOTS = ("title_card", "graphic")


def read_json(path):
    def bad_constant(value):
        raise ValueError("non-finite JSON number: " + value)
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=bad_constant)


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def text(value):
    return isinstance(value, str) and bool(value.strip())


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def asset_path(root, value):
    if not text(value):
        raise ValueError("참조 파일 경로가 미정입니다.")
    p = (root / value).resolve()
    if not p.is_relative_to(root.resolve()):
        raise ValueError("참조 파일은 프로젝트 안에 보관하세요: " + value)
    if not p.is_file():
        raise ValueError("참조 파일 없음: " + value)
    return p


def fingerprints(plan, root):
    design = {k: v for k, v in plan.items() if k != "execution"}
    snapshots = []
    for a in plan.get("assets", []):
        try:
            actual = hashlib.sha256(asset_path(root, a.get("path")).read_bytes()).hexdigest()
        except ValueError:
            actual = None
        snapshots.append({"id": a.get("id"), "actual_sha256": actual})
    design_hash = digest({"design": design, "files": snapshots})
    # Price refreshes do not invalidate an unchanged design or already sufficient cap.
    requests = [{k: v for k, v in r.items() if k != "quote"} for r in plan.get("execution", {}).get("requests", [])]
    execution_hash = digest({"design_hash": design_hash, "requests": requests, "retries": plan.get("execution", {}).get("retries", 0)})
    return {"design_hash": design_hash, "execution_hash": execution_hash}


def direction_errors(plan, require_animatic=False):
    """Check a timed directing plan, never the artistic truth of its contents."""
    errors = []
    def require(ok, code, message):
        if not ok:
            errors.append({"code": code, "message": message})
    direction = plan.get("direction")
    if not isinstance(direction, dict):
        return [{"code": "direction_missing", "message": "화면·행동의 시간 설계가 없습니다. 새 계획 버전에 direction을 추가하세요."}]
    for field in ("opening_visual", "closing_payoff", "visual_style", "sound_strategy"):
        require(text(direction.get(field)), "direction_detail", "연출 설계 미정: " + field)
    level = direction.get("preview_level")
    require(level in ("layout_only", "animatic", "motion_blocking"), "direction_preview_level", "미리보기 검증 범위를 layout_only/animatic/motion_blocking으로 구별하세요.")
    if require_animatic:
        require(level in ("animatic", "motion_blocking"), "direction_layout_only", "사진·문구 배치 검토만으로 생성용 연출을 통과시킬 수 없습니다. 시간에 따른 화면·행동을 검토하세요.")
    beats = direction.get("beats")
    if not isinstance(beats, list) or not beats:
        errors.append({"code": "direction_beats", "message": "시간에 따른 화면·행동 비트를 작성하세요. 샷 수와 비트 수는 다를 수 있습니다."})
        return errors
    shots = {s.get("id"): s for s in plan.get("shots", []) if isinstance(s, dict)}
    ids, cursor = set(), 0
    representations = []
    for beat in beats:
        if not isinstance(beat, dict):
            errors.append({"code": "direction_beat", "message": "각 연출 비트는 객체여야 합니다."})
            continue
        bid = beat.get("id")
        require(text(bid) and bid not in ids, "direction_beat_id", "연출 비트 ID가 없거나 중복됩니다.")
        if text(bid):
            ids.add(bid)
        a, b = beat.get("start"), beat.get("end")
        valid_time = number(a) and number(b) and b > a
        require(valid_time, "direction_beat_time", "비트 시간 오류: " + str(bid))
        shot = shots.get(beat.get("shot_id"))
        require(shot is not None, "direction_beat_shot", "비트를 실제 장면에 연결하세요: " + str(bid))
        if valid_time:
            require(abs(a - cursor) < .001, "direction_beat_gap", "화면·행동 시간표의 공백·겹침: " + str(bid))
            cursor = b
            if shot is not None:
                require(number(shot.get("start")) and number(shot.get("end")) and shot["start"] <= a < b <= shot["end"], "direction_beat_shot", "비트는 연결한 장면의 시간 안에 있어야 합니다: " + str(bid))
        for field in ("new_information", "subject_action", "camera_action", "attention_target", "audio_plan", "exit_state"):
            require(text(beat.get(field)), "direction_beat_detail", str(bid) + " 연출 미정: " + field)
        development = beat.get("development")
        require(development in ("action", "comparison", "reveal", "graphic_explanation", "intentional_hold"), "direction_development", "행동·비교·발견·설명 도식·의도한 유지 중 화면 전개를 지정하세요: " + str(bid))
        if development == "intentional_hold":
            require(text(beat.get("hold_reason")), "direction_hold_reason", "정적으로 머무는 동안 볼 이유를 기록하세요: " + str(bid))
        representation = beat.get("representation")
        require(representation in ("existing_motion", "blender_blocking", "animated_graphic", "composed_graphic", "static_reference", "unbuilt"), "direction_representation", "비트의 실제 미리보기 표현과 한계를 구별하세요: " + str(bid))
        if representation == "composed_graphic":
            require(shot is not None and shot.get("shot_type") in GRAPHIC_SHOTS, "direction_representation", "합성 제목·도식을 인물 시범의 동작 근거로 사용할 수 없습니다: " + str(bid))
        representations.append(representation)
    total = plan.get("brief", {}).get("target_seconds")
    require(number(total) and abs(cursor - total) < .001, "direction_beat_end", "화면·행동 시간표가 전체 길이를 덮어야 합니다.")
    if require_animatic:
        require(all(r != "unbuilt" for r in representations) and any(r in ("existing_motion", "blender_blocking", "animated_graphic", "composed_graphic") for r in representations), "direction_representation", "미구현 구간이나 정지 참고만으로 연출 검토를 완료할 수 없습니다. 실제 시계열 관찰은 품질 검토에 별도 연결하세요.")
    return errors


def audit(plan_path, root=ROOT, stage="design", request_id=None):
    plan_path, root = Path(plan_path), Path(root)
    plan = read_json(plan_path)
    reviews = read_json(plan_path.with_name("reviews.json"))
    ledger = read_json(plan_path.with_name("jobs.json"))
    policy = read_json(root / "config/production-policy.json")
    errors, warnings = [], []

    def require(ok, code, message):
        if not ok:
            errors.append({"code": code, "message": message})

    require(plan.get("schema_version") == 1, "schema", "지원하는 계획 버전은 1입니다.")
    brief = plan.get("brief", {})
    for key in ("topic", "viewer_question", "takeaway", "format"):
        require(text(brief.get(key)), "brief", "브리프 미정: " + key)
    total = brief.get("target_seconds")
    require(number(total) and total > 0, "duration", "목표 길이를 양수로 지정하세요.")
    require(type(brief.get("customer_preview")) is bool, "audience", "customer_preview는 true/false여야 합니다.")
    require(bool(re.fullmatch(r"[1-9]\d*:[1-9]\d*", str(brief.get("aspect_ratio", "")))), "ratio", "화면비를 지정하세요.")
    if policy.get("direction_design", {}).get("required", False):
        errors.extend(direction_errors(plan, require_animatic=True))
    cast = plan.get("cast", [])
    cast_ids = [c.get("id") for c in cast]
    has_performance = any(s.get("shot_type", "performance") not in GRAPHIC_SHOTS for s in plan.get("shots", []))
    require((bool(cast) or not has_performance) and all(text(v) for v in cast_ids) and len(set(cast_ids)) == len(cast_ids), "cast", "인물 장면에는 구분 가능한 출연자·역할을 지정하세요.")
    for c in cast:
        require(text(c.get("role")) and text(c.get("character_record")), "cast", "출연자의 역할과 기준 자산 기록이 필요합니다.")
        if text(c.get("character_record")):
            try:
                character = read_json(asset_path(root, c["character_record"]))
                require(character.get("current_design_status") == "appearance_adopted_by_user", "appearance", "인물 외형 채택 기록을 확인하세요: " + str(c.get("id")))
            except (ValueError, OSError):
                require(False, "appearance", "캐릭터 기준 기록을 읽을 수 없습니다.")
    assets = {}
    for a in plan.get("assets", []):
        aid = a.get("id")
        require(text(aid) and aid not in assets, "asset_id", "자산 ID가 없거나 중복됩니다.")
        assets[aid] = a
        try:
            p = asset_path(root, a.get("path"))
            actual = hashlib.sha256(p.read_bytes()).hexdigest()
            require(a.get("sha256") == actual, "asset_changed", "자산 해시가 없거나 변경됨: " + str(aid))
        except ValueError as e:
            require(False, "asset_missing", str(e))
    shots = plan.get("shots", [])
    require(bool(shots), "shots", "장면표가 없습니다.")
    ids, previous_end, by_id = [], 0, {}
    for shot in shots:
        sid = shot.get("id")
        require(text(sid) and sid not in ids, "shot_id", "장면 ID가 없거나 중복됩니다.")
        ids.append(sid)
        by_id[sid] = shot
        start, end = shot.get("start"), shot.get("end")
        times_ok = number(start) and number(end) and end > start
        require(times_ok, "shot_time", "장면 시간 오류: " + str(sid))
        if times_ok:
            require(abs(start - previous_end) < 0.001, "timeline_gap", "장면 사이 공백·겹침: " + str(sid))
            previous_end = end
        shot_type = shot.get("shot_type", "performance")
        require(shot_type in ("performance", *GRAPHIC_SHOTS), "shot_type", "인물 시범·제목 카드·도식을 구별하세요: " + str(sid))
        graphic = shot_type in GRAPHIC_SHOTS
        fields = ("purpose", "action", "view", "framing", "speech_mode")
        if not graphic:
            fields += ("start_pose", "end_pose")
        for key in fields:
            require(text(shot.get(key)), "shot_detail", str(sid) + " 미정: " + key)
        if not graphic or shot.get("cast_id") is not None:
            require(shot.get("cast_id") in cast_ids, "cast", "장면 출연자 미지정: " + str(sid))
        require(isinstance(shot.get("dialogue"), str), "dialogue", "대본 미작성: " + str(sid))
        estimate = shot.get("speech_estimate_seconds")
        require(number(estimate) and estimate >= 0 and (not times_ok or estimate <= end - start), "speech_fit", "발화 예상 시간이 미정이거나 장면 길이를 초과함: " + str(sid))
        if isinstance(shot.get("dialogue"), str) and number(estimate):
            require((text(shot["dialogue"]) and estimate > 0) or (not text(shot["dialogue"]) and estimate == 0), "speech_fit", "무음 장면은 0초, 대사가 있으면 발화 예상 시간을 지정하세요.")
        refs = shot.get("reference_ids", [])
        require(isinstance(refs, list) and (bool(refs) or graphic) and all(r in assets for r in refs), "references", "사용한 실제 참조 자산을 연결하세요: " + str(sid))
    if shots and number(total):
        require(abs(previous_end - total) < .001, "timeline_end", "장면표와 목표 길이가 다릅니다.")
    if brief.get("format") == "self_demonstration":
        require(len(set(s.get("cast_id") for s in shots if s.get("cast_id") is not None)) <= 1, "role_switch", "한 명의 시범 강사로 이어지도록 구성하세요.")
        require(not any(c.get("role") != "instructor" for c in cast), "role_switch", "본인 시범 형식에서 강사·수강생 이중 역할을 사용하지 않습니다.")
    transitions = plan.get("transitions", [])
    require(len(transitions) == max(0, len(shots) - 1), "transition_count", "장면 경계마다 전환 이유와 연결 상태가 필요합니다.")
    for i, tr in enumerate(transitions):
        if i + 1 >= len(shots):
            break
        before, after = shots[i], shots[i + 1]
        require(tr.get("from") == before.get("id") and tr.get("to") == after.get("id"), "transition_order", "전환 순서가 장면표와 다릅니다.")
        require(text(tr.get("purpose")) and text(tr.get("continuity_explanation")), "transition_reason", "왜 자르는지, 자세가 어떻게 이어지는지 기록하세요.")
        if tr.get("continuity") == "matched_pose":
            require(before.get("shot_type", "performance") == after.get("shot_type", "performance") == "performance" and text(before.get("end_pose")) and before.get("end_pose") == after.get("start_pose"), "pose_jump", "matched_pose는 실제 인물 장면의 일치하는 자세에만 사용하세요.")
        else:
            require(tr.get("continuity") == "visible_change", "pose_jump", "일치하는 자세 또는 화면에 보이는 자세 전환을 설계하세요.")
    cycles = plan.get("motion_cycles", [])
    if brief.get("format") == "self_demonstration":
        require(bool(cycles), "motion_cycle", "준비→수행→복귀를 한 번 온전히 보여주는 구간이 없습니다.")
    for cycle in cycles:
        sids = cycle.get("shot_ids", [])
        require(len(sids) == 1 and sids[0] in by_id, "cycle_cut", "첫 동작 한 주기는 하나의 끊기지 않는 장면으로 설계하세요.")
        if len(sids) == 1 and sids[0] in by_id:
            require(by_id[sids[0]].get("shot_type", "performance") == "performance", "motion_proof", "제목 카드·도식을 실제 운동 한 주기로 사용할 수 없습니다.")
        phases = cycle.get("phases", [])
        names = [p.get("phase") for p in phases]
        require(len(phases) >= 3 and names[0] == "prepare" and names[-1] == "return", "cycle_phases", "준비·수행·복귀 단계가 필요합니다.")
        last = None
        for phase in phases:
            a, b = phase.get("start"), phase.get("end")
            ok = number(a) and number(b) and b > a
            require(ok, "phase_time", "동작 단계 시간 오류")
            if ok and sids and sids[0] in by_id:
                sh = by_id[sids[0]]
                require(number(sh.get("start")) and number(sh.get("end")) and sh["start"] <= a < b <= sh["end"], "phase_time", "동작 단계가 장면 밖에 있습니다.")
            if ok and last is not None:
                require(abs(a - last) < .001, "phase_gap", "동작 단계가 끊기거나 겹칩니다.")
            if ok:
                last = b
        proof = assets.get(cycle.get("proof_asset_id"), {})
        require(proof.get("kind") in ("motion_video", "blender_scene"), "motion_proof", "정지 사진만으로 동작 연속성을 통과시킬 수 없습니다.")
        suffix = Path(str(proof.get("path", ""))).suffix.lower()
        require((proof.get("kind") == "motion_video" and suffix in (".mp4", ".mov", ".webm", ".mkv")) or (proof.get("kind") == "blender_scene" and suffix == ".blend"), "motion_proof", "동작 근거의 실제 파일 종류를 확인하세요.")
        require(cycle.get("proof_covers_full_cycle") is True, "motion_proof", "기준 자료가 상승만이 아니라 한 주기 전체를 보여주는지 확인하세요.")
    fp = fingerprints(plan, root)
    # Technical metadata and short role verdicts are insufficient for quality.
    quality_result = None
    if policy.get("quality_review", {}).get("required", True):
        reference = reviews.get("quality_review")
        require(text(reference), "quality_review_missing", "장면별 관찰·반례·결함 재검수가 있는 품질 검토를 연결하세요.")
        if text(reference):
            try:
                import quality
                quality_result = quality.audit(plan_path, asset_path(root, reference), root, "generation")
                errors.extend(e for e in quality_result["errors"] if e not in errors)
            except (ValueError, OSError, KeyError, TypeError, IndexError, StopIteration) as exc:
                require(False, "quality_review_invalid", "품질 검토를 확인할 수 없습니다: " + str(exc))
    for role in ROLES:
        r = reviews.get("roles", {}).get(role, {})
        require(r.get("verdict") == "pass" and text(r.get("notes")) and bool(r.get("evidence")), "review_" + role, role + " 검토·근거가 미완료입니다.")
        if r.get("verdict") == "pass":
            require(r.get("design_hash") == fp["design_hash"], "stale_review", role + " 검토 이후 설계·자산이 바뀌었습니다.")
        for ev in r.get("evidence", []):
            try:
                asset_path(root, ev)
            except ValueError as e:
                require(False, "review_evidence", str(e))
    approval = reviews.get("design_approval", {})
    require(approval.get("decision") == "approved" and text(approval.get("source")) and approval.get("design_hash") == fp["design_hash"], "design_approval", "현재 설계에 대한 사용자 검토·채택이 필요합니다. 기존 대화 승인으로 충족되면 기록하고 다시 묻지 않습니다.")
    if stage == "generate":
        execution = plan.get("execution", {})
        require(reviews.get("roles", {}).get("feasibility", {}).get("execution_hash") == fp["execution_hash"], "execution_feasibility", "최종 모델 입력에 대한 실행 가능성 검토가 필요합니다.")
        require(type(execution.get("retries")) is int and execution.get("retries") == 0, "retry_policy", "이 도구는 자동 유료 재시도 0회 실행안을 점검합니다. 추가 시도는 새 실행안으로 기록하세요.")
        requests = execution.get("requests", [])
        require(bool(requests), "requests", "실행 입력과 실제 견적이 없습니다.")
        selected = [r for r in requests if request_id is None or r.get("id") == request_id]
        require(bool(selected), "request_id", "선택한 실행 요청이 없습니다.")
        request_ids = [r.get("id") for r in requests]
        require(all(text(i) for i in request_ids) and len(set(request_ids)) == len(request_ids), "request_id", "실행 요청 ID가 없거나 중복됩니다.")
        costs, scene_costs = {}, {}
        for r in requests:
            params = r.get("params", {})
            quote = r.get("quote", {})
            require(text(params.get("model")) and text(params.get("prompt")), "request_params", "모델과 최종 프롬프트를 확정하세요.")
            require(text(r.get("purpose")) and bool(r.get("success_criteria")), "test_goal", "이번 요청으로 확인할 불확실성과 성공 기준을 지정하세요.")
            require(params.get("count") == 1 and type(params.get("count")) is int, "request_count", "요청당 생성 수는 1로 분리해 기록하세요.")
            require(bool(r.get("shot_ids")) and all(v in by_id for v in r.get("shot_ids", [])), "request_shots", "실행 요청과 장면을 연결하세요.")
            require(quote.get("params_hash") == digest(params) and text(quote.get("checked_at")), "quote_changed", "현재 입력에 대한 실제 견적을 다시 조회하세요.")
            provider = r.get("provider", "higgsfield")
            if provider in ("ltx", "openai_tts"):
                try:
                    if r.get("task"):
                        import generation
                        generation.validate_request(r, plan, root)
                    import official_media
                    official_media.validate_quote(r, root)
                    if provider == "ltx" and params.get("operation") == "image-to-video":
                        require(any(v.get("path") == params.get("image_path") and v.get("sha256") == params.get("image_sha256") for v in assets.values()),
                                "request_asset", "API 기준 이미지를 검토한 설계 자산에 연결하세요.")
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    require(False, "official_quote", "공식 API 입력·가격 근거를 확인하세요: " + str(exc))
                amount, unit = quote.get("amount"), quote.get("currency")
                require(unit == "USD" and "credits" not in quote and number(amount) and amount >= 0,
                        "quote_missing", "공식 API 달러 견적과 크레딧을 구별하세요.")
                require(reviews.get("execution_approval", {}).get("accept_published_rate_estimate") is True,
                        "estimate_acceptance", "공개 요금 기준 추정이며 세금·환율·실제 청구액은 별도라는 실행 범위를 기록하세요.")
            else:
                require(provider == "higgsfield", "provider_unsupported", "실행 점검이 구현되지 않은 공급자입니다.")
                amount, unit = quote.get("credits"), "credits"
                require(number(amount) and amount >= 0 and "currency" not in quote, "quote_missing", "정확한 크레딧 견적이 없습니다.")
            if number(amount) and amount >= 0:
                key = (provider, unit)
                costs[key] = costs.get(key, 0) + amount
                if provider in ("ltx", "openai_tts"):
                    for shot_id in r.get("shot_ids", []):
                        scene_key = (shot_id, provider, unit)
                        scene_costs[scene_key] = scene_costs.get(scene_key, 0) + amount
            if brief.get("customer_preview") is True and provider != "openai_tts":
                value = str(params.get("resolution", ""))
                m = re.fullmatch(r"(\d+)p", value)
                xy = re.fullmatch(r"(\d+)x(\d+)", value)
                edge = int(m.group(1)) if m else min(map(int, xy.groups())) if xy else 0
                require(edge >= policy["customer_min_short_edge"], "customer_resolution", "고객 검토본은 기본 720p 이상입니다. 사용자 별도 지시가 있으면 정책과 실행안을 명시적으로 갱신하세요.")
        for r in selected:
            for job in ledger:
                if job.get("params_hash") == digest(r.get("params", {})) and job.get("provider", "higgsfield") == r.get("provider", "higgsfield") and job.get("status") in ("submitting", "submitted", "in_progress", "completed", "submission_unknown", "failed", "canceled", "rejected"):
                    require(False, "duplicate_job", "기존 작업 조회·재사용이 우선입니다: " + str(job.get("job_id")))
        a = reviews.get("execution_approval", {})
        require(a.get("decision") == "approved" and text(a.get("source")) and a.get("execution_hash") == fp["execution_hash"], "execution_approval", "현재 입력·버전의 실행 승인 기록이 필요합니다.")
        require(type(a.get("retries")) is int and a.get("retries") == 0, "cost_cap", "자동 재시도 범위는 0회입니다.")
        for (provider, unit), cost in costs.items():
            budget = a.get("budgets", {}).get(provider, {})
            cap = a.get("max_credits") if provider == "higgsfield" and unit == "credits" else budget.get("max_amount")
            correct_unit = (provider == "higgsfield" and unit == "credits") or budget.get("currency") == unit == "USD"
            require(correct_unit and number(cap) and cap >= cost - 1e-10, "cost_cap", "공급자별 승인 한도가 견적 합계보다 작거나 비용 단위가 다릅니다: " + str(provider))
        if policy.get("official_api", {}).get("scene_budget_required", False):
            for (shot_id, provider, unit), cost in scene_costs.items():
                cap = a.get("scene_budgets", {}).get(shot_id, {}).get(provider, {})
                require(cap.get("currency") == unit and number(cap.get("max_amount")) and cap["max_amount"] >= cost - 1e-10,
                        "scene_cost_cap", "장면별 공급자 비용 한도를 확인하세요: " + str(shot_id) + "/" + str(provider))
    warnings.append("이 점검은 기록·참조 일치만 검사합니다. 실제 동작·연출·청취 검토와 사용자 결정을 대신하지 않습니다.")
    return {"stage": stage, "ready": not errors, **fp, "quality": quality_result, "errors": errors, "warnings": warnings}


def packet(plan_path, root=ROOT):
    p = read_json(plan_path)
    fp = fingerprints(p, root)
    b = p["brief"]
    lines = ["# 영상 기획·연출 검토", "", "이 문서는 계획을 읽기 쉽게 정리한 검토본입니다. 문서 출력은 영상 관찰·사용자 채택·실행 승인이 아닙니다.", ""]
    labels = {"topic": "기획 주제", "viewer_question": "시청자 질문", "takeaway": "보고 나서 이해할 내용", "format": "형식", "target_seconds": "목표 길이(초)", "aspect_ratio": "화면비"}
    for key, label in labels.items():
        value = b.get(key) or "미정"
        if key == "format" and value == "self_demonstration":
            value = "강사 한 명의 직접 시범"
        lines.append("- **" + label + "**: " + str(value))
    clean = lambda v: str(v if v is not None else "미정").replace("|", "／").replace("\n", "<br>")
    direction = p.get("direction", {})
    lines += ["", "## 연출 의도와 보는 흐름", ""]
    if text(direction.get("editorial_intent")):
        lines += [clean(direction["editorial_intent"]), ""]
    for field, label in (("opening_visual", "처음 보게 될 화면"), ("closing_payoff", "끝에서 확인할 답"), ("visual_style", "화면의 공통 기준"), ("sound_strategy", "말·소리·정적의 역할")):
        lines.append("- **" + label + "**: " + clean(direction.get(field)))
    lines += ["", "다음은 장면의 의미와 연결을 읽는 순서입니다. 시간·발화는 계획이며 실측 여부를 별도로 확인합니다.", ""]
    transitions = {t.get("to"): t for t in p.get("transitions", [])}
    for shot in p.get("shots", []):
        lines.append("**" + clean(shot.get("id")) + " · " + clean(shot.get("purpose")) + "**")
        kind = shot.get("shot_type", "performance")
        kind_label = {"performance": "인물 시범·연기", "title_card": "제목 카드", "graphic": "설명 도식"}.get(kind, kind)
        lines.append("\n화면 종류: " + clean(kind_label))
        if text(shot.get("production_method")):
            lines.append("\n제작 방식: " + clean(shot["production_method"]) + " · " + clean(shot.get("production_reason")))
        lines.append("\n보이는 행동: " + clean(shot.get("action")) + "  \n화면: " + clean(shot.get("view")))
        if shot.get("dialogue") == "":
            lines.append("\n대사 없음. 정적·음악·효과음의 유무는 위 소리 계획과 실제 미디어에서 확인합니다.")
        else:
            lines.append("\n대사: “" + clean(shot.get("dialogue")) + "”")
        if shot.get("id") in transitions:
            tr = transitions[shot["id"]]
            lines.append("\n앞 장면에서 이어지는 이유: " + clean(tr.get("purpose")) + " · " + clean(tr.get("continuity_explanation")))
        lines.append("")
    lines += ["미리보기 검증 범위: " + clean(direction.get("preview_level")) + ". 정지 배치·대역·실제 생성 결과와 아직 없는 음성을 구분해서 읽습니다.", ""]
    lines += ["", "## 장면과 전환", "", "| 시간 | 장면·목적 | 실제 화면·동작 | 대사 / 예상 발화 시간 | 구도·자세 연결 |", "|---|---|---|---|---|"]
    for s in p.get("shots", []):
        pose_labels = p.get("pose_labels", {})
        pose = lambda k: pose_labels.get(s.get(k), s.get(k))
        framing = "전신" if s.get("framing") == "full_body" else s.get("framing")
        speech = clean(s.get("dialogue")) if s.get("dialogue") != "" else "의도한 무음"
        estimate = "미정" if s.get("speech_estimate_seconds") is None else str(s["speech_estimate_seconds"]) + "초(추정)"
        connection = "화면 상태·전환 설명 참조" if s.get("shot_type") in GRAPHIC_SHOTS else str(pose("start_pose")) + " → " + str(pose("end_pose"))
        lines.append("| " + " | ".join(clean(v) for v in [str(s.get("start")) + "–" + str(s.get("end")) + "초", str(s.get("id")) + ": " + str(s.get("purpose")), s.get("action"), speech + " / " + estimate, str(s.get("view")) + " · " + str(framing) + " · " + connection]) + " |")
    if not p.get("transitions"):
        lines.append("\n현재 예시는 한 장면을 연속해서 보여주는 설계이며 중간 화면 전환이 없습니다.")
    for t in p.get("transitions", []):
        lines.append("\n- 전환 " + clean(t.get("from")) + " → " + clean(t.get("to")) + ": " + clean(t.get("purpose")) + "; " + clean(t.get("continuity_explanation")))
    direction = p.get("direction", {})
    lines += ["", "## 화면·행동의 시간 설계", "", "미리보기 범위: " + clean(direction.get("preview_level")), ""]
    for field, label in (("opening_visual", "첫 화면"), ("closing_payoff", "마지막 회수"), ("visual_style", "화면 톤"), ("sound_strategy", "소리의 역할")):
        lines.append("- " + label + ": " + clean(direction.get(field)))
    lines += ["", "| 시간·장면 | 새 정보·화면 전개 | 인물·카메라 | 주시점·소리 | 끝 상태·시안 표현 |", "|---|---|---|---|---|"]
    for beat in direction.get("beats", []):
        lines.append("| " + " | ".join(clean(v) for v in [str(beat.get("start")) + "–" + str(beat.get("end")) + " / " + str(beat.get("shot_id")), str(beat.get("new_information")) + " / " + str(beat.get("development")), str(beat.get("subject_action")) + " / " + str(beat.get("camera_action")), str(beat.get("attention_target")) + " / " + str(beat.get("audio_plan")), str(beat.get("exit_state")) + " / " + str(beat.get("representation"))]) + " |")
    lines += ["", "## 동작 한 주기", ""]
    phase_labels = {"prepare": "준비", "rise": "올리기", "hold": "유지", "lower": "내리기", "return": "복귀", "action": "수행"}
    for cycle in p.get("motion_cycles", []):
        lines += ["| 계획 시간 | 동작 단계 |", "|---|---|"]
        for phase in cycle.get("phases", []):
            lines.append("| " + clean(phase.get("start")) + "–" + clean(phase.get("end")) + "초 | " + clean(phase_labels.get(phase.get("phase"), phase.get("phase"))) + " |")
        lines.append("\n동작 자료 대조: " + ("범위 확인 기록 있음(운동 전문가 승인은 별도)" if cycle.get("proof_covers_full_cycle") is True else "미완료. 시간표만으로 실제 동작을 확인했다고 판단하지 않음."))
    lines += ["", "## 기존 자료와 재사용", ""]
    for a in p.get("assets", []):
        target = (root / a.get("path", "")).resolve()
        lines.append("- [" + clean(a.get("id")) + "](<" + str(target) + ">) / " + clean(a.get("kind")))
    result = audit(plan_path, root)
    lines += ["", "## 남은 검토", ""] + ["- " + e["code"] + ": " + e["message"] for e in result["errors"]]
    lines += ["", "## 버전 식별", "", "- design_hash: " + fp["design_hash"], "- execution_hash: " + fp["execution_hash"], "", "검토와 승인은 reviews.json에 실제 대화·근거와 함께 기록합니다. 이 문서 생성은 승인이나 생성 실행이 아닙니다."]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--episode", required=True)
    init.add_argument("--version", required=True)
    for name in ("check", "packet", "fingerprints"):
        p = sub.add_parser(name)
        p.add_argument("plan", type=Path)
        if name == "check":
            p.add_argument("--stage", choices=("design", "generate"), default="design")
            p.add_argument("--request")
            p.add_argument("--report", type=Path)
        if name == "packet":
            p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        if args.command == "init":
            if not re.fullmatch(r"EP\d{3,}", args.episode) or not re.fullmatch(r"[a-zA-Z0-9_-]+", args.version):
                raise ValueError("편 번호·버전 형식 오류")
            folder = root / "episodes" / args.episode / "planning" / args.version
            folder.mkdir(parents=True, exist_ok=False)
            plan = read_json(root / "templates/production/plan.json")
            plan.update(episode_id=args.episode, version=args.version)
            write_json(folder / "plan.json", plan)
            write_json(folder / "reviews.json", read_json(root / "templates/production/reviews.json"))
            write_json(folder / "jobs.json", [])
            print(folder)
        elif args.command == "packet":
            if args.out.exists():
                raise ValueError("기존 검토 문서 보존: 새 파일명을 사용하세요.")
            args.out.write_text(packet(args.plan, root), encoding="utf-8")
            print(args.out)
        elif args.command == "fingerprints":
            print(json.dumps(fingerprints(read_json(args.plan), root), indent=2))
        else:
            result = audit(args.plan, root, args.stage, args.request)
            if args.report:
                write_json(args.report, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ready"] else 2
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as e:
        print(json.dumps({"ready": False, "error": str(e)}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
