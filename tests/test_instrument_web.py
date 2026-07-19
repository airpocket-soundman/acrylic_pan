import json
import re
import tempfile
import threading
import unittest
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from pc.acrylic_pan_web.server import AcquisitionController, create_server


class InstrumentWebTests(unittest.TestCase):
    """Contract tests for the browser-based electronic instrument view."""

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

    def get_json(self, path):
        with urlopen(self.base + path, timeout=2) as response:
            return response.status, json.loads(response.read())

    def post_json(self, path, body):
        request = Request(
            self.base + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())

    def test_instrument_page_exposes_eight_area_performance_controls(self):
        page = self.read_text("/instrument.html")

        self.assertEqual(page.count('data-class="'), 8)
        self.assertIn('class="instrument-main"', page)
        self.assertIn('class="instrument-controls-row"', page)
        self.assertIn('id="instrumentSelect"', page)
        for timbre in ("steel_drum", "harpsichord", "piano", "guitar", "drums"):
            self.assertRegex(page, rf'<option[^>]+value="{timbre}"')
        for area in range(8):
            self.assertIn(f'id="areaNote{area}"', page)
        for control in ("mappingProfileSelect", "mappingProfileEditSelect", "mappingProfileName", "mappingProfileAdd", "mappingProfileDelete"):
            self.assertIn(f'id="{control}"', page)

        for control in (
            "soundSettingsOpen",
            "soundSettingsDialog",
            "mappingSettingsOpen",
            "mappingSettingsDialog",
            "usbCamera",
            "cameraDevice",
            "cameraStart",
            "cameraStop",
        ):
            self.assertIn(f'id="{control}"', page)
        self.assertRegex(page, r'<dialog id="soundSettingsDialog"')
        self.assertRegex(page, r'<dialog id="mappingSettingsDialog"')
        self.assertRegex(page, r'<video id="usbCamera"[^>]+autoplay[^>]+playsinline')

        for control in (
            "attackControl",
            "releaseControl",
            "echoDelayControl",
            "echoFeedbackControl",
            "retriggerGuardControl",
        ):
            self.assertIn(f'id="{control}"', page)
        self.assertRegex(page, r'id="retriggerGuardMs"[^>]+min="0"[^>]+max="500"')

    def test_instrument_page_uses_existing_live_inference_api(self):
        page = self.read_text("/instrument.html")
        script_paths = re.findall(r'<script[^>]+src="([^"]+)"', page)
        self.assertTrue(script_paths, "instrument page must load its client script")
        script = "\n".join(
            self.read_text(urljoin("/instrument.html", path)) for path in script_paths
        )

        self.assertIn("/api/inference/start", script)
        self.assertIn("/api/inference/stop", script)
        self.assertIn("/api/ai/latest", script)
        self.assertIn("mode:'instrument'", script)
        self.assertIn("/api/status", script)
        self.assertNotIn("/api/ai/wait", script)
        self.assertIn("latest_ai", script)
        self.assertIn("lastPlayedSequence", script)
        self.assertIn("displayArea(area)", script)
        self.assertIn("padStart(3,'0')", script)
        self.assertIn("/api/inference/retrigger", script)
        self.assertIn("synchronizeStartupState", script)
        self.assertIn("retriggerGuardMs", script)
        self.assertIn("演奏再開待ち", script)
        self.assertIn("deviceRunning&&performanceEnabled", script)
        self.assertRegex(script, r"(?:AudioContext|webkitAudioContext)")
        self.assertIn("localStorage", script)
        self.assertIn("mappingProfiles", script)
        self.assertIn("activeMappingProfileId", script)
        self.assertIn("syncActiveProfile", script)
        self.assertIn("name:'mario'", script)
        self.assertIn("['E4', 'G4', 'A4', 'A#4', 'B4', 'C5', 'E5', 'G5']", script)
        self.assertIn("STEEL_PARTIALS", script)
        self.assertIn("highNoteDamping", script)
        self.assertIn("filter.type='lowpass'", script)
        self.assertIn("showModal()", script)
        self.assertIn("navigator.mediaDevices", script)
        self.assertIn("enumerateDevices", script)
        self.assertIn("getUserMedia", script)
        self.assertIn("devicechange", script)
        self.assertIn("cameraStream", script)
        camera_setup = re.search(
            r"async function setupCamera\(\)\{(?P<body>.*?)\n\}", script, re.DOTALL
        )
        self.assertIsNotNone(camera_setup)
        self.assertNotIn("if(devices.length)await startCamera()", camera_setup.group("body"))
        self.assertIn("開始待ち", camera_setup.group("body"))

        # The low-latency endpoint returns immediately for a newer sequence
        # and otherwise waits, avoiding a fixed browser polling interval.
        self.controller.latest_ai = {"sequence": 41, "predicted_class": 2, "outputs": [0.0] * 8}
        wait_code, waited = self.get_json("/api/ai/wait?after=40&timeout=0.01")
        self.assertEqual(wait_code, 200)
        self.assertEqual(waited["sequence"], 41)

        status_code, status = self.get_json("/api/status")
        self.assertEqual(status_code, 200)
        self.assertIn("inference", status)
        stop_code, stopped = self.post_json("/api/inference/stop", {})
        self.assertEqual(stop_code, 200)
        self.assertFalse(stopped["active"])

    def test_all_operating_pages_link_to_the_instrument_tab(self):
        for path in ("/", "/collector.html", "/position.html", "/instrument.html"):
            with self.subTest(path=path):
                page = self.read_text(path)
                self.assertIn('href="/instrument.html"', page)

    def test_all_operating_pages_share_explicit_button_states(self):
        controls = self.read_text("/controls.css")
        self.assertIn("button:disabled", controls)
        self.assertIn("button.is-running", controls)

        for path in ("/", "/collector.html", "/position.html", "/instrument.html"):
            with self.subTest(path=path):
                page = self.read_text(path)
                self.assertIn('href="/controls.css?', page)
                self.assertRegex(page, r'id="disconnect" disabled')

        instrument = self.read_text("/instrument.html")
        self.assertRegex(instrument, r'id="instrumentStart" disabled')
        script = self.read_text("/instrument.js")
        self.assertIn("updateActionState", script)
        self.assertIn("is-running", script)


if __name__ == "__main__":
    unittest.main()
