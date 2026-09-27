import unittest

import numpy as np

from sim.mpu9250_position_experiment import (
    convert_to_mpu9250,
    extract_rich_features,
)


class Mpu9250PositionExperimentTests(unittest.TestCase):
    def test_conversion_rate_range_and_clipping(self):
        source = np.zeros((2, 2048), dtype=np.float32)
        source[1, 900:1100] = 32 * 1024
        converted, clipping = convert_to_mpu9250(source)
        self.assertEqual(converted.shape, (2, 320))
        self.assertEqual(converted.dtype, np.int16)
        self.assertGreaterEqual(clipping["clipped_event_count"], 1)

    def test_feature_contracts_for_20_and_80_ms(self):
        self.assertEqual(extract_rich_features(np.zeros(80)).shape, (120,))
        self.assertEqual(extract_rich_features(np.zeros(320)).shape, (480,))


if __name__ == "__main__":
    unittest.main()
