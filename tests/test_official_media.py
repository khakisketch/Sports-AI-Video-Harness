"""Money/identity/duplicate invariants. All network calls use a fake transport."""
import copy
import datetime as dt
import json
import os
import urllib.error
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import official_media as api
import production as prod
import test_production as fixtures


class OfficialMedia(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ProductionGate()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        cfg = api.registry()
        cfg["checked_at"] = api.now().date().isoformat()
        prod.write_json(self.root / "config/official-media-api.json", cfg)
        self.r = self.f.plan["execution"]["requests"][0]
        self.r["provider"] = "ltx"
        self.r["params"] = {"model": "ltx-2-5-fast", "operation": "image-to-video",
            "prompt": "A fictional adult instructor performs one continuous cycle.",
            "duration": 8, "resolution": "720x1280", "fps": 24, "generate_audio": False, "count": 1,
            "image_path": "assets/identity.png", "image_sha256": self.f.plan["assets"][0]["sha256"]}
        self.approve()

    def approve(self, cap=2):
        self.r["quote"] = api.estimate(self.r, self.root)
        self.f.bind_reviews(cap=1000)
        reviews = prod.read_json(self.root / "reviews.json")
        reviews["execution_approval"].update(budgets={self.r["provider"]: {"currency": "USD", "max_amount": cap}},
                                             accept_published_rate_estimate=True)
        prod.write_json(self.root / "reviews.json", reviews)

    def errors(self):
        return {e["code"] for e in self.f.result()["errors"]}

    def test_offline_usd_estimate_with_all_original_gates(self):
        self.assertEqual(self.r["quote"]["amount"], .72)
        self.assertTrue(self.f.result()["ready"])
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(api.doctor(self.root)["providers"]["ltx"]["credential_present"])
        reviews = prod.read_json(self.root / "reviews.json")
        reviews["roles"]["motion"]["verdict"] = "pending"
        prod.write_json(self.root / "reviews.json", reviews)
        self.assertIn("review_motion", self.errors())

    def test_credit_approval_does_not_authorize_usd(self):
        reviews = prod.read_json(self.root / "reviews.json")
        reviews["execution_approval"].pop("budgets")
        prod.write_json(self.root / "reviews.json", reviews)
        self.assertIn("cost_cap", self.errors())

    def test_usd_cap_currency_and_estimate_disclosure(self):
        self.approve(.71)
        self.assertIn("cost_cap", self.errors())
        self.approve()
        reviews = prod.read_json(self.root / "reviews.json")
        reviews["execution_approval"]["budgets"]["ltx"]["currency"] = "credits"
        reviews["execution_approval"].pop("accept_published_rate_estimate")
        prod.write_json(self.root / "reviews.json", reviews)
        self.assertTrue({"cost_cap", "estimate_acceptance"} <= self.errors())

    def test_scene_budget_is_separate_from_provider_budget(self):
        policy_path = self.root / "config/production-policy.json"
        policy = prod.read_json(policy_path)
        policy["official_api"] = {"scene_budget_required": True}
        prod.write_json(policy_path, policy)
        self.assertIn("scene_cost_cap", self.errors())
        reviews_path = self.root / "reviews.json"
        reviews = prod.read_json(reviews_path)
        reviews["execution_approval"]["scene_budgets"] = {"S01": {"ltx": {"currency": "USD", "max_amount": .71}}}
        prod.write_json(reviews_path, reviews)
        self.assertIn("scene_cost_cap", self.errors())
        reviews["execution_approval"]["scene_budgets"]["S01"]["ltx"]["max_amount"] = .72
        prod.write_json(reviews_path, reviews)
        self.assertTrue(self.f.result()["ready"])

    def test_crash_after_download_publish_recovers_file_without_new_network(self):
        output = self.root / "G01-official.mp4"
        output.write_bytes(b"downloaded-before-crash")
        jobs = [{"request_id": "G01", "provider": "ltx", "job_id": "fake-job-123", "endpoint": "/v2/image-to-video",
                 "status": "completed", "output_path": output.name,
                 "download_expected_sha256": prod.hashlib.sha256(output.read_bytes()).hexdigest()}]
        prod.write_json(self.root / "jobs.json", jobs)
        def forbidden(*args, **kwargs):
            self.fail("already saved bytes must not be fetched or regenerated")
        with patch.object(api, "ROOT", self.root):
            result = api.resume(self.f.path, "G01", timeout_seconds=0, transport=forbidden)
        self.assertEqual(result["job"]["download_status"], "saved")

    def test_price_tampering_stale_price_and_new_input_are_blocked(self):
        self.r["quote"]["amount"] = 0
        self.assertIn("official_quote", self.errors())
        self.approve()
        self.r["quote"]["checked_at"] = (api.now() - dt.timedelta(days=2)).isoformat()
        self.assertIn("official_quote", self.errors())
        self.approve()
        self.r["params"]["duration"] = 10
        self.assertTrue({"official_quote", "execution_approval"} <= self.errors())

    def test_changed_image_and_missing_design_reference_block(self):
        (self.root / "assets/identity.png").write_bytes(b"changed after approval")
        with self.assertRaises(ValueError):
            api.payload(self.r, self.root)
        self.assertIn("official_quote", self.errors())

    def test_path_escape_unknown_options_auto_duration_rejected(self):
        for field, value in (("image_path", "../escape.png"), ("duration", None), ("unsupported_option", True)):
            r = copy.deepcopy(self.r)
            r["params"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                api.payload(r, self.root)

    def test_wire_payload_preserves_image_and_never_carries_account_data(self):
        endpoint, body = api.payload(self.r, self.root)
        self.assertEqual(endpoint, "/v2/image-to-video")
        self.assertTrue(body["image_uri"].startswith("data:image/png;base64,"))
        self.assertFalse({"count", "image_path", "image_sha256", "quote", "provider"} & set(body))

    def test_submit_timeout_is_durable_and_never_retried(self):
        calls = []
        def timeout(*args, **kwargs):
            calls.append(args)
            raise TimeoutError("contains-secret-and-signed-url")
        with patch.object(api, "ROOT", self.root), patch.dict(os.environ, {"LTX_API_KEY": "fake-test-key"}):
            with self.assertRaisesRegex(ValueError, "재전송하지"):
                api.submit(self.f.path, "G01", transport=timeout)
            with self.assertRaises(ValueError):
                api.submit(self.f.path, "G01", transport=timeout)
        self.assertEqual(len(calls), 1)
        records = (self.root / "jobs.json").read_text()
        self.assertEqual(json.loads(records)[0]["status"], "submission_unknown")
        self.assertNotIn("fake-test-key", records)
        self.assertNotIn("contains-secret", records)
        self.assertNotIn("base64", records)

    def test_status_download_uses_same_job_and_no_auth_for_storage(self):
        calls = []
        def transport(method, url, **kw):
            calls.append((method, url, kw))
            if method == "POST":
                return {"id": "fake-job-123"}
            if "api.ltx.io" in url:
                return {"status": "completed", "result": {"video_url": "https://storage.example/result?secret-signature"}}
            self.assertNotIn("key", kw)
            return b"test-video-bytes-only-not-real-render"
        with patch.object(api, "ROOT", self.root), patch.dict(os.environ, {"LTX_API_KEY": "test-only-key"}):
            self.assertEqual(api.submit(self.f.path, "G01", transport)["status"], "submitted")
            self.assertEqual(api.status(self.f.path, "G01", transport, download=True)["technical_qa"], "pending")
            self.assertEqual(api.status(self.f.path, "G01", transport, download=True)["output_sha256"],
                             api.prod.read_json(self.root / "jobs.json")[0]["output_sha256"])
        self.assertEqual(len(calls), 3)
        self.assertNotIn("secret-signature", (self.root / "jobs.json").read_text())

    def test_resume_only_gets_existing_job_after_download_failure(self):
        calls = []
        fail_download = [True]
        def transport(method, url, **kw):
            calls.append(method)
            if method == "POST":
                return {"id": "fake-job-123"}
            if "api.ltx.io" in url:
                return {"status": "completed", "result": {"video_url": "https://storage.example/output"}}
            if fail_download[0]:
                fail_download[0] = False
                raise TimeoutError("signed-url-secret")
            return b"same-provider-result"
        with patch.object(api, "ROOT", self.root), patch.dict(os.environ, {"LTX_API_KEY": "test-key"}):
            api.submit(self.f.path, "G01", transport)
            with self.assertRaises(TimeoutError):
                api.status(self.f.path, "G01", transport, download=True)
            jobs = prod.read_json(self.root / "jobs.json")
            self.assertEqual(jobs[0]["status"], "completed")
            self.assertEqual(jobs[0]["download_status"], "failed")
            jobs[0]["last_poll_at"] = (api.now() - dt.timedelta(seconds=10)).isoformat()
            prod.write_json(self.root / "jobs.json", jobs)
            # A fresh call recovers from disk; no in-memory job object or paid POST is used.
            result = api.resume(self.f.path, "G01", timeout_seconds=0, transport=transport)
            self.assertFalse(result["submitted_new_job"])
            self.assertEqual(result["job"]["download_status"], "saved")
        self.assertEqual(calls.count("POST"), 1)
        self.assertEqual((self.root / "G01-official.mp4").read_bytes(), b"same-provider-result")

    def test_expired_job_and_ambiguous_submission_never_recreate(self):
        def expired(*args, **kwargs):
            self.assertEqual(args[0], "GET")
            raise urllib.error.HTTPError(args[1], 404, "expired", {}, None)
        jobs = [{"request_id": "G01", "provider": "ltx", "job_id": "fake-job-123", "endpoint": "/v2/image-to-video", "status": "submitted"}]
        prod.write_json(self.root / "jobs.json", jobs)
        with patch.object(api, "ROOT", self.root), patch.dict(os.environ, {"LTX_API_KEY": "test-key"}):
            self.assertTrue(api.resume(self.f.path, "G01", timeout_seconds=0, transport=expired)["manual_recovery_required"])
            jobs[0].update(job_id=None, status="submission_unknown")
            prod.write_json(self.root / "jobs.json", jobs)
            with self.assertRaises(ValueError):
                api.resume(self.f.path, "G01", timeout_seconds=0, transport=expired)

    def test_voice_quote_and_payload_are_separate_from_lipsync(self):
        self.r.update(provider="openai_tts", params={"model": "tts-1-hd", "prompt": "천천히 일어서요.",
            "voice": "nova", "response_format": "wav", "speed": 1.0, "count": 1})
        self.approve(.05)
        self.assertTrue(self.f.result()["ready"])
        endpoint, body = api.payload(self.r, self.root)
        self.assertEqual(endpoint, "/v1/audio/speech")
        self.assertEqual(body["input"], "천천히 일어서요.")
        self.assertEqual(self.r["quote"]["quantity"], len(body["input"]))
        self.r["params"]["model"] = "gpt-4o-mini-tts"
        with self.assertRaises(ValueError):
            api.estimate(self.r, self.root)

    def test_voice_submission_saves_original_and_blocks_second_call(self):
        self.r.update(provider="openai_tts", params={"model": "tts-1-hd", "prompt": "천천히 일어서요.",
            "voice": "nova", "response_format": "wav", "speed": 1.0, "count": 1})
        self.approve(.05)
        calls = []
        def transport(method, url, **kwargs):
            calls.append((method, url))
            self.assertEqual(kwargs["body"]["voice"], "nova")
            return b"test-audio-bytes-only-not-real-speech"
        with patch.object(api, "ROOT", self.root), patch.dict(os.environ, {"OPENAI_API_KEY": "fake-key"}):
            job = api.submit(self.f.path, "G01", transport)
            self.assertEqual(job["listening_review"], "pending")
            self.assertIsNone(job["actual_charge"])
            with self.assertRaises(ValueError):
                api.submit(self.f.path, "G01", transport)
        self.assertEqual(len(calls), 1)
        self.assertEqual((self.root / "G01-official.wav").read_bytes(), b"test-audio-bytes-only-not-real-speech")

    def test_duplicate_in_another_plan_version_is_blocked(self):
        path = self.root / "episodes/EP999/planning/older/jobs.json"
        path.parent.mkdir(parents=True)
        prod.write_json(path, [{"provider": "ltx", "params_hash": prod.digest(self.r["params"]), "status": "completed"}])
        def forbidden(*args, **kwargs):
            self.fail("cross-version duplicates must not reach the API")
        with patch.object(api, "ROOT", self.root), patch.dict(os.environ, {"LTX_API_KEY": "fake-key"}):
            with self.assertRaisesRegex(ValueError, "다른 계획 버전"):
                api.submit(self.f.path, "G01", forbidden)

    def test_real_registry_quote_validates_on_korean_check_day(self):
        with patch.object(api, "now", return_value=dt.datetime(2026, 10, 19, 23, 43, tzinfo=dt.timezone.utc)):
            cfg = api.registry(self.root)
            cfg["checked_at"] = "2026-10-20"
            prod.write_json(self.root / "config/official-media-api.json", cfg)
            self.r["quote"] = api.estimate(self.r, self.root)
            self.assertEqual(api.validate_quote(self.r, self.root)["amount"], .72)

    def test_no_key_or_missing_approval_means_no_network(self):
        def forbidden(*args, **kwargs):
            self.fail("must not call a paid API")
        with patch.object(api, "ROOT", self.root), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "LTX_API_KEY"):
                api.submit(self.f.path, "G01", forbidden)
        self.assertEqual(prod.read_json(self.root / "jobs.json"), [])
        reviews = prod.read_json(self.root / "reviews.json")
        reviews["execution_approval"]["decision"] = "pending"
        prod.write_json(self.root / "reviews.json", reviews)
        with patch.object(api, "ROOT", self.root), self.assertRaisesRegex(ValueError, "execution_approval"):
            api.submit(self.f.path, "G01", forbidden)


if __name__ == "__main__":
    unittest.main()
