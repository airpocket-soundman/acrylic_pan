import unittest

import numpy as np

from sim.geometry import (
    coordinate_to_cell,
    coordinate_to_note,
    denormalize,
    distance_error_mm,
    normalize,
)
from sim.solid_fem import hex8_stiffness


class GeometryTests(unittest.TestCase):
    def test_normalize_and_denormalize(self):
        self.assertEqual(normalize(200, 100), (0.5, 0.5))
        self.assertEqual(denormalize(0.5, 0.5), (200.0, 100.0))

    def test_model_output_is_clipped_to_panel(self):
        self.assertEqual(denormalize(-0.1, 1.2), (0.0, 200.0))

    def test_cell_conversion(self):
        self.assertEqual(coordinate_to_cell(0, 0), (0, 0))
        self.assertEqual(coordinate_to_cell(199.9, 100), (1, 1))
        self.assertEqual(coordinate_to_cell(400, 200), (3, 1))

    def test_note_mapping(self):
        self.assertEqual(coordinate_to_note(10, 10), "C4")
        self.assertEqual(coordinate_to_note(399, 199), "E5")

    def test_distance(self):
        self.assertEqual(distance_error_mm((0, 0), (30, 40)), 50.0)

    def test_hex8_element_stiffness(self):
        stiffness = hex8_stiffness(0.0125, 0.0125, 0.001)
        self.assertEqual(stiffness.shape, (24, 24))
        self.assertTrue(np.allclose(stiffness, stiffness.T, rtol=1e-10, atol=1e-5))
        values = np.linalg.eigvalsh(stiffness)
        self.assertEqual(np.count_nonzero(values < values.max() * 1e-9), 6)


if __name__ == "__main__":
    unittest.main()
