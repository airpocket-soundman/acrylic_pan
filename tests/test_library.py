import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from pc.acrylic_pan_monitor.library import Library, LibraryError
from pc.acrylic_pan_monitor.recorder import Recorder, make_demo_event


def build_session(root, count=3, mode=None):
    recorder = Recorder(root)
    metadata = {"mode": mode} if mode else {}
    session = recorder.begin_session(metadata)
    for index in range(count):
        recorder.record_event(
            make_demo_event(sequence=index + 1),
            class_id=index % 8,
            annotations={"target_point_name": "center", "repetition": index + 1},
        )
    return recorder, session


class LibraryReadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_lists_sessions_and_events(self):
        recorder, session = build_session(self.root, count=3, mode="guided_8area_points")
        recorder.close()
        library = Library(self.root)

        sessions = library.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["session_id"], session.name)
        self.assertEqual(sessions[0]["event_count"], 3)
        self.assertEqual(sessions[0]["mode"], "guided_8area_points")
        self.assertTrue(sessions[0]["consistent"])
        self.assertEqual(sessions[0]["class_ids"], [0, 1, 2])

        events = library.list_events(session.name)
        self.assertEqual([event["index"] for event in events], [1, 2, 3])
        self.assertTrue(all(event["exists"] for event in events))
        self.assertEqual(events[0]["annotations"]["target_point_name"], "center")

    def test_load_event_round_trips_the_waveform(self):
        recorder, session = build_session(self.root, count=1)
        recorder.close()
        original = make_demo_event(sequence=1)

        event, record = Library(self.root).load_event(session.name, 1)
        self.assertEqual(event.samples, original.samples)
        self.assertEqual(event.sample_rate_hz, original.sample_rate_hz)
        self.assertEqual(event.trigger_index, original.trigger_index)
        self.assertEqual(event.sequence, 1)
        self.assertEqual(record["class_id"], 0)
        self.assertEqual(record["area"], 1)

    def test_unreadable_session_is_reported_not_raised(self):
        recorder, session = build_session(self.root, count=1)
        recorder.close()
        (session / "manifest.jsonl").unlink()

        sessions = Library(self.root).list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertIn("manifest.jsonl", sessions[0]["error"])

    def test_missing_event_file_is_flagged_but_listing_survives(self):
        recorder, session = build_session(self.root, count=2)
        recorder.close()
        next(iter((session / "events").glob("event_000001_*.npz"))).unlink()

        events = Library(self.root).list_events(session.name)
        self.assertEqual([event["exists"] for event in events], [False, True])
        with self.assertRaisesRegex(LibraryError, "missing"):
            Library(self.root).load_event(session.name, 1)

    def test_rejects_session_ids_outside_the_recorder_format(self):
        library = Library(self.root)
        for bad in ("..", "../secrets", "", "not-a-session", "20260101_010101_ZZZZZZZZ"):
            with self.assertRaises(LibraryError):
                library.session_dir(bad)


class LibraryDeleteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def read_manifest(self, session):
        lines = (session / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def test_delete_removes_file_manifests_and_updates_event_count(self):
        recorder, session = build_session(self.root, count=3)
        recorder.close()
        library = Library(self.root)
        target = Path(self.read_manifest(session)[1]["file"])

        result = library.delete_event(session.name, 2)

        self.assertEqual(result["event_count"], 2)
        self.assertFalse((session / target).exists())
        rows = self.read_manifest(session)
        self.assertEqual([row["index"] for row in rows], [1, 3])
        metadata = json.loads((session / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["event_count"], 2)
        csv_text = (session / "manifest.csv").read_text(encoding="utf-8-sig")
        self.assertEqual(len(csv_text.strip().splitlines()), 3)  # header plus two rows

    def test_deleted_session_stays_loadable_by_the_dataset_reader(self):
        """The dataset loader rejects event_count/manifest disagreement."""
        from sim.solist_dataset import load_recorded_sessions

        recorder = Recorder(self.root)
        session = recorder.begin_session()
        for index in range(16):
            recorder.record_event(make_demo_event(sequence=index + 1), class_id=index % 8)
        recorder.close()

        Library(self.root).delete_event(session.name, 5)

        dataset = load_recorded_sessions(session)
        self.assertEqual(len(dataset.labels), 15)
        self.assertEqual(len(dataset.event_paths), 15)
        self.assertTrue(all(path.is_file() for path in dataset.event_paths))

    def test_delete_is_rejected_for_unknown_event(self):
        recorder, session = build_session(self.root, count=1)
        recorder.close()
        with self.assertRaisesRegex(LibraryError, "not in"):
            Library(self.root).delete_event(session.name, 99)

    def test_active_recorder_keeps_event_count_correct_after_delete(self):
        recorder, session = build_session(self.root, count=3)
        library = Library(self.root)

        library.delete_event(session.name, 2)
        recorder.refresh_event_count()
        saved = recorder.record_event(make_demo_event(sequence=99), class_id=4)
        recorder.close()

        self.assertEqual(saved.index, 4, "index must not be reused after a deletion")
        rows = self.read_manifest(session)
        self.assertEqual([row["index"] for row in rows], [1, 3, 4])
        metadata = json.loads((session / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["event_count"], 3)
        self.assertEqual(metadata["event_count"], len(rows))

    def test_delete_session_removes_the_whole_directory(self):
        recorder, session = build_session(self.root, count=2)
        recorder.close()

        result = Library(self.root).delete_session(session.name)

        self.assertEqual(result["removed_events"], 2)
        self.assertFalse(session.exists())
        self.assertEqual(Library(self.root).list_sessions(), [])


if __name__ == "__main__":
    unittest.main()
