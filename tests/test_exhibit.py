import json
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pc.acrylic_pan_monitor.protocol import Frame, MessageType, decode_frame
from pc.acrylic_pan_web.server import AcquisitionController, create_server
from pc.acrylic_pan_web.exhibit import AutoConnector


class ExhibitServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.videos = Path(self.temporary.name) / "videos"
        self.videos.mkdir()
        (self.videos / "demo.mp4").write_bytes(bytes(range(256)) * 4)
        (self.videos / "notes.txt").write_text("not a video")
        self.controller = AcquisitionController(self.temporary.name)
        self.server = create_server("127.0.0.1", 0, self.controller, self.videos)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.controller.close()
        self.thread.join(timeout=1)
        self.temporary.cleanup()

    def fetch(self, path, headers=None):
        with urlopen(Request(self.base + path, headers=headers or {}), timeout=3) as response:
            return response.status, dict(response.headers), response.read()

    def post(self, path, body, headers=None):
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        request = Request(
            self.base + path, data=json.dumps(body).encode(), headers=request_headers,
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())

    def test_the_three_pages_are_served(self):
        for page in (
            "play.html", "odl.html", "video.html", "admin.html", "exhibit.js",
            "exhibit.css", "odl.js", "admin.js", "admin.css",
        ):
            status, _, body = self.fetch("/exhibit/" + page)
            self.assertEqual(status, 200, page)
            self.assertTrue(body)
        self.assertIn(b"kiosk=1", self.fetch("/exhibit/")[2])

    def test_pages_do_not_escape_the_exhibit_folder(self):
        with self.assertRaises(HTTPError) as caught:
            self.fetch("/exhibit/../server.py")
        self.assertEqual(caught.exception.code, 404)

    def test_video_list_only_has_videos(self):
        _, _, body = self.fetch("/api/exhibit/videos")
        self.assertEqual([item["name"] for item in json.loads(body)["videos"]], ["demo.mp4"])

    def test_video_supports_range_requests(self):
        status, headers, body = self.fetch("/exhibit-media/demo.mp4", {"Range": "bytes=10-19"})
        self.assertEqual(status, 206)
        self.assertEqual(headers["Content-Range"], "bytes 10-19/1024")
        self.assertEqual(body, bytes(range(10, 20)))
        status, _, body = self.fetch("/exhibit-media/demo.mp4")
        self.assertEqual((status, len(body)), (200, 1024))

    def test_video_folder_is_the_only_source(self):
        for path in ("/exhibit-media/notes.txt", "/exhibit-media/..%2Fserver.py", "/exhibit-media/missing.mp4"):
            with self.assertRaises(HTTPError) as caught:
                self.fetch(path)
            self.assertEqual(caught.exception.code, 404, path)

    def test_auto_connector_skips_a_serial_port_that_does_not_answer(self):
        class Link:
            connected = False

        class Controller:
            def __init__(self):
                self.link = Link()
                self.attempts = []
                self.profile = None

            def connect(self, port, require_response=False):
                self.attempts.append((port, require_response))
                if port == "/dev/ttyUSB0":
                    raise OSError("not the board")
                self.link.connected = True

            def disconnect(self):
                self.link.connected = False

            def set_panel_profile(self, profile):
                self.profile = profile

        controller = Controller()
        with patch("pc.acrylic_pan_web.exhibit.available_ports",
                   return_value=["/dev/ttyUSB0", "/dev/ttyACM0"]):
            AutoConnector(controller)._try_connect()
        self.assertEqual(controller.attempts, [
            ("/dev/ttyUSB0", True), ("/dev/ttyACM0", True),
        ])
        self.assertTrue(controller.link.connected)
        self.assertEqual(controller.profile, "400x300x5")

    def test_probe_device_matches_hello_and_status_sequences(self):
        packets = []

        def answer(packet):
            request = decode_frame(packet)
            packets.append(request)
            if request.message_type == MessageType.HELLO:
                # An unrelated response must not complete this request.
                self.controller._queue.put(Frame(
                    MessageType.HELLO, request.sequence + 100, b"other-device"
                ))
                self.controller._queue.put(Frame(
                    MessageType.HELLO, request.sequence, b"AcrylicPanCollector"
                ))
            elif request.message_type == MessageType.STATUS:
                payload = struct.pack("<BBHHIB", 3, 0x0F, 512, 64, 25_600, 3)
                self.controller._queue.put(Frame(
                    MessageType.STATUS, request.sequence, payload
                ))

        self.controller.link.send = answer
        with patch.object(type(self.controller.link), "connected",
                          new_callable=PropertyMock, return_value=True):
            result = self.controller.probe_device(timeout=0.2)
        self.assertTrue(result["ok"])
        self.assertEqual(result["hello"]["identity"], "AcrylicPanCollector")
        self.assertEqual(result["firmware"]["sample_rate_hz"], 25_600)
        self.assertEqual(result["firmware"]["mode"], "device_position")
        self.assertEqual([item.message_type for item in packets], [
            MessageType.HELLO, MessageType.STATUS,
        ])
        self.assertFalse(self.controller._control_waiters)

    def test_probe_device_never_writes_while_inference_is_active(self):
        writes = []
        self.controller.link.send = writes.append
        self.controller.inference_active = True
        self.controller.device_mode = "device_position"
        with patch.object(type(self.controller.link), "connected",
                          new_callable=PropertyMock, return_value=True):
            result = self.controller.probe_device(timeout=0.01)
        self.assertEqual(result["active_probe"], "skipped_busy")
        self.assertIsNone(result["ok"])
        self.assertEqual(writes, [])

    def test_probe_device_cleans_waiter_after_write_error(self):
        self.controller.link.send = lambda _packet: (_ for _ in ()).throw(
            OSError("USB removed")
        )
        with patch.object(type(self.controller.link), "connected",
                          new_callable=PropertyMock, return_value=True):
            result = self.controller.probe_device(timeout=0.01)
        self.assertEqual(result["active_probe"], "failed")
        self.assertIn("USB removed", result["error"])
        self.assertFalse(self.controller._control_waiters)

    def test_probe_device_cleans_waiter_after_timeout(self):
        self.controller.link.send = lambda _packet: None
        with patch.object(type(self.controller.link), "connected",
                          new_callable=PropertyMock, return_value=True):
            result = self.controller.probe_device(timeout=0.01)
        self.assertEqual(result["active_probe"], "failed")
        self.assertIn("HELLO response timed out", result["error"])
        self.assertFalse(self.controller._control_waiters)

    def test_admin_diagnostics_requires_nonce_and_returns_report(self):
        _, _, body = self.fetch("/api/admin/session")
        nonce = json.loads(body)["nonce"]
        with self.assertRaises(HTTPError) as caught:
            self.post("/api/admin/diagnostics", {})
        self.assertEqual(caught.exception.code, 403)

        expected = {"overall": "pass", "checks": [], "system": {}}
        with patch("pc.acrylic_pan_web.server.run_diagnostics", return_value=expected):
            status, report = self.post("/api/admin/diagnostics", {"nonce": nonce})
        self.assertEqual(status, 200)
        self.assertEqual(report, expected)


if __name__ == "__main__":
    unittest.main()
