from pathlib import Path
import unittest

import numpy as np

from sim.pc_position_grid_runtime import (
    DEFAULT_SESSION_IDS,
    EXTERNAL_EVAL_SESSION_IDS,
    GRID_SESSION_ID,
    GRID_SESSION_IDS,
    extract_grid_features,
    load_position_dataset,
)


class PcPositionGridRuntimeTests(unittest.TestCase):
    def test_rich_feature_contract(self):
        samples = np.arange(512, dtype=np.float64)
        features = extract_grid_features(samples)
        self.assertEqual(features.shape, (714,))
        self.assertTrue(np.isfinite(features).all())

    def test_wrong_live_sample_count_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "512"):
            extract_grid_features(np.zeros(511))

    def test_declared_dataset_includes_new_grid_session(self):
        self.assertIn(GRID_SESSION_ID, DEFAULT_SESSION_IDS)
        self.assertEqual(len(GRID_SESSION_IDS), 4)
        self.assertEqual(len(DEFAULT_SESSION_IDS), 8)
        self.assertEqual(len(EXTERNAL_EVAL_SESSION_IDS), 0)
        self.assertTrue(set(EXTERNAL_EVAL_SESSION_IDS).isdisjoint(DEFAULT_SESSION_IDS))

    def test_local_measured_dataset_contract_when_available(self):
        root = Path("data/raw/sessions")
        if not all((root / session / "session.json").is_file() for session in DEFAULT_SESSION_IDS):
            self.skipTest("local measured sessions are not checked into Git")
        dataset = load_position_dataset(root)
        self.assertEqual(len(dataset.samples), 4265)
        self.assertEqual(int(np.sum(dataset.session_ids == GRID_SESSION_ID)), 480)
        self.assertEqual(int(np.sum(np.isin(dataset.session_ids, GRID_SESSION_IDS))), 1920)
        self.assertEqual(len(np.unique(dataset.xy_mm, axis=0)), 60)


if __name__ == "__main__":
    unittest.main()
