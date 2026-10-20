#!/usr/bin/env python3
"""Inspect existing audio activity and check supplied speech/action/caption timing.

No ASR/TTS/model downloads. Activity regions are not word boundaries or listening evidence.
"""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
import production as prod
from media_runtime import media_binary

ROOT = prod.ROOT


def audio_duration(path):
    result = subprocess.run([media_binary("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                            check=True, capture_output=True, text=True, timeout=60)
    return float(json.loads(result.stdout)["format"]["duration"])


def activity(path, root=ROOT):
    path = prod.asset_path(root, str(path))
    duration = audio_duration(path)
    proc = subprocess.run([media_binary("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path),
        "-vn", "-af", "silencedetect=noise=-35dB:d=0.15", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120, check=True)
    events = [(kind, float(value)) for kind, value in re.findall(r"silence_(start|end):\s*([0-9.]+)", proc.stderr)]
    spans, cursor = [], 0.0
    for kind, value in events:
        value = min(duration, value)
        if kind == "start" and value > cursor:
            spans.append([round(cursor, 4), round(value, 4)])
        if kind == "end":
            cursor = value
    if events and events[-1][0] == "start":
        cursor = duration
    if cursor < duration:
        spans.append([round(cursor, 4), round(duration, 4)])
    return {"audio_path": str(path.relative_to(root.resolve())), "audio_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "duration_seconds": duration, "method": "acoustic_activity_silencedetect",
            "threshold_db": -35, "minimum_silence_seconds": .15, "activity_regions_seconds": spans,
            "word_alignment": "not_performed", "direct_listening": "not_performed",
            "external_calls": 0, "quality_approved": False}


def check(spec, root=ROOT, duration=None):
    audio = spec["audio"]
    path = prod.asset_path(root, audio["path"])
    errors, pending = [], []
    def fail(code, message):
        errors.append({"code": code, "message": message})
    if hashlib.sha256(path.read_bytes()).hexdigest() != audio.get("sha256"):
        fail("audio_changed", "음성 원본이 변경됐습니다.")
    duration = audio_duration(path) if duration is None else duration
    offset = spec.get("audio_timeline_offset", 0)
    if not prod.number(offset) or offset < 0:
        raise ValueError("오디오 배치 시점을 확인하세요.")
    speech, actions = spec.get("speech", []), spec.get("actions", [])
    if not speech:
        fail("speech_missing", "실제 발화 구간 또는 확인 대기 기록이 필요합니다.")
    by_id, action_ids = {}, {}
    for s in speech:
        key, a, b = s.get("id"), s.get("start"), s.get("end")
        if not prod.text(key) or key in by_id or not prod.text(s.get("text")) or not prod.number(a) or not prod.number(b) or not 0 <= a < b <= duration:
            fail("speech_range", "발화 ID·원본 오디오 내 구간 오류")
            continue
        by_id[key] = s
        if s.get("timing_basis") not in ("forced_alignment", "manual_listening"):
            pending.append("발화 시점 확인: " + key)
        else:
            ev = s.get("timing_evidence", {})
            try:
                evidence = prod.asset_path(root, ev.get("path"))
                if hashlib.sha256(evidence.read_bytes()).hexdigest() != ev.get("sha256"):
                    fail("timing_evidence", "발화 정렬 근거가 바뀌었습니다: " + key)
            except (ValueError, OSError):
                fail("timing_evidence", "발화 정렬 근거가 없습니다: " + key)
    for a in actions:
        key = a.get("id")
        if not prod.text(key) or key in action_ids or not prod.number(a.get("onset")) or a["onset"] < 0:
            fail("action", "실제 동작 시점이 미정이거나 중복됩니다.")
            continue
        action_ids[key] = a
        if a.get("timing_basis") != "observed_output":
            pending.append("실제 동작 시점 확인: " + key)
    results = []
    for cue in spec.get("cues", []):
        s, a = by_id.get(cue.get("speech_id")), action_ids.get(cue.get("action_id"))
        anchor = cue.get("speech_anchor", "start")
        lo, hi = cue.get("lead_min_seconds"), cue.get("lead_max_seconds")
        if not s or not a or anchor not in ("start", "end") or not prod.number(lo) or not prod.number(hi) or lo > hi:
            fail("cue", "안내와 동작의 연결·허용 간격을 확인하세요.")
            continue
        lead = a["onset"] - (offset + s[anchor])
        ok = lo - 1e-6 <= lead <= hi + 1e-6
        results.append({"speech": s["id"], "action": a["id"], "lead_seconds": round(lead, 4), "within_window": ok})
        if not ok:
            fail("cue_timing", "안내가 동작에 비해 늦거나 빠릅니다: " + s["id"])
    if actions and not spec.get("cues"):
        fail("cues_missing", "운동과 안내의 연결이 없습니다.")
    linked = set()
    tolerance = spec.get("caption_tolerance_seconds", .15)
    if not prod.number(tolerance) or not 0 <= tolerance <= .5:
        raise ValueError("자막 허용 오차는 0~0.5초 범위로 명시하세요.")
    for caption in spec.get("captions", []):
        s = by_id.get(caption.get("speech_id"))
        if not s:
            fail("caption", "자막을 실제 발화에 연결하세요.")
            continue
        linked.add(s["id"])
        if not all(prod.number(caption.get(k)) for k in ("start", "end")):
            fail("caption_timing", "자막 시간이 없습니다.")
            continue
        if abs(caption["start"] - offset - s["start"]) > tolerance or abs(caption["end"] - offset - s["end"]) > tolerance:
            fail("caption_timing", "자막과 발화 시점이 다릅니다: " + s["id"])
        if re.sub(r"\s+", "", caption.get("text", "")) != re.sub(r"\s+", "", s["text"]):
            fail("caption_text", "발화 전사 자막과 문구가 다릅니다: " + s["id"])
    if set(by_id) - linked:
        pending.append("발화에 연결되지 않은 자막 구간이 있습니다.")
    return {"ready": not errors and not pending, "errors": errors, "pending": pending, "cue_results": results,
            "duration_seconds": duration, "audio_sha256": audio.get("sha256"),
            "direct_listening_by_this_tool": False, "motion_accuracy_approved": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("activity")
    a.add_argument("audio", type=Path)
    b = sub.add_parser("check")
    b.add_argument("spec", type=Path)
    for item in (a, b):
        item.add_argument("--out", type=Path)
    args = p.parse_args()
    try:
        result = activity(args.audio) if args.command == "activity" else check(prod.read_json(args.spec))
        if args.out:
            with args.out.open("x", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(result.get("ready") is False)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ready": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
