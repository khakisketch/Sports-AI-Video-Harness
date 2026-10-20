"""Agent handoff and local worker invariants, independent of model availability."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import media_worker
import studio
from setup_studio_agents import definitions


class StudioContracts(unittest.TestCase):
    def test_generated_agent_definitions_match_the_shared_source(self):
        for path, expected in definitions().items():
            self.assertEqual(path.read_text(), expected)
        self.assertEqual(len(studio.audit()["agents"]), 4)

    def test_review_packet_keeps_self_evaluation_out_of_independent_review(self):
        p = studio.packet("Check the rendered output", "review", "existing/plan.json")
        self.assertEqual([a["name"] for a in p["agents"]], ["creative-director", "creative-reviewer"])
        self.assertIn("director_verdict", p["review_handoff"]["exclude"])
        self.assertTrue(p["review_handoff"]["fresh_context_required"])
        self.assertFalse(p["permissions"]["paid_media_generation"])

    def test_specific_edit_does_not_invoke_research_again(self):
        p = studio.packet("Move captions", "edit")
        self.assertEqual([a["name"] for a in p["agents"]], ["creative-director", "visual-director"])


class WorkerContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "original.mp4"
        self.source.write_bytes(b"source fingerprint fixture")
        self.plan = {"schema_version": 1, "mode": "local_edit", "inputs": {"original": {"path": str(self.source), "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest()}}, "steps": [{"id": "trim", "tool": "cut", "input": "original", "output": "trim.mp4", "params": {"start": 0, "duration": 2, "accurate": True}}]}

    def test_changed_source_is_rejected_before_execution(self):
        self.source.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "Input changed"):
            media_worker.compile_recipe(self.plan, self.root / "new")

    def test_overwrite_unknown_tool_and_remote_input_are_rejected(self):
        out = self.root / "new"
        out.mkdir()
        with self.assertRaisesRegex(ValueError, "already exists"):
            media_worker.compile_recipe(self.plan, out)
        self.plan["steps"][0]["tool"] = "run_shell"
        with self.assertRaisesRegex(ValueError, "Unsupported local tool"):
            media_worker.compile_recipe(self.plan, self.root / "other")
        self.plan["inputs"]["original"]["path"] = "https://example.com/clip.mp4"
        with self.assertRaisesRegex(ValueError, "Only local"):
            media_worker.compile_recipe(self.plan, self.root / "other")

    def test_path_escape_and_transcription_are_rejected(self):
        self.plan["steps"][0]["output"] = "../original.mp4"
        with self.assertRaisesRegex(ValueError, "unique MP4"):
            media_worker.compile_recipe(self.plan, self.root / "other")
        self.plan["steps"][0]["output"] = "trim.mp4"
        self.plan["steps"][0]["params"]["transcribe"] = True
        with self.assertRaisesRegex(ValueError, "Unsupported parameter"):
            media_worker.compile_recipe(self.plan, self.root / "other")

    def test_downstream_step_uses_previous_output(self):
        self.plan["steps"].append({"id": "volume", "tool": "loudness", "input": "trim", "output": "final.mp4", "params": {"lufs": -16}})
        steps = media_worker.compile_recipe(self.plan, self.root / "new")
        self.assertEqual(steps[1]["argv"][2], str(self.root / "new/trim.mp4"))
        self.assertNotIn("--overwrite", steps[1]["argv"])


if __name__ == "__main__":
    unittest.main()
