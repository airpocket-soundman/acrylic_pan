import csv
from pathlib import Path
import tempfile
import unittest

import numpy as np

from sim.solist_dataset import (
    FeatureScaler,
    SessionDataset,
    export_solist_csv,
    extract_fft_features,
    load_recorded_sessions,
    load_npz_events,
    make_synthetic_events,
    one_hot,
    split_dataset,
    split_dataset_by_session,
    validate_guided_collection,
)
from sim.solist_elm import SolistELM, accuracy
from pc.acrylic_pan_monitor.recorder import Recorder, make_demo_event
from sim.dummy_model_pipeline import (
    CLASS_COUNT,
    HIDDEN_COUNT,
    INPUT_COUNT,
    from_bfloat16_bits,
    generate,
    make_dataset,
    to_bfloat16_bits,
    train_model,
)


class SolistPipelineTests(unittest.TestCase):
    def test_guided_collection_validates_five_and_nine_points(self):
        point_names = ("center", "left", "right", "up", "down",
                       "up_left", "up_right", "down_left", "down_right")
        offsets = ((0, 0), (-10, 0), (10, 0), (0, -10), (0, 10),
                   (-10, -10), (10, -10), (-10, 10), (10, 10))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for point_count in (5, 9):
                recorder = Recorder(root)
                recorder.begin_session({"mode": "guided_8area_points", "point_count": point_count})
                sequence = 1
                for class_id in range(8):
                    center_x = 50.0 + 100.0 * (class_id % 4)
                    center_y = 50.0 + 100.0 * (class_id // 4)
                    for point_id in range(point_count):
                        dx, dy = offsets[point_id]
                        for repetition in (1, 2):
                            recorder.record_event(make_demo_event(sequence), class_id=class_id, annotations={
                                "target_class_id": class_id,
                                "target_point_id": point_id,
                                "target_point_name": point_names[point_id],
                                "target_x_mm": center_x + dx,
                                "target_y_mm": center_y + dy,
                                "offset_x_mm": dx,
                                "offset_y_mm": dy,
                                "repetition": repetition,
                            })
                            sequence += 1
                recorder.close()
            summaries = validate_guided_collection(root, repetitions=2)
            self.assertEqual(sorted(summary.point_count for summary in summaries), [5, 9])
            self.assertEqual(sorted(summary.event_count for summary in summaries), [80, 144])

    def test_eight_class_recorder_sessions_split_without_leakage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            # Each independent run contains all eight classes. Runs, not
            # individual events, are the train/test grouping boundary.
            for repeat in range(2):
                recorder = Recorder(root)
                recorder.begin_session({"fixture": f"complete-run-{repeat}"})
                for label in range(8):
                    recorder.record_event(make_demo_event(label * 10 + repeat, seed=label * 2 + repeat),
                                          class_id=label, annotations={"source": "synthetic-test"})
                recorder.close()
            dataset = load_recorded_sessions(root, require_all_classes=True)
            train_x, train_y, test_x, test_y = split_dataset_by_session(dataset, 0.5, 11)
            self.assertEqual(train_x.shape, (8, 128))
            self.assertEqual(test_x.shape, (8, 128))
            self.assertEqual(set(train_y.tolist()), set(range(8)))
            self.assertEqual(set(test_y.tolist()), set(range(8)))

    def test_recorded_session_loader_rejects_unlabelled_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            recorder = Recorder(temporary)
            recorder.begin_session()
            recorder.record_event(make_demo_event())
            with self.assertRaisesRegex(ValueError, "class_id"):
                load_recorded_sessions(Path(temporary))

    def test_session_split_rejects_incomplete_class_set(self):
        dataset = SessionDataset(
            np.zeros((4, 128), dtype=np.float32),
            np.asarray([0, 1, 0, 1]),
            np.asarray(["run-a", "run-a", "run-b", "run-b"]),
            tuple(Path(f"event-{index}.npz") for index in range(4)),
        )
        with self.assertRaisesRegex(ValueError, "every class"):
            split_dataset_by_session(dataset, 0.5, 1)

    def test_dummy_model_is_deterministic_and_separable(self):
        features, labels = make_dataset(samples_per_class=8)
        alpha, beta = train_model(features, labels)
        self.assertEqual(alpha.shape, (INPUT_COUNT, HIDDEN_COUNT))
        self.assertEqual(beta.shape, (HIDDEN_COUNT, CLASS_COUNT))
        self.assertTrue(np.array_equal(alpha, train_model(features, labels)[0]))
        self.assertAlmostEqual(float(np.max(np.abs(alpha))), 0.20520325, places=6)

    def test_bfloat16_round_trip(self):
        values = np.array([-1.25, 0.0, 0.1, 42.5], dtype=np.float32)
        actual = from_bfloat16_bits(to_bfloat16_bits(values))
        np.testing.assert_allclose(actual, values, rtol=4e-3, atol=1e-4)

    def test_dummy_export_has_eight_correct_golden_cases(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = generate(root / "data", root / "model.h")
            self.assertEqual(len(result["cases"]), CLASS_COUNT)
            self.assertEqual([case["board_case_id"] for case in result["cases"]], list(range(8)))
            self.assertTrue(all(case["expected_class"] == case["predicted_class"]
                                for case in result["cases"]))
            self.assertTrue((root / "model.h").exists())

    def test_fft_feature_shape_and_peak(self):
        sample_rate = 25_600
        time = np.arange(512) / sample_rate
        features = extract_fft_features(np.sin(2 * np.pi * 1_000 * time), 128)
        self.assertEqual(features.shape, (128,))
        self.assertAlmostEqual((np.argmax(features) + 1) * sample_rate / 512, 1_000, delta=50)

    def test_synthetic_eight_class_elm(self):
        features, labels = make_synthetic_events(samples_per_class=12, seed=7)
        train_x, train_y, test_x, test_y = split_dataset(features, labels, 0.25, 8)
        scaler = FeatureScaler.fit(train_x)
        model = SolistELM(n_hidden=64, seed=3).fit(scaler.transform(train_x), train_y)
        self.assertGreaterEqual(accuracy(test_y, model.predict(scaler.transform(test_x))), 0.90)

    def test_fit_targets_supports_coordinate_regression(self):
        rng = np.random.default_rng(1)
        features = rng.normal(size=(80, 5))
        targets = np.column_stack((features[:, 0] * 0.2, features[:, 1] * -0.1, features[:, 2] * 0.3))
        model = SolistELM(n_hidden=40, activation_name="linear", ridge=1e-6, seed=2)
        actual = model.fit_targets(features, targets).decision(features)
        self.assertEqual(actual.shape, (80, 3))
        self.assertLess(np.mean((actual - targets) ** 2), 1e-6)

    def test_npz_loader_requires_and_reads_label(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            samples = np.arange(512, dtype=np.int16)
            np.savez(root / "event.npz", samples=samples, class_id=np.uint8(3))
            features, labels = load_npz_events(root)
            self.assertEqual(features.shape, (1, 128))
            self.assertEqual(labels.tolist(), [3])
            np.savez(root / "unlabelled.npz", samples=samples)
            with self.assertRaisesRegex(ValueError, "missing class_id"):
                load_npz_events(root)

    def test_csv_layout_and_one_hot(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "dataset.csv"
            features = np.arange(12, dtype=float).reshape(3, 4)
            rows = export_solist_csv(output, features, one_hot(np.array([0, 2, 7])))
            with output.open(encoding="utf-8", newline="") as stream:
                content = list(csv.reader(stream))
            self.assertEqual(rows, 3)
            self.assertEqual(len(content), 4)
            self.assertEqual(len(content[0]), 12)
            self.assertEqual(content[2][-8:], ["0.0", "0.0", "1.0", "0.0", "0.0", "0.0", "0.0", "0.0"])

    def test_csv_cell_limit_rejects_or_truncates(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "limited.csv"
            features = np.ones((10, 3))
            targets = np.ones((10, 2))
            with self.assertRaisesRegex(ValueError, "limit"):
                export_solist_csv(output, features, targets, max_cells=40)
            rows = export_solist_csv(output, features, targets, max_cells=40, limit_rows=True)
            self.assertEqual(rows, 7)


if __name__ == "__main__":
    unittest.main()
