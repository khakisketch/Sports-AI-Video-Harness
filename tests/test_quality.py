"""Metadata invariants using synthetic review fixtures, never real approvals."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import production as prod
import quality
import test_production as legacy_tests


class QualityGate(unittest.TestCase):
    def setUp(self):
        self.legacy = legacy_tests.ProductionGate("test_complete_metadata_with_bound_reviews_passes")
        self.legacy.setUp()
        self.addCleanup(self.legacy.temp.cleanup)
        self.root, self.plan_path = self.legacy.root, self.legacy.path
        self.plan = copy.deepcopy(self.legacy.plan)
        prod.write_json(self.root / "config/quality-rubric.json", quality.read(prod.ROOT / "config/quality-rubric.json"))
        self.preview = self.root / "preview"
        self.preview.mkdir()
        (self.preview / "index.html").write_text("Synthetic test composition")
        (self.preview / "assets").mkdir()
        (self.preview / "assets/font.otf").write_bytes(b"synthetic font")
        prod.write_json(self.preview / "build-manifest.json", {"plan": "plan.json", "plan_sha256": quality.sha(self.plan_path), "composition_sha256": quality.sha(self.preview / "index.html"), "assets": []})
        prod.write_json(self.root / "technical.json", {"ok": True, "browserSkipped": False, "lint": {"errorCount": 0, "findings": []}, "runtime": {"errorCount": 0}, "layout": {"errorCount": 0}})
        self.review_path = self.root / "quality-review.json"
        self.review = quality.initialize(self.plan_path, self.review_path, "preview/build-manifest.json", self.root)
        self.review["evidence"] = [
            {"id": "video", "kind": "video", "inspection": "ai_temporal", "path": "assets/cycle.mp4", "sha256": quality.sha(self.root / "assets/cycle.mp4"), "coverage": [0, 10], "times": [0, 3, 9]},
            {"id": "technical", "kind": "technical_report", "inspection": "automation", "path": "technical.json", "sha256": quality.sha(self.root / "technical.json"), "preview_hash": self.review["preview"]["hash"]}
        ]
        self.review["technical_evidence_id"] = "technical"
        for item in self.review["assessments"]:
            item.update(verdict="pass", rating=3, observation="Synthetic observation, not a real visual inspection", countercheck="Synthetic alternate interpretation check", rationale="Synthetic anchored rating", evidence_ids=["video"])

    def result(self, profile="generation"):
        prod.write_json(self.review_path, self.review)
        return quality.audit(self.plan_path, self.review_path, self.root, profile)

    def codes(self):
        return {e["code"] for e in self.result()["errors"]}

    def assessment(self, criterion):
        return next(a for a in self.review["assessments"] if a["criterion"] == criterion)

    def evidence(self, eid):
        return next(e for e in self.review["evidence"] if e["id"] == eid)

    def finding(self, severity="major", status="open"):
        item = {"id": "F01", "assessment_key": "action_clarity:S01", "severity": severity, "status": status, "description": "Synthetic visual defect", "before_evidence_ids": ["video"]}
        self.review["findings"].append(item)
        return item

    def test_complete_bound_synthetic_metadata_passes(self):
        self.assertTrue(self.result()["quality_ready"])

    def test_visual_development_and_staging_need_observed_time(self):
        for criterion in ("visual_development", "staging_performance"):
            with self.subTest(criterion=criterion):
                self.assertEqual(self.assessment(criterion)["evidence_mode"], "temporal")
        self.evidence("video").update(kind="frame", inspection="ai_visual", times=[3])
        messages = [e["message"] for e in self.result()["errors"] if e["code"] == "quality_temporal_unseen"]
        for criterion in ("visual_development", "staging_performance"):
            self.assertTrue(any(criterion in message for message in messages))

    def test_standalone_quality_cannot_approve_layout_only(self):
        self.plan["direction"]["preview_level"] = "layout_only"
        self.rebind_test_plan()
        self.assertIn("direction_layout_only", self.codes())

    def test_internal_layout_review_does_not_require_generation_animatic(self):
        self.plan["direction"]["preview_level"] = "layout_only"
        self.review["profile"] = "internal_preview"
        self.rebind_test_plan()
        self.assertTrue(self.result("internal_preview")["quality_ready"])

    def rebind_test_plan(self):
        # Synthetic fixture maintenance only, never a real re-inspection.
        prod.write_json(self.plan_path, self.plan)
        manifest_path = self.preview / "build-manifest.json"
        manifest = quality.read(manifest_path)
        manifest["plan_sha256"] = quality.sha(self.plan_path)
        prod.write_json(manifest_path, manifest)
        self.review["design_hash"] = prod.fingerprints(self.plan, self.root)["design_hash"]
        self.review["preview"] = quality.preview_snapshot(self.root.resolve(), "preview/build-manifest.json", self.plan_path.resolve())
        self.evidence("technical")["preview_hash"] = self.review["preview"]["hash"]

    def test_global_pass_word_does_not_override_pending(self):
        self.review["overall_verdict"] = "pass"
        self.assessment("action_clarity")["verdict"] = "pending"
        self.assertIn("quality_unpassed", self.codes())

    def test_high_scores_do_not_average_away_low_score(self):
        for item in self.review["assessments"]:
            item["rating"] = 4
        self.assessment("action_clarity")["rating"] = 2
        self.assertFalse(self.result()["quality_ready"])

    def test_missing_countercheck_is_not_a_review(self):
        self.assessment("visual_job")["countercheck"] = ""
        self.assertIn("quality_reasoning", self.codes())

    def test_each_shot_check_is_required(self):
        self.review["assessments"].pop()
        self.assertIn("quality_coverage", self.codes())

    def test_duplicate_check_cannot_cover_missing_target(self):
        self.review["assessments"][-1] = copy.deepcopy(self.review["assessments"][0])
        self.assertIn("quality_coverage", self.codes())

    def test_automation_log_cannot_prove_visual_quality(self):
        self.evidence("video")["inspection"] = "automation"
        self.assertIn("quality_visual_unseen", self.codes())

    def test_still_frame_cannot_prove_whole_motion(self):
        self.evidence("video").update(kind="frame", inspection="ai_visual", times=[3])
        self.assertIn("quality_temporal_unseen", self.codes())

    def test_visual_evidence_must_be_in_reviewed_time(self):
        self.evidence("video").update(kind="frame", inspection="ai_visual", times=[15])
        self.assertIn("quality_visual_unseen", self.codes())

    def test_supports_require_every_phase(self):
        self.evidence("video").update(kind="frame", inspection="ai_visual", times=[0])
        self.assertIn("quality_phase_unseen", self.codes())

    def test_partial_motion_is_not_full_cycle(self):
        self.evidence("video")["coverage"] = [2, 8]
        self.assertIn("quality_temporal_unseen", self.codes())

    def test_open_major_is_blocking_despite_all_passes(self):
        self.finding()
        self.assertIn("quality_open_defect", self.codes())

    def test_major_cannot_be_accepted_as_minor_exception(self):
        self.finding(status="accepted")["acceptance_reason"] = "Synthetic preference"
        self.assertIn("quality_open_defect", self.codes())

    def test_resolved_word_without_recheck_is_blocking(self):
        self.finding(status="resolved")["fix"] = "Changed camera"
        self.assertIn("quality_unverified_fix", self.codes())

    def test_reusing_same_proof_does_not_close_visual_defect(self):
        f = self.finding(status="resolved")
        f.update(fix="Synthetic correction", recheck={"design_hash": self.review["design_hash"], "observation": "Synthetic recheck", "evidence_ids": ["video"]})
        self.assertIn("quality_same_fix_evidence", self.codes())

    def test_new_observed_proof_can_close_defect(self):
        f = self.finding(status="resolved")
        (self.root / "after.png").write_bytes(b"different synthetic image")
        self.review["evidence"].append({"id": "after", "kind": "frame", "inspection": "ai_visual", "times": [3], "path": "after.png", "sha256": quality.sha(self.root / "after.png")})
        f.update(fix="Synthetic correction", recheck={"design_hash": self.review["design_hash"], "observation": "Synthetic observed correction", "evidence_ids": ["after"]})
        self.assertTrue(self.result()["quality_ready"])

    def test_minor_exception_requires_reason(self):
        self.finding(severity="minor", status="accepted")
        self.assertIn("quality_unexplained_exception", self.codes())

    def test_changed_observation_file_invalidates_review(self):
        (self.root / "assets/cycle.mp4").write_bytes(b"changed synthetic source")
        self.assertIn("quality_evidence_changed", self.codes())

    def test_changed_html_invalidates_review(self):
        (self.preview / "index.html").write_text("changed scene")
        p = self.preview / "build-manifest.json"
        data = quality.read(p)
        data["composition_sha256"] = quality.sha(self.preview / "index.html")
        prod.write_json(p, data)
        self.assertIn("quality_stale_preview", self.codes())

    def test_changed_font_invalidates_review(self):
        (self.preview / "assets/font.otf").write_bytes(b"changed glyphs")
        self.assertIn("quality_stale_preview", self.codes())

    def test_added_subcomposition_invalidates_review(self):
        (self.preview / "compositions").mkdir()
        (self.preview / "compositions/next.html").write_text("new scene")
        self.assertIn("quality_stale_preview", self.codes())

    def test_rubric_change_requires_reassessment(self):
        p = self.root / "config/quality-rubric.json"
        d = quality.read(p)
        d["minimum_rating"] = 4
        prod.write_json(p, d)
        self.assertIn("quality_stale_rubric", self.codes())

    def test_internal_preview_does_not_authorize_generation(self):
        self.review["profile"] = "internal_preview"
        self.assertIn("quality_scope", self.codes())

    def test_skipped_browser_is_not_technical_pass(self):
        p = self.root / "technical.json"
        d = quality.read(p)
        d["browserSkipped"] = True
        prod.write_json(p, d)
        self.evidence("technical")["sha256"] = quality.sha(p)
        self.assertIn("quality_technical", self.codes())

    def test_old_technical_report_binding_is_blocked(self):
        self.evidence("technical")["preview_hash"] = "old"
        self.assertIn("quality_technical_binding", self.codes())

    def test_uncertainty_cannot_be_resolved_without_proof(self):
        self.review["risks"].append({"unknown": "voice identity", "validation": "listen to fixed voice", "status": "resolved"})
        self.assertIn("quality_risk_proof", self.codes())

    def test_minimum_test_must_exist(self):
        self.review["risks"].append({"unknown": "voice identity", "validation": "listen to fixed voice", "status": "test_first", "request_id": "missing"})
        self.assertIn("quality_risk_test", self.codes())

    def test_transition_needs_both_sides(self):
        plan = copy.deepcopy(self.plan)
        plan["shots"][0]["end"] = 5
        second = copy.deepcopy(plan["shots"][0])
        second.update(id="S02", start=5, end=10)
        plan["shots"].append(second)
        plan["transitions"] = [{"from": "S01", "to": "S02"}]
        prod.write_json(self.plan_path, plan)
        prod.write_json(self.preview / "build-manifest.json", {"plan": "plan.json", "plan_sha256": quality.sha(self.plan_path), "composition_sha256": quality.sha(self.preview / "index.html"), "assets": []})
        self.review_path.unlink()
        review = quality.initialize(self.plan_path, self.review_path, "preview/build-manifest.json", self.root)
        review["evidence"] = copy.deepcopy(self.review["evidence"])
        review["technical_evidence_id"] = "technical"
        review["evidence"][1]["preview_hash"] = review["preview"]["hash"]
        for item in review["assessments"]:
            item.update(verdict="pass", rating=3, observation="Synthetic", countercheck="Synthetic", rationale="Synthetic", evidence_ids=["video"])
        self.review = review
        self.evidence("video")["times"] = [4]
        self.assertIn("quality_seam_pair", self.codes())
        self.evidence("video")["times"] = [4.9, 5.1]
        self.assertNotIn("quality_seam_pair", self.codes())

    def test_generate_cannot_use_legacy_pass_only_reviews(self):
        prod.write_json(self.root / "config/production-policy.json", {"customer_min_short_edge": 720, "quality_review": {"required": True}})
        self.assertIn("quality_review_missing", {e["code"] for e in prod.audit(self.plan_path, self.root, "generate")["errors"]})

    def test_generate_integrates_quality_results(self):
        prod.write_json(self.root / "config/production-policy.json", {"customer_min_short_edge": 720, "quality_review": {"required": True}})
        r = prod.read_json(self.root / "reviews.json")
        r["quality_review"] = "quality-review.json"
        prod.write_json(self.root / "reviews.json", r)
        prod.write_json(self.review_path, self.review)
        self.assertTrue(prod.audit(self.plan_path, self.root, "generate")["ready"])
        self.assessment("action_clarity")["rating"] = 1
        prod.write_json(self.review_path, self.review)
        self.assertFalse(prod.audit(self.plan_path, self.root, "generate")["ready"])

    def make_sequence(self, times):
        frames = []
        for i, t in enumerate(times):
            path = self.root / ("sample-%03d.png" % i)
            path.write_bytes(("synthetic frame %d" % i).encode())
            frames.append({"time": t, "path": path.name, "sha256": quality.sha(path)})
        prod.write_json(self.root / "sequence.json", {"source_path": "assets/cycle.mp4", "source_sha256": quality.sha(self.root / "assets/cycle.mp4"), "frames": frames})
        return {"id": "sequence", "path": "sequence.json", "sha256": quality.sha(self.root / "sequence.json"), "kind": "frame_sequence", "inspection": "ai_sequence", "coverage": [0, 10]}

    def test_dense_observed_sequence_can_cover_motion_window(self):
        item = self.make_sequence([n / 2 for n in range(20)])
        self.assertTrue(quality.temporal(item, [0, 10], self.root, .5))

    def test_sparse_pose_cards_cannot_be_called_continuous(self):
        item = self.make_sequence([0, 5, 9.5])
        self.assertFalse(quality.temporal(item, [0, 10], self.root, .5))

    def test_prepared_but_uninspected_sequence_does_not_pass(self):
        item = self.make_sequence([n / 2 for n in range(20)])
        item["inspection"] = "uninspected"
        self.assertFalse(quality.temporal(item, [0, 10], self.root, .5))

    def test_sequence_frame_tampering_is_detected(self):
        item = self.make_sequence([n / 2 for n in range(20)])
        (self.root / "sample-004.png").write_bytes(b"changed synthetic frame")
        self.assertFalse(quality.temporal(item, [0, 10], self.root, .5))

    def test_sequence_missing_return_cannot_exaggerate_coverage(self):
        item = self.make_sequence([n / 2 for n in range(15)])
        self.assertFalse(quality.temporal(item, [0, 10], self.root, .5))

    def test_unrelated_motion_video_cannot_prove_this_cycle(self):
        path = self.root / "different.mp4"
        path.write_bytes(b"unrelated synthetic motion")
        self.evidence("video").update(path=path.name, sha256=quality.sha(path))
        self.assertIn("quality_motion_source", self.codes())

    def test_boolean_is_not_a_quality_rating(self):
        self.assessment("action_clarity")["rating"] = True
        self.assertIn("quality_rating", self.codes())

    def test_unreviewed_technical_warning_is_blocking(self):
        path = self.root / "technical.json"
        data = quality.read(path)
        data["lint"]["findings"] = [{"severity": "warning", "code": "synthetic_warning"}]
        prod.write_json(path, data)
        self.evidence("technical")["sha256"] = quality.sha(path)
        self.assertIn("quality_warning_unreviewed", self.codes())

    def test_changed_plan_does_not_inherit_previous_quality(self):
        self.plan["shots"][0]["dialogue"] = "changed instruction"
        prod.write_json(self.plan_path, self.plan)
        data = quality.read(self.preview / "build-manifest.json")
        data["plan_sha256"] = quality.sha(self.plan_path)
        prod.write_json(self.preview / "build-manifest.json", data)
        self.assertIn("quality_stale_design", self.codes())


if __name__ == "__main__":
    unittest.main()
