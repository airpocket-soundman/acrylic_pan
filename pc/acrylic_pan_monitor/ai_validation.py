"""Golden-output comparison for the deterministic board AI self-test."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .protocol import AiResult


DEFAULT_GOLDEN_PATH = Path("data/dummy_model/golden_outputs.json")


def load_golden_case(path: str | Path, board_case_id: int) -> dict[str, Any] | None:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = document.get("cases", []) if isinstance(document, dict) else document
    if not isinstance(cases, list):
        raise ValueError("golden output must contain a cases list")
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            continue
        numeric_id = case.get("board_case_id", index)
        if int(numeric_id) == board_case_id:
            return case
    return None


def compare_ai_result(
    result: AiResult,
    golden_case: dict[str, Any],
    *,
    absolute_tolerance: float = 0.035,
    relative_tolerance: float = 0.05,
) -> dict[str, Any]:
    """Compare all eight scores, allowing for accelerator BF16 MAC rounding.

    The PC reference quantizes layer boundaries.  The ML63Q25x7 accelerator
    rounds inside its multiply-accumulate path as well, so bit-exact equality
    is not expected.  The 0.035 limit is above the measured 0.03125 maximum
    from the fixed eight-case hardware qualification run.
    """
    expected = tuple(float(value) for value in golden_case["outputs"])
    if len(expected) != len(result.outputs):
        raise ValueError("golden output vector length does not match board result")
    expected_class = int(
        golden_case.get("predicted_class", golden_case.get("expected_class", -1))
    )
    errors = [abs(actual - reference) for actual, reference in zip(result.outputs, expected)]
    matches = [
        math.isclose(actual, reference, abs_tol=absolute_tolerance, rel_tol=relative_tolerance)
        for actual, reference in zip(result.outputs, expected)
    ]
    return {
        "available": True,
        "case_name": str(golden_case.get("case_id", result.case_id)),
        "expected_class": expected_class,
        "class_match": result.predicted_class == expected_class,
        "expected_outputs": list(expected),
        "absolute_errors": errors,
        "max_absolute_error": max(errors, default=0.0),
        "outputs_match": all(matches),
        "passed": result.predicted_class == expected_class and all(matches),
        "absolute_tolerance": absolute_tolerance,
        "relative_tolerance": relative_tolerance,
    }
