import tempfile
from pathlib import Path
import unittest

import numpy as np

from sim.sampling_experiment import (
    RateSpec,
    classification_metrics,
    downsample_waveform,
    extract_features,
    fit_numeric_beta,
    make_loso_folds,
    regression_metrics,
)


SOURCE_RATE_HZ = 25_600
SOURCE_SAMPLE_COUNT = 2_048
SOURCE_TRIGGER_INDEX = 64


class SamplingExperimentTests(unittest.TestCase):
    @staticmethod
    def rate_specs() -> tuple[RateSpec, ...]:
        return (
            RateSpec(
                target_rate_hz=25_600,
                factor=1,
                output_count=2_048,
                trigger_index=64,
            ),
            RateSpec(
                target_rate_hz=12_800,
                factor=2,
                output_count=1_024,
                trigger_index=32,
            ),
            RateSpec(
                target_rate_hz=6_400,
                factor=4,
                output_count=512,
                trigger_index=16,
            ),
        )

    @staticmethod
    def impact_waveform() -> np.ndarray:
        sample_index = np.arange(SOURCE_SAMPLE_COUNT)
        seconds = sample_index / SOURCE_RATE_HZ
        posttrigger = np.maximum(sample_index - SOURCE_TRIGGER_INDEX, 0)
        envelope = np.where(
            sample_index >= SOURCE_TRIGGER_INDEX,
            np.exp(-posttrigger / 500.0),
            0.0,
        )
        waveform = 9_000.0 * envelope * (
            np.sin(2.0 * np.pi * 850.0 * seconds)
            + 0.35 * np.sin(2.0 * np.pi * 2_100.0 * seconds)
        )
        waveform += 120.0 * np.sin(2.0 * np.pi * 90.0 * seconds)
        return waveform.astype(np.float32)

    def test_downsampling_preserves_duration_and_scales_trigger(self):
        waveform = self.impact_waveform()

        for spec in self.rate_specs():
            with self.subTest(rate_hz=spec.target_rate_hz):
                actual = downsample_waveform(waveform, SOURCE_RATE_HZ, spec)
                self.assertEqual(actual.shape, (spec.output_count,))
                self.assertEqual(
                    (spec.output_count, spec.trigger_index),
                    (SOURCE_SAMPLE_COUNT // spec.factor, SOURCE_TRIGGER_INDEX // spec.factor),
                )
                self.assertTrue(np.isfinite(actual).all())

        full_rate = downsample_waveform(waveform, SOURCE_RATE_HZ, self.rate_specs()[0])
        np.testing.assert_allclose(full_rate, waveform, rtol=0.0, atol=0.0)

    def test_antialias_filter_suppresses_four_khz_before_6k4_decimation(self):
        sample_index = np.arange(SOURCE_SAMPLE_COUNT)
        waveform = np.sin(2.0 * np.pi * 4_000.0 * sample_index / SOURCE_RATE_HZ)
        spec = self.rate_specs()[-1]

        filtered = downsample_waveform(waveform, SOURCE_RATE_HZ, spec)
        naively_decimated = waveform[::spec.factor]
        # Ignore filter startup/end transients and compare steady-state energy.
        interior = slice(32, -32)
        filtered_rms = float(np.sqrt(np.mean(filtered[interior] ** 2)))
        naive_rms = float(np.sqrt(np.mean(naively_decimated[interior] ** 2)))

        self.assertGreater(naive_rms, 0.6)
        self.assertLess(filtered_rms, naive_rms * 0.2)

    def test_all_feature_modes_return_128_finite_values_at_each_rate(self):
        waveform = self.impact_waveform()

        for spec in self.rate_specs():
            downsampled = downsample_waveform(waveform, SOURCE_RATE_HZ, spec)
            for mode in ("time", "fft", "hybrid"):
                with self.subTest(rate_hz=spec.target_rate_hz, mode=mode):
                    features = extract_features(
                        downsampled,
                        spec.target_rate_hz,
                        spec.trigger_index,
                        mode,
                    )
                    self.assertEqual(features.shape, (128,))
                    self.assertTrue(np.isfinite(features).all())

    def test_numeric_beta_supports_two_coordinate_outputs(self):
        rng = np.random.default_rng(20260718)
        features = rng.normal(size=(96, 128)).astype(np.float32)
        targets = rng.uniform(0.0, 1.0, size=(96, 2)).astype(np.float32)
        alpha = rng.normal(scale=0.05, size=(128, 32)).astype(np.float32)

        beta = fit_numeric_beta(features, targets, alpha, ridge=0.1)

        self.assertEqual(beta.shape, (32, 2))
        self.assertTrue(np.isfinite(beta).all())

    def test_classification_metrics_have_expected_basic_values(self):
        expected = np.asarray([0, 1, 1, 2], dtype=np.int64)
        predicted = np.asarray([0, 0, 1, 2], dtype=np.int64)

        metrics = classification_metrics(expected, predicted, class_count=3)

        self.assertAlmostEqual(metrics["accuracy"], 0.75)
        np.testing.assert_array_equal(
            np.asarray(metrics["confusion_matrix"]),
            np.asarray([[1, 0, 0], [1, 1, 0], [0, 0, 1]]),
        )
        np.testing.assert_allclose(metrics["per_class_recall"], [1.0, 0.5, 1.0])
        self.assertAlmostEqual(metrics["balanced_accuracy"], 5.0 / 6.0)

    def test_regression_metrics_are_reported_in_millimetres(self):
        expected = np.asarray([[0.0, 0.0], [100.0, 100.0]])
        predicted = np.asarray([[3.0, 4.0], [94.0, 108.0]])

        metrics = regression_metrics(expected, predicted)

        self.assertAlmostEqual(metrics["mae_x_mm"], 4.5)
        self.assertAlmostEqual(metrics["mae_y_mm"], 6.0)
        self.assertAlmostEqual(metrics["rmse_x_mm"], np.sqrt(22.5))
        self.assertAlmostEqual(metrics["rmse_y_mm"], np.sqrt(40.0))
        self.assertAlmostEqual(metrics["mean_distance_mm"], 7.5)
        self.assertAlmostEqual(metrics["median_distance_mm"], 7.5)
        self.assertAlmostEqual(metrics["p90_distance_mm"], 9.5)
        self.assertAlmostEqual(metrics["within_25mm"], 1.0)
        self.assertAlmostEqual(metrics["within_50mm"], 1.0)

    def test_loso_builds_four_folds_with_three_train_sessions_each(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session_names = [f"session-{index}" for index in range(4)]
            for name in session_names:
                (root / name).mkdir()
            # Repeated IDs model multiple events belonging to each session.
            session_ids = np.repeat(
                np.asarray([path.name for path in sorted(root.iterdir())]), 3
            )

            folds = list(make_loso_folds(session_ids))

        self.assertEqual(len(folds), 4)
        observed_tests = set()
        all_sessions = set(session_names)
        for train_sessions, test_session in folds:
            train_sessions = set(train_sessions)
            self.assertEqual(len(train_sessions), 3)
            self.assertNotIn(test_session, train_sessions)
            self.assertEqual(train_sessions | {test_session}, all_sessions)
            observed_tests.add(test_session)
        self.assertEqual(observed_tests, all_sessions)


if __name__ == "__main__":
    unittest.main()
