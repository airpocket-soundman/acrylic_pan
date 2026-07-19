import unittest

from sim.pc_xy_regression import parameter_count


class PcXYRegressionTests(unittest.TestCase):
    def test_parameter_count_includes_weights_and_biases(self):
        self.assertEqual(parameter_count(3, (4, 2), 1), 29)

    def test_pc_direct_model_is_materially_larger_than_solist_trainable_beta(self):
        self.assertGreater(parameter_count(128, (256, 128, 64), 2), 64_000)


if __name__ == "__main__":
    unittest.main()
