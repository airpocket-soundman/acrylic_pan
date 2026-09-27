import re
import unittest
from pathlib import Path

import numpy as np

from sim.odl_calibration_experiment import (
    CLASS_COUNT,
    calibration_pool,
    draw_calibration,
    initial_p,
    os_elm_update,
)
from sim.odl_calibration_prior import (
    DEFAULT_MODEL_HEADER,
    DEFAULT_PRIOR_HEADER,
    header_beta_bits,
    model_crc32,
)


class OdlCalibrationExperimentTests(unittest.TestCase):
    def test_float64_os_elm_matches_weighted_batch_fit(self):
        rng = np.random.default_rng(3)
        factory_h = rng.uniform(0, 1, (200, 8))
        factory_y = np.eye(4)[rng.integers(0, 4, 200)]
        cal_h = rng.uniform(0, 1, (12, 8))
        cal_labels = rng.integers(0, 4, 12)
        ridge, weight = 2.0, 30.0
        gram = factory_h.T @ factory_h + ridge * np.eye(8)
        beta0 = np.linalg.solve(gram, factory_h.T @ factory_y)

        beta, p = os_elm_update(beta0, initial_p(factory_h, ridge, weight),
                                cal_h, cal_labels, "float64")

        cal_y = np.eye(4)[cal_labels]
        expected = np.linalg.solve(
            gram + weight * cal_h.T @ cal_h,
            factory_h.T @ factory_y + weight * cal_h.T @ cal_y,
        )
        np.testing.assert_allclose(beta, expected, atol=1e-9)
        np.testing.assert_allclose(
            p, weight * np.linalg.inv(gram + weight * cal_h.T @ cal_h), atol=1e-9
        )

    def test_reduced_precisions_stay_finite(self):
        rng = np.random.default_rng(5)
        factory_h = rng.uniform(0, 1, (100, 8))
        p0 = initial_p(factory_h, 10.0, 10.0)
        beta0 = rng.normal(0, 0.1, (8, 4))
        for precision in ("float32", "float32_bf16beta", "bfloat16"):
            beta, p = os_elm_update(beta0, p0, rng.uniform(0, 1, (8, 8)),
                                    rng.integers(0, 4, 8), precision)
            self.assertTrue(np.isfinite(beta).all())
            self.assertTrue(np.isfinite(p).all())
        with self.assertRaises(ValueError):
            os_elm_update(beta0, p0, factory_h[:1], np.array([0]), "float16")

    def test_pool_cycles_points_and_draw_is_guided_rounds(self):
        labels = np.repeat(np.arange(CLASS_COUNT), 12)
        repetitions = np.tile(np.repeat(np.arange(1, 4), 4), CLASS_COUNT)
        points = np.tile(np.array(["a", "b", "c", "d"] * 3), CLASS_COUNT)
        pool = calibration_pool(labels, repetitions, points,
                                np.arange(len(labels)), per_class=4)
        for class_id, indices in pool.items():
            self.assertEqual(set(points[indices]), {"a", "b", "c", "d"})
            self.assertTrue(np.all(repetitions[indices] == 1))
            self.assertTrue(np.all(labels[indices] == class_id))

        chosen = draw_calibration(pool, 2, seed=0)
        self.assertEqual(len(chosen), 2 * CLASS_COUNT)
        np.testing.assert_array_equal(labels[chosen], np.tile(np.arange(CLASS_COUNT), 2))
        self.assertEqual(len(set(chosen.tolist())), len(chosen))
        np.testing.assert_array_equal(chosen, draw_calibration(pool, 2, seed=0))

    def test_firmware_prior_belongs_to_deployed_beta(self):
        prior = Path(DEFAULT_PRIOR_HEADER).read_text(encoding="ascii")
        match = re.search(r"APAN_CALIBRATION_MODEL_CRC32 \(0x([0-9A-F]{8})UL\)", prior)
        self.assertIsNotNone(match)
        beta_bits = header_beta_bits(Path(DEFAULT_MODEL_HEADER))
        self.assertEqual(beta_bits.size, 32 * 12)
        self.assertEqual(int(match.group(1), 16), model_crc32(beta_bits))


if __name__ == "__main__":
    unittest.main()
