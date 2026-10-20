from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import production as prod
import timeline_sync as sync


class TimelineSync(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        audio = self.root / "audio.wav"
        audio.write_bytes(b"fixture: not real speech")
        evidence = self.root / "listening.md"
        evidence.write_text("Test-only timing observation")
        self.spec = {"audio": {"path": "audio.wav", "sha256": prod.hashlib.sha256(audio.read_bytes()).hexdigest()},
            "speech": [{"id": "A01", "text": "천천히 일어서요.", "start": 1.0, "end": 2.0,
                        "timing_basis": "manual_listening", "timing_evidence": {"path": evidence.name, "sha256": prod.hashlib.sha256(evidence.read_bytes()).hexdigest()}}],
            "actions": [{"id": "rise", "onset": 2.4, "timing_basis": "observed_output"}],
            "cues": [{"speech_id": "A01", "action_id": "rise", "speech_anchor": "end", "lead_min_seconds": .2, "lead_max_seconds": .7}],
            "captions": [{"speech_id": "A01", "start": 1.0, "end": 2.0, "text": "천천히 일어서요."}]}

    def run_check(self):
        return sync.check(self.spec, self.root, duration=5)

    def test_observed_cue_and_caption_timing_passes_without_claiming_listening(self):
        r = self.run_check()
        self.assertTrue(r["ready"])
        self.assertEqual(r["cue_results"][0]["lead_seconds"], .4)
        self.assertFalse(r["direct_listening_by_this_tool"])

    def test_late_instruction_and_caption_drift_are_separate_failures(self):
        self.spec["actions"][0]["onset"] = 1.5
        self.spec["captions"][0]["start"] = 1.4
        self.assertTrue({"cue_timing", "caption_timing"} <= {e["code"] for e in self.run_check()["errors"]})

    def test_estimated_transcript_cannot_pass_as_actual_alignment(self):
        self.spec["speech"][0]["timing_basis"] = "existing_caption_estimate"
        r = self.run_check()
        self.assertFalse(r["ready"])
        self.assertTrue(r["pending"])

    def test_audio_change_or_missing_timing_evidence_blocks_reuse(self):
        (self.root / "audio.wav").write_bytes(b"different")
        (self.root / "listening.md").unlink()
        self.assertTrue({"audio_changed", "timing_evidence"} <= {e["code"] for e in self.run_check()["errors"]})

    def test_source_audio_bounds_and_missing_action_links(self):
        self.spec["speech"][0]["end"] = 6
        self.assertIn("speech_range", {e["code"] for e in self.run_check()["errors"]})


if __name__ == "__main__":
    unittest.main()
