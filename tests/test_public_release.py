import importlib.util
from pathlib import Path
import tempfile
import unittest
import hashlib
import json

SPEC = importlib.util.spec_from_file_location('public_check', Path(__file__).resolve().parents[1] / 'scripts/check_public.py')
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class PublicFilesTest(unittest.TestCase):
    def evidence(self, root, data, relative='docs/evidence/capture.png', mime='image/png'):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        manifest = root / 'docs/evidence/files.json'
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({'files': {relative: {
            'sha256': hashlib.sha256(data).hexdigest(), 'mime': mime,
            'privacy_review': 'visually_inspected_public_sample'}}}))
        return path

    def test_reviewed_capture_and_video_have_explicit_hashes(self):
        for data, suffix, mime in ((b'\x89PNG\r\n\x1a\n\xff', '.png', 'image/png'),
                                   (b'\0\0\0\x18ftypmp42\xff', '.mp4', 'video/mp4')):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                self.evidence(root, data, 'docs/evidence/example' + suffix, mime)
                self.assertTrue(CHECK.check(root)['passed'])

    def test_changed_capture_requires_new_review_record(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = self.evidence(root, b'\x89PNG\r\n\x1a\n\xff')
            path.write_bytes(path.read_bytes() + b'changed')
            self.assertFalse(CHECK.check(root)['passed'])

    def test_unlisted_or_wrong_format_binary_is_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'unlisted.png').write_bytes(b'\x89PNG\r\n\x1a\n\xff')
            self.assertFalse(CHECK.check(root)['passed'])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.evidence(root, b'\xffnot-a-png')
            self.assertFalse(CHECK.check(root)['passed'])

    def test_listing_private_assets_does_not_allow_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.evidence(root, b'\x89PNG\r\n\x1a\n\xff', 'assets/person.png')
            self.assertFalse(CHECK.check(root)['passed'])

    def test_example_environment_and_placeholder_paths_are_allowed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '.env.example').write_text('OPENAI_API_KEY=\n')
            (root / 'README.md').write_text('/Users/you/project /Users/<user>/example C:/Users/t/file')
            self.assertTrue(CHECK.check(root)['passed'])

    def test_private_runtime_files_block_publication(self):
        for relative in ('assets/person.png', 'episodes/EP001/plan.json', '.env.local', 'jobs.json'):
            with self.subTest(path=relative), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('private')
                self.assertFalse(CHECK.check(root)['passed'])

    def test_secret_is_flagged_without_echoing_value(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            token = 'ghp_' + 'x' * 30
            (root / 'example.txt').write_text(token)
            result = CHECK.check(root)
            self.assertFalse(result['passed'])
            self.assertNotIn(token, str(result))

    def test_machine_path_is_flagged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'config.json').write_text('/Users/' + 'private-person/project')
            self.assertFalse(CHECK.check(root)['passed'])
