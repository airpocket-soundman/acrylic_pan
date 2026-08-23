import unittest

import numpy as np

from sim.compare_area_classification import _area_ids
from sim.pc_xy_regression import parameter_count


class PcXYRegressionTests(unittest.TestCase):
    def test_400_by_300_area_ids_include_the_third_row(self):
        coordinates = np.asarray([
            [0.0, 0.0], [399.9, 99.9], [0.0, 100.0], [399.9, 299.9]
        ])
        np.testing.assert_array_equal(_area_ids(coordinates), [0, 3, 4, 11])

    def test_parameter_count_includes_weights_and_biases(self):
        self.assertEqual(parameter_count(3, (4, 2), 1), 29)

    def test_pc_direct_model_is_materially_larger_than_solist_trainable_beta(self):
        self.assertGreater(parameter_count(128, (256, 128, 64), 2), 64_000)


if __name__ == "__main__":
    unittest.main()
