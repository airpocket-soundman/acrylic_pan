"""Pure NumPy reference implementation of the Solist-AI one-layer ELM.

Solist-AI fixes the input-to-hidden weights (alpha) and trains only the
hidden-to-output weights (beta).  Classification is represented by one-hot
numeric targets; ``fit_targets`` also supports future x/y/strength regression.
"""

from __future__ import annotations

import numpy as np


def activation(name: str, value: np.ndarray) -> np.ndarray:
    """Apply an activation available in the Solist-AI simulator."""
    if name == "hard_sigmoid":
        return np.clip(0.2 * value + 0.5, 0.0, 1.0)
    if name == "hard_tanh":
        return np.clip(value, -1.0, 1.0)
    if name == "linear":
        return value
    if name == "sigmoid":
        return 1.0 / (1.0 + np.exp(-np.clip(value, -700.0, 700.0)))
    if name == "tanh":
        return np.tanh(value)
    if name == "relu":
        return np.maximum(value, 0.0)
    raise ValueError(f"unsupported activation: {name}")


class SolistELM:
    """Solist-AI-compatible ELM for offline feasibility checks.

    This model does not replace validation in the official simulator.  It is a
    reproducible reference for feature and target design before CSV export.
    """

    def __init__(
        self,
        n_hidden: int = 64,
        activation_name: str = "hard_sigmoid",
        ridge: float = 1e-2,
        seed: int = 1,
        alpha_scale: float | None = None,
    ) -> None:
        if n_hidden <= 0 or ridge < 0:
            raise ValueError("n_hidden must be positive and ridge non-negative")
        self.n_hidden = n_hidden
        self.activation_name = activation_name
        self.ridge = ridge
        self.seed = seed
        self.alpha_scale = alpha_scale
        self.alpha: np.ndarray | None = None
        self.bias: np.ndarray | None = None
        self.beta: np.ndarray | None = None
        self.output_count: int | None = None

    def _validate_x(self, x: np.ndarray) -> np.ndarray:
        result = np.asarray(x, dtype=np.float64)
        if result.ndim != 2 or result.shape[0] == 0 or result.shape[1] == 0:
            raise ValueError("X must be a non-empty 2-D array")
        if not np.isfinite(result).all():
            raise ValueError("X contains non-finite values")
        return result

    def _initialize_projection(self, input_count: int) -> None:
        rng = np.random.default_rng(self.seed)
        scale = self.alpha_scale
        if scale is None:
            scale = 1.0 / np.sqrt(input_count)
        self.alpha = rng.standard_normal((input_count, self.n_hidden)) * scale
        self.bias = rng.standard_normal(self.n_hidden) * 0.1

    def _hidden(self, x: np.ndarray) -> np.ndarray:
        if self.alpha is None or self.bias is None:
            raise RuntimeError("model is not fitted")
        return activation(self.activation_name, x @ self.alpha + self.bias)

    def fit_targets(self, x: np.ndarray, targets: np.ndarray) -> "SolistELM":
        """Fit arbitrary numeric targets with shape ``(samples, outputs)``.

        For classification, targets are one-hot.  For coordinate inference,
        targets may instead contain normalized ``x, y, strength`` values.
        """
        x = self._validate_x(x)
        targets = np.asarray(targets, dtype=np.float64)
        if targets.ndim != 2 or targets.shape[0] != x.shape[0] or targets.shape[1] == 0:
            raise ValueError("targets must have shape (len(X), output_count)")
        if not np.isfinite(targets).all():
            raise ValueError("targets contain non-finite values")
        self._initialize_projection(x.shape[1])
        hidden = self._hidden(x)
        gram = hidden.T @ hidden + self.ridge * np.eye(self.n_hidden)
        self.beta = np.linalg.solve(gram, hidden.T @ targets)
        self.output_count = targets.shape[1]
        return self

    def fit(self, x: np.ndarray, labels: np.ndarray, class_count: int = 8) -> "SolistELM":
        """Fit integer class labels as one-hot targets."""
        labels = np.asarray(labels)
        if labels.ndim != 1 or len(labels) != len(x):
            raise ValueError("labels must be a 1-D array with len(X) entries")
        if class_count <= 1 or not np.issubdtype(labels.dtype, np.integer):
            raise ValueError("class_count must be >1 and labels must be integers")
        if np.any(labels < 0) or np.any(labels >= class_count):
            raise ValueError("label is outside the configured class range")
        targets = np.eye(class_count, dtype=np.float64)[labels]
        return self.fit_targets(x, targets)

    def decision(self, x: np.ndarray) -> np.ndarray:
        """Return raw per-output values (the values exported by the model)."""
        x = self._validate_x(x)
        if self.beta is None or self.alpha is None or x.shape[1] != self.alpha.shape[0]:
            raise RuntimeError("model is not fitted for this input shape")
        return self._hidden(x) @ self.beta

    def predict(self, x: np.ndarray) -> np.ndarray:
        """Return the CPU-side argmax class decision."""
        return np.argmax(self.decision(x), axis=1)


def accuracy(expected: np.ndarray, actual: np.ndarray) -> float:
    expected, actual = np.asarray(expected), np.asarray(actual)
    if expected.shape != actual.shape or expected.size == 0:
        raise ValueError("expected and actual must have the same non-empty shape")
    return float(np.mean(expected == actual))
