import json
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen

import numpy as np

from pc.acrylic_pan_monitor.protocol import (
    AI_RESULT_PAYLOAD,
    POSITION_RESULT_PAYLOAD,
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
            "positionHeatmap", "positionProbabilityCells", "positionMarker", "coordinateReadout",
            "areaProbabilities", "metricRegion", "metricExpectedCoordinate", "positionStart", "positionDemo",
            "positionSource", "metricDeviceTiming",
        ):
            self.assertIn(f'id="{element}"', page)
        self.assertIn("drawHeatmap", script)
        self.assertIn("class_probabilities", script)
        self.assertIn("/api/ai/wait", script)
        self.assertIn("probability_map", script)
        self.assertIn("credible_90_indices", script)
        self.assertNotIn("function gaussian", script)
        self.assertIn(".position-marker", css)
        self.assertIn("image-rendering:pixelated", css)
        self.assertIn("rasterWidth = 40", script)
        self.assertIn("imageSmoothingEnabled = false", script)
        self.assertIn("Math.round(normalized * 9) / 9", script)
        self.assertIn("if (canvas.height !== canvasHeight)", script)
        self.assertIn("条件付き確率", page)

    def test_all_operating_pages_link_to_position_tab(self):
        for path in ("/", "/collector.html", "/position.html", "/instrument.html", "/instrument-probability.html"):
            with self.subTest(path=path):
                self.assertIn('href="/position.html"', self.read_text(path))

    def test_probability_instrument_combines_heatmap_camera_and_audio(self):
        page = self.read_text("/instrument-probability.html")
        script = self.read_text("/instrument-probability.js")
        css = self.read_text("/instrument-probability.css")
        for element in (
            "positionHeatmap", "areaSelectionGrid", "pseudoPositionMarker",
            "pseudoCoordinateReadout", "areaProbabilities", "usbCamera",
            "cameraDevice", "performanceStart", "positionSource", "instrumentSelect",
            "lastNote", "areaReadout", "peakReadout", "displayMode",
            "cameraHeatmapOverlay", "calibrationStart", "calibrationClear",
            "heatmapToggle", "panelAreaToggle", "songSelect", "songProgress", "songReset",
            "noteLength", "noteLengthValue", "languageToggle", "guidePlay",
        ):
            self.assertIn(f'id="{element}"', page)
        self.assertIn("function areaProbabilities", script)
        self.assertIn("function pseudoCoordinate", script)
        self.assertIn("position.expected_x_mm", script)
        self.assertIn("area-selection-cell", script)
        self.assertNotIn('id="positionMarker"', page)
        self.assertIn("MIN_HEATMAP_DISPLAY_MS = 450", script)
        self.assertIn("scheduleLivePosition(result.position)", script)
        self.assertIn("function quadPoint", script)
        self.assertIn("function beginCalibration", script)
        self.assertIn("['左上','右上','右下','左下']", script)
        self.assertIn("function overlayPointerMove", script)
        self.assertIn("saveCalibration();", script)
        self.assertIn("function toggleHeatmap", script)
        self.assertIn("function togglePanelArea", script)
        self.assertIn("PANEL_AREA_VISIBLE_KEY", script)
        self.assertIn("if(panelAreaVisible&&calibrationPoints.length===4)", script)
        self.assertIn("function configureSong", script)
        self.assertIn("function advanceSong", script)
        self.assertIn("function currentSongArea(offset=0)", script)
        self.assertIn("currentSongArea(1)", script)
        self.assertIn("function toggleGuide", script)
        self.assertIn("function stopGuide", script)
        self.assertIn("source==='live'&&guidePlaying", script)
        self.assertIn("playArea(currentSongArea(),1,'guide',timing.beats)", script)
        self.assertIn("const SONG_GUIDE", script)
        self.assertIn("function guideTiming", script)
        self.assertIn("function validateSongGuides", script)
        self.assertNotIn("index%rhythm.length", script)
        self.assertIn("60000/score.bpm*beats", script)
        self.assertIn("fur_elise:{bpm:72,bars:16,expectedBeats:25", script)
        self.assertIn("score.bars!==16", script)
        self.assertIn("new Set(song.notes).size>12", script)
        self.assertIn("Math.abs(beats-score.expectedBeats)>.001", script)
        self.assertNotIn("480*noteLengthScale", script)
        self.assertIn("function applyLanguage", script)
        self.assertIn("LANGUAGE_KEY", script)
        self.assertIn("Probability Instrument", script)
        self.assertIn("エリーゼのために", script)
        self.assertNotIn("さくらさくら", script)
        self.assertNotIn("sakura:", script)
        self.assertNotIn("ハッピーバースデー", script)
        self.assertNotIn("ジングルベル", script)
        self.assertNotIn("きらきら星", script)
        self.assertNotIn("メリーさんのひつじ", script)
        self.assertNotIn("ロンドン橋", script)
        self.assertNotIn("カノン", script)
        self.assertIn("korobeiniki", script)
        self.assertIn("アメイジング・グレイス", script)
        self.assertIn("オールド・ラング・サイン", script)
        self.assertIn("フレール・ジャック", script)
        self.assertNotIn("ベートーヴェン「運命」冒頭動機", script)
        self.assertNotIn("モーツァルト「トルコ行進曲」", script)
        self.assertIn("ペツォールト「メヌエット ト長調」", script)
        self.assertIn("ブラームスの子守歌", script)
        self.assertNotIn("8bitプラットフォーマー", page)
        self.assertNotIn("レトロRPG序曲", page)
        self.assertIn("*noteLengthScale", script)
        self.assertIn("NOTE_LENGTH_KEY", script)
        self.assertNotIn("保存済みの4点位置合わせを使用します", script)
        self.assertIn("if(heatmapVisible&&support.length", script)
        self.assertIn("camera-overlay-mode", css)
        self.assertIn("background:transparent", css)
        self.assertIn("#cameraHeatmapOverlay.calibrated", css)
        self.assertIn("grid-template-rows:repeat(12", css)
        self.assertIn("font:800 18px/1 Consolas", css)
        self.assertIn("min-height:44px", css)
        self.assertIn(".area-selection-cell.next-note", css)
        self.assertIn(".area-selection-cell.second-next-note", css)
        self.assertIn(".area-probability.second-next-note", css)
        self.assertIn("probabilities.indexOf(Math.max(...probabilities))", script)
        self.assertIn("playArea(area,areaProbability)", script)
        self.assertIn("/api/ai/wait", script)
        self.assertIn("device_position", script)
        self.assertIn("getUserMedia", script)
        self.assertIn(".probability-layout", css)

    def test_all_operating_pages_link_to_probability_instrument(self):
        for path in ("/", "/collector.html", "/position.html", "/instrument.html", "/instrument-probability.html"):
            with self.subTest(path=path):
                self.assertIn('href="/instrument-probability.html"', self.read_text(path))

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

    def test_density_bundle_returns_normalized_60_class_probability_map(self):
        class Scaler:
            def transform(self, values):
                return values

        class Regressor:
            def predict(self, values):
                return np.asarray([[0.5, 0.5]])

        class DensityModel:
            def predict_proba(self, values):
                probability = np.full((1, 60), 0.1 / 59)
                probability[0, 17] = 0.9
                return probability

        support = np.asarray([
            (x, y) for x in range(25, 400, 50) for y in range(25, 300, 50)
        ] + [
            (x, y) for x in range(50, 400, 100) for y in range(50, 300, 100)
        ], dtype=np.float64)
        bundle = {
            "contract": {
                "sample_rate_hz": 25_600,
                "sample_count": 512,
                "trigger_index": 64,
                "feature_mode": "pc_rich_20ms_v1",
            },
            "scaler": Scaler(),
            "models": [Regressor(), Regressor(), Regressor()],
            "density_models": [DensityModel(), DensityModel(), DensityModel()],
            "density_support_xy_mm": support,
            "density_temperature": 1.0,
            "density_validation": {"top1_cell_accuracy": 0.8},
            "scope": "test density",
        }
        panel = {
            "id": "400x300x5", "width_mm": 400.0, "height_mm": 300.0,
            "columns": 4, "rows": 3, "class_count": 12,
        }
        from pc.acrylic_pan_monitor.protocol import EventData
        event = EventData(25_600, 64, 1000, tuple([0] * 512))
        with patch("pc.acrylic_pan_web.position_model.load_bundle", return_value=bundle):
            result = PositionEstimator().predict(event, [0.0] * 12, 0, panel)
        probability_map = result["probability_map"]
        self.assertEqual(len(probability_map["support_xy_mm"]), 60)
        self.assertAlmostEqual(sum(probability_map["probabilities"]), 1.0)
        self.assertEqual(int(np.argmax(probability_map["probabilities"])), 17)
        np.testing.assert_allclose(
            [result["x_mm"], result["y_mm"]], support[17], rtol=0, atol=1e-9
        )
        np.testing.assert_allclose(
            [result["expected_x_mm"], result["expected_y_mm"]],
            np.asarray(probability_map["probabilities"]) @ support,
        )
        self.assertGreater(len(probability_map["credible_90_indices"]), 0)
        self.assertEqual(result["method"], "pc_mlp_60class_probability_map")

    def test_device_position_result_is_displayed_without_pc_model_inference(self):
        self.controller.set_panel_profile("400x300x5")
        self.controller.inference_active = True
        probabilities = [0.0] * 60
        probabilities[17] = 0.8
        probabilities[18] = 0.2
        payload = POSITION_RESULT_PAYLOAD.pack(
            17, 60, 1, 2200, 700, 2900, *probabilities
        )
        with patch.object(self.controller, "send_command") as send_command:
            self.controller._queue.put(Frame(MessageType.POSITION_RESULT, 124, payload))
            deadline = time.monotonic() + 3
            while ((self.controller.latest_ai is None or not send_command.called)
                   and time.monotonic() < deadline):
                time.sleep(0.01)
            send_command.assert_called_once_with("start")
        position = self.controller.latest_ai["position"]
        self.assertEqual(position["method"], "device_solist_60class_probability_map")
        self.assertEqual(position["inference_source"], "device")
        self.assertAlmostEqual(sum(position["probability_map"]["probabilities"]), 1.0)
        self.assertEqual(position["device_timing_us"]["solist_inference"], 2200)
        self.assertEqual(position["device_timing_us"]["softmax"], 700)
        self.assertEqual(position["device_timing_us"]["total"], 2900)

    def test_status_response_stays_valid_json_for_nonfinite_device_data(self):
        self.controller.latest_ai = {"outputs": [1.0, float("inf"), float("nan")]}
        with urlopen(f"{self.base}/api/status") as response:
            status = json.load(response)
        self.assertEqual(status["latest_ai"]["outputs"], [1.0, None, None])

    def test_invalid_device_position_result_is_still_rearmed(self):
        self.controller.set_panel_profile("400x300x5")
        self.controller.inference_active = True
        probabilities = [0.0] * 60
        probabilities[0] = float("nan")
        payload = POSITION_RESULT_PAYLOAD.pack(0, 60, 1, 1, 1, 2, *probabilities)
        with patch.object(self.controller, "send_command") as send_command:
            self.controller._queue.put(Frame(MessageType.POSITION_RESULT, 125, payload))
            deadline = time.monotonic() + 3
            while (not send_command.called) and time.monotonic() < deadline:
                time.sleep(0.01)
            send_command.assert_called_once_with("start")
        self.assertIn("invalid probability", self.controller.last_error)


if __name__ == "__main__":
    unittest.main()
