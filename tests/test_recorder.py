import csv
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from pc.acrylic_pan_monitor.app import configure_matplotlib_font
from pc.acrylic_pan_monitor.recorder import Recorder, ReceiveStats, make_demo_event


class RecorderTests(unittest.TestCase):
    def test_records_atomic_npz_and_manifests(self):
        with tempfile.TemporaryDirectory() as temporary:
            recorder = Recorder(temporary)
            session = recorder.begin_session({"panel": "test"})
            event = make_demo_event(sequence=42)
            result = recorder.record_event(event, class_id=3, annotations={"source": "test"})
            recorder.close()

            self.assertTrue(result.path.is_file())
            self.assertFalse(list((session / "events").glob("*.tmp")))
            with np.load(result.path) as saved:
                np.testing.assert_array_equal(saved["samples"], np.asarray(event.samples, dtype=np.int16))
                self.assertEqual(int(saved["class_id"]), 3)
                self.assertEqual(int(saved["sequence"]), 42)

            metadata = json.loads((session / "session.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["event_count"], 1)
            self.assertIsNotNone(metadata["closed_at"])
            json_rows = [json.loads(line) for line in (session / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(json_rows[0]["class_id"], 3)
            self.assertEqual(json_rows[0]["annotations"]["source"], "test")
            with (session / "manifest.csv").open(encoding="utf-8-sig", newline="") as input_file:
                csv_rows = list(csv.DictReader(input_file))
            self.assertEqual(csv_rows[0]["class_id"], "3")
            self.assertEqual(csv_rows[0]["sequence"], "42")

    def test_unlabeled_event_has_empty_manifest_class(self):
        with tempfile.TemporaryDirectory() as temporary:
            recorder = Recorder(temporary)
            session = recorder.begin_session()
            result = recorder.record_event(make_demo_event())
            with np.load(result.path) as saved:
                self.assertEqual(int(saved["class_id"]), -1)
            with (session / "manifest.csv").open(encoding="utf-8-sig", newline="") as input_file:
                row = next(csv.DictReader(input_file))
            self.assertEqual(row["class_id"], "")

    def test_sequence_statistics_include_wrap_and_anomalies(self):
        stats = ReceiveStats()
        for sequence in (0xFFFFFFFE, 0xFFFFFFFF, 1, 1, 0):
            stats.observe_event(sequence)
        self.assertEqual(stats.events_received, 5)
        self.assertEqual(stats.missing_sequences, 1)  # wrapped sequence zero was absent
        self.assertEqual(stats.duplicate_sequences, 1)
        self.assertEqual(stats.out_of_order_sequences, 1)

    def test_demo_is_deterministic(self):
        first = make_demo_event(7)
        second = make_demo_event(7)
        self.assertEqual(first, second)
        self.assertEqual(len(first.samples), 512)

    def test_japanese_font_selection_prefers_yu_gothic(self):
        selected = configure_matplotlib_font({"Meiryo", "Yu Gothic"})
        self.assertEqual(selected, "Yu Gothic")


if __name__ == "__main__":
    unittest.main()
