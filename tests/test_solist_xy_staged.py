import tempfile
import unittest
from pathlib import Path

import numpy as np

from sklearn.preprocessing import StandardScaler

from sim.solist_xy_staged import (
    BANK_WIDTH,
    export_header,
    solist_stage_layout,
    staged_bfloat16_predict,
)


class _Model:
    def __init__(self):
        rng = np.random.default_rng(7)
        self.coefs_ = [
            rng.normal(0, 0.08, (128, 128)),
            rng.normal(0, 0.08, (128, 64)),
            rng.normal(0, 0.08, (64, 2)),
        ]
        self.intercepts_ = [np.zeros(128), np.zeros(64), np.asarray((0.4, 0.6))]


class StagedSolistTests(unittest.TestCase):
    def test_layout_never_exceeds_driver_hidden_limit(self):
        layout = solist_stage_layout(_Model())
        self.assertEqual(len(layout), 4)
        self.assertTrue(all(stage["hidden_size"] <= BANK_WIDTH for stage in layout))
        self.assertEqual([stage["input_size"] for stage in layout], [129, 129, 129, 129])
        self.assertTrue(all(stage["input_size"] <= 256 for stage in layout))

    def test_firmware_architecture_reserves_bias_input(self):
        rng = np.random.default_rng(11)
        model = _Model()
        model.coefs_[0] = rng.normal(0, 0.08, (128, 64))
        model.coefs_[1] = rng.normal(0, 0.08, (64, 64))
        model.intercepts_[0] = np.zeros(64)
        layout = solist_stage_layout(model)
        self.assertTrue(all(stage["input_size"] <= 256 for stage in layout))
        self.assertTrue(all(stage["hidden_size"] == 64 for stage in layout))

    def test_bfloat_prediction_is_finite_and_normalized(self):
        values = staged_bfloat16_predict(_Model(), np.zeros((3, 128), dtype=np.float32))
        self.assertEqual(values.shape, (3, 2))
        self.assertTrue(np.isfinite(values).all())
        self.assertTrue(np.all((0.0 <= values) & (values <= 1.0)))

    def test_header_contains_golden_selftest_vectors(self):
        model = _Model()
        scaler = StandardScaler().fit(np.zeros((2, 128), dtype=np.float32))
        inputs = np.zeros((2, 128), dtype=np.float32)
        outputs = staged_bfloat16_predict(model, inputs)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model.h"
            export_header(path, scaler, model, inputs, outputs)
            content = path.read_text(encoding="ascii")
        self.assertIn("APAN_XY_GOLDEN_CASE_COUNT 2", content)
        self.assertIn("apan_xy_golden_inputs", content)
        self.assertIn("apan_xy_golden_outputs", content)


if __name__ == "__main__":
    unittest.main()
