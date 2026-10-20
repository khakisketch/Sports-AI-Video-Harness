import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class PreviewScopeTests(unittest.TestCase):
    def test_default_tracks_active_preview_instead_of_old_episode(self):
        runtime = module("hyperframes")
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "config").mkdir()
            config = base / "config/series.json"
            config.write_text(json.dumps({"production": {"preproduction": {"active_preview": "episodes/EP999/previews/new"}}}))
            with patch.object(runtime, "ROOT", base):
                self.assertEqual(runtime.default_project(), base / "episodes/EP999/previews/new")

    def test_reset_does_not_fall_back_to_retired_preview(self):
        runtime = module("hyperframes")
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "config").mkdir()
            (base / "config/series.json").write_text(json.dumps({"production": {"preproduction": {"active_preview": None}}}))
            with patch.object(runtime, "ROOT", base), self.assertRaisesRegex(ValueError, "활성 미리보기"):
                runtime.default_project()

    def test_preview_uses_child_project_and_omits_hosted_credentials(self):
        runtime = module("hyperframes")
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder).resolve()
            entry = base / "tools/hyperframes/node_modules/hyperframes/bin/hyperframes.mjs"
            entry.parent.mkdir(parents=True)
            entry.write_text("")
            project = base / "preview"
            project.mkdir()
            with patch.object(runtime, "ROOT", base), patch.object(runtime, "node_path", return_value=Path("/fake/node")), patch.dict(os.environ, {"PWD": "/wrong-parent", "HEYGEN_API_KEY": "test-only"}):
                cmd, env, cwd = runtime.command_and_environment("preview", project, ["--background"])
            self.assertEqual(env["PWD"], str(project.resolve()))
            self.assertEqual(cmd[-2:], [str(project.resolve()), "--background"])
            self.assertNotIn("HEYGEN_API_KEY", env)
            self.assertEqual(cwd, project.resolve())

    def test_hosted_and_generation_commands_never_reach_runtime(self):
        runtime = module("hyperframes")
        with patch.object(runtime, "node_path") as node:
            for command in ("render", "cloud", "lambda", "cloudrun", "publish", "tts", "auth", "media-use", "transcribe"):
                with self.subTest(command=command), self.assertRaises(ValueError):
                    runtime.command_and_environment(command, ROOT, [])
            node.assert_not_called()

    def test_project_outside_workspace_rejected_before_runtime(self):
        runtime = module("hyperframes")
        with patch.object(runtime, "node_path") as node, self.assertRaises(ValueError):
            runtime.command_and_environment("preview", ROOT.parent, [])
        node.assert_not_called()

    def test_source_tampering_rejected_before_copy(self):
        builder = module("build_hyperframes_preview")
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            asset = Path(folder) / "input.png"
            asset.write_bytes(b"changed input")
            with self.assertRaisesRegex(ValueError, "해시"):
                builder.source({"id": "reference", "path": str(asset.relative_to(ROOT)), "sha256": "0" * 64})

    def test_existing_review_is_not_overwritten(self):
        builder = module("build_hyperframes_preview")
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            out = Path(folder)
            (out / "build-manifest.json").write_text("reviewed")
            with self.assertRaisesRegex(ValueError, "덮어쓰지"):
                builder.build(out / "unused-plan.json", out)
            self.assertEqual((out / "build-manifest.json").read_text(), "reviewed")


if __name__ == "__main__":
    unittest.main()
