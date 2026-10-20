"""Offline production-factory routing and safety invariants."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import factory


class FactoryRouting(unittest.TestCase):
    def test_detects_specific_stage_without_restarting_full_workflow(self):
        self.assertEqual(factory.infer_stage("자막 위치만 편집하고 다시 확인해"), "edit")
        self.assertEqual(factory.infer_stage("현재 편의 검수조서와 결과보고서 정리"), "delivery")
        self.assertEqual(factory.infer_stage("운동 영상 새 기획 대본과 흐름 설계"), "preproduction")

    def test_prepare_selects_only_stage_relevant_roles_and_tools(self):
        packet = factory.prepare("기존 결과의 자막만 고치고 기술 검사", stage="edit", episode="EP003")
        ids = [r["id"] for r in packet["roles_in_order"]]
        self.assertIn("visual_director_editor", ids)
        self.assertIn("technical_qa", ids)
        self.assertNotIn("execution_preflight", ids)
        self.assertEqual({t["tool_id"] for t in packet["local_tool_routes"]}, {"python", "hyperframes", "ffmpeg", "ffprobe"})
        self.assertFalse(packet["generation_gate"])

    def test_generation_packet_is_explicitly_non_submitting_and_gated(self):
        packet = factory.prepare("승인된 대본으로 영상 생성", stage="generation", episode="EP003")
        self.assertFalse(packet["generation_gate"]["current_generation_authorized"])
        self.assertFalse(packet["generation_gate"]["external_job_submitted"])
        self.assertEqual(packet["generation_gate"]["automatic_retries"], 0)
        self.assertIn("execution_preflight", [x["id"] for x in packet["roles_in_order"]])
        self.assertEqual({x["id"] for x in packet["external_capabilities_for_separate_preflight"]}, {"higgsfield", "heygen", "ltx", "openai_tts"})

    def test_edit_stage_keeps_specialized_remote_edit_optional(self):
        packet = factory.prepare("기존 영상 편집", stage="edit", episode="EP003")
        self.assertEqual([x["id"] for x in packet["external_capabilities_for_separate_preflight"]], ["higgsfield"])
        self.assertTrue(packet["scene_pipeline"]["post_generation_cut_acceptance_required"])
        self.assertEqual(packet["scene_pipeline"]["new_edit_recipe_schema"], 2)
        self.assertFalse(packet["scene_pipeline"]["runs_tools_or_accepts_cuts"])

    def test_audit_does_not_call_external_services(self):
        with patch.object(factory.subprocess, "run", wraps=factory.subprocess.run) as run:
            result = factory.audit()
        self.assertEqual(result["authorization"]["external_calls_made"], 0)
        self.assertTrue(all(x["status"] == "route_configured_account_not_probed" for x in result["external_services"]))
        self.assertEqual({x["id"] for x in result["external_services"]}, {"higgsfield", "heygen", "ltx", "openai_tts"})
        self.assertGreaterEqual(run.call_count, 0)
        self.assertTrue(result["roles"]["count"] >= 8)
        self.assertFalse(result["active_work"]["current_generation_authorized"])

    def test_every_stage_has_roles_and_tool_routes(self):
        roles = json.loads((ROOT / "config/production-roles.json").read_text(encoding="utf-8"))["roles"]
        for stage in factory.STAGES:
            self.assertTrue(any("all" in r["stages"] or stage in r["stages"] for r in roles), stage)
            self.assertTrue(factory.STAGE_TOOLS[stage], stage)


if __name__ == "__main__":
    unittest.main()
