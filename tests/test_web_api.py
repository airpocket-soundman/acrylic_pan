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
    CLAMP_FOOTPRINT_MM,
    AcquisitionController,
    build_collection_targets,
    create_server,
)
from pc.acrylic_pan_monitor.protocol import EVENT_CHUNK_HEADER, Frame, MessageType, decode_frame
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
        self.assertIn("Acrylic Pan 推論結果", page)
        self.assertIn("推論開始", page)
        self.assertIn("学習データ採取", page)
        self.assertIn("推論結果", page)
        self.assertIn("判定エリア", page)
        self.assertIn("判定に使用した振動波形", page)
        self.assertIn("振動波形のFFT", page)
        self.assertEqual(page.count('data-class="'), 8)
        with urlopen(self.base + "/collector.html", timeout=2) as response:
            collector_page = response.read().decode("utf-8")
        self.assertIn("Acrylic Pan Vibration Monitor", collector_page)
        self.assertIn("8エリア ガイド付きデータ採取", collector_page)
        self.assertIn("採取開始", collector_page)
        self.assertIn("collectionPattern", collector_page)
        self.assertIn('id="collectionRepetitions" type="number" min="1" max="1000" value="50"', collector_page)
        self.assertIn("collectionMarker", collector_page)
        self.assertIn("collectionUndo", collector_page)
        self.assertIn('href="/collector.html">学習データ採取', collector_page)
        self.assertIn('href="/">推論結果', collector_page)
        self.assertIn("libraryPrevious", collector_page)
        self.assertIn("libraryNext", collector_page)
        with urlopen(self.base + "/collector.css", timeout=2) as response:
            collector_css = response.read().decode("utf-8")
        self.assertIn(".collection-marker", collector_css)
        self.assertNotIn("AI推論を開始", collector_page)
        self.assertIn("FFT", page)

    def connected_link(self):
        """Patch the link as connected so start/re-arm behave as on real hardware."""
        self.controller.link.send = lambda packet: None
        return patch.object(
            type(self.controller.link), "connected", new_callable=PropertyMock, return_value=True
        )

    def arm_collection(self, repetitions=2, pattern="corners"):
        self.controller.start_collection(repetitions, self.temporary.name, pattern)
        return self.controller.collection

    def test_collection_targets_preview_matches_the_pattern(self):
        _, corners = self.get_json("/api/collection/targets?pattern=corners")
        self.assertEqual(len(corners["targets"]), 32)
        self.assertEqual(corners["points_per_class"], 4)
        self.assertEqual(corners["panel"], {
            "width_mm": 400.0,
            "height_mm": 200.0,
            "clamp": {"x_min": 200.0, "x_max": 300.0, "y_min": 0.0, "y_max": 20.0},
        })
        self.assertEqual(corners["targets"][0]["target_index"], 0)
        self.assertEqual(corners["targets"][0]["count"], 0)
        self.assertFalse(corners["targets"][0]["complete"])
        self.assertEqual((corners["targets"][0]["x_mm"], corners["targets"][0]["y_mm"]), (25.0, 25.0))

        _, center = self.get_json("/api/collection/targets?pattern=center")
        self.assertEqual(len(center["targets"]), 8)
        self.assertEqual(center["targets"][0]["x_mm"], 50.0)
        self.assertEqual(center["targets"][0]["y_mm"], 50.0)

    def test_collection_targets_rejects_unknown_pattern(self):
        with self.assertRaises(Exception) as caught:
            urlopen(self.base + "/api/collection/targets?pattern=bogus", timeout=2)
        self.assertEqual(caught.exception.code, 400)

    def expect_bad_request(self, path, body):
        request = Request(
            self.base + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(Exception) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 400)

    def test_select_target_activates_any_incomplete_point(self):
        with self.connected_link():
            self.arm_collection(repetitions=2, pattern="corners")
            _, before = self.get_json("/api/collection")
            self.assertEqual(before["current_target_index"], 0)

            status_code, after = self.post_json("/api/collection/select", {"target_index": 17})
        self.assertEqual(status_code, 200)
        self.assertEqual(after["selected_index"], 17)
        self.assertEqual(after["current_target_index"], 17)
        self.assertEqual(after["current_class_id"], 4, "target 17 is area 5's up_right point")
        self.assertEqual(after["current_point_name"], "up_right")
        self.assertEqual((after["current_x_mm"], after["current_y_mm"]), (75.0, 125.0))
        self.assertEqual(after["current_repetition"], 1)

    def test_selected_target_receives_the_next_event_and_its_label(self):
        with self.connected_link():
            collection = self.arm_collection(repetitions=2, pattern="corners")
            self.post_json("/api/collection/select", {"target_index": 17})
            self.controller._process_event(make_demo_event(1), "serial", True)
            _, status = self.get_json("/api/collection")

        self.assertEqual(collection.target_counts[17], 1)
        self.assertEqual(collection.target_counts[0], 0, "the default first point must be untouched")
        self.assertEqual(collection.per_class_counts[4], 1)
        self.assertEqual(status["current_target_index"], 17, "stays until the repetitions are done")
        self.assertEqual(status["current_repetition"], 2)

    def test_selected_point_label_reaches_the_saved_manifest(self):
        with self.connected_link():
            self.arm_collection(repetitions=1, pattern="corners")
            self.post_json("/api/collection/select", {"target_index": 17})
            self.controller._process_event(make_demo_event(1), "serial", True)
        assert self.controller.recorder is not None
        session = self.controller.recorder.session_dir
        assert session is not None
        row = json.loads((session / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["class_id"], 4, "the manifest must record the point actually struck")
        self.assertEqual(row["annotations"]["target_class_id"], 4)
        self.assertEqual(row["annotations"]["target_point_id"], 1)
        self.assertEqual(row["annotations"]["target_point_name"], "up_right")
        self.assertEqual(row["annotations"]["target_x_mm"], 75.0)
        self.assertEqual(row["annotations"]["target_y_mm"], 125.0)

    def test_selection_releases_once_the_point_is_complete(self):
        with self.connected_link():
            collection = self.arm_collection(repetitions=2, pattern="corners")
            self.post_json("/api/collection/select", {"target_index": 17})
            for sequence in (1, 2):
                self.controller._process_event(make_demo_event(sequence), "serial", True)
            _, status = self.get_json("/api/collection")

        self.assertTrue(collection.is_complete(17))
        self.assertIsNone(collection.selected_index, "a finished point must not stay selected")
        self.assertEqual(status["current_target_index"], 0, "the guide falls back to the first gap")

    def test_undo_deletes_the_last_sample_and_restores_its_selected_target(self):
        with self.connected_link():
            collection = self.arm_collection(repetitions=2, pattern="corners")
            self.post_json("/api/collection/select", {"target_index": 17})
            self.controller._process_event(make_demo_event(1), "serial", True)
            status_code, undone = self.post_json(
                "/api/collection/undo", {"expected_completed_samples": 1}
            )

            self.assertEqual(status_code, 200)
            self.assertEqual(undone["undone_event"]["target_index"], 17)
            self.assertEqual(undone["completed_samples"], 0)
            self.assertEqual(undone["current_target_index"], 17)
            self.assertEqual(undone["current_repetition"], 1)
            self.assertEqual(collection.target_counts[17], 0)
            self.assertEqual(collection.per_class_counts[4], 0)

            assert self.controller.recorder is not None
            session_id = self.controller.recorder.session_id
            _, events = self.get_json(f"/api/library/events?session={session_id}")
            self.assertEqual(events["events"], [])

            self.controller._process_event(make_demo_event(2), "serial", True)
            _, events = self.get_json(f"/api/library/events?session={session_id}")

        self.assertEqual([event["index"] for event in events["events"]], [2])
        annotations = events["events"][0]["annotations"]
        self.assertEqual(annotations["target_index"], 17)
        self.assertEqual(annotations["collection_index"], 0)
        self.assertEqual(annotations["repetition"], 1)

    def test_undo_rejects_empty_or_stale_collection_progress(self):
        with self.connected_link():
            self.arm_collection(repetitions=2, pattern="center")
            self.expect_bad_request(
                "/api/collection/undo", {"expected_completed_samples": 0}
            )
            self.controller._process_event(make_demo_event(1), "serial", True)
            self.expect_bad_request(
                "/api/collection/undo", {"expected_completed_samples": 0}
            )
            _, events = self.get_json(
                f"/api/library/events?session={self.controller.recorder.session_id}"
            )
        self.assertEqual(len(events["events"]), 1)

    def test_out_of_order_points_still_complete_the_run(self):
        """Every point must be reachable however the operator jumps around."""
        with self.connected_link():
            collection = self.arm_collection(repetitions=1, pattern="center")
            for target_index in (7, 3, 0, 5, 1, 6, 2, 4):
                self.post_json("/api/collection/select", {"target_index": target_index})
                self.controller._process_event(make_demo_event(target_index + 1), "serial", True)

        self.assertEqual(collection.target_counts, [1] * 8)
        self.assertEqual(collection.completed_samples, 8)
        self.assertFalse(collection.active)
        self.assertTrue(collection.finished)

    def test_select_rejects_completed_and_out_of_range_points(self):
        with self.connected_link():
            collection = self.arm_collection(repetitions=1, pattern="center")
            self.post_json("/api/collection/select", {"target_index": 2})
            self.controller._process_event(make_demo_event(1), "serial", True)
            self.assertTrue(collection.is_complete(2))

            for target_index in (2, 8, -1):
                self.expect_bad_request("/api/collection/select", {"target_index": target_index})

    def test_select_requires_an_active_collection(self):
        self.expect_bad_request("/api/collection/select", {"target_index": 1})

    def library_session(self, count=3):
        """Record `count` demo events through the controller's own Recorder."""
        self.controller.new_session(self.temporary.name, class_id=2)
        for _ in range(count):
            self.controller.demo()
        assert self.controller.recorder is not None
        assert self.controller.recorder.session_dir is not None
        return self.controller.recorder.session_dir.name

    def test_library_lists_sessions_and_events(self):
        session_id = self.library_session(count=3)
        _, sessions = self.get_json("/api/library/sessions")
        self.assertEqual([item["session_id"] for item in sessions["sessions"]], [session_id])
        self.assertEqual(sessions["sessions"][0]["event_count"], 3)

        _, events = self.get_json(f"/api/library/events?session={session_id}")
        self.assertEqual([event["index"] for event in events["events"]], [1, 2, 3])
        self.assertEqual(events["events"][0]["class_id"], 2)

    def test_library_event_returns_plottable_waveform_and_fft(self):
        session_id = self.library_session(count=1)
        _, event = self.get_json(f"/api/library/event?session={session_id}&index=1")
        self.assertEqual(event["source"], "library")
        self.assertEqual(len(event["samples"]), 512)
        self.assertEqual(len(event["time_ms"]), 512)
        self.assertEqual(len(event["frequency_hz"]), 257)
        self.assertEqual(len(event["magnitude_db"]), 257)
        self.assertEqual(event["stored"]["session_id"], session_id)
        self.assertEqual(event["stored"]["index"], 1)

    def test_library_browsing_does_not_disturb_the_latest_live_event(self):
        session_id = self.library_session(count=2)
        _, before = self.get_json("/api/events/latest")
        self.get_json(f"/api/library/event?session={session_id}&index=1")
        _, after = self.get_json("/api/events/latest")
        self.assertEqual(after["sequence"], before["sequence"])
        self.assertEqual(after["source"], before["source"])

    def test_library_delete_removes_the_event(self):
        session_id = self.library_session(count=3)
        status_code, result = self.post_json("/api/library/delete", {"session": session_id, "index": 2})
        self.assertEqual(status_code, 200)
        self.assertEqual(result["event_count"], 2)

        _, events = self.get_json(f"/api/library/events?session={session_id}")
        self.assertEqual([event["index"] for event in events["events"]], [1, 3])
        _, sessions = self.get_json("/api/library/sessions")
        self.assertEqual(sessions["sessions"][0]["event_count"], 2)
        self.assertTrue(sessions["sessions"][0]["consistent"])

    def test_library_delete_keeps_active_recorder_metadata_consistent(self):
        session_id = self.library_session(count=3)
        self.post_json("/api/library/delete", {"session": session_id, "index": 2})
        self.controller.demo()  # the live Recorder keeps writing into the same session

        _, sessions = self.get_json("/api/library/sessions")
        summary = sessions["sessions"][0]
        self.assertEqual(summary["event_count"], 3)
        self.assertTrue(summary["consistent"], "session.json must still match the manifest")
        _, events = self.get_json(f"/api/library/events?session={session_id}")
        self.assertEqual([event["index"] for event in events["events"]], [1, 3, 4])

    def test_library_delete_is_refused_during_guided_collection(self):
        session_id = self.library_session(count=2)
        self.controller.collection.active = True
        request = Request(
            self.base + "/api/library/delete",
            data=json.dumps({"session": session_id, "index": 1}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(Exception) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 400)
        _, events = self.get_json(f"/api/library/events?session={session_id}")
        self.assertEqual(len(events["events"]), 2)

    def test_library_rejects_traversal_and_unknown_ids(self):
        for session_id in ("..", "../..", "not-a-session"):
            request = self.base + f"/api/library/events?session={session_id}"
            with self.assertRaises(Exception) as caught:
                urlopen(request, timeout=2)
            self.assertIn(caught.exception.code, (400, 404))

    def test_library_delete_session_removes_everything(self):
        session_id = self.library_session(count=2)
        self.controller.new_session(self.temporary.name)  # stop writing into it
        status_code, result = self.post_json("/api/library/delete_session", {"session": session_id})
        self.assertEqual(status_code, 200)
        self.assertEqual(result["removed_events"], 2)
        _, sessions = self.get_json("/api/library/sessions")
        self.assertNotIn(session_id, [item["session_id"] for item in sessions["sessions"]])

    def test_library_can_delete_the_last_session_after_collection_stops(self):
        session_id = self.library_session(count=1)
        status_code, result = self.post_json(
            "/api/library/delete_session", {"session": session_id}
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(result["removed_events"], 1)
        self.assertIsNone(self.controller.recorder)
        _, sessions = self.get_json("/api/library/sessions")
        self.assertEqual(sessions["sessions"], [])

        # A later event starts a genuinely new session rather than writing to
        # the deleted directory.
        self.controller.demo()
        assert self.controller.recorder is not None
        self.assertNotEqual(self.controller.recorder.session_id, session_id)

    def test_collection_only_protects_its_current_session_from_deletion(self):
        previous_session = self.library_session(count=1)
        with self.connected_link():
            self.arm_collection(repetitions=2, pattern="center")
        assert self.controller.recorder is not None
        current_session = self.controller.recorder.session_id

        status_code, result = self.post_json(
            "/api/library/delete_session", {"session": previous_session}
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(result["removed_events"], 1)
        self.assertTrue(self.controller.collection.active)

        request = Request(
            self.base + "/api/library/delete_session",
            data=json.dumps({"session": current_session}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(Exception) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 400)
        error = json.loads(caught.exception.read())
        self.assertEqual(error["error"], "採取中のセッションのため削除できません。")
        _, sessions = self.get_json("/api/library/sessions")
        self.assertEqual(
            [item["session_id"] for item in sessions["sessions"]],
            [current_session],
        )

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
        sensor = session["user_metadata"]["sensor_configuration"]
        self.assertEqual(sensor["range_g"], 32)
        self.assertEqual(sensor["counts_per_g"], 1024)
        self.assertEqual(sensor["pretrigger_samples"], 64)
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

    def test_four_chunks_are_saved_as_one_long_collection_event(self):
        packets = []
        self.controller.link.send = packets.append
        samples = tuple((index % 2000) - 1000 for index in range(2048))
        with patch.object(type(self.controller.link), "connected", new_callable=PropertyMock, return_value=True):
            self.controller.start_collection(1, self.temporary.name, "center")
            for chunk_index in (2, 0, 3):
                part = samples[chunk_index * 512:(chunk_index + 1) * 512]
                payload = EVENT_CHUNK_HEADER.pack(
                    55, chunk_index, 4, 25_600, 2048, 64, 1000, 512
                ) + struct.pack("<512h", *part)
                self.controller._queue.put(Frame(MessageType.EVENT_CHUNK, 200 + chunk_index, payload))
            time.sleep(0.05)
            self.assertEqual(self.controller.collection.completed_samples, 0)
            part = samples[512:1024]
            payload = EVENT_CHUNK_HEADER.pack(
                55, 1, 4, 25_600, 2048, 64, 1000, 512
            ) + struct.pack("<512h", *part)
            self.controller._queue.put(Frame(MessageType.EVENT_CHUNK, 201, payload))
            deadline = time.monotonic() + 1.0
            while self.controller.collection.completed_samples == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertEqual(self.controller.collection.completed_samples, 1)
        self.assertEqual(self.controller.stats.events_received, 1)
        self.assertEqual(self.controller.stats.events_saved, 1)
        self.assertEqual(len(packets), 2)  # initial START and one re-arm
        assert self.controller.latest is not None
        self.assertEqual(len(self.controller.latest["samples"]), 2048)

    def test_corner_pattern_reproduces_the_specified_50mm_grid(self):
        """docs/design.md section 3: 32 grid points, X=25..375, Y=25..175."""
        corners = build_collection_targets("corners")
        self.assertEqual(len(corners), 32)
        grid = sorted({(target.x_mm, target.y_mm) for target in corners})
        self.assertEqual(len(grid), 32, "every grid point must be distinct")
        expected = sorted(
            {(x, y) for x in range(25, 400, 50) for y in range(25, 200, 50)}
            - {(225, 25), (275, 25)}
            | {(225.0, 35.0), (275.0, 35.0)}
        )
        self.assertEqual(grid, [(float(x), float(y)) for x, y in expected])

    def test_clamp_points_move_clear_of_the_fixture(self):
        """The two points under the x=200..300, y=0..20 clamp move to y=35."""
        by_position = {
            (target.class_id, target.point_name): target
            for target in build_collection_targets("corners")
        }
        moved_left, moved_right = by_position[(2, "up_left")], by_position[(2, "up_right")]
        self.assertEqual((moved_left.x_mm, moved_left.y_mm), (225.0, 35.0))
        self.assertEqual((moved_right.x_mm, moved_right.y_mm), (275.0, 35.0))
        self.assertEqual((moved_left.offset_x_mm, moved_left.offset_y_mm), (-25.0, -15.0))

        # The area centre must still be recoverable from position minus offset,
        # because validate_guided_collection derives it that way.
        self.assertEqual(moved_left.x_mm - moved_left.offset_x_mm, 250.0)
        self.assertEqual(moved_left.y_mm - moved_left.offset_y_mm, 50.0)

        # Only those two move; the same point IDs elsewhere stay on the grid.
        untouched = by_position[(1, "up_left")]
        self.assertEqual((untouched.x_mm, untouched.y_mm), (125.0, 25.0))
        self.assertEqual((untouched.offset_x_mm, untouched.offset_y_mm), (-25.0, -25.0))
        self.assertEqual((by_position[(2, "down_left")].x_mm, by_position[(2, "down_left")].y_mm),
                         (225.0, 75.0))

    def test_no_collection_point_sits_under_the_clamp(self):
        clamp = CLAMP_FOOTPRINT_MM
        for pattern in ("center", "corners"):
            for target in build_collection_targets(pattern):
                inside = (clamp["x_min"] <= target.x_mm <= clamp["x_max"]
                          and clamp["y_min"] <= target.y_mm <= clamp["y_max"])
                self.assertFalse(inside, f"{pattern} {target.x_mm},{target.y_mm} is on the clamp")

    def test_panel_payload_carries_the_clamp_geometry_for_the_diagram(self):
        """The GUI draws the clamp from this, so it must not drift from the constant."""
        for path in ("/api/collection/targets?pattern=corners", "/api/collection"):
            _, payload = self.get_json(path)
            self.assertEqual(payload["panel"]["clamp"], CLAMP_FOOTPRINT_MM)
            self.assertEqual(payload["panel"]["width_mm"], 400.0)
            self.assertEqual(payload["panel"]["height_mm"], 200.0)

    def test_center_pattern_matches_the_specified_teaching_centres(self):
        centers = build_collection_targets("center")
        self.assertEqual(len(centers), 8)
        self.assertEqual(
            [(target.x_mm, target.y_mm) for target in centers],
            [(x, y) for y in (50.0, 150.0) for x in (50.0, 150.0, 250.0, 350.0)],
        )

    def test_collection_position_patterns_have_exact_panel_coordinates(self):
        corners = build_collection_targets("corners")
        self.assertEqual(len(corners), 32)
        self.assertEqual(
            [(target.point_name, target.x_mm, target.y_mm) for target in corners[:4]],
            [
                ("up_left", 25.0, 25.0),
                ("up_right", 75.0, 25.0),
                ("down_left", 25.0, 75.0),
                ("down_right", 75.0, 75.0),
            ],
        )
        self.assertEqual(corners[-1].point_name, "down_right")
        self.assertEqual((corners[-1].x_mm, corners[-1].y_mm), (375.0, 175.0))
        with self.assertRaisesRegex(ValueError, "center or corners"):
            build_collection_targets("invalid")
        for retired in ("five", "nine"):
            with self.assertRaises(ValueError):
                build_collection_targets(retired)

    def test_corner_collection_is_training_loader_compatible(self):
        """A full B-series run, clamp exception included, must validate."""
        self.controller.link.send = lambda packet: None
        with patch.object(type(self.controller.link), "connected", new_callable=PropertyMock, return_value=True):
            self.controller.start_collection(1, self.temporary.name, "corners")
            for sequence in range(1, 33):
                self.controller._process_event(make_demo_event(sequence), "serial", True)
        assert self.controller.recorder is not None
        assert self.controller.recorder.session_dir is not None
        summaries = validate_guided_collection(
            self.controller.recorder.session_dir, point_count=4, repetitions=1
        )
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].event_count, 32)

    def test_center_collection_is_training_loader_compatible(self):
        self.controller.link.send = lambda packet: None
        with patch.object(type(self.controller.link), "connected", new_callable=PropertyMock, return_value=True):
            self.controller.start_collection(2, self.temporary.name, "center")
            for sequence in range(1, 17):
                self.controller._process_event(make_demo_event(sequence), "serial", True)
        assert self.controller.recorder is not None
        assert self.controller.recorder.session_dir is not None
        summaries = validate_guided_collection(
            self.controller.recorder.session_dir, point_count=1, repetitions=2
        )
        self.assertEqual(summaries[0].event_count, 16)

    def test_collection_api_and_stop(self):
        packets = []
        self.controller.link.send = packets.append
        with patch.object(type(self.controller.link), "connected", new_callable=PropertyMock, return_value=True):
            status_code, started = self.post_json("/api/collection/start", {
                "repetitions": 3,
                "output_root": self.temporary.name,
                "position_pattern": "corners",
            })
            self.assertEqual(status_code, 200)
            self.assertEqual(started["current_class_id"], 0)
            self.assertEqual(started["current_point_name"], "up_left")
            self.assertEqual(started["position_pattern"], "corners")
            self.assertEqual(started["total_samples"], 96)
            self.assertEqual(len(started["per_position_counts"]), 32)
            self.controller._process_event(make_demo_event(101), "serial", True)
            progressed = self.controller.collection_status()
            self.assertEqual(progressed["current_point_name"], "up_left")
            self.assertEqual(progressed["current_repetition"], 2)
            self.assertEqual(progressed["per_position_counts"][0]["count"], 1)
            _, stopped = self.post_json("/api/collection/stop", {})
        self.assertFalse(stopped["active"])
        self.assertEqual(decode_frame(packets[0]).message_type, MessageType.START)
        self.assertEqual(decode_frame(packets[-1]).message_type, MessageType.STOP)


if __name__ == "__main__":
    unittest.main()
