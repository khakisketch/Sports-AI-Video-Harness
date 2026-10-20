import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import scene_pipeline as scene
import production as prod
import media_worker


class ScenePackages(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name).resolve()
        def node(key, kind, deps=()):
            path = self.root / (key + ".bin")
            path.write_bytes((key + " immutable fixture").encode())
            return {"kind": kind, "version": "v1", "path": path.name,
                    "sha256": prod.hashlib.sha256(path.read_bytes()).hexdigest(), "depends_on": list(deps), "duration_seconds": 8}
        self.package = {"schema_version": 1, "nodes": {
            "identity": node("identity", "image"), "motion": node("motion", "video", ("identity",)),
            "caption1": node("caption1", "captions"), "caption2": node("caption2", "captions"),
            "render1": node("render1", "scene_render", ("motion", "caption1")),
            "render2": node("render2", "scene_render", ("motion", "caption2")),
            "final": node("final", "final_render", ("render1", "render2"))},
            "scenes": [{"id": "S01", "start": 0, "end": 8, "performance": True,
                        "components": {"video": "motion", "captions": "caption1", "render": "render1"}},
                       {"id": "S02", "start": 8, "end": 16, "components": {"video": "motion", "captions": "caption2", "render": "render2"}}],
            "cut_reviews": {}, "final_render": "final"}
        self.plan = self.root / "plan.json"
        self.plan.write_text('{"fixture_only":true}')
        self.package["plan_ref"] = {"path": self.plan.name, "sha256": prod.hashlib.sha256(self.plan.read_bytes()).hexdigest()}
        self.package_path = self.root / "scenes.json"

    def accept_motion(self):
        evidence = {"path": "motion.bin", "sha256": self.package["nodes"]["motion"]["sha256"]}
        checks = {k: {"verdict": "pass", "observation": "test-only observation fixture", "evidence": [evidence],
                      "method": "time_series_visual", "observed_range_seconds": [0, 8]} for k in ("technical", "identity", "framing", "motion")}
        self.package["cut_reviews"]["motion"] = {"verdict": "pass", "reviewer": "test fixture only",
            "asset_fingerprint": scene.node_fingerprint(self.package["nodes"], "motion"), "checks": checks}

    def test_caption_change_rebuilds_one_scene_and_final_preserves_other_sources(self):
        change = scene.impact(self.package, ["caption1"])
        self.assertEqual(set(change["affected_nodes"]), {"caption1", "render1", "final"})
        self.assertEqual(change["affected_scenes"], ["S01"])
        self.assertTrue({"motion", "identity", "render2", "caption2"} <= set(change["preserve_nodes"]))
        self.assertFalse(change["new_generation_authorized"])

    def test_missing_cut_observation_blocks_edit_but_not_inventory(self):
        self.assertTrue(scene.audit(self.package, self.root)["ready"])
        self.assertFalse(scene.audit(self.package, self.root, "edit")["ready"])
        self.accept_motion()
        self.assertTrue(scene.audit(self.package, self.root, "edit")["ready"])

    def test_still_samples_and_modified_identity_do_not_reuse_motion_pass(self):
        self.accept_motion()
        self.package["cut_reviews"]["motion"]["checks"]["motion"]["method"] = "still_frames"
        self.assertFalse(scene.audit(self.package, self.root, "edit")["ready"])
        self.accept_motion()
        self.package["nodes"]["identity"]["version"] = "v2"
        self.assertFalse(scene.audit(self.package, self.root, "edit")["ready"])

    def test_exact_motion_requires_verified_reference_and_match_evidence(self):
        self.accept_motion()
        self.package["scenes"][0]["exact_motion_required"] = True
        self.assertFalse(scene.audit(self.package, self.root, "edit")["ready"])

    def test_cycles_missing_dependencies_changed_sources_are_rejected(self):
        self.package["nodes"]["identity"]["depends_on"] = ["motion"]
        with self.assertRaises(ValueError):
            scene.impact(self.package, ["caption1"])
        self.package["nodes"]["identity"]["depends_on"] = []
        (self.root / "motion.bin").write_bytes(b"changed")
        self.assertFalse(scene.audit(self.package, self.root)["ready"])

    def test_new_edit_recipe_enforces_cut_gate_before_any_output(self):
        prod.write_json(self.package_path, self.package)
        recipe = {"schema_version": 2, "mode": "local_edit", "scene_package": str(self.package_path)}
        with patch.object(media_worker, "ROOT", self.root), self.assertRaisesRegex(ValueError, "cut_not_accepted"):
            media_worker.scene_gate(recipe)
        self.assertFalse((self.root / "output").exists())
        recipe["scene_profile"] = "internal_preview"
        with patch.object(media_worker, "ROOT", self.root):
            self.assertFalse(media_worker.scene_gate(recipe)["content_quality_automatically_approved"])

    def test_missing_voice_and_full_review_block_delivery(self):
        self.accept_motion()
        self.package["scenes"][0]["voice_required"] = True
        result = scene.audit(self.package, self.root, "delivery")
        self.assertTrue({"voice_missing", "final_review_pending"} <= {e["code"] for e in result["errors"]})


if __name__ == "__main__":
    unittest.main()
