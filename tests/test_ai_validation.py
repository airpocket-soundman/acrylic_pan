import json
from pathlib import Path
import tempfile
import unittest

from pc.acrylic_pan_monitor.ai_validation import compare_ai_result, load_golden_case
from pc.acrylic_pan_monitor.protocol import AiResult


class AiValidationTests(unittest.TestCase):
    def test_load_by_board_case_id_and_compare_all_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "golden.json"
            path.write_text(json.dumps({"cases": [{
                "board_case_id": 3,
                "case_id": "class2_sample0",
                "outputs": [0.0, 0.1, 0.8, 0.2, 0.0, -0.1, 0.1, 0.0],
                "predicted_class": 2,
            }]}), encoding="utf-8")
            golden = load_golden_case(path, 3)
            self.assertIsNotNone(golden)
            comparison = compare_ai_result(
                AiResult(3, 2, (0.0, 0.1, 0.800001, 0.2, 0.0, -0.1, 0.1, 0.0)),
                golden,
            )
        self.assertTrue(comparison["passed"])
        self.assertEqual(comparison["case_name"], "class2_sample0")
        self.assertLess(comparison["max_absolute_error"], 1e-5)

    def test_one_bad_score_fails_even_when_argmax_matches(self):
        golden = {"outputs": [0.0] * 7 + [1.0], "predicted_class": 7}
        comparison = compare_ai_result(AiResult(0, 7, (0.0,) * 7 + (0.8,)), golden)
        self.assertTrue(comparison["class_match"])
        self.assertFalse(comparison["outputs_match"])
        self.assertFalse(comparison["passed"])


if __name__ == "__main__":
    unittest.main()
