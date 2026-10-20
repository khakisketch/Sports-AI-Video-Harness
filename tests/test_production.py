import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import production as prod


class ProductionGate(unittest.TestCase):
    """Exercise spend/continuity invariants using metadata fixtures, not real approvals."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "assets").mkdir()
        # These fixtures isolate existing metadata gates. test_quality.py covers
        # the additional mandatory evidence/semantic-review integration.
        prod.write_json(self.root / "config/production-policy.json", {"customer_min_short_edge": 720, "quality_review": {"required": False}, "direction_design": {"required": True}})
        prod.write_json(self.root / "config/character.json", {"current_design_status": "appearance_adopted_by_user"})
        (self.root / "assets/identity.png").write_bytes(b"image metadata fixture")
        (self.root / "assets/cycle.mp4").write_bytes(b"motion metadata fixture")
        (self.root / "evidence.md").write_text("Test-only review evidence")
        self.path = self.root / "plan.json"
        self.plan = {
            "schema_version": 1, "episode_id": "EP999", "version": "test",
            "brief": {"topic": "test", "viewer_question": "what moves?", "takeaway": "one complete cycle", "format": "self_demonstration", "target_seconds": 10, "aspect_ratio": "9:16", "customer_preview": True},
            "cast": [{"id": "host", "role": "instructor", "character_record": "config/character.json"}],
            "assets": [{"id": "identity", "kind": "image", "path": "assets/identity.png"}, {"id": "cycle", "kind": "motion_video", "path": "assets/cycle.mp4"}],
            "shots": [{"id": "S01", "start": 0, "end": 10, "purpose": "show a cycle", "cast_id": "host", "action": "prepare, move, return", "start_pose": "rest", "end_pose": "rest", "view": "locked near-side", "framing": "full_body", "speech_mode": "demonstrator_voiceover", "dialogue": "Test dialogue", "speech_estimate_seconds": 2, "reference_ids": ["identity", "cycle"]}],
            "transitions": [],
            "motion_cycles": [{"id": "C01", "shot_ids": ["S01"], "phases": [{"phase": "prepare", "start": 0, "end": 2}, {"phase": "action", "start": 2, "end": 8}, {"phase": "return", "start": 8, "end": 10}], "proof_asset_id": "cycle", "proof_covers_full_cycle": True}],
            "execution": {"retries": 0, "requests": [{"id": "G01", "shot_ids": ["S01"], "purpose": "test continuity", "success_criteria": ["all phases visible"], "params": {"model": "fixture_model", "prompt": "test only", "count": 1, "resolution": "720p", "duration": 10}, "quote": {"credits": 10, "checked_at": "2026-10-15T00:00:00+09:00"}}]},
        }
        self.plan["direction"] = {
            "opening_visual": "Test-only visible setup", "closing_payoff": "Test-only complete return",
            "visual_style": "Test-only readable body and mat", "sound_strategy": "Test-only deliberate silence",
            "preview_level": "motion_blocking",
            "beats": [{"id": "B" + str(i), "shot_id": "S01", "start": a, "end": b,
                       "new_information": phase, "subject_action": phase, "camera_action": "locked for support visibility",
                       "attention_target": "body and supports", "audio_plan": "deliberate silence",
                       "exit_state": phase + " complete", "development": "action", "representation": "existing_motion"}
                      for i, (a, b, phase) in enumerate(((0, 2, "prepare"), (2, 8, "perform"), (8, 10, "return")), 1)]
        }
        for a in self.plan["assets"]:
            a["sha256"] = prod.hashlib.sha256((self.root / a["path"]).read_bytes()).hexdigest()
        self.plan["execution"]["requests"][0]["quote"]["params_hash"] = prod.digest(self.plan["execution"]["requests"][0]["params"])
        prod.write_json(self.root / "jobs.json", [])
        self.bind_reviews()

    def bind_reviews(self, cap=10):
        prod.write_json(self.path, self.plan)
        fp = prod.fingerprints(self.plan, self.root)
        r = {"roles": {k: {"verdict": "pass", "design_hash": fp["design_hash"], "notes": "Test-only inspected evidence", "evidence": ["evidence.md"]} for k in prod.ROLES}, "design_approval": {"decision": "approved", "source": "test fixture only", "design_hash": fp["design_hash"]}, "execution_approval": {"decision": "approved", "source": "test fixture only", "execution_hash": fp["execution_hash"], "max_credits": cap, "retries": 0}}
        r["roles"]["feasibility"]["execution_hash"] = fp["execution_hash"]
        prod.write_json(self.root / "reviews.json", r)

    def result(self, stage="generate", request_id=None):
        prod.write_json(self.path, self.plan)
        return prod.audit(self.path, self.root, stage, request_id)

    def codes(self):
        return {e["code"] for e in self.result()["errors"]}

    def test_complete_metadata_with_bound_reviews_passes(self):
        self.assertTrue(self.result()["ready"])

    def test_legacy_plan_requires_direction_before_new_execution(self):
        self.plan.pop("direction")
        self.assertIn("direction_missing", self.codes())

    def test_layout_only_does_not_pass_as_animatic(self):
        self.plan["direction"]["preview_level"] = "layout_only"
        self.assertIn("direction_layout_only", self.codes())

    def test_still_references_cannot_be_relabelled_animatic(self):
        for beat in self.plan["direction"]["beats"]:
            beat["representation"] = "static_reference"
        self.assertIn("direction_representation", self.codes())

    def test_missing_or_unbuilt_beat_cannot_hide_in_complete_timeline(self):
        self.plan["direction"]["beats"][1]["representation"] = "unbuilt"
        self.assertIn("direction_representation", self.codes())
        self.plan["direction"]["beats"].pop(1)
        self.assertIn("direction_beat_gap", self.codes())

    def test_direction_requires_real_shot_and_in_range_times(self):
        beat = self.plan["direction"]["beats"][0]
        beat["shot_id"] = "not-a-shot"
        self.assertIn("direction_beat_shot", self.codes())
        beat["shot_id"], beat["start"] = "S01", -1
        self.assertIn("direction_beat_shot", self.codes())

    def test_camera_and_subject_actions_must_be_separate(self):
        self.plan["direction"]["beats"][1]["camera_action"] = ""
        self.assertIn("direction_beat_detail", self.codes())

    def test_intentional_hold_requires_viewing_reason(self):
        beat = self.plan["direction"]["beats"][1]
        beat["development"] = "intentional_hold"
        self.assertIn("direction_hold_reason", self.codes())
        beat["hold_reason"] = "Test-only time to observe support points"
        self.assertNotIn("direction_hold_reason", self.codes())

    def test_fixed_camera_and_single_shot_are_valid_when_action_progresses(self):
        self.assertEqual(len(self.plan["shots"]), 1)
        self.assertFalse(prod.direction_errors(self.plan, require_animatic=True))

    def test_direction_change_invalidates_bound_reviews(self):
        self.plan["direction"]["beats"][1]["subject_action"] = "Different planned performance"
        self.assertIn("stale_review", self.codes())

    def test_direction_packet_exposes_action_camera_and_sound(self):
        self.assertIn("locked for support visibility", prod.packet(self.path, self.root))
        self.assertIn("deliberate silence", prod.packet(self.path, self.root))

    def test_no_motion_cycle_is_blocked(self):
        self.plan["motion_cycles"] = []
        self.assertIn("motion_cycle", self.codes())

    def add_title_card(self):
        self.plan["brief"]["target_seconds"] = 12
        self.plan["shots"].append({"id": "T01", "shot_type": "title_card", "start": 10, "end": 12,
            "purpose": "Test-only editorial recap", "action": "Hold one readable sentence on a dark background",
            "view": "flat graphic", "framing": "full_frame", "speech_mode": "none", "dialogue": "",
            "speech_estimate_seconds": 0, "reference_ids": [], "cast_id": None})
        self.plan["transitions"].append({"from": "S01", "to": "T01", "purpose": "Recap after the completed cycle",
            "continuity": "visible_change", "continuity_explanation": "A clear cut to a text card after the performer returns"})
        self.plan["direction"]["beats"].append({"id": "BT", "shot_id": "T01", "start": 10, "end": 12,
            "new_information": "One editorial recap", "subject_action": "Hold the recap text",
            "camera_action": "No camera", "attention_target": "Recap sentence", "audio_plan": "Silence",
            "exit_state": "Readable full sentence", "development": "intentional_hold", "hold_reason": "Time to read",
            "representation": "composed_graphic"})

    def test_local_title_with_full_cycle_needs_no_fake_actor_or_pose(self):
        self.add_title_card()
        self.bind_reviews()
        self.assertTrue(self.result()["ready"])

    def test_graphic_does_not_remove_first_cycle_requirement(self):
        self.add_title_card()
        self.plan["motion_cycles"] = []
        self.assertIn("motion_cycle", self.codes())

    def test_title_card_cannot_be_claimed_as_exercise_cycle(self):
        self.add_title_card()
        self.plan["motion_cycles"][0]["shot_ids"] = ["T01"]
        self.assertIn("motion_proof", self.codes())

    def test_composed_graphic_cannot_prove_performer_motion(self):
        self.plan["direction"]["beats"][1]["representation"] = "composed_graphic"
        self.assertIn("direction_representation", self.codes())

    def test_title_transition_cannot_fake_matching_pose_with_nulls(self):
        self.add_title_card()
        self.plan["transitions"][0]["continuity"] = "matched_pose"
        self.plan["shots"][0]["end_pose"] = None
        self.assertIn("pose_jump", self.codes())

    def test_standalone_graphic_design_needs_no_character_or_motion_asset(self):
        self.add_title_card()
        card = self.plan["shots"][-1]
        card.update(start=0, end=10)
        beat = self.plan["direction"]["beats"][-1]
        beat.update(start=0, end=10)
        self.plan["brief"].update(format="graphic", target_seconds=10)
        self.plan.update(cast=[], assets=[], shots=[card], transitions=[], motion_cycles=[])
        self.plan["direction"]["beats"] = [beat]
        self.plan["execution"]["requests"] = []
        self.bind_reviews()
        self.assertTrue(self.result(stage="design")["ready"])
        self.assertIn("requests", {e["code"] for e in self.result(stage="generate")["errors"]})

    def test_photo_is_not_motion_evidence(self):
        self.plan["motion_cycles"][0]["proof_asset_id"] = "identity"
        self.assertIn("motion_proof", self.codes())

    def test_renaming_photo_kind_does_not_make_it_video(self):
        self.plan["assets"][0]["kind"] = "motion_video"
        self.plan["motion_cycles"][0]["proof_asset_id"] = "identity"
        self.assertIn("motion_proof", self.codes())

    def test_cut_inside_first_cycle_is_blocked(self):
        self.plan["motion_cycles"][0]["shot_ids"] = ["S01", "S02"]
        self.assertIn("cycle_cut", self.codes())

    def test_same_character_teacher_student_is_not_direct_demonstration(self):
        self.plan["cast"].append({"id": "student", "role": "student", "character_record": "config/character.json"})
        self.assertIn("role_switch", self.codes())

    def test_unexplained_transition_is_blocked(self):
        self.plan["transitions"] = [{"from": "S01", "to": "missing"}]
        self.assertIn("transition_count", self.codes())

    def test_gap_in_phases_is_blocked(self):
        self.plan["motion_cycles"][0]["phases"][1]["start"] = 3
        self.assertIn("phase_gap", self.codes())

    def test_speech_overruns_shot(self):
        self.plan["shots"][0]["speech_estimate_seconds"] = 11
        self.assertIn("speech_fit", self.codes())

    def test_editing_dialogue_invalidates_reviews_and_approval(self):
        self.plan["shots"][0]["dialogue"] = "Changed words"
        self.assertIn("stale_review", self.codes())
        self.assertIn("design_approval", self.codes())

    def test_editing_asset_bytes_invalidates_snapshot(self):
        (self.root / "assets/cycle.mp4").write_bytes(b"changed content")
        self.assertIn("asset_changed", self.codes())
        self.assertIn("stale_review", self.codes())

    def test_execution_change_preserves_design_but_invalidates_quote(self):
        before = prod.fingerprints(self.plan, self.root)
        self.plan["execution"]["requests"][0]["params"]["resolution"] = "1080p"
        after = prod.fingerprints(self.plan, self.root)
        self.assertEqual(before["design_hash"], after["design_hash"])
        self.assertNotEqual(before["execution_hash"], after["execution_hash"])
        self.assertIn("quote_changed", self.codes())
        self.assertIn("execution_approval", self.codes())
        self.assertIn("execution_feasibility", self.codes())

    def test_price_refresh_within_cap_does_not_revoke_approval(self):
        self.bind_reviews(cap=20)
        self.plan["execution"]["requests"][0]["quote"]["credits"] = 15
        self.assertTrue(self.result()["ready"])

    def test_price_above_cap_is_blocked(self):
        self.plan["execution"]["requests"][0]["quote"]["credits"] = 11
        self.assertIn("cost_cap", self.codes())

    def test_customer_480p_is_blocked(self):
        self.plan["execution"]["requests"][0]["params"]["resolution"] = "480p"
        self.assertIn("customer_resolution", self.codes())

    def test_non_numeric_quote_is_blocked(self):
        self.plan["execution"]["requests"][0]["quote"]["credits"] = "10"
        self.assertIn("quote_missing", self.codes())

    def test_completed_and_ambiguous_jobs_are_not_resubmitted(self):
        for status in ("submitted", "completed", "submission_unknown", "failed"):
            with self.subTest(status=status):
                prod.write_json(self.root / "jobs.json", [{"params_hash": prod.digest(self.plan["execution"]["requests"][0]["params"]), "job_id": "fixture-job", "status": status}])
                self.assertIn("duplicate_job", self.codes())

    def test_selecting_remaining_request_avoids_replaying_completed_sibling(self):
        other = copy.deepcopy(self.plan["execution"]["requests"][0])
        other["id"] = "G02"
        other["params"]["prompt"] = "second distinct request"
        other["quote"]["params_hash"] = prod.digest(other["params"])
        self.plan["execution"]["requests"].append(other)
        self.bind_reviews(cap=20)
        prod.write_json(self.root / "jobs.json", [{"params_hash": prod.digest(self.plan["execution"]["requests"][0]["params"]), "job_id": "fixture-job", "status": "completed"}])
        self.assertTrue(self.result(request_id="G02")["ready"])
        self.assertFalse(self.result()["ready"])

    def test_unsigned_review_does_not_pass_on_verdict_alone(self):
        p = self.root / "reviews.json"
        r = prod.read_json(p)
        r["roles"]["direction"]["notes"] = ""
        prod.write_json(p, r)
        self.assertIn("review_direction", self.codes())

    def test_cli_malformed_input_fails_closed(self):
        self.path.write_text('{"broken": NaN}')
        result = subprocess.run([sys.executable, str(prod.ROOT / "scripts/production.py"), "--root", str(self.root), "check", str(self.path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(json.loads(result.stdout)["ready"])

    def test_init_never_overwrites_existing_folder(self):
        templates = self.root / "templates/production"
        templates.mkdir(parents=True)
        for filename in ("plan.json", "reviews.json"):
            (templates / filename).write_text((prod.ROOT / "templates/production" / filename).read_text())
        cmd = [sys.executable, str(prod.ROOT / "scripts/production.py"), "--root", str(self.root), "init", "--episode", "EP999", "--version", "plan-v1"]
        first = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0)
        target = self.root / "episodes/EP999/planning/plan-v1/plan.json"
        target.write_text("preserve this")
        second = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(second.returncode, 2)
        self.assertEqual(target.read_text(), "preserve this")


if __name__ == "__main__":
    unittest.main()
