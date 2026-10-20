#!/usr/bin/env python3
"""Observed cost per accepted generated asset, never estimate-as-actual accounting."""
import argparse
import hashlib
import json
from pathlib import Path
import production as prod

ROOT = prod.ROOT


def summarize(jobs, accepted_assets=None, root=ROOT):
    groups = {}
    for job in jobs:
        quote = job.get("quote", {})
        if not quote and "quoted_credits" in job:
            quote = {"credits": job["quoted_credits"]}  # Preserve legacy estimate, never promote to actual.
        unit = quote.get("currency") or ("credits" if "credits" in quote else "unknown")
        key = (job.get("kind", "unrecorded"), job.get("model", "unrecorded"), job.get("provider", "unrecorded"), unit)
        g = groups.setdefault(key, {"kind": key[0], "model": key[1], "provider": key[2], "currency_or_unit": unit,
            "attempts": 0, "generated": 0, "downloaded": 0, "unknown_submission_outcomes": 0,
            "known_actual_subtotal": 0, "actual_cost_unknown_jobs": 0,
            "estimate_total": 0, "estimate_unknown_jobs": 0, "accepted_assets": {}})
        g["attempts"] += 1
        g["generated"] += job.get("status") == "completed"
        g["downloaded"] += bool(job.get("output_sha256") or job.get("result_sha256"))
        g["unknown_submission_outcomes"] += job.get("status") in ("submitting", "submission_unknown")
        amount = quote.get("amount") if unit != "credits" else quote.get("credits")
        if prod.number(amount) and amount >= 0:
            g["estimate_total"] += amount
        else:
            g["estimate_unknown_jobs"] += 1
        actual = job.get("actual_charge")
        verified = False
        if isinstance(actual, dict) and actual.get("currency") == unit and prod.number(actual.get("amount")) and actual["amount"] >= 0:
            evidence = actual.get("evidence", {})
            try:
                path = prod.asset_path(root, evidence.get("path"))
                verified = bool(prod.text(actual.get("source"))) and hashlib.sha256(path.read_bytes()).hexdigest() == evidence.get("sha256")
            except (ValueError, OSError):
                pass
        if verified:
            g["known_actual_subtotal"] += actual["amount"]
        else:
            g["actual_cost_unknown_jobs"] += 1
        sha = job.get("output_sha256") or job.get("result_sha256")
        if accepted_assets is not None and sha in accepted_assets:
            g["accepted_assets"][sha] = accepted_assets[sha]
    result = []
    for g in groups.values():
        accepted = g.pop("accepted_assets")
        count = len(accepted) if accepted_assets is not None else None
        durations = list(accepted.values())
        seconds = sum(durations) if durations and all(prod.number(d) and d > 0 for d in durations) else None
        actual = g["known_actual_subtotal"] if g["actual_cost_unknown_jobs"] == 0 else None
        g.update(actual_total=actual, accepted_result_count=count, accepted_unique_seconds=seconds,
                 actual_cost_per_accepted_result=actual / count if actual is not None and count else None,
                 actual_cost_per_accepted_second=actual / seconds if actual is not None and seconds else None)
        result.append(g)
    return {"groups": result, "cross_provider_or_currency_total": None,
            "denominator": "unique cut-accepted generated assets; repeated editorial reuse is not a new generation",
            "adoption_evaluated": accepted_assets is not None, "external_calls": 0,
            "invoice_truth_verified_by_code": False}


def accepted_assets(package, root=ROOT):
    import scene_pipeline
    result = {}
    for scene in package.get("scenes", []):
        checked = scene_pipeline.audit(package, root, "edit", [scene["id"]])
        if not checked["ready"]:
            continue
        for role in ("video", "voice"):
            node = package["nodes"].get(scene.get("components", {}).get(role), {})
            if node.get("sha256"):
                result[node["sha256"]] = node.get("duration_seconds")
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("jobs", type=Path, nargs="+")
    p.add_argument("--scene-package", type=Path)
    args = p.parse_args()
    try:
        jobs = []
        for path in dict.fromkeys(p.resolve() for p in args.jobs):
            entries = prod.read_json(path)
            if not isinstance(entries, list):
                raise ValueError("작업 기록 목록이 필요합니다.")
            jobs.extend(entries)
        adopted = accepted_assets(prod.read_json(args.scene_package)) if args.scene_package else None
        print(json.dumps(summarize(jobs, adopted), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"ready": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
