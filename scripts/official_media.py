#!/usr/bin/env python3
"""Official media API adapter. Estimate/doctor are offline; submit is explicitly gated.

No SDK retries, automatic fallback, .env loading, background submission or key storage.
"""
import argparse
import base64
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path

import production as prod

ROOT = prod.ROOT
PROVIDERS = {"ltx": ("https://api.ltx.io", "LTX_API_KEY"),
             "openai_tts": ("https://api.openai.com", "OPENAI_API_KEY")}


def now():
    return dt.datetime.now(dt.timezone.utc)


def registry(root=ROOT):
    return prod.read_json(root / "config/official-media-api.json")


def doctor(root=ROOT):
    return {"offline": True, "external_calls": 0, "providers": {
        name: {"adapter": "implemented", "credential_present": bool(os.environ.get(env)),
               "credential_env": env, "account_access": "not_probed",
               "paid_quality_trial": "not_performed"}
        for name, (_, env) in PROVIDERS.items()}}


def compile_input(task, plan, root=ROOT):
    """Translate capability-level inputs to this adapter's provider-specific schema."""
    data = task.get("input", {})
    if not isinstance(data, dict):
        raise ValueError("공통 작업 입력은 항목별 객체여야 합니다.")
    params = {"model": task["model"], "count": 1}
    if task["kind"] == "VideoGeneration":
        if set(data) - {"prompt", "duration_seconds", "width", "height", "fps", "generate_audio", "reference_asset_id"}:
            raise ValueError("현재 영상 연결에서 지원하지 않는 입력입니다.")
        if any(type(data.get(k)) is not int or data[k] <= 0 for k in ("width", "height")):
            raise ValueError("영상 가로·세로 크기는 양의 정수로 지정하세요.")
        params.update(prompt=data.get("prompt"), duration=data.get("duration_seconds"),
                      resolution=str(data["width"]) + "x" + str(data["height"]),
                      fps=data.get("fps"), generate_audio=data.get("generate_audio"), operation="text-to-video")
        reference_id = data.get("reference_asset_id")
        if reference_id:
            matches = [a for a in plan.get("assets", []) if a.get("id") == reference_id]
            if len(matches) != 1:
                raise ValueError("확정된 기준 자산을 하나 지정하세요.")
            asset = matches[0]
            actual = hashlib.sha256(prod.asset_path(root, asset.get("path")).read_bytes()).hexdigest()
            if actual != asset.get("sha256"):
                raise ValueError("기준 자산이 변경됐습니다.")
            params.update(operation="image-to-video", image_path=asset["path"], image_sha256=actual)
    elif task["kind"] == "SpeechSynthesis":
        if set(data) - {"text", "voice", "format", "speed"}:
            raise ValueError("현재 음성 연결에서 지원하지 않는 입력입니다.")
        params.update(prompt=data.get("text"), voice=data.get("voice"), response_format=data.get("format"), speed=data.get("speed"))
    else:
        raise ValueError("이 연결부에는 해당 작업 종류가 구현되지 않았습니다.")
    return params


def validate(request, root=ROOT):
    provider, p = request.get("provider"), request.get("params", {})
    if provider not in PROVIDERS:
        raise ValueError("지원하는 공식 API 경로가 아닙니다.")
    common = {"model", "prompt", "count"}
    extra = ({"operation", "duration", "resolution", "fps", "generate_audio", "image_path", "image_sha256"}
             if provider == "ltx" else {"voice", "response_format", "speed"})
    if set(p) - common - extra:
        raise ValueError("미지원 입력을 임의로 버리지 않습니다. 모델 입력을 확인하세요.")
    if type(p.get("count")) is not int or p["count"] != 1 or not prod.text(p.get("prompt")):
        raise ValueError("확정 대본/프롬프트와 생성 수 1이 필요합니다.")
    cfg = registry(root)["providers"][provider]
    if provider == "ltx":
        if p.get("model") not in cfg["rate_per_second"] or p.get("operation") not in ("text-to-video", "image-to-video"):
            raise ValueError("현재는 LTX 2.5 Fast/Pro의 텍스트·이미지 영상만 지원합니다.")
        if len(p["prompt"]) > 5000 or p.get("resolution") not in ("1280x720", "720x1280", "1920x1080", "1080x1920"):
            raise ValueError("프롬프트 최대 5000자, 720/1080 세로·가로 화면만 지원합니다.")
        limit = (6, 8, 10, 12, 14, 16, 18, 20) if p["model"].endswith("fast") else (6, 8, 10)
        if type(p.get("duration")) is not int or p["duration"] not in limit or type(p.get("fps")) is not int or p["fps"] not in (24, 25):
            raise ValueError("고정 길이와 지원 fps를 지정하세요. 자동 길이는 비용 통제를 위해 제외합니다.")
        if type(p.get("generate_audio")) is not bool:
            raise ValueError("동시 소리 생성 여부를 명시하세요.")
        if p["operation"] == "image-to-video":
            image = prod.asset_path(root, p.get("image_path"))
            if image.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                raise ValueError("PNG/JPEG/WebP 기준 이미지만 지원합니다.")
            if hashlib.sha256(image.read_bytes()).hexdigest() != p.get("image_sha256"):
                raise ValueError("기준 이미지가 확정 입력 이후 변경됐습니다.")
            if 4 * ((image.stat().st_size + 2) // 3) > 7_000_000:
                raise ValueError("인라인 이미지의 base64 크기가 공식 7MB 한도를 넘습니다. 별도 업로드는 자동 실행하지 않습니다.")
        elif "image_path" in p or "image_sha256" in p:
            raise ValueError("텍스트 영상에 이미지 입력을 섞지 않습니다.")
    else:
        if p.get("model") not in cfg["rate_per_character"]:
            raise ValueError("검증된 문자 과금 TTS 모델만 지원합니다. 토큰 과금 모델은 별도 견적 정책이 필요합니다.")
        if p.get("voice") not in cfg["candidate_voices"] or p.get("response_format") not in ("mp3", "wav"):
            raise ValueError("공식 지원 음성과 MP3/WAV 출력을 지정하세요.")
        if len(p["prompt"]) > 4096 or not prod.number(p.get("speed")) or not .25 <= p["speed"] <= 4:
            raise ValueError("TTS는 최대 4096자, 속도는 0.25~4입니다.")
    return cfg


def estimate(request, root=ROOT):
    cfg, p = validate(request, root), request["params"]
    if request["provider"] == "ltx":
        unit = str(min(map(int, p["resolution"].split("x"))))
        rate, quantity = cfg["rate_per_second"][p["model"]][unit], p["duration"]
        basis = "output_seconds"
    else:
        rate, quantity = cfg["rate_per_character"][p["model"]], len(p["prompt"])
        basis = "input_unicode_characters"
    return {"provider": request["provider"], "currency": "USD",
            "amount": float(Decimal(str(rate)) * quantity), "unit_price": rate,
            "quantity": quantity, "basis": basis, "kind": "published_rate_estimate",
            "params_hash": prod.digest(p), "checked_at": now().isoformat(),
            "rates_checked_at": registry(root)["checked_at"], "source": cfg["price_source"],
            "excludes": ["tax", "FX_and_payment_fees"], "actual_charge": None}


def validate_quote(request, root=ROOT):
    expected, quote = estimate(request, root), request.get("quote", {})
    for key in ("provider", "currency", "amount", "unit_price", "quantity", "basis", "kind", "params_hash", "rates_checked_at", "source"):
        if quote.get(key) != expected[key]:
            raise ValueError("공식 API 현재 입력과 비용 근거 불일치: " + key)
    cfg = registry(root)
    # The dated source check is recorded in the project's Korean business day.
    age = (now().astimezone(dt.timezone(dt.timedelta(hours=9))).date() - dt.date.fromisoformat(cfg["checked_at"])).days
    quoted_age = (now() - dt.datetime.fromisoformat(quote["checked_at"])).total_seconds()
    if not 0 <= age <= cfg["price_max_age_days"] or not 0 <= quoted_age <= 86400:
        raise ValueError("가격 근거 7일/실행 견적 24시간 범위를 벗어났습니다. 공식 요금을 다시 확인하세요.")
    return expected


def payload(request, root=ROOT):
    validate(request, root)
    p = request["params"]
    if request["provider"] == "openai_tts":
        return "/v1/audio/speech", {"model": p["model"], "input": p["prompt"],
            "voice": p["voice"], "response_format": p["response_format"], "speed": p["speed"]}
    body = {k: p[k] for k in ("model", "prompt", "duration", "resolution", "fps", "generate_audio")}
    if p["operation"] == "image-to-video":
        image = prod.asset_path(root, p["image_path"])
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}[image.suffix.lower()]
        image_bytes = image.read_bytes()
        if hashlib.sha256(image_bytes).hexdigest() != p["image_sha256"]:
            raise ValueError("전송 준비 중 기준 이미지가 변경됐습니다.")
        body["image_uri"] = "data:" + mime + ";base64," + base64.b64encode(image_bytes).decode("ascii")
    return "/v2/" + p["operation"], body


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # API keys must never be forwarded to a redirected host.


def http(method, url, key=None, body=None, binary=False):
    if not url.startswith("https://"):
        raise ValueError("HTTPS가 필요합니다.")
    headers = {"Accept": "*/*" if binary else "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
                                 headers=headers, method=method)
    # urllib performs no application retries. Errors are deliberately redacted by the caller.
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=60) as response:
        data = response.read()
        return data if binary else json.loads(data)


def atomic_json(path, data):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".api-state-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def locked_jobs(plan_path):
    # All adapter mutations share a project-wide lock, including jobs in other plan versions.
    with (ROOT / ".official-media.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = plan_path.parent / "jobs.json"
        jobs = prod.read_json(path) if path.exists() else []
        if not isinstance(jobs, list):
            raise ValueError("작업 기록 형식 오류")
        yield path, jobs


def load_request(plan_path, request_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", request_id):
        raise ValueError("실행 요청 ID에는 영문·숫자·밑줄·하이픈만 사용하세요.")
    plan = prod.read_json(plan_path)
    matches = [r for r in plan.get("execution", {}).get("requests", []) if r.get("id") == request_id]
    if len(matches) != 1:
        raise ValueError("실행 요청을 하나 지정하세요.")
    return plan, matches[0]


def duplicate_anywhere(request):
    for path in (ROOT / "episodes").glob("*/planning/**/jobs.json"):
        entries = prod.read_json(path)
        if isinstance(entries, list) and any(j.get("provider") == request["provider"] and
                j.get("params_hash") == prod.digest(request["params"]) for j in entries):
            raise ValueError("다른 계획 버전의 기존 작업을 먼저 조회·재사용하세요.")


def submit(plan_path, request_id, transport=http):
    plan_path = prod.asset_path(ROOT, str(plan_path))
    with locked_jobs(plan_path) as (path, jobs):
        plan, request = load_request(plan_path, request_id)
        result = prod.audit(plan_path, ROOT, "generate", request_id)
        if not result["ready"]:
            raise ValueError("생성 전 검토/실행 범위가 미완료입니다: " + ", ".join(sorted({e["code"] for e in result["errors"]})))
        quote = validate_quote(request, ROOT)
        duplicate_anywhere(request)
        if any(j.get("provider") == request["provider"] and j.get("params_hash") == prod.digest(request["params"]) for j in jobs):
            raise ValueError("이 입력의 이전 작업 기록이 있습니다. 자동 재시도하지 않습니다.")
        base, env = PROVIDERS[request["provider"]]
        key = os.environ.get(env)
        if not key:
            raise ValueError(env + " 연결이 필요합니다. 키를 대화에 붙이지 마세요.")
        endpoint, body = payload(request, ROOT)
        extension = ".mp4" if request["provider"] == "ltx" else "." + request["params"]["response_format"]
        output = plan_path.parent / (request_id + "-official" + extension)
        if output.exists() or output.is_symlink():
            raise ValueError("기존 결과를 덮어쓰지 않습니다.")
        job = {"request_id": request_id, "provider": request["provider"], "endpoint": endpoint,
               "kind": "VideoGeneration" if request["provider"] == "ltx" else "SpeechSynthesis",
               "model": request["params"]["model"], "route": request.get("task", {}).get("route"),
               "params_hash": prod.digest(request["params"]), "execution_hash": result["execution_hash"],
               "quote": quote, "actual_charge": None, "status": "submitting", "job_id": None,
               "created_at": now().isoformat(), "output_path": str(output.relative_to(ROOT.resolve()))}
        jobs.append(job)
        atomic_json(path, jobs)  # Durable intent BEFORE the only paid POST.
        try:
            response = transport("POST", base + endpoint, key=key, body=body,
                                 binary=request["provider"] == "openai_tts")
            if request["provider"] == "ltx":
                if not isinstance(response, dict) or not prod.text(response.get("id")):
                    raise ValueError("Missing provider job ID")
                job.update(job_id=response["id"], status="submitted")
            else:
                if not isinstance(response, bytes) or not response:
                    raise ValueError("Empty speech response")
                with output.open("xb") as f:
                    f.write(response)
                job.update(status="completed", output_sha256=hashlib.sha256(response).hexdigest(),
                           technical_qa="pending", listening_review="pending", cut_review="pending")
        except Exception as exc:
            job.update(status="submission_unknown", error_class=type(exc).__name__)
            atomic_json(path, jobs)
            raise ValueError("요청 결과를 확정할 수 없습니다. 작업/계정 기록을 확인하세요. 재전송하지 않습니다.") from None
        atomic_json(path, jobs)
        return job


def status(plan_path, request_id, transport=http, download=False):
    plan_path = prod.asset_path(ROOT, str(plan_path))
    with locked_jobs(plan_path) as (path, jobs):
        matches = [j for j in jobs if j.get("request_id") == request_id and j.get("provider") == "ltx"]
        if len(matches) != 1:
            raise ValueError("조회할 LTX 작업을 하나 찾을 수 없습니다.")
        job = matches[0]
        expected_hash = job.get("output_sha256") or job.get("download_expected_sha256")
        if download and expected_hash and (ROOT / job.get("output_path", "")).is_file():
            output = prod.asset_path(ROOT, job.get("output_path"))
            if hashlib.sha256(output.read_bytes()).hexdigest() != expected_hash:
                raise ValueError("저장된 결과 파일이 변경됐습니다. 덮어쓰거나 재생성하지 않습니다.")
            job.update(output_sha256=expected_hash, download_status="saved", technical_qa=job.get("technical_qa", "pending"),
                       creative_review=job.get("creative_review", "pending"), cut_review=job.get("cut_review", "pending"))
            atomic_json(path, jobs)
            return job
        job_id = job.get("job_id")
        if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9-]+", job_id):
            raise ValueError("작업 번호가 없습니다. 공급자 기록에서 확인하고 수동 대조가 필요합니다.")
        if job.get("endpoint") not in ("/v2/image-to-video", "/v2/text-to-video"):
            raise ValueError("알 수 없는 조회 경로")
        if job.get("last_poll_at") and (now() - dt.datetime.fromisoformat(job["last_poll_at"])).total_seconds() < 5:
            raise ValueError("상태 조회는 최소 5초 간격입니다.")
        key = os.environ.get("LTX_API_KEY")
        if not key:
            raise ValueError("LTX_API_KEY 연결이 필요합니다.")
        job["last_poll_at"] = now().isoformat()
        atomic_json(path, jobs)
        response = transport("GET", PROVIDERS["ltx"][0] + job["endpoint"] + "/" + job_id, key=key)
        state = response.get("status")
        if state not in ("pending", "processing", "completed", "failed"):
            raise ValueError("공급자 응답 상태가 미확인입니다.")
        job["status"] = {"pending": "submitted", "processing": "in_progress"}.get(state, state)
        # Preserve provider state before download. Signed result URL is never journalled.
        atomic_json(path, jobs)
        if download and state == "completed":
            output = (ROOT / job["output_path"]).resolve()
            if not output.is_relative_to(ROOT.resolve()) or output.exists() or output.is_symlink():
                raise ValueError("기존 결과 덮어쓰기/프로젝트 밖 저장은 하지 않습니다.")
            url = response.get("result", {}).get("video_url", "")
            try:
                data = transport("GET", url, binary=True)  # No API key on storage requests.
                if not isinstance(data, bytes) or not data:
                    raise ValueError("빈 다운로드 결과")
                job["download_expected_sha256"] = hashlib.sha256(data).hexdigest()
                atomic_json(path, jobs)  # Recover a crash after publishing bytes but before final journal update.
                fd, temporary = tempfile.mkstemp(dir=output.parent, prefix=".api-state-")
                try:
                    with os.fdopen(fd, "wb") as f:
                        f.write(data)
                        f.flush()
                        os.fsync(f.fileno())
                    os.link(temporary, output)  # Atomic publish, fails if a result already exists.
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
                job.update(output_sha256=hashlib.sha256(data).hexdigest(), download_status="saved",
                           technical_qa="pending", creative_review="pending", cut_review="pending")
            except Exception as exc:
                job.update(download_status="failed", download_error_class=type(exc).__name__)
                atomic_json(path, jobs)
                if isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 404, 410):
                    raise ConnectionError("결과 주소 갱신을 위해 같은 작업을 다시 조회하세요.") from None
                raise  # The paid job remains completed; only GET is eligible for retry.
            atomic_json(path, jobs)
        return job


def resume(plan_path, request_id, timeout_seconds=300, poll_seconds=5, transport=http, sleep=time.sleep):
    """Recover a persisted job and collect its result. Never calls submit or POST."""
    if not 0 <= timeout_seconds <= 3600 or not 5 <= poll_seconds <= 30:
        raise ValueError("재개 대기는 0~3600초, 조회 간격은 5~30초로 지정하세요.")
    start = time.monotonic()
    attempts, read_failures = 0, 0
    while True:
        jobs = prod.read_json(Path(plan_path).parent / "jobs.json")
        selected = [j for j in jobs if j.get("request_id") == request_id and j.get("provider") == "ltx"]
        if len(selected) != 1 or not selected[0].get("job_id"):
            raise ValueError("저장된 작업 번호를 확인하세요. 불명확한 제출을 다시 생성하지 않습니다.")
        previous = selected[0]
        if previous.get("status") == "failed":
            return {"job": previous, "read_attempts": attempts, "submitted_new_job": False}
        last = previous.get("last_poll_at")
        wait = max(0, poll_seconds - (now() - dt.datetime.fromisoformat(last)).total_seconds()) if last else 0
        if wait and not previous.get("output_sha256"):
            if time.monotonic() - start + wait > timeout_seconds:
                return {"job": previous, "read_attempts": attempts, "submitted_new_job": False, "resume_pending": True}
            sleep(wait)
        try:
            attempts += 1
            job = status(plan_path, request_id, transport=transport, download=True)
            if job.get("output_sha256") or job.get("status") == "failed":
                return {"job": job, "read_attempts": attempts, "read_failures": read_failures, "submitted_new_job": False}
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {"job": previous, "read_attempts": attempts, "submitted_new_job": False,
                        "recovery_status": "provider_record_missing_or_expired", "manual_recovery_required": True}
            if exc.code not in (408, 429, 500, 502, 503, 504):
                raise ValueError("조회가 거절됐습니다. 계정·권한을 확인하세요. 재생성하지 않습니다.") from None
            read_failures += 1
        except (TimeoutError, ConnectionError, urllib.error.URLError):
            read_failures += 1
        if time.monotonic() - start >= timeout_seconds:
            return {"request_id": request_id, "read_attempts": attempts, "read_failures": read_failures,
                    "resume_pending": True, "submitted_new_job": False}
        delay = min(30, poll_seconds * (2 ** min(read_failures, 3)))
        remaining = timeout_seconds - (time.monotonic() - start)
        sleep(min(delay, remaining))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    for command in ("estimate", "submit", "status", "resume"):
        p = sub.add_parser(command)
        p.add_argument("--plan", type=Path, required=True)
        p.add_argument("--request-id", required=True)
        if command == "estimate":
            p.add_argument("--out", type=Path)
        if command == "submit":
            p.add_argument("--execute", action="store_true", help="유료 전송을 명시하며 현재 승인·견적·품질 검토도 필요합니다.")
        if command == "status":
            p.add_argument("--download", action="store_true")
        if command == "resume":
            p.add_argument("--timeout-seconds", type=int, default=300)
            p.add_argument("--poll-seconds", type=int, default=5)
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            result = doctor()
        elif args.command == "estimate":
            _, request = load_request(args.plan, args.request_id)
            result = estimate(request)
            if args.out:
                with args.out.open("x", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
        elif args.command == "submit":
            if not args.execute:
                raise ValueError("견적은 estimate로 확인합니다. 유료 전송은 승인 기록과 --execute가 필요합니다.")
            result = submit(args.plan, args.request_id)
        elif args.command == "status":
            result = status(args.plan, args.request_id, download=args.download)
        else:
            result = resume(args.plan, args.request_id, args.timeout_seconds, args.poll_seconds)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        # urllib exceptions can contain signed URLs. Never print raw transport errors.
        message = str(exc) if isinstance(exc, ValueError) else "작업을 완료하지 못했습니다: " + type(exc).__name__
        print(json.dumps({"ready": False, "error": message}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
