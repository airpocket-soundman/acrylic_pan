import json
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import PropertyMock, patch
from urllib.request import Request, urlopen

from pc.acrylic_pan_web.server import AcquisitionController, create_server
from pc.acrylic_pan_monitor.protocol import Frame, MessageType, decode_frame
from pc.acrylic_pan_monitor.recorder import make_demo_event


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
        with urlopen(self.base + "/collector.html", timeout=2) as response:
            collector_page = response.read().decode("utf-8")
        self.assertIn("Acrylic Pan Vibration Monitor", collector_page)
        self.assertIn("8エリア ガイド付きデータ採取", collector_page)
        self.assertIn("採取開始", collector_page)
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
        golden_path.write_text(json.dumps({"cases": [{
            "board_case_id": 2,
            "case_id": "class2_sample0",
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
        self.assertEqual(plan["order"], list(range(8)))
        records = [json.loads(line) for line in
                   (session_dir / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([record["class_id"] for record in records],
                         [area for area in range(8) for _ in range(2)])
        self.assertEqual(records[0]["annotations"]["target_area"], 1)
        self.assertEqual(records[-1]["annotations"]["target_area"], 8)

    def test_collection_api_and_stop(self):
        packets = []
        self.controller.link.send = packets.append
        with patch.object(type(self.controller.link), "connected", new_callable=PropertyMock, return_value=True):
            status_code, started = self.post_json("/api/collection/start", {
                "repetitions": 3,
                "output_root": self.temporary.name,
            })
            self.assertEqual(status_code, 200)
            self.assertEqual(started["current_class_id"], 0)
            _, stopped = self.post_json("/api/collection/stop", {})
        self.assertFalse(stopped["active"])
        self.assertEqual(decode_frame(packets[0]).message_type, MessageType.START)
        self.assertEqual(decode_frame(packets[-1]).message_type, MessageType.STOP)


if __name__ == "__main__":
    unittest.main()
