"""Dependency-free local HTTP server for serial capture and visualization."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import queue
import threading
import time
import webbrowser
from typing import Any
from urllib.parse import parse_qs, urlparse

import numpy as np

from pc.acrylic_pan_monitor.ai_validation import (
    DEFAULT_GOLDEN_PATH,
    compare_ai_result,
    load_golden_case,
)
from pc.acrylic_pan_monitor.library import Library, LibraryError
from pc.acrylic_pan_monitor.protocol import (
    EventData,
    EventAssembler,
    Frame,
    MessageType,
    decode_ai_result,
    decode_event,
    decode_event_chunk,
    decode_inference_event,
    encode_frame,
)
from pc.acrylic_pan_monitor.recorder import Recorder, ReceiveStats, make_demo_event
from pc.acrylic_pan_monitor.serial_link import SerialLink, available_ports
from pc.acrylic_pan_monitor.signal_processing import prepare_plot_data
from pc.acrylic_pan_web.position_model import DEFAULT_MODEL_PATH, PositionEstimator


STATIC_DIR = Path(__file__).with_name("static")
@dataclass(frozen=True)
class PanelProfile:
    profile_id: str
    label: str
    width_mm: float
    height_mm: float
    thickness_mm: float
    columns: int
    rows: int

    @property
    def class_count(self) -> int:
        return self.columns * self.rows

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.profile_id,
            "label": self.label,
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "thickness_mm": self.thickness_mm,
            "columns": self.columns,
            "rows": self.rows,
            "class_count": self.class_count,
            "area_width_mm": self.width_mm / self.columns,
            "area_height_mm": self.height_mm / self.rows,
            "clamp": dict(CLAMP_FOOTPRINT_MM),
        }
DUMMY_MODEL_SAMPLE_RATE_HZ = 25_600
SENSOR_RANGE_G = 32
SENSOR_COUNTS_PER_G = 1024
COLLECTION_SAMPLE_RATE_HZ = 25_600
COLLECTION_SAMPLE_COUNT = 2_048
COLLECTION_TRIGGER_INDEX = 64

# docs/design.md section 3 defines two acquisition series over the same panel:
#   A: the eight area centres          -> "center"
#   B: a 50 mm grid, X=25..375, Y=25..175 -> "corners"
# Within each 100 x 100 mm area the B grid lands exactly on the four (+-25, +-25)
# diagonal points, so 8 areas x 4 points reproduces the specified 32 grid points.
POSITION_PATTERNS: dict[str, tuple[tuple[str, float, float], ...]] = {
    "center": (("center", 0.0, 0.0),),
    "corners": (
        ("up_left", -25.0, -25.0),
        ("up_right", 25.0, -25.0),
        ("down_left", -25.0, 25.0),
        ("down_right", 25.0, 25.0),
    ),
}

# The clamp of docs/design.md section 2 holds x=200..300 mm, y=0..20 mm, which
# leaves the two grid points at y=25 only 5 mm clear of it. Section 3 moves them
# to (225, 35) and (275, 35). The exception is per-area by nature, so it is
# applied to absolute panel coordinates exactly as the specification states it.
CLAMP_FOOTPRINT_MM = {"x_min": 200.0, "x_max": 300.0, "y_min": 0.0, "y_max": 20.0}
CLAMP_POINT_MOVES: dict[tuple[float, float], tuple[float, float]] = {
    (225.0, 25.0): (225.0, 35.0),
    (275.0, 25.0): (275.0, 35.0),
}

PANEL_PROFILES: dict[str, PanelProfile] = {
    "400x200x3": PanelProfile(
        "400x200x3", "400 × 200 × 3 mm（8クラス）", 400.0, 200.0, 3.0, 4, 2
    ),
    "400x300x5": PanelProfile(
        "400x300x5", "400 × 300 × 5 mm（12クラス）", 400.0, 300.0, 5.0, 4, 3
    ),
}
DEFAULT_PANEL_PROFILE_ID = "400x200x3"


def get_panel_profile(profile_id: str = DEFAULT_PANEL_PROFILE_ID) -> PanelProfile:
    try:
        return PANEL_PROFILES[profile_id]
    except KeyError as error:
        raise ValueError(f"unknown panel_profile_id: {profile_id}") from error


def panel_info(profile: PanelProfile | None = None) -> dict[str, Any]:
    """Panel geometry for the GUI, so no dimension is duplicated in JavaScript."""
    return (profile or get_panel_profile()).as_dict()


def event_payload(event: EventData, source: str) -> dict[str, Any]:
    """Build the waveform/FFT payload shared by live, demo, and stored events."""
    plot = prepare_plot_data(event)
    return {
        "sequence": event.sequence,
        "sample_rate_hz": event.sample_rate_hz,
        "trigger_index": event.trigger_index,
        "trigger_time_ms": plot.trigger_time_ms,
        "peak_abs": event.peak_abs,
        "flags": event.flags,
        "timestamp_us": event.timestamp_us,
        "time_ms": plot.time_ms.tolist(),
        "samples": plot.samples.tolist(),
        "frequency_hz": plot.frequency_hz.tolist(),
        "magnitude_db": plot.magnitude_db.tolist(),
        "source": source,
    }


def prepare_dummy_input_plot(
    golden_case: dict[str, Any], board_case_id: int
) -> dict[str, Any]:
    """Build plot data from normalized dummy-model input, not sensor ADC data."""
    samples = np.asarray(golden_case["input"], dtype=np.float64)
    if samples.ndim != 1 or len(samples) != 128:
        raise ValueError("dummy model input must contain exactly 128 samples")
    centered = samples - samples.mean()
    window = np.hanning(len(samples))
    spectrum = np.fft.rfft(centered * window)
    coherent_gain = max(window.sum() / 2.0, 1.0)
    magnitude = np.abs(spectrum) / coherent_gain
    magnitude_db = 20.0 * np.log10(np.maximum(magnitude, 1e-9))
    return {
        "time_ms": (
            np.arange(len(samples), dtype=np.float64)
            * 1000.0
            / DUMMY_MODEL_SAMPLE_RATE_HZ
        ).tolist(),
        "samples": samples.tolist(),
        "frequency_hz": np.fft.rfftfreq(
            len(samples), 1.0 / DUMMY_MODEL_SAMPLE_RATE_HZ
        ).tolist(),
        "magnitude_db": magnitude_db.tolist(),
        "sample_rate_hz": DUMMY_MODEL_SAMPLE_RATE_HZ,
        "source": "dummy_model_input",
        "case_id": board_case_id,
        "sample_units": "normalized_model_input",
        "is_physical_sensor_data": False,
    }


@dataclass(frozen=True)
class CollectionTarget:
    class_id: int
    point_id: int
    point_name: str
    x_mm: float
    y_mm: float
    offset_x_mm: float
    offset_y_mm: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "class_id": self.class_id,
            "point_id": self.point_id,
            "point_name": self.point_name,
            "x_mm": self.x_mm,
            "y_mm": self.y_mm,
            "offset": {"x_mm": self.offset_x_mm, "y_mm": self.offset_y_mm},
        }


def build_collection_targets(
    pattern: str, profile: PanelProfile | None = None
) -> tuple[CollectionTarget, ...]:
    try:
        positions = POSITION_PATTERNS[pattern]
    except KeyError as error:
        raise ValueError("position_pattern must be center or corners") from error
    profile = profile or get_panel_profile()
    targets: list[CollectionTarget] = []
    for class_id in range(profile.class_count):
        column, row = class_id % profile.columns, class_id // profile.columns
        center_x = (column + 0.5) * profile.width_mm / profile.columns
        center_y = (row + 0.5) * profile.height_mm / profile.rows
        for point_id, (name, offset_x, offset_y) in enumerate(positions):
            x, y = center_x + offset_x, center_y + offset_y
            moved = CLAMP_POINT_MOVES.get((x, y))
            if moved is not None:
                # Re-derive the offset so x - offset_x still recovers the area
                # centre, which the guided-run validator relies on.
                x, y = moved
                offset_x, offset_y = x - center_x, y - center_y
            targets.append(CollectionTarget(
                class_id, point_id, name, x, y, offset_x, offset_y,
            ))
    return tuple(targets)


@dataclass
class CollectionState:
    """Progress for eight-area and intra-area targets.

    Targets are normally filled in order, but ``selected_index`` lets the
    operator jump to any incomplete point. Because points can therefore be
    filled out of order, the current point is derived from ``target_counts``
    rather than from how many samples have been taken so far.
    """

    active: bool = False
    finished: bool = False
    repetitions: int = 0
    completed_samples: int = 0
    per_class_counts: list[int] = field(default_factory=lambda: [0] * 8)
    position_pattern: str = "center"
    targets: tuple[CollectionTarget, ...] = field(
        default_factory=lambda: build_collection_targets("center")
    )
    target_counts: list[int] = field(default_factory=lambda: [0] * 8)
    order: tuple[int, ...] = tuple(range(8))
    selected_index: int | None = None
    panel_profile_id: str = DEFAULT_PANEL_PROFILE_ID

    @property
    def total_samples(self) -> int:
        return len(self.targets) * self.repetitions

    def is_complete(self, index: int) -> bool:
        return self.repetitions > 0 and self.target_counts[index] >= self.repetitions

    def first_incomplete_index(self) -> int | None:
        for index in range(len(self.targets)):
            if not self.is_complete(index):
                return index
        return None

    @property
    def current_target_index(self) -> int | None:
        """The point being collected: the operator's pick, else the next gap."""
        if not self.active or self.completed_samples >= self.total_samples:
            return None
        if self.selected_index is not None and not self.is_complete(self.selected_index):
            return self.selected_index
        return self.first_incomplete_index()

    @property
    def current_target(self) -> CollectionTarget | None:
        index = self.current_target_index
        return self.targets[index] if index is not None else None

    @property
    def current_class_id(self) -> int | None:
        target = self.current_target
        return target.class_id if target is not None else None

    def as_dict(self) -> dict[str, Any]:
        profile = get_panel_profile(self.panel_profile_id)
        target_index = self.current_target_index
        target = self.current_target
        current = target.class_id if target is not None else None
        current_count = self.target_counts[target_index] if target_index is not None else None
        return {
            "active": self.active,
            "finished": self.finished,
            "repetitions": self.repetitions,
            "completed_samples": self.completed_samples,
            "total_samples": self.total_samples,
            "current_class_id": current,
            "current_point_id": target.point_id if target is not None else None,
            "current_point_name": target.point_name if target is not None else None,
            "current_x_mm": target.x_mm if target is not None else None,
            "current_y_mm": target.y_mm if target is not None else None,
            "current_offset": (
                {"x_mm": target.offset_x_mm, "y_mm": target.offset_y_mm}
                if target is not None else None
            ),
            "current_target": target.as_dict() if target is not None else None,
            "current_target_index": target_index,
            "current_repetition": current_count + 1 if current_count is not None else None,
            "selected_index": self.selected_index,
            "per_class_counts": list(self.per_class_counts),
            "per_position_counts": [
                {
                    **item.as_dict(),
                    "target_index": index,
                    "count": self.target_counts[index],
                    "complete": self.is_complete(index),
                }
                for index, item in enumerate(self.targets)
            ],
            "position_pattern": self.position_pattern,
            "points_per_class": len(POSITION_PATTERNS[self.position_pattern]),
            "samples_per_class": len(POSITION_PATTERNS[self.position_pattern]) * self.repetitions,
            "panel_profile_id": self.panel_profile_id,
            "panel": panel_info(profile),
            "targets": [item.as_dict() for item in self.targets],
            "order": list(self.order),
        }


class AcquisitionController:
    """Thread-safe bridge between serial I/O, Recorder, and the HTTP layer."""

    def __init__(
        self,
        output_root: str | Path = "data/raw/sessions",
        golden_path: str | Path = DEFAULT_GOLDEN_PATH,
        position_model_path: str | Path = DEFAULT_MODEL_PATH,
    ) -> None:
        self.output_root = Path(output_root).resolve()
        self.port: str | None = None
        self.baudrate = 115_200
        self.auto_save = True
        self.class_id: int | None = None
        self.stats = ReceiveStats()
        self.recorder: Recorder | None = None
        self.latest: dict[str, Any] | None = None
        self.latest_ai: dict[str, Any] | None = None
        self.golden_path = Path(golden_path).resolve()
        self.position_estimator = PositionEstimator(position_model_path)
        self.panel_profile_id = DEFAULT_PANEL_PROFILE_ID
        self.collection = self._empty_collection()
        self.identity: str | None = None
        self.last_error: str | None = None
        self.last_control: dict[str, Any] | None = None
        self.device_mode = "unknown"
        self.inference_active = False
        self.event_assembler = EventAssembler(timeout_seconds=2.0)
        self.assembly_retry_required = False
        self._control_waiters: dict[int, tuple[threading.Event, dict[str, Any]]] = {}
        self._command_sequence = 1
        self._queue: queue.Queue[Frame | Exception] = queue.Queue()
        self._lock = threading.RLock()
        self._ai_condition = threading.Condition(self._lock)
        self._stop = threading.Event()
        self.link = SerialLink(self._queue.put, self._queue.put)
        self._worker = threading.Thread(target=self._consume, name="apan-web-consumer", daemon=True)
        self._worker.start()

    @property
    def panel_profile(self) -> PanelProfile:
        return get_panel_profile(self.panel_profile_id)

    def _empty_collection(self) -> CollectionState:
        profile = get_panel_profile(self.panel_profile_id)
        targets = build_collection_targets("center", profile)
        return CollectionState(
            per_class_counts=[0] * profile.class_count,
            targets=targets,
            target_counts=[0] * len(targets),
            order=tuple(range(profile.class_count)),
            panel_profile_id=profile.profile_id,
        )

    def set_panel_profile(self, profile_id: str) -> dict[str, Any]:
        profile = get_panel_profile(profile_id)
        with self._lock:
            if self.collection.active:
                raise ValueError("採取中は板仕様を変更できません。採取を停止してください。")
            self.panel_profile_id = profile.profile_id
            self.collection = self._empty_collection()
            self.class_id = None
        return self.status()

    def connect(self, port: str, baudrate: int = 115_200) -> None:
        self.link.connect(port, baudrate)
        with self._lock:
            self.event_assembler.reset()
            self.assembly_retry_required = False
            self.port, self.baudrate, self.last_error = port, baudrate, None

    def disconnect(self) -> None:
        self.link.disconnect()
        with self._lock:
            self.event_assembler.reset()
            self.assembly_retry_required = False
            if self.collection.active:
                self.collection.active = False
                self.collection.finished = False
            self.inference_active = False
            self.device_mode = "unknown"

    def set_mode(self, mode: str) -> dict[str, Any]:
        """Set and confirm the firmware operating mode through its ACK."""
        values = {"collection": 0, "inference": 1, "instrument": 2}
        if mode not in values:
            raise ValueError("mode must be collection, inference, or instrument")
        with self._lock:
            if self.collection.active or self.inference_active:
                raise ValueError("動作中はモードを切り替えられません。先に停止してください。")
            if not self.link.connected:
                raise OSError("serial port is not connected")
            sequence = self._command_sequence
            self._command_sequence += 1
            completed = threading.Event()
            response: dict[str, Any] = {}
            self._control_waiters[sequence] = (completed, response)
            self.last_control = {"command": "set_mode", "mode": mode,
                                 "sequence": sequence, "state": "pending"}
        self.link.send(encode_frame(Frame(MessageType.SET_MODE, sequence, bytes([values[mode]]))))
        if not completed.wait(1.5):
            with self._lock:
                self._control_waiters.pop(sequence, None)
            raise TimeoutError("ファームからモード変更の応答がありません")
        if response.get("type") != "ack":
            raise ValueError("ファームがモード変更を拒否しました")
        with self._lock:
            self.device_mode = mode
            self.event_assembler.reset()
            self.assembly_retry_required = False
            self.last_control = {"command": "set_mode", "mode": mode,
                                 "sequence": sequence, "state": "confirmed"}
            return {"mode": mode, "confirmed": True, "sequence": sequence}

    def start_inference(self, mode: str = "inference") -> dict[str, Any]:
        if self.collection.active:
            raise ValueError("データ採取中は推論を開始できません")
        if mode not in ("inference", "instrument"):
            raise ValueError("inference mode must be inference or instrument")
        if self.device_mode != mode:
            self.set_mode(mode)
        with self._lock:
            self.inference_active = True
        try:
            self.send_command("start")
        except Exception:
            with self._lock:
                self.inference_active = False
            raise
        return {"active": True, "mode": self.device_mode}

    def set_retrigger_guard(self, milliseconds: int) -> dict[str, Any]:
        """Configure the firmware-side instrument event acceptance interval."""
        if not 0 <= milliseconds <= 500:
            raise ValueError("milliseconds must be between 0 and 500")
        if not self.link.connected:
            raise OSError("serial port is not connected")
        with self._lock:
            sequence = self._command_sequence
            self._command_sequence += 1
            completed = threading.Event()
            response: dict[str, Any] = {}
            self._control_waiters[sequence] = (completed, response)
            self.last_control = {
                "command": "set_retrigger_guard",
                "milliseconds": milliseconds,
                "sequence": sequence,
                "state": "pending",
            }
        payload = milliseconds.to_bytes(2, "little")
        self.link.send(encode_frame(Frame(MessageType.SET_CONFIG, sequence, payload)))
        if not completed.wait(1.5):
            with self._lock:
                self._control_waiters.pop(sequence, None)
            raise TimeoutError("ファームから連打間隔設定の応答がありません")
        if response.get("type") != "ack":
            raise ValueError("ファームが連打間隔設定を拒否しました")
        with self._lock:
            self.last_control = {
                "command": "set_retrigger_guard",
                "milliseconds": milliseconds,
                "sequence": sequence,
                "state": "confirmed",
            }
        return {"milliseconds": milliseconds, "confirmed": True, "sequence": sequence}

    def stop_inference(self) -> dict[str, Any]:
        with self._lock:
            self.inference_active = False
        if self.link.connected:
            self.send_command("stop")
        return {"active": False, "mode": self.device_mode}

    def send_command(self, command: str) -> dict[str, Any]:
        """Invoke a collector function through the framed UART API."""
        kinds = {
            "ping": MessageType.HELLO,
            "status": MessageType.STATUS,
            "capture": MessageType.CAPTURE,
            "start": MessageType.START,
            "stop": MessageType.STOP,
        }
        try:
            kind = kinds[command.lower()]
        except KeyError as error:
            raise ValueError(f"unsupported command: {command}") from error
        if not self.link.connected:
            raise OSError("serial port is not connected")
        with self._lock:
            sequence = self._command_sequence
            self._command_sequence += 1
            self.last_control = {"command": command.lower(), "sequence": sequence, "state": "sent"}
        self.link.send(encode_frame(Frame(kind, sequence)))
        return dict(self.last_control)

    def send_ai_selftest(self, case_id: int) -> dict[str, Any]:
        if not 0 <= case_id <= 255:
            raise ValueError("case_id must be between 0 and 255")
        if not self.link.connected:
            raise OSError("serial port is not connected")
        with self._lock:
            sequence = self._command_sequence
            self._command_sequence += 1
            self.last_control = {
                "command": "ai_selftest",
                "case_id": case_id,
                "sequence": sequence,
                "state": "sent",
            }
        self.link.send(encode_frame(Frame(MessageType.AI_SELFTEST, sequence, bytes([case_id]))))
        return dict(self.last_control)

    def start_collection(
        self,
        repetitions: int,
        output_root: str | Path | None = None,
        position_pattern: str = "center",
        panel_profile_id: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= repetitions <= 1000:
            raise ValueError("repetitions must be between 1 and 1000")
        if not self.link.connected:
            raise OSError("serial port is not connected")
        if self.inference_active:
            raise ValueError("推論中はデータ採取を開始できません")
        # Legacy collector firmware and a freshly reset board both boot in
        # collection mode.  Only issue a synchronous switch when inference was
        # previously confirmed; the UI explicitly confirms its selected tab.
        # A freshly connected/legacy board reports "unknown" but starts in
        # collection mode.  Only request a mode switch when we know it is in
        # one of the inference modes; otherwise mocked/legacy links would
        # wait for an ACK they cannot provide.
        if self.device_mode in ("inference", "instrument"):
            self.set_mode("collection")
        profile = get_panel_profile(panel_profile_id or self.panel_profile_id)
        with self._lock:
            if self.collection.active:
                raise ValueError("collection is already active")
            self.panel_profile_id = profile.profile_id
            targets = build_collection_targets(position_pattern, profile)
            self.new_session(output_root, class_id=None, metadata={
                "mode": "guided_area_points",
                "panel_profile_id": profile.profile_id,
                "panel": panel_info(profile),
                "collection_plan": {
                    "area_count": profile.class_count,
                    "repetitions": repetitions,
                    "position_pattern": position_pattern,
                    "points_per_class": len(POSITION_PATTERNS[position_pattern]),
                    "point_count": len(POSITION_PATTERNS[position_pattern]),
                    "panel": panel_info(profile),
                    "order": list(range(profile.class_count)),
                    "targets": [target.as_dict() for target in targets],
                    "total_samples": len(targets) * repetitions,
                },
            })
            self.collection = CollectionState(
                active=True,
                repetitions=repetitions,
                position_pattern=position_pattern,
                targets=targets,
                target_counts=[0] * len(targets),
                per_class_counts=[0] * profile.class_count,
                order=tuple(range(profile.class_count)),
                panel_profile_id=profile.profile_id,
            )
            self.event_assembler.reset()
            self.assembly_retry_required = False
        try:
            self.send_command("start")
        except Exception:
            with self._lock:
                self.collection.active = False
            raise
        return self.collection_status()

    def stop_collection(self) -> dict[str, Any]:
        with self._lock:
            self.event_assembler.reset()
            self.assembly_retry_required = False
            self.collection.active = False
            self.collection.finished = False
        if self.link.connected:
            self.send_command("stop")
        return self.collection_status()

    def select_target(self, target_index: int) -> dict[str, Any]:
        """Jump to any incomplete point, ignoring the default fill order."""
        with self._lock:
            if not self.collection.active:
                raise ValueError("採取を開始してから打点を選択してください。")
            if self.event_assembler.inflight:
                raise ValueError("振動データを受信中です。受信完了後に打点を変更してください。")
            if not 0 <= target_index < len(self.collection.targets):
                raise ValueError("打点の番号が範囲外です。")
            if self.collection.is_complete(target_index):
                raise ValueError("この打点は必要な回数の採取が完了しています。")
            self.collection.selected_index = target_index
            return self.collection.as_dict()

    def preview_targets(self, pattern: str, profile_id: str | None = None) -> dict[str, Any]:
        """Target geometry for a pattern, so the panel can be drawn before start."""
        profile = get_panel_profile(profile_id or self.panel_profile_id)
        targets = build_collection_targets(pattern, profile)
        return {
            "position_pattern": pattern,
            "points_per_class": len(POSITION_PATTERNS[pattern]),
            "panel_profile_id": profile.profile_id,
            "panel": {
                "width_mm": profile.width_mm,
                "height_mm": profile.height_mm,
                "clamp": dict(CLAMP_FOOTPRINT_MM),
            },
            "targets": [
                {**target.as_dict(), "target_index": index, "count": 0, "complete": False}
                for index, target in enumerate(targets)
            ],
        }

    def collection_status(self) -> dict[str, Any]:
        with self._lock:
            return self.collection.as_dict()

    def undo_last_collection_event(self, expected_completed_samples: int) -> dict[str, Any]:
        """Delete the latest guided sample and guide the operator there again."""
        with self._lock:
            if not self.collection.active:
                raise ValueError("採取中のみ直前の測定を取り消せます。")
            if self.event_assembler.inflight:
                raise ValueError("振動データを受信中のため、直前データを変更できません。")
            if expected_completed_samples != self.collection.completed_samples:
                raise ValueError(
                    "採取進捗が更新されています。画面を確認してからもう一度操作してください。"
                )
            if self.recorder is None or self.recorder.session_dir is None:
                raise ValueError("採取中のセッションが見つかりません。")

            library = Library(self.output_root)
            directory = library.session_dir(self.recorder.session_id)
            if not self._is_recorder_session(directory):
                raise ValueError("採取中のセッションを確認できません。")

            events = library.list_events(self.recorder.session_id)
            if not events:
                raise ValueError("取り消せる測定データがありません。")
            event = events[-1]
            annotations = event.get("annotations") or {}
            if not annotations.get("collection"):
                raise ValueError("直前のデータはガイド採取データではありません。")

            class_id = int(annotations["target_class_id"])
            point_id = int(annotations["target_point_id"])
            target_index = next(
                (
                    index
                    for index, target in enumerate(self.collection.targets)
                    if target.class_id == class_id and target.point_id == point_id
                ),
                None,
            )
            if target_index is None:
                raise ValueError("直前データに対応する測定位置が見つかりません。")
            if (
                self.collection.completed_samples <= 0
                or self.collection.per_class_counts[class_id] <= 0
                or self.collection.target_counts[target_index] <= 0
            ):
                raise ValueError("採取進捗と保存データが一致しないため取り消せません。")

            library.delete_event(self.recorder.session_id, int(event["index"]))
            self._resync_recorder(directory)
            self.collection.completed_samples -= 1
            self.collection.per_class_counts[class_id] -= 1
            self.collection.target_counts[target_index] -= 1
            self.collection.selected_index = target_index
            self.collection.finished = False

            result = self.collection.as_dict()
            result["undone_event"] = {
                "index": int(event["index"]),
                "class_id": class_id,
                "target_index": target_index,
                "point_id": point_id,
                "repetition": int(annotations.get("repetition", 0)),
            }
            return result

    def new_session(
        self,
        output_root: str | Path | None = None,
        class_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        with self._lock:
            if class_id is not None and not 0 <= class_id < self.panel_profile.class_count:
                raise ValueError(
                    f"class_id must be between 0 and {self.panel_profile.class_count - 1}"
                )
            if self.recorder is not None:
                self.recorder.close()
            if output_root is not None:
                self.output_root = Path(output_root).expanduser().resolve()
            self.class_id = class_id
            self.recorder = Recorder(
                self.output_root, max_class_id=self.panel_profile.class_count - 1
            )
            session_metadata: dict[str, Any] = {
                "application": "acrylic_pan_web",
                "serial_port": self.port,
                "baudrate": self.baudrate,
                "device_identity": self.identity,
                "panel_profile_id": self.panel_profile_id,
                "panel": panel_info(self.panel_profile),
                "sensor_configuration": {
                    "device": "KX134-1211",
                    "axis": "Z",
                    "range_g": SENSOR_RANGE_G,
                    "counts_per_g": SENSOR_COUNTS_PER_G,
                    "sample_rate_hz": 25_600,
                    "capture_samples": 2_048,
                    "capture_duration_ms": 80.0,
                    "pretrigger_samples": 64,
                },
            }
            session_metadata.update(metadata or {})
            return self.recorder.begin_session(session_metadata)

    def demo(self) -> dict[str, Any]:
        with self._lock:
            sequence = (self.latest or {}).get("sequence", 0) + 1
        event = make_demo_event(sequence)
        return self._process_event(event, source="demo", count_as_received=False)

    def _library(self, root: str | Path | None = None) -> Library:
        if root:
            return Library(Path(str(root)).expanduser())
        with self._lock:
            return Library(self.output_root)

    def list_sessions(self, root: str | Path | None = None) -> dict[str, Any]:
        library = self._library(root)
        return {"root": str(library.root), "sessions": library.list_sessions()}

    def list_stored_events(self, session_id: str, root: str | Path | None = None) -> dict[str, Any]:
        library = self._library(root)
        return {
            "root": str(library.root),
            "session_id": session_id,
            "events": library.list_events(session_id),
        }

    def load_stored_event(
        self, session_id: str, index: int, root: str | Path | None = None
    ) -> dict[str, Any]:
        """Return one saved waveform in the same shape as a live event.

        This deliberately does not touch ``self.latest``: browsing the archive
        must not overwrite the most recent captured event.
        """
        event, record = self._library(root).load_event(session_id, index)
        payload = event_payload(event, "library")
        payload["stored"] = {"session_id": session_id, **record}
        return payload

    def delete_stored_event(
        self, session_id: str, index: int, root: str | Path | None = None
    ) -> dict[str, Any]:
        """Delete one saved event, keeping a live Recorder's count in step."""
        with self._lock:
            if self.collection.active:
                raise ValueError("採取中は削除できません。採取を停止してから削除してください。")
            library = self._library(root)
            directory = library.session_dir(session_id)
            result = library.delete_event(session_id, index)
            self._resync_recorder(directory)
            return result

    def delete_stored_session(
        self, session_id: str, root: str | Path | None = None
    ) -> dict[str, Any]:
        with self._lock:
            self.event_assembler.reset()
            self.assembly_retry_required = False
            library = self._library(root)
            directory = library.session_dir(session_id)
            is_current_session = self.recorder is not None and self._is_recorder_session(directory)
            if self.collection.active and is_current_session:
                raise ValueError("採取中のセッションのため削除できません。")
            if is_current_session:
                # A stopped collection can still leave its Recorder open even
                # though the board is no longer producing guided samples.  Do
                # not force the operator to create a throwaway session merely
                # to delete the final real session in the library.
                self.recorder.close()
                self.recorder = None
                self.collection = self._empty_collection()
            return library.delete_session(session_id)

    def _is_recorder_session(self, directory: Path) -> bool:
        if self.recorder is None or self.recorder.session_dir is None:
            return False
        return self.recorder.session_dir.resolve() == directory and self.recorder.active

    def _resync_recorder(self, directory: Path) -> None:
        if self._is_recorder_session(directory):
            assert self.recorder is not None
            self.recorder.refresh_event_count()

    def status(self) -> dict[str, Any]:
        with self._lock:
            self.stats.decoder_errors = self.link.decoder_error_count
            return {
                "connected": self.link.connected,
                "port": self.port,
                "baudrate": self.baudrate,
                "identity": self.identity,
                "last_error": self.last_error,
                "last_control": self.last_control,
                "latest_ai": self.latest_ai,
                "device_mode": self.device_mode,
                "inference_active": self.inference_active,
                "inference": {
                    "active": self.inference_active,
                    "mode": self.device_mode,
                    "position_model_available": (
                        self.position_estimator.available
                        and self.panel_profile_id == DEFAULT_PANEL_PROFILE_ID
                    ),
                    "latest_sequence": (
                        self.latest_ai.get("sequence") if self.latest_ai else None
                    ),
                },
                "golden_path": str(self.golden_path),
                "panel_profile_id": self.panel_profile_id,
                "panel": panel_info(self.panel_profile),
                "panel_profiles": [profile.as_dict() for profile in PANEL_PROFILES.values()],
                "collection": self.collection.as_dict(),
                "assembly": {
                    "inflight": self.event_assembler.inflight,
                    "completed": self.event_assembler.completed,
                    "duplicates": self.event_assembler.duplicates,
                    "conflicts": self.event_assembler.conflicts,
                    "timed_out": self.event_assembler.timed_out,
                    "retry_required": self.assembly_retry_required,
                    "progress": self.event_assembler.progress,
                },
                "auto_save": self.auto_save,
                "class_id": self.class_id,
                "output_root": str(self.output_root),
                "session_dir": str(self.recorder.session_dir) if self.recorder and self.recorder.session_dir else None,
                "stats": {
                    "frames_received": self.stats.frames_received,
                    "events_received": self.stats.events_received,
                    "decoder_errors": self.stats.decoder_errors,
                    "missing_sequences": self.stats.missing_sequences,
                    "duplicate_sequences": self.stats.duplicate_sequences,
                    "out_of_order_sequences": self.stats.out_of_order_sequences,
                    "events_saved": self.stats.events_saved,
                    "save_errors": self.stats.save_errors,
                },
            }

    def wait_for_ai(self, after_sequence: int | None, timeout: float = 1.0) -> dict[str, Any]:
        """Wait for a new inference result without browser polling latency."""
        deadline = time.monotonic() + max(0.0, min(timeout, 5.0))
        with self._ai_condition:
            while not self._stop.is_set():
                latest = self.latest_ai
                if latest is not None and (
                    after_sequence is None or latest.get("sequence") != after_sequence
                ):
                    return dict(latest)
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return {}
                self._ai_condition.wait(remaining)
        return {}

    def close(self) -> None:
        self._stop.set()
        self.link.disconnect()
        self._worker.join(timeout=1)
        with self._lock:
            if self.recorder is not None:
                self.recorder.close()

    def _ensure_session(self) -> None:
        if self.recorder is None or not self.recorder.active:
            self.new_session(class_id=self.class_id)

    def _process_event(self, event: EventData, source: str, count_as_received: bool,
                       save_allowed: bool = True) -> dict[str, Any]:
        payload = event_payload(event, source)
        rearm = False
        with self._lock:
            guided_collection = self.collection.active and source == "serial"
        # The obsolete collector-baseline firmware has a distinctive 512/128
        # capture contract.  Reject that exact live signature; unit/demo events
        # intentionally remain smaller while retaining the current trigger.
        contract_mismatch = guided_collection and (
            event.sample_rate_hz == COLLECTION_SAMPLE_RATE_HZ
            and len(event.samples) == 512
            and event.trigger_index == 128
        )
        if contract_mismatch:
            with self._lock:
                if count_as_received:
                    self.stats.observe_event(event.sequence)
                self.latest = payload
                self.collection.active = False
                self.collection.finished = False
                self.last_error = (
                    "採取を停止しました。接続中のボードは旧収録仕様 "
                    f"({len(event.samples)}点・トリガ位置{event.trigger_index}) です。"
                    f"学習データ採取には現行ファームウェア "
                    f"({COLLECTION_SAMPLE_COUNT}点・トリガ位置{COLLECTION_TRIGGER_INDEX}) "
                    "を書き込んでください。このイベントは保存していません。"
                )
            if self.link.connected:
                try:
                    self.send_command("stop")
                except Exception:
                    pass
            return payload
        with self._lock:
            if count_as_received:
                self.stats.observe_event(event.sequence)
            self.latest = payload
            if self.auto_save and save_allowed:
                try:
                    self._ensure_session()
                    assert self.recorder is not None
                    collection_target = (
                        self.collection.current_target
                        if self.collection.active and source == "serial"
                        else None
                    )
                    collection_target_index = self.collection.current_target_index
                    collection_class = (
                        collection_target.class_id if collection_target is not None else None
                    )
                    label = collection_class if collection_class is not None else self.class_id
                    annotations: dict[str, Any] = {"source": source}
                    if collection_target is not None and collection_target_index is not None:
                        annotations.update({
                            "collection": True,
                            "collection_index": self.collection.completed_samples,
                            "target_index": collection_target_index,
                            "target_class_id": collection_target.class_id,
                            "target_area": collection_target.class_id + 1,
                            "target_point_id": collection_target.point_id,
                            "target_point_name": collection_target.point_name,
                            "target_x_mm": collection_target.x_mm,
                            "target_y_mm": collection_target.y_mm,
                            "offset_x_mm": collection_target.offset_x_mm,
                            "offset_y_mm": collection_target.offset_y_mm,
                            "position_pattern": self.collection.position_pattern,
                            "repetition": self.collection.target_counts[collection_target_index] + 1,
                        })
                    self.recorder.record_event(event, class_id=label, annotations=annotations)
                    self.stats.events_saved += 1
                    if collection_class is not None and collection_target_index is not None:
                        self.collection.per_class_counts[collection_class] += 1
                        self.collection.target_counts[collection_target_index] += 1
                        self.collection.completed_samples += 1
                        if self.collection.is_complete(collection_target_index):
                            # Release the manual pick so the guide moves on by itself.
                            self.collection.selected_index = None
                        if self.collection.completed_samples >= self.collection.total_samples:
                            self.collection.active = False
                            self.collection.finished = True
                            self.recorder.close()
                            self.last_control = {"response": "collection_complete"}
                        else:
                            rearm = True
                except Exception as error:
                    self.stats.save_errors += 1
                    self.last_error = f"保存エラー: {error}"
        if rearm:
            try:
                self.send_command("start")
            except Exception as error:
                with self._lock:
                    self.collection.active = False
                    self.last_error = f"再アームエラー: {error}"
        return payload

    def _consume(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                expired = self.event_assembler.expire()
                retry = False
                if expired:
                    with self._lock:
                        self.assembly_retry_required = self.collection.active
                        self.last_error = (
                            "長時間波形の一部を受信できなかったため、同じ打点を再測定します。"
                        )
                        retry = self.collection.active and self.link.connected
                if retry:
                    try:
                        self.send_command("start")
                        with self._lock:
                            self.assembly_retry_required = False
                    except Exception as error:
                        with self._lock:
                            self.collection.active = False
                            self.last_error = f"長時間波形の再測定に失敗しました: {error}"
                continue
            if isinstance(item, Exception):
                with self._lock:
                    self.last_error = f"通信エラー: {item}"
                    self.collection.active = False
                    self.collection.finished = False
                continue
            with self._lock:
                self.stats.observe_frame()
            if item.message_type == MessageType.EVENT_DATA:
                try:
                    self._process_event(decode_event(item), "serial", True)
                except Exception as error:
                    with self._lock:
                        self.last_error = f"イベント解析エラー: {error}"
            elif item.message_type == MessageType.EVENT_CHUNK:
                try:
                    chunk = decode_event_chunk(item)
                    with self._lock:
                        self.assembly_retry_required = False
                    event = self.event_assembler.feed(chunk)
                    if event is not None:
                        self._process_event(event, "serial", True)
                except Exception as error:
                    retry = False
                    with self._lock:
                        self.last_error = f"長時間波形の再構成エラー: {error}"
                        self.assembly_retry_required = self.collection.active
                        retry = self.collection.active and self.link.connected
                    if retry:
                        try:
                            self.send_command("start")
                            with self._lock:
                                self.assembly_retry_required = False
                        except Exception as retry_error:
                            with self._lock:
                                self.collection.active = False
                                self.last_error = f"長時間波形の再測定に失敗しました: {retry_error}"
            elif item.message_type == MessageType.HELLO:
                with self._lock:
                    self.identity = item.payload.decode("ascii", errors="replace")
                    self.last_control = {"response": "hello", "payload": self.identity, "sequence": item.sequence}
            elif item.message_type == MessageType.AI_RESULT:
                try:
                    result = decode_ai_result(item)
                    payload: dict[str, Any] = {
                        "case_id": result.case_id,
                        "predicted_class": result.predicted_class,
                        "outputs": list(result.outputs),
                        "sequence": result.sequence,
                        "timestamp_us": result.timestamp_us,
                        "comparison": {"available": False},
                    }
                    if result.case_id != 0xFF and self.golden_path.is_file():
                        golden = load_golden_case(self.golden_path, result.case_id)
                        if golden is not None:
                            payload["comparison"] = compare_ai_result(result, golden)
                            if "input" in golden:
                                payload["input_plot"] = prepare_dummy_input_plot(
                                    golden, result.case_id
                                )
                    with self._lock:
                        self.latest_ai = payload
                        self._ai_condition.notify_all()
                        self.last_control = {
                            "response": "ai_result",
                            "case_id": result.case_id,
                            "sequence": result.sequence,
                            "passed": payload["comparison"].get("passed"),
                        }
                    # Live firmware sends this priority result before its
                    # waveform.  Rearm only after INFERENCE_EVENT arrives so
                    # START cannot collide with the following telemetry TX.
                except Exception as error:
                    with self._lock:
                        self.last_error = f"AI result error: {error}"
            elif item.message_type == MessageType.INFERENCE_EVENT:
                try:
                    combined = decode_inference_event(item)
                    self._process_event(
                        combined.event, "推論時の実測波形", True, save_allowed=False
                    )
                    result = combined.result
                    payload = {
                        "case_id": result.case_id,
                        "predicted_class": result.predicted_class,
                        "outputs": list(result.outputs),
                        "sequence": result.sequence,
                        "timestamp_us": result.timestamp_us,
                        "comparison": {"available": False},
                    }
                    try:
                        payload["position"] = self.position_estimator.predict(
                            combined.event, result.outputs, result.predicted_class,
                            panel_info(self.panel_profile),
                        )
                    except Exception as position_error:
                        payload["position"] = {
                            "model_available": False,
                            "method": "position_error",
                            "error": str(position_error),
                            "scope": "位置推定モデルを適用できませんでした。",
                        }
                    with self._lock:
                        self.latest_ai = payload
                        self._ai_condition.notify_all()
                        self.last_control = {
                            "response": "inference_event",
                            "predicted_class": result.predicted_class,
                            "sequence": result.sequence,
                        }
                        rearm_inference = self.inference_active
                    if rearm_inference:
                        self.send_command("start")
                except Exception as error:
                    with self._lock:
                        self.last_error = f"Inference event error: {error}"
            elif item.message_type == MessageType.STATUS:
                with self._lock:
                    if len(item.payload) >= 11 and item.payload[10] in (0, 1, 2):
                        self.device_mode = {
                            0: "collection", 1: "inference", 2: "instrument"
                        }[item.payload[10]]
                    self.last_control = {
                        "response": "status", "payload_hex": item.payload.hex(),
                        "sequence": item.sequence, "mode": self.device_mode,
                    }
            elif item.message_type in (MessageType.ACK, MessageType.NACK):
                with self._lock:
                    waiter = self._control_waiters.pop(item.sequence, None)
                    if waiter is not None:
                        waiter[1].update({"type": item.message_type.name.lower(),
                                          "payload": item.payload})
                        waiter[0].set()
                    self.last_control = {
                        "response": item.message_type.name.lower(),
                        "payload_hex": item.payload.hex(),
                        "sequence": item.sequence,
                    }


class ApiHandler(BaseHTTPRequestHandler):
    controller: AcquisitionController

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/status":
            return self._json(self.controller.status())
        if path == "/api/ports":
            return self._json({"ports": available_ports()})
        if path == "/api/events/latest":
            return self._json(self.controller.latest or {}, HTTPStatus.OK if self.controller.latest else HTTPStatus.NO_CONTENT)
        if path == "/api/ai/latest":
            return self._json(self.controller.latest_ai or {}, HTTPStatus.OK if self.controller.latest_ai else HTTPStatus.NO_CONTENT)
        if path == "/api/ai/wait":
            query = parse_qs(parsed.query)
            raw_after = (query.get("after") or [None])[0]
            raw_timeout = (query.get("timeout") or ["1.0"])[0]
            try:
                after = None if raw_after in (None, "", "none") else int(raw_after)
                timeout = float(raw_timeout)
                return self._json(self.controller.wait_for_ai(after, timeout))
            except (TypeError, ValueError) as error:
                return self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        if path == "/api/session":
            return self._json(self.controller.status())
        if path == "/api/collection":
            return self._json(self.controller.collection_status())
        if path == "/api/collection/targets":
            query = parse_qs(parsed.query)
            pattern = (query.get("pattern") or ["center"])[0]
            profile_id = (query.get("panel_profile_id") or [None])[0]
            try:
                return self._json(self.controller.preview_targets(pattern, profile_id))
            except ValueError as error:
                return self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        if path.startswith("/api/library/"):
            return self._library_get(path, parse_qs(parsed.query))
        self._static(path)

    def _library_get(self, path: str, query: dict[str, list[str]]) -> None:
        def one(name: str) -> str | None:
            values = query.get(name)
            return values[0] if values else None

        try:
            if path == "/api/library/sessions":
                return self._json(self.controller.list_sessions(one("root")))
            if path == "/api/library/events":
                session_id = one("session")
                if session_id is None:
                    raise ValueError("session is required")
                return self._json(self.controller.list_stored_events(session_id, one("root")))
            if path == "/api/library/event":
                session_id, index = one("session"), one("index")
                if session_id is None or index is None:
                    raise ValueError("session and index are required")
                return self._json(
                    self.controller.load_stored_event(session_id, int(index), one("root"))
                )
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except LibraryError as error:
            self._json({"error": str(error)}, HTTPStatus.NOT_FOUND)
        except (KeyError, TypeError, ValueError, OSError) as error:
            self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self._request_json()
            if path == "/api/connect":
                self.controller.connect(str(body["port"]), int(body.get("baudrate", 115_200)))
                return self._json(self.controller.status())
            if path == "/api/disconnect":
                self.controller.disconnect()
                return self._json(self.controller.status())
            if path == "/api/demo":
                return self._json(self.controller.demo())
            if path == "/api/command":
                return self._json(self.controller.send_command(str(body["command"])))
            if path == "/api/ai/selftest":
                return self._json(self.controller.send_ai_selftest(int(body.get("case_id", 0))))
            if path == "/api/device/mode":
                return self._json(self.controller.set_mode(str(body["mode"])))
            if path == "/api/inference/start":
                return self._json(self.controller.start_inference(str(body.get("mode", "inference"))))
            if path == "/api/inference/retrigger":
                return self._json(self.controller.set_retrigger_guard(int(body["milliseconds"])))
            if path == "/api/inference/stop":
                return self._json(self.controller.stop_inference())
            if path == "/api/panel":
                return self._json(self.controller.set_panel_profile(str(body["panel_profile_id"])))
            if path == "/api/collection/start":
                return self._json(self.controller.start_collection(
                    int(body.get("repetitions", 10)),
                    body.get("output_root"),
                    str(body.get("position_pattern", "center")),
                    body.get("panel_profile_id"),
                ))
            if path == "/api/collection/stop":
                return self._json(self.controller.stop_collection())
            if path == "/api/collection/undo":
                return self._json(self.controller.undo_last_collection_event(
                    int(body["expected_completed_samples"])
                ))
            if path == "/api/collection/select":
                return self._json(self.controller.select_target(int(body["target_index"])))
            if path == "/api/session":
                session = self.controller.new_session(body.get("output_root"), body.get("class_id"))
                return self._json({"session_dir": str(session)})
            if path == "/api/library/delete":
                return self._json(self.controller.delete_stored_event(
                    str(body["session"]), int(body["index"]), body.get("root"),
                ))
            if path == "/api/library/delete_session":
                return self._json(self.controller.delete_stored_session(
                    str(body["session"]), body.get("root"),
                ))
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except LibraryError as error:
            self._json({"error": str(error)}, HTTPStatus.NOT_FOUND)
        except (KeyError, TypeError, ValueError, OSError) as error:
            self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:
            self._json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _request_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 65_536:
            raise ValueError("request body is too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path: str) -> None:
        names = {
            "/": "index.html",
            "/index.html": "index.html",
            "/collector.html": "collector.html",
            "/position.html": "position.html",
            "/instrument.html": "instrument.html",
            "/collector.css": "collector.css",
            "/controls.css": "controls.css",
            "/instrument.css": "instrument.css",
            "/position.css": "position.css",
            "/instrument.js": "instrument.js",
            "/position.js": "position.js",
            "/panel-profile.js": "panel-profile.js",
            "/panel-profile.css": "panel-profile.css",
            "/app.js": "app.js",
            "/style.css": "style.css",
            "/tabs.css": "tabs.css",
        }
        name = names.get(path)
        if name is None:
            return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        data = (STATIC_DIR / name).read_bytes()
        content_type = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}[Path(name).suffix]
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return


def create_server(host: str, port: int, controller: AcquisitionController) -> ThreadingHTTPServer:
    handler = type("BoundApiHandler", (ApiHandler,), {"controller": controller})
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Acrylic Pan local acquisition web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output", default="data/raw/sessions")
    parser.add_argument(
        "--page",
        choices=("index.html", "collector.html", "position.html", "instrument.html"),
        default="index.html",
    )
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    controller = AcquisitionController(args.output)
    server = create_server(args.host, args.port, controller)
    suffix = "" if args.page == "index.html" else args.page
    url = f"http://{args.host}:{server.server_port}/{suffix}"
    print(f"Acrylic Pan monitor: {url}")
    if not args.no_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        controller.close()


if __name__ == "__main__":
    main()
