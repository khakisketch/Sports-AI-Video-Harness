import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('public_check', Path(__file__).resolve().parents[1] / 'scripts/check_public.py')
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class PublicFilesTest(unittest.TestCase):
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
