import json
import math
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import PropertyMock, patch
from urllib.request import Request, urlopen

from pc.acrylic_pan_web.server import (
    AcquisitionController,
    build_collection_targets,
    create_server,
)
from pc.acrylic_pan_monitor.protocol import Frame, MessageType, decode_frame
from pc.acrylic_pan_monitor.recorder import make_demo_event
from sim.solist_dataset import validate_guided_collection


class WebApiTests(unittest.TestCase):
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

    def get_json(self, path):
        with urlopen(self.base + path, timeout=2) as response:
            return response.status, json.loads(response.read())

    def post_json(self, path, body):
        request = Request(
            self.base + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())

    def test_status_ports_and_static_page(self):
        status_code, status = self.get_json("/api/status")
        self.assertEqual(status_code, 200)
        self.assertFalse(status["connected"])
        self.assertIn("decoder_errors", status["stats"])
        self.assertFalse(status["collection"]["active"])
        _, ports = self.get_json("/api/ports")
        self.assertIsInstance(ports["ports"], list)
        with urlopen(self.base + "/", timeout=2) as response:
            page = response.read().decode("utf-8")
        self.assertIn("Acrylic Pan AI Demo", page)
        self.assertIn("AI推論を開始", page)
        self.assertIn("ダミー入力波形（正規化値）", page)
        self.assertIn("実センサ波形ではありません", page)
        with urlopen(self.base + "/collector.html", timeout=2) as response:
            collector_page = response.read().decode("utf-8")
        self.assertIn("Acrylic Pan Vibration Monitor", collector_page)
        self.assertIn("8エリア ガイド付きデータ採取", collector_page)
        self.assertIn("採取開始", collector_page)
        self.assertIn("collectionPattern", collector_page)
        self.assertIn("collectionMarker", collector_page)
        with urlopen(self.base + "/collector.css", timeout=2) as response:
            collector_css = response.read().decode("utf-8")
        self.assertIn(".collection-marker", collector_css)
        self.assertNotIn("AI推論を開始", collector_page)
        self.assertIn("FFT", page)

    def test_session_demo_latest_and_recording(self):
        _, session = self.post_json("/api/session", {"output_root": self.temporary.name, "class_id": 5})
        self.assertTrue(session["session_dir"].startswith(self.temporary.name))
        _, demo = self.post_json("/api/demo", {})
        self.assertEqual(len(demo["samples"]), 512)
        self.assertEqual(len(demo["frequency_hz"]), 257)
        _, latest = self.get_json("/api/events/latest")
        self.assertEqual(latest["sequence"], demo["sequence"])
        _, status = self.get_json("/api/status")
        self.assertEqual(status["stats"]["events_saved"], 1)

    def test_capture_command_uses_framed_uart_api(self):
        packets = []
        self.controller.link.send = packets.append
        with patch.object(type(self.controller.link), "connected", new_callable=PropertyMock, return_value=True):
            result = self.controller.send_command("capture")
        self.assertEqual(result["state"], "sent")
        self.assertEqual(decode_frame(packets[0]).message_type, MessageType.CAPTURE)

    def test_ai_selftest_command_and_golden_comparison(self):
        packets = []
        self.controller.link.send = packets.append
        with patch.object(type(self.controller.link), "connected", new_callable=PropertyMock, return_value=True):
            result = self.controller.send_ai_selftest(2)
        request = decode_frame(packets[0])
        self.assertEqual(result["case_id"], 2)
        self.assertEqual(request.message_type, MessageType.AI_SELFTEST)
        self.assertEqual(request.payload, b"\x02")

        packets.clear()
        with patch.object(type(self.controller.link), "connected", new_callable=PropertyMock, return_value=True):
            status_code, api_result = self.post_json("/api/ai/selftest", {"case_id": 2})
        self.assertEqual(status_code, 200)
        self.assertEqual(api_result["command"], "ai_selftest")
        request = decode_frame(packets[0])

        golden_path = Path(self.temporary.name) / "golden.json"
        outputs = [0.0, 0.1, 0.8, 0.2, 0.0, -0.1, 0.1, 0.0]
        model_input = [
            0.25 + math.sin(2.0 * math.pi * 5 * index / 128)
            for index in range(128)
        ]
        golden_path.write_text(json.dumps({"cases": [{
            "board_case_id": 2,
            "case_id": "class2_sample0",
            "input": model_input,
            "outputs": outputs,
            "predicted_class": 2,
        }]}), encoding="utf-8")
        self.controller.golden_path = golden_path
        self.controller._queue.put(Frame(
            MessageType.AI_RESULT,
            request.sequence,
            struct.pack("<BBH8f", 2, 2, 0, *outputs),
        ))
        deadline = time.monotonic() + 1
        while self.controller.latest_ai is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(self.controller.latest_ai)
        self.assertTrue(self.controller.latest_ai["comparison"]["passed"])
        plot = self.controller.latest_ai["input_plot"]
        self.assertEqual(plot["source"], "dummy_model_input")
        self.assertEqual(plot["case_id"], 2)
        self.assertEqual(plot["sample_rate_hz"], 25_600)
        self.assertEqual(plot["sample_units"], "normalized_model_input")
        self.assertFalse(plot["is_physical_sensor_data"])
        self.assertEqual(plot["samples"], model_input)
        self.assertEqual(len(plot["time_ms"]), 128)
        self.assertEqual(len(plot["frequency_hz"]), 65)
        self.assertEqual(len(plot["magnitude_db"]), 65)
        peak_index = max(range(1, 65), key=lambda index: plot["magnitude_db"][index])
        self.assertAlmostEqual(plot["frequency_hz"][peak_index], 1000.0)

    def test_guided_collection_labels_all_areas_and_rearms(self):
        packets = []
        self.controller.link.send = packets.append
        with patch.object(type(self.controller.link), "connected", new_callable=PropertyMock, return_value=True):
            started = self.controller.start_collection(2, self.temporary.name)
            self.assertTrue(started["active"])
            for sequence in range(1, 17):
                self.controller._process_event(make_demo_event(sequence), "serial", True)

        collection = self.controller.collection_status()
        self.assertFalse(collection["active"])
        self.assertTrue(collection["finished"])
        self.assertEqual(collection["completed_samples"], 16)
        self.assertEqual(collection["per_class_counts"], [2] * 8)
        self.assertEqual(len(packets), 16)  # initial START plus one re-arm except after final event
        self.assertTrue(all(decode_frame(packet).message_type == MessageType.START for packet in packets))

        assert self.controller.recorder is not None
        assert self.controller.recorder.session_dir is not None
        session_dir = self.controller.recorder.session_dir
        session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
        plan = session["user_metadata"]["collection_plan"]
        self.assertEqual(plan["repetitions"], 2)
        self.assertEqual(plan["position_pattern"], "center")
        self.assertEqual(plan["points_per_class"], 1)
        self.assertEqual(plan["order"], list(range(8)))
        records = [json.loads(line) for line in
                   (session_dir / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([record["class_id"] for record in records],
                         [area for area in range(8) for _ in range(2)])
        self.assertEqual(records[0]["annotations"]["target_area"], 1)
        self.assertEqual(records[-1]["annotations"]["target_area"], 8)
        first = records[0]["annotations"]
        self.assertEqual(first["target_point_id"], 0)
        self.assertEqual(first["target_point_name"], "center")
        self.assertEqual((first["target_x_mm"], first["target_y_mm"]), (50.0, 50.0))
        self.assertEqual((first["offset_x_mm"], first["offset_y_mm"]), (0.0, 0.0))
        self.assertEqual(first["repetition"], 1)

    def test_collection_position_patterns_have_exact_panel_coordinates(self):
        five = build_collection_targets("five")
        self.assertEqual(len(five), 40)
        self.assertEqual(
            [(target.point_name, target.x_mm, target.y_mm) for target in five[:5]],
            [
                ("center", 50.0, 50.0),
                ("left", 25.0, 50.0),
                ("right", 75.0, 50.0),
                ("up", 50.0, 25.0),
                ("down", 50.0, 75.0),
            ],
        )
        nine = build_collection_targets("nine")
        self.assertEqual(len(nine), 72)
        self.assertEqual(nine[-1].point_name, "down_right")
        self.assertEqual((nine[-1].x_mm, nine[-1].y_mm), (375.0, 175.0))
        with self.assertRaisesRegex(ValueError, "center, five, or nine"):
            build_collection_targets("invalid")

    def test_five_point_collection_is_training_loader_compatible(self):
        self.controller.link.send = lambda packet: None
        with patch.object(type(self.controller.link), "connected", new_callable=PropertyMock, return_value=True):
            self.controller.start_collection(1, self.temporary.name, "five")
            for sequence in range(1, 41):
                self.controller._process_event(make_demo_event(sequence), "serial", True)
        assert self.controller.recorder is not None
        assert self.controller.recorder.session_dir is not None
        summaries = validate_guided_collection(
            self.controller.recorder.session_dir, point_count=5, repetitions=1
        )
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].event_count, 40)

    def test_collection_api_and_stop(self):
        packets = []
        self.controller.link.send = packets.append
        with patch.object(type(self.controller.link), "connected", new_callable=PropertyMock, return_value=True):
            status_code, started = self.post_json("/api/collection/start", {
                "repetitions": 3,
                "output_root": self.temporary.name,
                "position_pattern": "five",
            })
            self.assertEqual(status_code, 200)
            self.assertEqual(started["current_class_id"], 0)
            self.assertEqual(started["current_point_name"], "center")
            self.assertEqual(started["position_pattern"], "five")
            self.assertEqual(started["total_samples"], 120)
            self.assertEqual(len(started["per_position_counts"]), 40)
            self.controller._process_event(make_demo_event(101), "serial", True)
            progressed = self.controller.collection_status()
            self.assertEqual(progressed["current_point_name"], "center")
            self.assertEqual(progressed["current_repetition"], 2)
            self.assertEqual(progressed["per_position_counts"][0]["count"], 1)
            _, stopped = self.post_json("/api/collection/stop", {})
        self.assertFalse(stopped["active"])
        self.assertEqual(decode_frame(packets[0]).message_type, MessageType.START)
        self.assertEqual(decode_frame(packets[-1]).message_type, MessageType.STOP)


if __name__ == "__main__":
    unittest.main()
