import json
import tempfile
import threading
import time
import unittest
from unittest.mock import PropertyMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pc.acrylic_pan_monitor.protocol import (
    CALIBRATION_ACK_PAYLOAD,
    CALIBRATION_RESULT_PAYLOAD,
    CalibrationOp,
    Frame,
    MessageType,
    decode_frame,
)
from pc.acrylic_pan_web.server import AcquisitionController, create_server


class FakeBoard:
    """Answers the web server's serial requests the way the firmware does."""

    def __init__(self, controller):
        self.controller = controller
        self.source = 0
        self.saved_valid = 0
        self.saved_count = 0
        self.work_count = 0
        self.predicted = None

    def send(self, packet):
        frame = decode_frame(packet)
        queue = self.controller._queue
        if frame.message_type == MessageType.SET_MODE:
            queue.put(Frame(MessageType.ACK, frame.sequence, frame.payload))
            return
        operation, argument = frame.payload[0], frame.payload[1]
        if operation == CalibrationOp.BEGIN:
            self.source, self.work_count = 2, 0
        elif operation == CalibrationOp.COMMIT:
            self.source, self.saved_valid, self.saved_count = 1, 1, self.work_count
        elif operation == CalibrationOp.DISCARD:
            self.source = 1 if self.saved_valid else 0
        elif operation == CalibrationOp.FACTORY_RESET:
            self.source, self.saved_valid, self.saved_count = 0, 0, 0
        queue.put(Frame(MessageType.ACK, frame.sequence, CALIBRATION_ACK_PAYLOAD.pack(
            MessageType.CALIBRATION, operation, self.source, self.saved_valid,
            self.saved_count, self.work_count)))
        if operation == CalibrationOp.ARM:
            trained = argument != 0xFF
            self.work_count += int(trained)
            predicted = argument if trained else 3
            outputs = [0.0] * 12
            outputs[predicted] = 1.0
            queue.put(Frame(MessageType.CALIBRATION_RESULT, 99, CALIBRATION_RESULT_PAYLOAD.pack(
                argument, predicted, 1 if trained else 0, self.source, self.work_count, 0,
                1500, 40000, *outputs)))


class WebCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.controller = AcquisitionController(self.temporary.name)
        self.board = FakeBoard(self.controller)
        self.controller.link.send = self.board.send
        self.connected = patch.object(type(self.controller.link), "connected",
                                      new_callable=PropertyMock, return_value=True)
        self.connected.start()
        self.server = create_server("127.0.0.1", 0, self.controller)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.connected.stop()
        self.server.shutdown()
        self.server.server_close()
        self.controller.close()
        self.thread.join(timeout=1)
        self.temporary.cleanup()

    def get_json(self, path):
        with urlopen(self.base + path, timeout=3) as response:
            return json.loads(response.read())

    def post_json(self, path, body):
        request = Request(self.base + path, data=json.dumps(body).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=3) as response:
            return json.loads(response.read())

    def wait_for_result(self, count):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = self.get_json("/api/calibration/state")
            if state["result_count"] >= count:
                return state
            time.sleep(0.02)
        self.fail("calibration result did not arrive")

    def test_enter_reports_the_factory_model(self):
        status = self.post_json("/api/calibration/enter", {})
        self.assertEqual(status["source"], "factory")
        self.assertFalse(status["saved_valid"])
        self.assertEqual(self.get_json("/api/calibration/state")["device_mode"], "calibration")

    def test_learn_and_commit_makes_the_saved_model_active(self):
        self.post_json("/api/calibration/enter", {})
        self.assertEqual(self.post_json("/api/calibration/begin", {})["source"], "working")
        for area in range(12):
            before = self.get_json("/api/calibration/state")["result_count"]
            self.post_json("/api/calibration/arm", {"target": area})
            state = self.wait_for_result(before + 1)
            self.assertEqual(state["result_count"], before + 1)
            self.assertEqual(state["result"]["target"], area)
            self.assertTrue(state["result"]["trained"])
        status = self.post_json("/api/calibration/commit", {})
        self.assertEqual((status["source"], status["saved_count"]), ("saved", 12))
        self.assertEqual(self.post_json("/api/calibration/factory_reset", {})["source"], "factory")

    def test_check_only_hit_does_not_train(self):
        self.post_json("/api/calibration/enter", {})
        self.post_json("/api/calibration/arm", {"target": None})
        result = self.wait_for_result(1)["result"]
        self.assertIsNone(result["target"])
        self.assertFalse(result["trained"])

    def test_operations_need_calibration_mode(self):
        with self.assertRaises(HTTPError) as caught:
            self.post_json("/api/calibration/begin", {})
        self.assertEqual(caught.exception.code, 400)

    def test_arm_rejects_unknown_area(self):
        self.post_json("/api/calibration/enter", {})
        with self.assertRaises(HTTPError) as caught:
            self.post_json("/api/calibration/arm", {"target": 12})
        self.assertEqual(caught.exception.code, 400)

    def test_note_audio_is_served_as_wav_and_cached(self):
        url = "/api/audio/note.wav?note=C4&area=0&instrument=piano"
        with urlopen(self.base + url, timeout=10) as response:
            body = response.read()
            self.assertEqual(response.headers["Content-Type"], "audio/wav")
            self.assertEqual(response.headers["X-Audio-Cache"], "miss")
        self.assertEqual(body[:4], b"RIFF")
        with urlopen(self.base + url, timeout=10) as response:
            self.assertEqual(response.headers["X-Audio-Cache"], "hit")


if __name__ == "__main__":
    unittest.main()
