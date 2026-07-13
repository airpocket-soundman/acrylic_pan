"""Panel coordinate conversion shared by simulation and visualizer."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Panel:
    width_mm: float = 400.0
    height_mm: float = 200.0
    cell_mm: float = 100.0

    @property
    def columns(self) -> int:
        return math.ceil(self.width_mm / self.cell_mm)

    @property
    def rows(self) -> int:
        return math.ceil(self.height_mm / self.cell_mm)


DEFAULT_PANEL = Panel()

NOTES = (
    ("C4", "D4", "E4", "G4"),
    ("A4", "C5", "D5", "E5"),
)


def clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def normalize(x_mm: float, y_mm: float, panel: Panel = DEFAULT_PANEL) -> tuple[float, float]:
    """Convert physical millimetres to Solist-AI target coordinates."""
    return (
        clamp(x_mm / panel.width_mm, 0.0, 1.0),
        clamp(y_mm / panel.height_mm, 0.0, 1.0),
    )


def denormalize(x_norm: float, y_norm: float, panel: Panel = DEFAULT_PANEL) -> tuple[float, float]:
    """Convert model output to a point constrained to the panel."""
    return (
        clamp(x_norm, 0.0, 1.0) * panel.width_mm,
        clamp(y_norm, 0.0, 1.0) * panel.height_mm,
    )


def coordinate_to_cell(
    x_mm: float, y_mm: float, panel: Panel = DEFAULT_PANEL
) -> tuple[int, int]:
    """Return zero-based (column, row), including points on the far edges."""
    x = clamp(x_mm, 0.0, panel.width_mm)
    y = clamp(y_mm, 0.0, panel.height_mm)
    column = min(int(x // panel.cell_mm), panel.columns - 1)
    row = min(int(y // panel.cell_mm), panel.rows - 1)
    return column, row


def coordinate_to_note(x_mm: float, y_mm: float, panel: Panel = DEFAULT_PANEL) -> str:
    column, row = coordinate_to_cell(x_mm, y_mm, panel)
    if row >= len(NOTES) or column >= len(NOTES[row]):
        raise ValueError("No note is assigned to this panel cell")
    return NOTES[row][column]


def distance_error_mm(
    predicted: tuple[float, float], expected: tuple[float, float]
) -> float:
    return math.hypot(predicted[0] - expected[0], predicted[1] - expected[1])

