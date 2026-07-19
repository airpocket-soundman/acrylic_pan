import json
import struct
import tempfile
import threading
import time
import unittest
from urllib.request import urlopen

import numpy as np

from pc.acrylic_pan_monitor.protocol import (
    AI_RESULT_PAYLOAD,
    EVENT_HEADER,
    Frame,
    MessageType,
)
from pc.acrylic_pan_web.position_model import PositionEstimator, class_probabilities
from pc.acrylic_pan_web.server import AcquisitionController, create_server


class PositionWebTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.controller = AcquisitionController(self.temporary.name)
        self.server = create_server("127.0.0.1", 0, self.controller)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.controller.close()
        self.thread.join(timeout=1)
        self.temporary.cleanup()

    def read_text(self, path):
        with urlopen(self.base + path, timeout=2) as response:
            self.assertEqual(response.status, 200)
            return response.read().decode("utf-8")

    def test_position_page_exposes_xy_uncertainty_heatmap(self):
        page = self.read_text("/position.html")
        script = self.read_text("/position.js")
        css = self.read_text("/position.css")
        for element in (
            "positionHeatmap", "positionMarker", "coordinateReadout",
            "areaProbabilities", "metricRegion", "positionStart", "positionDemo",
        ):
            self.assertIn(f'id="{element}"', page)
        self.assertIn("drawHeatmap", script)
        self.assertIn("class_probabilities", script)
        self.assertIn("/api/ai/latest", script)
        self.assertIn("gaussian", script)
        self.assertIn("rho_xy", script)
        self.assertIn("confidence_ellipse_90", script)
        self.assertIn(".position-marker", css)
        self.assertIn("8中心点", page)

    def test_all_operating_pages_link_to_position_tab(self):
        for path in ("/", "/collector.html", "/position.html", "/instrument.html"):
            with self.subTest(path=path):
                self.assertIn('href="/position.html"', self.read_text(path))

    def test_class_scores_become_a_normalized_distribution(self):
        probability = class_probabilities((0.0, 0.1, 0.9, 0.2, 0.0, 0.0, 0.0, 0.0))
        self.assertAlmostEqual(float(probability.sum()), 1.0)
        self.assertEqual(int(np.argmax(probability)), 2)
        self.assertTrue(np.all(probability > 0.0))

    def test_missing_model_falls_back_to_area_probability(self):
        estimator = PositionEstimator(self.temporary.name + "/missing.joblib")
        from pc.acrylic_pan_monitor.protocol import EventData
        event = EventData(25_600, 64, 1000, tuple([0] * 512))
        result = estimator.predict(event, [0.0, 0.0, 0.0, 0.9, 0.0, 0.0, 0.0, 0.0], 3)
        self.assertFalse(result["model_available"])
        self.assertEqual((result["x_mm"], result["y_mm"]), (350.0, 50.0))
        self.assertEqual(len(result["class_probabilities"]), 8)
        self.assertEqual(result["ensemble_positions_mm"], [])
        self.assertEqual(result["covariance_mm2"], [])
        self.assertEqual(result["confidence_level"], 0.0)

    def test_live_inference_event_gets_pc_position_metadata(self):
        sample_index = np.arange(512)
        samples = np.rint(6000 * np.sin(2 * np.pi * 900 * sample_index / 25_600)).astype(np.int16)
        outputs = [0.02, 0.05, 0.82, 0.08, 0.01, 0.01, 0.005, 0.005]
        payload = EVENT_HEADER.pack(25_600, 512, 64, int(np.max(np.abs(samples))), 0)
        payload += AI_RESULT_PAYLOAD.pack(0xFF, 2, 0, *outputs)
        payload += struct.pack("<512h", *samples)
        self.controller._queue.put(Frame(MessageType.INFERENCE_EVENT, 123, payload))
        deadline = time.monotonic() + 3
        while (self.controller.latest_ai is None or "position" not in self.controller.latest_ai) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(self.controller.latest_ai)
        position = self.controller.latest_ai["position"]
        self.assertTrue(0.0 <= position["x_mm"] <= 400.0)
        self.assertTrue(0.0 <= position["y_mm"] <= 200.0)
        self.assertAlmostEqual(sum(position["class_probabilities"]), 1.0)
        self.assertEqual(len(position["ensemble_positions_mm"]), 3)
        self.assertEqual(np.asarray(position["covariance_mm2"]).shape, (2, 2))
        self.assertGreater(position["sigma_x_mm"], 0.0)
        self.assertGreater(position["sigma_y_mm"], 0.0)
        self.assertAlmostEqual(position["confidence_level"], 0.90)
        self.assertGreater(position["confidence_ellipse_90"]["semi_major_mm"], 0.0)
        self.assertEqual(position["method"], "pc_mlp_xy_calibrated_gaussian")


if __name__ == "__main__":
    unittest.main()
