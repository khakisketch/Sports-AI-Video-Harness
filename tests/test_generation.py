import copy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import generation
import official_media as api
import production as prod
import test_official_media as fixtures


class GenerationInterface(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.OfficialMedia()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        self.plan = self.f.f.plan
        prod.write_json(self.root / "config/generation-capabilities.json", generation.registry())
        self.task = {"schema_version": 1, "id": "G01", "kind": "VideoGeneration", "route": "ltx-official-v2",
            "model": "ltx-2-5-fast", "shot_ids": ["S01"], "purpose": "test continuity",
            "success_criteria": ["all phases visible"], "input": {"prompt": "Show one full movement cycle.",
                "duration_seconds": 8, "width": 720, "height": 1280, "fps": 24,
                "generate_audio": False, "reference_asset_id": "identity"}}

    def test_common_video_task_compiles_without_changing_business_plan(self):
        before = copy.deepcopy(self.plan)
        request = generation.compile_task(self.task, self.plan, self.root)
        self.assertEqual(request["provider"], "ltx")
        self.assertEqual(request["params"]["resolution"], "720x1280")
        self.assertEqual(request["params"]["image_path"], "assets/identity.png")
        self.assertEqual(self.plan, before)
        self.assertEqual(generation.validate_request(request, self.plan, self.root)["provider"], "lightricks")

    def test_speech_task_is_not_implicitly_voice_cloning_or_lipsync(self):
        self.task.update(kind="SpeechSynthesis", route="openai-speech", model="tts-1-hd",
                         input={"text": "천천히 일어서요.", "voice": "nova", "format": "wav", "speed": 1.0})
        request = generation.compile_task(self.task, self.plan, self.root)
        self.assertEqual(request["provider"], "openai_tts")
        self.assertEqual(api.payload(request, self.root)[1]["input"], "천천히 일어서요.")
        self.task["kind"] = "VoiceCloning"
        with self.assertRaises(ValueError):
            generation.compile_task(self.task, self.plan, self.root)

    def test_candidate_provider_or_model_and_unknown_fields_cannot_execute(self):
        for route, model in (("fal-wan", "wan-2.2"), ("ltx-official-v2", "arbitrary-model")):
            task = copy.deepcopy(self.task)
            task.update(route=route, model=model)
            with self.assertRaises(ValueError):
                generation.compile_task(task, self.plan, self.root)
        self.task["input"]["unsupported_motion_option"] = True
        with self.assertRaises(ValueError):
            generation.compile_task(self.task, self.plan, self.root)

    def test_task_cannot_be_rebound_to_different_wire_params(self):
        request = generation.compile_task(self.task, self.plan, self.root)
        request["params"]["duration"] = 10
        with self.assertRaises(ValueError):
            generation.validate_request(request, self.plan, self.root)

    def test_common_submit_uses_original_quality_and_approval_gate(self):
        request = generation.compile_task(self.task, self.plan, self.root)
        request["quote"] = api.estimate(request, self.root)
        self.plan["execution"]["requests"] = [request]
        self.f.f.plan = self.plan
        self.f.f.bind_reviews(cap=1000)
        reviews = prod.read_json(self.root / "reviews.json")
        reviews["execution_approval"].update(accept_published_rate_estimate=True,
            budgets={"ltx": {"currency": "USD", "max_amount": 1}})
        prod.write_json(self.root / "reviews.json", reviews)
        calls = []
        def transport(method, url, **kwargs):
            calls.append(method)
            return {"id": "test-only-job"}
        with patch.object(generation, "ROOT", self.root), patch.object(api, "ROOT", self.root), patch.dict(os.environ, {"LTX_API_KEY": "fake-key"}):
            result = generation.submit(self.f.f.path, "G01", transport)
        self.assertEqual(calls, ["POST"])
        self.assertEqual(result["state"], "queued")
        self.assertEqual(result["model"], self.task["model"])
        self.assertFalse(result["generation_completion_is_adoption"])
        self.assertIsNone(result["actual_charge"])

    def test_receipt_does_not_promote_download_to_cut_adoption(self):
        r = generation.receipt({"status": "completed", "request_id": "test", "output_sha256": "fixture"})
        self.assertTrue(r["downloaded"])
        self.assertEqual(r["cut_review"], "pending")


if __name__ == "__main__":
    unittest.main()
