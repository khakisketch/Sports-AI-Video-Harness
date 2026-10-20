#!/usr/bin/env python3
"""Connect host-native agents to existing project plans; no model or media API calls."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
from setup_studio_agents import definitions

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    cfg = json.loads((ROOT / "config/studio-agents.json").read_text())
    errors = []
    for path, expected in definitions(ROOT).items():
        if not path.is_file() or path.read_text() != expected:
            errors.append("Agent definition absent or stale: " + str(path.relative_to(ROOT)))
    for agent in cfg["agents"]:
        for skill in agent["skills"]:
            if not (ROOT / ".agents/skills" / skill / "SKILL.md").is_file():
                errors.append("Skill missing: " + skill)
    return {"status": "configured" if not errors else "needs_attention", "execution_model": cfg["execution_model"], "agents": [{"id": a["id"], "label": a["label"], "source": a["source"]} for a in cfg["agents"]], "codex_available": bool(shutil.which("codex")), "claude_available": bool(shutil.which("claude")), "errors": errors, "runtime_verification": "Run local checks in this checkout; configuration is not live execution evidence", "media_service_calls": 0}


def packet(request: str, stage: str, plan: str | None = None) -> dict:
    if not request.strip():
        raise ValueError("Empty request")
    cfg = json.loads((ROOT / "config/studio-agents.json").read_text())
    agents = [a for a in cfg["agents"] if "all" in a["stages"] or stage in a["stages"]]
    return {"request": request, "stage": stage, "canonical_plan": plan, "execution_model": cfg["execution_model"], "agents": [{"name": a["id"], "label": a["label"], "instructions": a["source"], "skills": a["skills"]} for a in agents], "worker": cfg["worker"], "dispatch": "host native agent tool; custom names on supported runtimes; explicit role prompt with fresh context otherwise", "review_handoff": {"include": ["frozen_request", "acceptance_criteria", "actual_output", "timed_evidence", "technical_metrics"], "exclude": ["director_verdict", "prior_review_verdicts", "expected_score"], "fresh_context_required": True}, "permissions": {"paid_media_generation": False, "automatic_retries": 0, "external_upload": False}, "note": "Packet creation does not run agents or render media. The host performs actual delegation; record runtime and observation scope."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit")
    p = sub.add_parser("prepare")
    p.add_argument("--request", required=True)
    p.add_argument("--stage", choices=["preproduction", "asset", "generation", "edit", "review", "delivery", "environment"], default="preproduction")
    p.add_argument("--plan")
    args = parser.parse_args()
    data = audit() if args.command == "audit" else packet(args.request, args.stage, args.plan)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return int(data.get("status") == "needs_attention")


if __name__ == "__main__":
    raise SystemExit(main())
