import copy
import json
from pathlib import Path
import runpy
import unittest

ROOT = Path(__file__).resolve().parents[1]
validate = runpy.run_path(str(ROOT / 'scripts/previs.py'))['validate']


class CameraAndGeometryInputs(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads((ROOT / 'config/previs/demo.json').read_text())

    def test_legacy_input_remains_valid(self):
        validate(self.spec)

    def test_ep001_input_valid(self):
        validate(json.loads((ROOT / 'examples/previs-bridge.json').read_text()))

    def test_nonfinite_camera_rejected(self):
        self.spec['camera_settings'] = {'position': [float('nan'), 0, 1]}
        with self.assertRaises(ValueError):
            validate(self.spec)

    def test_coincident_camera_rejected(self):
        self.spec['camera_settings'] = {'position': [0, 0, 0], 'target': [0, 0, 0]}
        with self.assertRaises(ValueError):
            validate(self.spec)

    def test_invalid_scale_rejected(self):
        self.spec['camera_settings'] = {'ortho_scale': 0}
        with self.assertRaises(ValueError):
            validate(self.spec)

    def test_invalid_mat_rejected(self):
        self.spec['mat'] = {'center': [0, 0, 0], 'dimensions': [-1, 1, 1]}
        with self.assertRaises(ValueError):
            validate(self.spec)


if __name__ == '__main__':
    unittest.main()
